"""Local-only HTTP API and control panel for the draft coordinator."""

from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import socket
import threading
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .contracts import RoomIdentity, LeagueConfig
from .coordinator import Coordinator, CoordinatorError, IdentityError
from .bridge import BrowserBridge
from .page_bundle import (
    BOOKMARKLET_PAGE_ORIGINS,
    advisor_bookmarklet_href,
    bookmarklet_bridge_page,
    build_bundle,
    recommendation_bookmarklet_runtime,
)
from .preflight import count_local_listeners, practice_preflight
from .runtime_info import runtime_info, startup_source


STATIC_DIR = Path(__file__).with_name("static")

# Pages allowed to reach the heartbeat endpoint across origins.  Only the ESPN
# draft room is listed; every other origin gets no CORS or Private Network
# Access allowance.  The CSRF token (injected by the operator, never served
# cross-origin) is still required on every cross-origin request.
PAGE_ORIGINS = frozenset({"https://fantasy.espn.com", "https://www.espn.com"})
PAGE_ALLOWED_HEADERS = ("content-type", "x-csrf-token")
# In-page guarded dispatch (default off) may additionally use these operator
# operations; every one still runs the full coordinator claim/guard/ack path.
PAGE_DISPATCH_OPERATIONS = frozenset({"prepare", "ack", "reconcile"})
# The harness-agnostic in-page runner (default off) drives the whole canonical
# sequence from the ESPN page.  Ownership claim/transfer and queue operations
# stay panel/CLI-only, and every page request must present the owner token of
# an already-owned session.
PAGE_RUNNER_OPERATIONS = frozenset({"connect-advisor", "setup-select-team", "status", "publish", "preflight", "start", "resume", "pause", "prepare", "ack", "reconcile", "recommend-preview"})
# A generic room loader receives a short-lived token that is deliberately
# narrower than normal page-runner ownership. It can observe its session and
# request an in-memory recommendation preview, but can never control ESPN or
# enter any action/queue lifecycle.
ADVISOR_TOKEN_OPERATIONS = frozenset({"status", "recommend-preview"})


def _canonical_page_origin(origin: str | None, page_origins: frozenset[str]) -> str | None:
    """Return the matching trusted origin value, never the request header.

    Header values are untrusted even when they compare equal to an allowlist
    entry.  Comparing against and returning the configured entry keeps values
    used in response headers on the trusted side of the boundary, while the
    explicit CR/LF checks reject header injection and malformed configuration.
    """
    if not isinstance(origin, str) or not origin or not origin.isascii() or "\r" in origin or "\n" in origin:
        return None
    for allowed in page_origins:
        if not isinstance(allowed, str) or not allowed or not allowed.isascii() or "\r" in allowed or "\n" in allowed:
            continue
        if origin == allowed:
            return allowed
    return None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


class DraftRequestHandler(BaseHTTPRequestHandler):
    server_version = "FBLDraftControl/1.0"

    @property
    def coordinator(self) -> Coordinator:
        return self.server.coordinator  # type: ignore[attr-defined]

    @property
    def coordinator_lock(self) -> threading.RLock:
        return self.server.coordinator_lock  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # The panel is local; avoid logging query strings or user-provided URLs.
        return

    def _host_ok(self) -> bool:
        raw = self.headers.get("Host", "")
        host = raw.split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost"}:
            return False
        try:
            port = int(raw.rsplit(":", 1)[1]) if ":" in raw else int(self.server.server_port)
        except ValueError:
            return False
        return port == int(self.server.server_port)

    def _origin_ok(self) -> bool:
        if not self._host_ok():
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            try:
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError:
                return False
            return parsed.scheme in {"http", "https"} and origin_host in {"127.0.0.1", "localhost"} and origin_port == int(self.server.server_port)
        return secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.coordinator.csrf_token)

    @property
    def page_origins(self) -> frozenset[str]:
        return getattr(self.server, "page_origins", PAGE_ORIGINS)

    def _page_origin(self) -> str | None:
        return _canonical_page_origin(self.headers.get("Origin"), self.page_origins)

    def _page_post_ok(self) -> bool:
        """A cross-origin POST from an allowed page: host, origin and token."""
        return bool(self._host_ok() and self._page_origin() and secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.coordinator.csrf_token))

    def _page_path_allowed(self, path: str, body: dict[str, Any]) -> bool:
        if path == "/api/heartbeat":
            return True
        if path == "/api/operator":
            operation = str(body.get("operation", "")).strip().lower().replace("_", "-")
            if getattr(self.server, "page_runner_enabled", False) and operation in PAGE_RUNNER_OPERATIONS:
                return True
            if getattr(self.server, "page_dispatch_enabled", False):
                return operation in PAGE_DISPATCH_OPERATIONS
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        """CORS + Private Network Access preflight for the in-page heartbeat."""
        parsed = urlparse(self.path)
        origin = self._page_origin()
        requested = {h.strip().lower() for h in self.headers.get("Access-Control-Request-Headers", "").split(",") if h.strip()}
        if not (self._host_ok() and origin and parsed.path in {"/api/heartbeat", "/api/operator"}
                and self.headers.get("Access-Control-Request-Method", "").upper() == "POST"
                and requested <= set(PAGE_ALLOWED_HEADERS)):
            self._send(HTTPStatus.FORBIDDEN, {"error": "preflight not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token")
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, status: int, body: Any, *, content_type: str = "application/json; charset=utf-8") -> None:
        payload = body if isinstance(body, bytes) else (body.encode("utf-8") if isinstance(body, str) else _json_bytes(body))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # CORS is granted only to POSTs that already passed the page-origin
        # and token checks, so no GET (notably /api/csrf) is readable
        # cross-origin.
        page_origin = self._page_origin()
        if page_origin and getattr(self, "_cors_response", False):
            self.send_header("Access-Control-Allow-Origin", page_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(payload)

    def _discard_body(self) -> None:
        """Drain an unread request body before an early rejection.

        Replying and closing with unread bytes in the socket makes Windows
        reset the connection, so the client sees an abort instead of the 403.
        """
        try:
            size = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            size = 0
        if 0 < size <= 2_000_000:
            self.rfile.read(size)
        self.close_connection = True

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if size > 2_000_000:
            raise ValueError("request too large")
        raw = self.rfile.read(size)
        value = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._host_ok():
            self._send(HTTPStatus.FORBIDDEN, {"error": "local host required"})
            return
        if parsed.path in {"/", "/index.html"}:
            panel = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            # The token is inserted as data, never as an unescaped user URL.
            panel = panel.replace("__CSRF_TOKEN__", html.escape(self.coordinator.csrf_token, quote=True))
            bookmarklet = (
                advisor_bookmarklet_href(f"http://127.0.0.1:{self.server.server_port}")
                if getattr(self.server, "page_runner_enabled", False)
                else "#advisor-bookmarklet-disabled"
            )
            panel = panel.replace("__BOOKMARKLET_HREF__", html.escape(bookmarklet, quote=True))
            panel = panel.replace(
                "__BOOKMARKLET_NOTE__",
                "Drag the link to your bookmarks bar, then click it from the ESPN practice or real draft room."
                if getattr(self.server, "page_runner_enabled", False)
                else "Start the panel with --page-runner to enable the recommendation-only bookmarklet.",
            )
            self._send(HTTPStatus.OK, panel, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send(HTTPStatus.OK, (STATIC_DIR / "app.js").read_bytes(), content_type="text/javascript; charset=utf-8")
            return
        if parsed.path == "/page-runner.js" and getattr(self.server, "page_runner_enabled", False):
            # Built from the checked-in module files on every request, so the
            # served code always matches the source hashes it reports.  It
            # carries no token; the loader supplies the CSRF token separately.
            self._send(HTTPStatus.OK, build_bundle().text, content_type="text/javascript; charset=utf-8")
            return
        if parsed.path == "/advisor-bookmarklet.js" and getattr(self.server, "page_runner_enabled", False):
            # The runtime contains loader code only.  It receives the current
            # token and bundle hash from the nonce-bound bridge in the ESPN
            # page, so this response never embeds either secret or a stale
            # checkout hash.
            self._send(HTTPStatus.OK, recommendation_bookmarklet_runtime(), content_type="text/javascript; charset=utf-8")
            return
        if parsed.path == "/bookmarklet-bridge" and getattr(self.server, "page_runner_enabled", False):
            query = parse_qs(parsed.query, keep_blank_values=True)
            origins = query.get("targetOrigin", [])
            nonces = query.get("nonce", [])
            target_origin = origins[0] if len(origins) == 1 else ""
            nonce = nonces[0] if len(nonces) == 1 else ""
            if target_origin not in self.page_origins or target_origin not in BOOKMARKLET_PAGE_ORIGINS:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "bookmarklet target origin is not an allowed ESPN page"})
                return
            if not nonce or len(nonce) > 200 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in nonce):
                self._send(HTTPStatus.BAD_REQUEST, {"error": "bookmarklet nonce is invalid"})
                return
            self._send(
                HTTPStatus.OK,
                bookmarklet_bridge_page(
                    csrf_token=self.coordinator.csrf_token,
                    bundle_hash=build_bundle().bundle_hash,
                    target_origin=target_origin,
                    nonce=nonce,
                ),
                content_type="text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/status":
            with self.coordinator_lock:
                result = self.coordinator.status()
            result["setup_required"] = not getattr(self.server, "setup_complete", False)
            result["configured_team"] = getattr(self.server, "setup_summary", None)
            self._send(HTTPStatus.OK, result)
            return
        if parsed.path == "/api/setup":
            self._send(HTTPStatus.OK, {"configured": getattr(self.server, "setup_complete", False), "team": getattr(self.server, "setup_summary", None)})
            return
        if parsed.path == "/api/setup/options":
            self._send(HTTPStatus.OK, getattr(self.server, "setup_candidates", None) or {})
            return
        if parsed.path == "/api/csrf":
            self._send(HTTPStatus.OK, {"csrf_token": self.coordinator.csrf_token})
            return
        if parsed.path == "/api/runtime-info":
            self._send(HTTPStatus.OK, runtime_info(self.server))
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        cross_origin = self._page_origin() is not None
        if cross_origin:
            if not self._page_post_ok():
                self._discard_body()
                self._send(HTTPStatus.FORBIDDEN, {"error": "allowed page origin with local CSRF token required"})
                return
            self._cors_response = True
        elif not self._origin_ok():
            self._discard_body()
            self._send(HTTPStatus.FORBIDDEN, {"error": "same-origin request or local CSRF token required"})
            return
        parsed = urlparse(self.path)
        if not getattr(self.server, "setup_complete", True) and parsed.path not in {"/api/setup", "/api/setup/begin", "/api/setup/source", "/api/operator"}:
            self._discard_body()
            self._send(HTTPStatus.CONFLICT, {"error": "Finish setup in the panel before connecting to an ESPN room."})
            return
        body: dict[str, Any] = {}
        try:
            body = self._body()
            if cross_origin and not self._page_path_allowed(parsed.path, body):
                self._send(HTTPStatus.FORBIDDEN, {"error": "this page origin may only publish heartbeats"})
                return
            # ThreadingHTTPServer gives every request its own handler thread,
            # while Coordinator deliberately shares one SQLite connection
            # across the panel and operator endpoints.  Serialize the full
            # coordinator portion of each request; browser I/O is outside
            # this server and is never held by this lock.
            with self.coordinator_lock:
                result = self._dispatch_post(parsed.path, body)
            self._send(HTTPStatus.OK, result.to_dict() if hasattr(result, "to_dict") else result)
        except (CoordinatorError, ValueError, KeyError) as exc:
            if parsed.path == "/api/operator":
                self._send(HTTPStatus.CONFLICT, self._operator_error(body, exc))
            else:
                self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - API returns a safe local error
            if parsed.path == "/api/operator":
                self._send(HTTPStatus.BAD_REQUEST, self._operator_error(body, exc))
            else:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    @staticmethod
    def _operator_error(body: dict[str, Any], exc: Exception) -> dict[str, Any]:
        operation = body.get("operation") if isinstance(body.get("operation"), str) else "unknown"
        request_id = body.get("request_id") if isinstance(body.get("request_id"), str) else None
        return {"operation": operation, "request_id": request_id, "command": None, "status": "error", "error": str(exc)}

    @staticmethod
    def _operator_envelope(operation: str, value: Any, *, command: Any = None, status: str | None = None) -> dict[str, Any]:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        if isinstance(value, dict):
            result = dict(value)
        elif isinstance(value, list):
            result = {"actions": value}
        else:
            result = {"result": value}
        result["operation"] = operation
        result["command"] = command
        result["status"] = status or result.get("status") or result.get("state") or "ok"
        return result

    def _operator_session_id(self, operation: str, body: dict[str, Any]) -> str | None:
        """Resolve the session an operator write targets from authoritative ids."""
        ownership = self.coordinator.ownership
        if operation == "ack":
            return ownership.session_for_action(str(body.get("action_id", "")))
        if operation in {"queue-claim", "queue-final-guard", "queue-ack"}:
            return ownership.session_for_queue_mutation(str(body.get("mutation_id", "")))
        if operation == "publish" and isinstance(body.get("observation"), dict):
            session_id = body["observation"].get("session_id")
            return session_id if isinstance(session_id, str) else None
        session_id = body.get("session_id")
        return session_id if isinstance(session_id, str) and session_id else None

    def _advisor_token_session(self, token: str | None) -> str | None:
        if not token:
            return None
        return getattr(self.server, "advisor_tokens", {}).get(token)

    def _advisor_session_from_body(self, body: dict[str, Any]) -> str | None:
        # Accept the compatibility owner_token spelling, but inspect both
        # fields so an empty or stale observer_token cannot mask a recognized
        # observer credential supplied in owner_token.
        for key in ("observer_token", "owner_token"):
            token = body.get(key)
            if isinstance(token, str) and token:
                session_id = self._advisor_token_session(token)
                if session_id is not None:
                    return session_id
        return None

    def _advisor_session_reusable(self, session_id: str, room: Any) -> bool:
        """Only nonce retries for an untouched advisory session may reuse it."""
        try:
            status = self.coordinator.status(session_id)
        except CoordinatorError:
            return False
        return (
            status.get("room") == room.__dict__
            and status.get("state") in {"observing", "paused"}
            and status.get("owner") is None
            and not status.get("actions")
            and not status.get("queue_mutations")
        )

    def _connect_advisor(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create an observation-only attachment for the currently visible ESPN room."""
        if not getattr(self, "_cors_response", False) or self._page_origin() is None:
            raise CoordinatorError("connect-advisor requires an allowed ESPN page origin")
        raw_url = body.get("current_room_url", body.get("room_url", body.get("url")))
        if not isinstance(raw_url, str) or not raw_url:
            raise ValueError("current_room_url is required")
        heading = body.get("visible_heading", body.get("visible_practice_heading", body.get("practice_heading", body.get("heading"))))
        if not isinstance(heading, str) or not heading.strip():
            raise ValueError("visible_heading is required")
        requested_practice = body.get("practice", True)
        if not isinstance(requested_practice, bool):
            raise ValueError("practice must be an explicit boolean")
        config = self.coordinator.config
        expected_seat = body.get("expected_seat")
        if expected_seat is not None and (
            isinstance(expected_seat, bool)
            or not isinstance(expected_seat, int)
            or not 1 <= expected_seat <= int(config.teams)
        ):
            raise ValueError(f"expected_seat must be an integer seat from 1 through {config.teams}")
        # The browser can send the visible 14-team roster when it cannot know
        # the configured team until this response.  Validate the complete
        # roster and derive the seat server-side; never accept a client team
        # identity or a partial list as an identity assertion.
        pick_order = body.get("pick_order", body.get("visible_roster"))
        if pick_order is not None:
            if not isinstance(pick_order, list) or len(pick_order) != int(config.teams) or not all(isinstance(item, str) and item.strip() for item in pick_order):
                raise ValueError(f"pick_order must contain exactly {config.teams} non-empty team names")
            normalized_order = [" ".join(item.split()).casefold() for item in pick_order]
            if len(set(normalized_order)) != len(normalized_order):
                raise ValueError("pick_order must contain unique team names")
            if not getattr(self.server, "setup_complete", True):
                source = getattr(self.server, "setup_pending_source", None)
                if not source:
                    raise IdentityError(f"Setup needs your source ESPN team URL first. Open the FBL panel at http://127.0.0.1:{getattr(self.server, 'server_port', 8765)}/ , paste your league's ESPN team URL, click Save source team, then click the bookmark again.")
                room = RoomIdentity.from_url(raw_url, practice=requested_practice)
                normalized_heading = self.coordinator._normalize_practice_heading(heading)  # type: ignore[attr-defined]
                if room.season_id != source["season_id"] or room.team_id != source["team_id"]:
                    raise IdentityError("This ESPN room does not match the saved source team ID. Check the source URL or choose Edit setup and observe the intended room.")
                if requested_practice:
                    if room.league_id == source["league_id"] or not self.coordinator._valid_practice_heading(normalized_heading):  # type: ignore[attr-defined]
                        raise IdentityError("Practice discovery requires a visible ESPN Practice Draft heading for the source team.")
                elif room.league_id != source["league_id"] or not normalized_heading.casefold().startswith("espn fantasy basketball draft"):
                    raise IdentityError("Real-room discovery must match the saved source league and show its ESPN draft heading.")
                self.server.setup_candidates = {**source, "room_url": room.safe_url(), "team_options": pick_order, "observed_at": self.coordinator.clock()}
                # One-time, memory-only nonce: only the page that received this
                # discovery response can ask the server to bind a team.
                self.server.setup_discovery_nonce = secrets.token_urlsafe(32)
                return {"setup_discovery": True, "setup_nonce": self.server.setup_discovery_nonce, "team_options": pick_order, "league_id": source["league_id"], "season_id": source["season_id"], "team_id": source["team_id"]}
            own_team_key = " ".join(config.our_team.split()).casefold()
            own_indexes = [index for index, item in enumerate(normalized_order) if item == own_team_key]
            if len(own_indexes) != 1:
                raise IdentityError("pick_order must contain the configured team exactly once")
            derived_seat = own_indexes[0] + 1
            if expected_seat is not None and expected_seat != derived_seat:
                raise IdentityError("expected_seat does not match the visible pick_order")
            expected_seat = derived_seat
        try:
            room = RoomIdentity.from_url(raw_url, practice=requested_practice)
        except (TypeError, ValueError) as exc:
            raise IdentityError(str(exc)) from exc
        if room.season_id != config.season_id or room.team_id != config.team_id:
            raise IdentityError("draft room season and team must match the configured source team")
        try:
            normalized_heading = self.coordinator._normalize_practice_heading(heading)  # type: ignore[attr-defined]
            if requested_practice:
                if room.league_id == config.league_id:
                    raise IdentityError("the configured live league cannot be labeled as a practice room")
                if not self.coordinator._valid_practice_heading(normalized_heading):  # type: ignore[attr-defined]
                    raise IdentityError("observed heading must explicitly identify an ESPN Fantasy Basketball practice draft")
            elif room.league_id != config.league_id:
                raise IdentityError("real draft room must match the configured live league")
            elif not normalized_heading.casefold().startswith("espn fantasy basketball draft"):
                raise IdentityError("observed heading must identify an ESPN Fantasy Basketball draft")
        except IdentityError:
            raise
        except (TypeError, ValueError) as exc:
            raise IdentityError(str(exc)) from exc

        nonce = body.get("request_nonce")
        if nonce is not None and (not isinstance(nonce, str) or not nonce or len(nonce) > 200):
            raise ValueError("request_nonce must be a non-empty string of at most 200 characters")
        nonce_record = getattr(self.server, "advisor_nonces", {}).get(nonce) if nonce else None
        if nonce_record is not None:
            existing_id = nonce_record["session_id"]
            if nonce_record["room"] != room.__dict__ or nonce_record["heading"] != normalized_heading or nonce_record["expected_seat"] != expected_seat:
                raise IdentityError("request_nonce is already bound to a different practice room")
            if not self._advisor_session_reusable(existing_id, room):
                raise CoordinatorError("the existing advisor session is no longer safely reusable")
            status = self.coordinator.status(existing_id)
            return {
                "session_id": existing_id,
                "observer_token": nonce_record["observer_token"],
                "own_team": config.our_team,
                "expected_seat": expected_seat,
                "room": status["room"],
                "source_league_id": config.league_id,
                "practice_attestation": status.get("practice_attestation"),
                "reused": True,
            }

        connected = self.coordinator.connect(room.safe_url(), practice=requested_practice)
        session_id = connected["session_id"]
        if requested_practice:
            # Use only server-side configuration for the source league. The
            # client cannot redirect attestation to another league by
            # supplying a field.
            connected = self.coordinator.attest_practice(session_id, normalized_heading, config.league_id)
        observer_token = secrets.token_urlsafe(32)
        self.server.advisor_tokens[observer_token] = session_id
        if nonce:
            self.server.advisor_nonces[nonce] = {
                "session_id": session_id,
                "observer_token": observer_token,
                "room": room.__dict__,
                "heading": normalized_heading,
                "expected_seat": expected_seat,
            }
        return {
            "session_id": session_id,
            "observer_token": observer_token,
            "own_team": config.our_team,
            "expected_seat": expected_seat,
            "room": connected["room"],
            "source_league_id": config.league_id,
            "practice_attestation": connected.get("practice_attestation"),
            "reused": False,
        }

    def _practice_preflight(self, session_id: str, body: dict[str, Any], *, required: bool):
        coordinator = self.coordinator
        row = coordinator._session_row(session_id)
        room = coordinator._room_from_row(row)
        if not room.practice:
            if required:
                raise CoordinatorError("practice preflight applies only to practice sessions")
            return None
        observation = coordinator._latest_observation(session_id)
        if observation is None:
            raise CoordinatorError("practice preflight requires a published observation")
        supplied = body.get("preflight")
        if not isinstance(supplied, dict):
            raise CoordinatorError("practice Start/Resume requires preflight read-back (native timer, seat)")
        counter = getattr(self.server, "listener_counter", count_local_listeners)
        result = practice_preflight(
            observation,
            coordinator.config,
            native_timer_seconds=supplied.get("native_timer_seconds"),
            expected_seat=supplied.get("expected_seat"),
            listener_count=counter(int(self.server.server_port)),
            recorded_timer_seconds=coordinator.pick_timer_seconds(session_id),
        )
        if not result.ok:
            raise CoordinatorError("practice preflight refused: " + "; ".join(result.reasons))
        return result

    def _setup_select_team(self, body: dict[str, Any]) -> dict[str, Any]:
        """Bind the observed team from the ESPN page during setup discovery only."""
        if not getattr(self, "_cors_response", False) or self._page_origin() is None:
            raise CoordinatorError("setup-select-team requires an allowed ESPN page origin")
        if getattr(self.server, "setup_complete", True) or not isinstance(getattr(self.server, "setup_candidates", None), dict):
            raise CoordinatorError("setup-select-team is only available while setup is waiting for a team choice")
        expected = getattr(self.server, "setup_discovery_nonce", None)
        supplied = body.get("setup_nonce")
        if not isinstance(expected, str) or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
            raise CoordinatorError("setup-select-team requires the current discovery nonce; click the bookmark again")
        result = self._save_setup_selection(body.get("our_team"))
        self.server.setup_discovery_nonce = None
        return result

    def _operator(self, body: dict[str, Any]) -> dict[str, Any]:
        operation = str(body.get("operation", "")).strip().lower().replace("_", "-")
        if operation == "setup-select-team":
            return self._operator_envelope(operation, self._setup_select_team(body), status="setup_saved")
        if not getattr(self.server, "setup_complete", True) and operation != "connect-advisor":
            raise CoordinatorError("Finish league setup in the panel before using the advisor.")
        queue_operations = {"queue-prepare", "queue-claim", "queue-final-guard", "queue-ack", "queue-reconcile"}
        ownership_operations = {"claim-owner", "transfer-owner"}
        if operation not in {"connect-advisor", "publish", "start", "resume", "pause", "prepare", "ack", "reconcile", "recommend-preview", "status", "preflight", *ownership_operations, *queue_operations}:
            raise ValueError("operation must be connect-advisor, publish, start, resume, pause, prepare, ack, reconcile, recommend-preview, status, preflight, claim-owner, transfer-owner, or a queue lifecycle operation")

        # Advisor tokens are capability credentials, rather than owner tokens.
        # Enforce their narrow scope before the owner claim/transfer early
        # returns and before any same-origin ownership fallback.  In
        # particular, an unowned observing session must not become controllable
        # merely because a recognized observer token was supplied locally.
        advisory_session = self._advisor_session_from_body(body)
        if advisory_session is not None:
            if operation not in ADVISOR_TOKEN_OPERATIONS:
                raise CoordinatorError("observer token permits only status and recommend-preview")
            if not getattr(self, "_cors_response", False) or self._page_origin() is None:
                raise CoordinatorError("observer token requires the allowed ESPN page origin")
            if self._operator_session_id(operation, body) != advisory_session:
                raise CoordinatorError("observer token does not match the requested session")

        if operation == "connect-advisor":
            return self._operator_envelope(operation, self._connect_advisor(body), status="advisor_connected")
        if operation == "claim-owner":
            return self._operator_envelope(operation, self.coordinator.ownership.claim(str(body["session_id"]), label=str(body.get("label", ""))), status="owner_claimed")
        if operation == "transfer-owner":
            return self._operator_envelope(
                operation,
                self.coordinator.ownership.transfer(str(body["session_id"]), last_confirmed_revision=body.get("last_confirmed_revision"), label=str(body.get("label", ""))),
                status="owner_transferred",
            )
        if getattr(self, "_cors_response", False) and getattr(self.server, "page_runner_enabled", False):
            page_session = self._operator_session_id(operation, body)
            if advisory_session is None:
                # A page-origin request never acts on an unowned session (where
                # ownership.require would otherwise let any caller through).
                if page_session is None or self.coordinator.ownership.describe(page_session) is None:
                    raise CoordinatorError("page-origin operator requests require a session that already has an owner")
                self.coordinator.ownership.require(page_session, body.get("owner_token"))
        if operation != "status":
            # Once a session has an owner every operator write must present its
            # current token; an old owner's writes are rejected after transfer.
            owned_session = self._operator_session_id(operation, body)
            if owned_session is not None:
                self.coordinator.ownership.require(owned_session, body.get("owner_token"))
        if body.get("observation") is not None and operation in {"start", "resume", "prepare", "reconcile", "recommend-preview", "queue-prepare", "queue-ack", "queue-reconcile"}:
            # queue-ack can identify its session only through mutation_id; the
            # coordinator validates that observation before ingesting it.
            if operation != "queue-ack" or body.get("session_id") is not None:
                if body["observation"].get("session_id") != body.get("session_id"):
                    raise ValueError("observation session must match operator session")

        if operation == "publish":
            result = self.coordinator.ingest_or_match_observation(body["observation"])
            return self._operator_envelope(operation, result)

        if operation == "recommend-preview":
            session_id = str(body["session_id"])
            recommendation = self.coordinator.recommend_preview(
                session_id,
                body.get("observation"),
                lookahead_enabled=bool(body.get("lookahead_enabled", False)),
            )
            return self._operator_envelope(operation, recommendation.to_dict(), status="preview")

        if operation == "preflight":
            session_id = str(body["session_id"])
            if body.get("observation") is not None:
                self.coordinator.ingest_or_match_observation(body["observation"], session_id=session_id)
            result = self._practice_preflight(session_id, body, required=True)
            return self._operator_envelope(operation, {"session_id": session_id, **result.to_dict()}, status="preflight_ok")

        if operation == "pause":
            result = self.coordinator.pause(str(body["session_id"]), reason=str(body.get("reason", "operator recovery"))[:200])
            return self._operator_envelope(operation, result)

        if operation in {"start", "resume"}:
            session_id = str(body["session_id"])
            # A single operator request can carry the fresh frame that justified
            # the explicit control command.  Ingesting first keeps the regular
            # readiness and revision checks in force.
            if body.get("observation") is not None:
                self.coordinator.ingest_or_match_observation(body["observation"], session_id=session_id)
            # Practice rooms additionally require the preflight read-back.
            preflight = self._practice_preflight(session_id, body, required=False)
            result = self.coordinator.start(session_id) if operation == "start" else self.coordinator.resume(session_id)
            if preflight is not None:
                # The first accepted read-back becomes the draft's pick timer;
                # later Resumes are held to it by the preflight above.
                result = {**result, "pick_timer_seconds": self.coordinator.record_pick_timer(session_id, float(body["preflight"]["native_timer_seconds"]))}
            recommendation = None
            if body.get("recommend"):
                recommendation = self.coordinator.recommend(
                    session_id,
                    lookahead_enabled=bool(body.get("lookahead_enabled", False)),
                )
            response = self._operator_envelope(operation, result)
            if recommendation is not None:
                response["recommendation"] = recommendation.to_dict()
            return response

        if operation == "prepare":
            session_id = str(body["session_id"])
            if body.get("observation") is not None:
                self.coordinator.ingest_or_match_observation(body["observation"], session_id=session_id)
            # Preparation never starts or resumes a session.  The final guard
            # in command_for is consumed exactly once for the returned action.
            recommendation = self.coordinator.recommend(
                session_id,
                lookahead_enabled=bool(body.get("lookahead_enabled", False)),
            )
            action = self.coordinator.next_action(session_id)
            command = None
            if action is not None:
                self.coordinator.claim_action(action.action_id)
                command = BrowserBridge(self.coordinator).command_for(action.action_id).to_dict()
            response = self._operator_envelope(operation, self.coordinator.status(session_id), command=command, status="command_ready" if command else "no_action")
            response["recommendation"] = recommendation.to_dict()
            return response

        if operation == "ack":
            result = self.coordinator.ack_action(
                str(body["action_id"]),
                str(body["status"]),
                detail=str(body.get("detail", "")),
                observation=body.get("observation"),
            )
            return self._operator_envelope(operation, result)

        if operation == "reconcile":
            session_id = str(body["session_id"])
            if body.get("observation") is not None:
                self.coordinator.ingest_or_match_observation(body["observation"], session_id=session_id)
            reconciled = self.coordinator.reconcile(session_id)
            snapshot = self.coordinator.status(session_id)
            # Keep the enriched status payload (including SQL claim/dispatched
            # provenance) in the envelope result.  The top-level actions field
            # is the subset that just reconciled, but it is looked up from the
            # fresh status snapshot so browser confirmation cannot lose the
            # direct-dispatch evidence when crossing the HTTP boundary.
            by_id = {item.get("action_id"): item for item in snapshot.get("actions", [])}
            actions = [by_id.get(action.action_id, action.to_dict()) for action in reconciled]
            response = self._operator_envelope(operation, snapshot, status="reconciled")
            response["actions"] = actions
            response["reconciled_actions"] = actions
            return response

        if operation == "queue-prepare":
            session_id = str(body["session_id"])
            mutation = self.coordinator.prepare_queue_mutation(
                session_id,
                str(body.get("queue_operation", body.get("mutation", ""))).strip().lower(),
                str(body["player_id"]),
                player_name=body.get("player_name"),
                observation=body.get("observation"),
                expected_revision=body.get("expected_revision"),
                expected_pick=body.get("expected_pick"),
                expected_team=body.get("expected_team"),
                queue=body.get("queue"),
            )
            return self._operator_envelope(operation, mutation, status="prepared")

        if operation == "queue-claim":
            mutation = self.coordinator.claim_queue_mutation(str(body["mutation_id"]))
            return self._operator_envelope(operation, mutation, status="claimed")

        if operation == "queue-final-guard":
            command = self.coordinator.final_queue_guard(str(body["mutation_id"]), queue=body.get("queue"))
            return self._operator_envelope(operation, command, command=command, status="command_ready")

        if operation == "queue-ack":
            mutation = self.coordinator.ack_queue_mutation(
                str(body["mutation_id"]),
                str(body.get("ack_status", body.get("status", ""))).strip().lower(),
                detail=str(body.get("detail", "")),
                queue=body.get("queue"),
                observation=body.get("observation"),
            )
            return self._operator_envelope(operation, mutation, status=mutation.get("status", "ok"))

        if operation == "queue-reconcile":
            session_id = str(body["session_id"])
            resolved = self.coordinator.reconcile_queue_mutations(
                session_id,
                queue=body.get("queue"),
                observation=body.get("observation"),
            )
            response = self._operator_envelope(operation, self.coordinator.status(session_id), status="reconciled")
            response["resolved_queue_mutations"] = resolved
            return response

        result = self.coordinator.status(body.get("session_id"))
        return self._operator_envelope(operation, result)

    def _save_setup_selection(self, name: Any) -> dict[str, Any]:
        """Persist the observed team selection; shared by the panel and the in-room picker."""
        config_path = getattr(self.server, "setup_config_path", None)
        if config_path is None:
            raise CoordinatorError("Setup saving is unavailable for this launch. Start the Windows launcher or pass --league-config under runtime/.")
        candidates = getattr(self.server, "setup_candidates", None)
        if not isinstance(candidates, dict) or not isinstance(candidates.get("team_options"), list):
            raise ValueError("Open the ESPN draft room and click the FBL Advisor bookmarklet to read its team list first.")
        if not isinstance(name, str) or name not in candidates["team_options"]:
            raise ValueError("Select one of the team names observed in the ESPN draft room.")
        previous = self.coordinator.config
        config = replace(previous, league_id=candidates["league_id"], season_id=candidates["season_id"], team_id=candidates["team_id"], our_team=name)
        changed = (previous.league_id, previous.season_id, previous.team_id, previous.our_team) != (config.league_id, config.season_id, config.team_id, config.our_team)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
        temp_path.replace(config_path)
        if changed:
            now = self.coordinator.clock()
            with self.coordinator._transaction() as db:
                rows = db.execute("SELECT session_id FROM sessions").fetchall()
                for row in rows:
                    sid = row["session_id"]
                    db.execute("UPDATE sessions SET state='disconnected', reason=?, control_version=control_version+1, updated_at=? WHERE session_id=?", ("league identity changed in setup; reconnect to the selected ESPN room", now, sid))
                    db.execute("DELETE FROM session_owners WHERE session_id=?", (sid,))
                    actions = db.execute("SELECT action_id,payload FROM actions WHERE session_id=? AND status='pending' AND dispatched=0", (sid,)).fetchall()
                    for action in actions:
                        payload = json.loads(action["payload"])
                        payload.update(status="cancelled", detail="cancelled because league identity changed before dispatch", updated_at=now)
                        db.execute("UPDATE actions SET status='cancelled',payload=?,claim_token=NULL,updated_at=? WHERE action_id=?", (json.dumps(payload, sort_keys=True), now, action["action_id"]))
                    mutations = db.execute("SELECT mutation_id,payload FROM queue_mutations WHERE session_id=? AND status='pending' AND dispatched=0", (sid,)).fetchall()
                    for mutation in mutations:
                        payload = json.loads(mutation["payload"])
                        payload.update(status="cancelled", detail="cancelled because league identity changed before dispatch", updated_at=now)
                        db.execute("UPDATE queue_mutations SET status='cancelled',payload=?,claim_token=NULL,updated_at=? WHERE mutation_id=?", (json.dumps(payload, sort_keys=True), now, mutation["mutation_id"]))
                    self.coordinator._journal_db(db, sid, "identity_invalidated", {"reason": "league setup changed; reconnect required"}, now)
            self.server.advisor_tokens.clear()
            self.server.advisor_nonces.clear()
        self.coordinator.config = config
        self.server.setup_complete = True
        self.server.setup_summary = {"league_id": config.league_id, "season_id": config.season_id, "team_id": config.team_id, "our_team": config.our_team}
        self.server.setup_candidates = None
        self.server.setup_discovery_nonce = None
        self.server.setup_pending_source = None
        pending_path = config_path.with_name("setup-pending.json")
        if pending_path.exists():
            pending_path.unlink()
        return {"configured": True, "team": self.server.setup_summary}

    def _dispatch_post(self, path: str, body: dict[str, Any]) -> Any:
        if path == "/api/setup":
            return self._save_setup_selection(body.get("our_team"))
        if path == "/api/setup/source":
            raw_url = body.get("url")
            if not isinstance(raw_url, str):
                raise ValueError("Paste an HTTPS ESPN basketball team or draft URL.")
            room = RoomIdentity.from_url(raw_url)
            self.server.setup_pending_source = {"league_id": room.league_id, "season_id": room.season_id, "team_id": room.team_id}
            marker = self.server.setup_config_path.with_name("setup-pending.json") if self.server.setup_config_path else None
            if marker:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(json.dumps(self.server.setup_pending_source, indent=2) + "\n", encoding="utf-8")
            self.server.setup_candidates = None
            return {"source_saved": True, **self.server.setup_pending_source}
        if path == "/api/setup/begin":
            self.server.setup_complete = False
            self.server.setup_candidates = None
            self.server.setup_pending_source = None
            marker = self.server.setup_config_path.with_name("setup-pending.json") if self.server.setup_config_path else None
            if marker:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(json.dumps({"editing": True}, indent=2) + "\n", encoding="utf-8")
            now = self.coordinator.clock()
            with self.coordinator._transaction() as db:
                rows = db.execute("SELECT session_id FROM sessions").fetchall()
                for row in rows:
                    sid = row["session_id"]
                    db.execute("UPDATE sessions SET state='disconnected', reason=?, control_version=control_version+1, updated_at=? WHERE session_id=?", ("league setup is being edited; reconnect after saving", now, sid))
                    db.execute("DELETE FROM session_owners WHERE session_id=?", (sid,))
                    for action in db.execute("SELECT action_id,payload FROM actions WHERE session_id=? AND status='pending' AND dispatched=0", (sid,)).fetchall():
                        payload = json.loads(action["payload"])
                        payload.update(status="cancelled", detail="cancelled because league setup editing started before dispatch", updated_at=now)
                        db.execute("UPDATE actions SET status='cancelled',payload=?,claim_token=NULL,updated_at=? WHERE action_id=?", (json.dumps(payload, sort_keys=True), now, action["action_id"]))
                    for mutation in db.execute("SELECT mutation_id,payload FROM queue_mutations WHERE session_id=? AND status='pending' AND dispatched=0", (sid,)).fetchall():
                        payload = json.loads(mutation["payload"])
                        payload.update(status="cancelled", detail="cancelled because league setup editing started before dispatch", updated_at=now)
                        db.execute("UPDATE queue_mutations SET status='cancelled',payload=?,claim_token=NULL,updated_at=? WHERE mutation_id=?", (json.dumps(payload, sort_keys=True), now, mutation["mutation_id"]))
                    self.coordinator._journal_db(db, sid, "identity_invalidated", {"reason": "setup edit started"}, now)
            self.server.advisor_tokens.clear()
            self.server.advisor_nonces.clear()
            return {"configured": False}
        if not getattr(self.server, "setup_complete", True) and path not in {"/api/setup", "/api/setup/source", "/api/operator"}:
            raise CoordinatorError("Finish setup in the panel before connecting to an ESPN room.")
        if path == "/api/connect":
            # URL is parsed and stored by the coordinator; it is not rendered
            # into the panel and connect never starts a draft.
            return self.coordinator.connect(str(body["url"]), practice=bool(body.get("practice", False)), session_id=body.get("session_id"))
        if path == "/api/observation":
            return self.coordinator.ingest_observation(body.get("observation", body))
        if path == "/api/heartbeat":
            visibility = body.get("visibility_state")
            return self.coordinator.heartbeat(body["observation"], visibility=visibility if isinstance(visibility, str) else None)
        if path == "/api/export-session":
            return self.coordinator.export_session(str(body["session_id"]))
        if path == "/api/recommend":
            return self.coordinator.recommend(str(body["session_id"]), lookahead_enabled=bool(body.get("lookahead_enabled", False)))
        if path == "/api/next-action":
            return self.coordinator.next_action(str(body["session_id"]))
        if path == "/api/claim-action":
            return self.coordinator.claim_action(str(body["action_id"]))
        if path == "/api/dispatch-command":
            return BrowserBridge(self.coordinator).command_for(str(body["action_id"])).to_dict()
        if path == "/api/ack-action":
            return self.coordinator.ack_action(str(body["action_id"]), str(body["status"]), detail=str(body.get("detail", "")), observation=body.get("observation"))
        if path == "/api/control":
            return self.coordinator.control(str(body["session_id"]), str(body["command"]), player_id=body.get("player_id"), action_id=body.get("action_id"), reason=str(body.get("reason", "")))
        if path == "/api/reconcile":
            return [a.to_dict() for a in self.coordinator.reconcile(str(body["session_id"]))]
        if path == "/api/queue-prepare":
            return self.coordinator.prepare_queue_mutation(
                str(body["session_id"]),
                str(body.get("queue_operation", body.get("mutation", ""))).strip().lower(),
                str(body["player_id"]),
                player_name=body.get("player_name"),
                observation=body.get("observation"),
                expected_revision=body.get("expected_revision"),
                expected_pick=body.get("expected_pick"),
                expected_team=body.get("expected_team"),
                queue=body.get("queue"),
            )
        if path == "/api/queue-claim":
            return self.coordinator.claim_queue_mutation(str(body["mutation_id"]))
        if path == "/api/queue-final-guard":
            return self.coordinator.final_queue_guard(str(body["mutation_id"]), queue=body.get("queue"))
        if path == "/api/queue-ack":
            return self.coordinator.ack_queue_mutation(
                str(body["mutation_id"]),
                str(body.get("ack_status", body.get("status", ""))).strip().lower(),
                detail=str(body.get("detail", "")),
                queue=body.get("queue"),
                observation=body.get("observation"),
            )
        if path == "/api/queue-reconcile":
            return self.coordinator.reconcile_queue_mutations(
                str(body["session_id"]), queue=body.get("queue"), observation=body.get("observation")
            )
        if path == "/api/operator":
            result = self._operator(body)
            if isinstance(result, dict):
                result["request_id"] = body.get("request_id") if isinstance(body.get("request_id"), str) else None
            return result
        raise KeyError("not found")


class DraftHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows must use an exclusive listener.  Reusing the address there can
    # allow two local coordinators to bind the same port, leaving the browser
    # connected to an older process while the operator inspects a newer one.
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            # Keep the fail-closed behavior even on a Windows Python build
            # that does not expose SO_EXCLUSIVEADDRUSE.
            self.allow_reuse_address = False
        super().server_bind()

    def __init__(self, server_address: tuple[str, int], coordinator: Coordinator, *, page_dispatch_enabled: bool = False, page_runner_enabled: bool = False, page_origins: frozenset[str] = PAGE_ORIGINS, setup_config_path: Path | None = None, setup_complete: bool = True, setup_pending_source: dict[str, str] | None = None):
        super().__init__(server_address, DraftRequestHandler)
        self.coordinator = coordinator
        # Overridable in tests; fails closed (None) when listeners are unknowable.
        self.listener_counter = count_local_listeners
        self.coordinator_lock = threading.RLock()
        self.page_dispatch_enabled = page_dispatch_enabled
        self.page_runner_enabled = page_runner_enabled
        # Ignore malformed configurable origins before they can reach a
        # response header.  _canonical_page_origin repeats the check for the
        # request value and keeps this boundary safe if a test or embedding
        # caller replaces page_origins after initialization.
        self.page_origins = frozenset(
            origin for origin in page_origins
            if isinstance(origin, str) and origin and "\r" not in origin and "\n" not in origin
        )
        self.setup_config_path = setup_config_path
        self.setup_complete = setup_complete
        self.setup_candidates = None
        self.setup_discovery_nonce: str | None = None
        self.setup_pending_source = setup_pending_source
        self.setup_summary = ({"league_id": coordinator.config.league_id, "season_id": coordinator.config.season_id, "team_id": coordinator.config.team_id, "our_team": coordinator.config.our_team} if setup_complete else None)
        # These credentials intentionally live only for this server process;
        # they are never persisted in Coordinator's SQLite state or exposed by
        # the normal owner registry.
        self.advisor_tokens: dict[str, str] = {}
        self.advisor_nonces: dict[str, dict[str, Any]] = {}
        self.startup_source = startup_source()


def create_server(coordinator: Coordinator, host: str = "127.0.0.1", port: int = 8765, *, page_dispatch_enabled: bool = False, page_runner_enabled: bool = False, setup_config_path: Path | None = None, setup_complete: bool = True, setup_pending_source: dict[str, str] | None = None) -> DraftHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("draft control server must bind to localhost")
    return DraftHTTPServer(("127.0.0.1" if host == "localhost" else host, int(port)), coordinator, page_dispatch_enabled=page_dispatch_enabled, page_runner_enabled=page_runner_enabled, setup_config_path=setup_config_path, setup_complete=setup_complete, setup_pending_source=setup_pending_source)


def serve(coordinator: Coordinator | None = None, *, host: str = "127.0.0.1", port: int = 8765, page_dispatch_enabled: bool = False, page_runner_enabled: bool = False, open_browser: bool = False, setup_config_path: Path | None = None, setup_complete: bool = True, setup_pending_source: dict[str, str] | None = None) -> None:
    own = coordinator is None
    coordinator = coordinator or Coordinator()
    httpd = create_server(coordinator, host, port, page_dispatch_enabled=page_dispatch_enabled, page_runner_enabled=page_runner_enabled, setup_config_path=setup_config_path, setup_complete=setup_complete, setup_pending_source=setup_pending_source)
    try:
        if open_browser:
            import webbrowser

            webbrowser.open(f"http://127.0.0.1:{httpd.server_port}/")
        httpd.serve_forever()
    finally:
        httpd.server_close()
        if own:
            coordinator.close()

