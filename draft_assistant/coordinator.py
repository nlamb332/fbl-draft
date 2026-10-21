"""Durable, explicit control plane for the live draft assistant.

The coordinator deliberately knows nothing about a browser.  It accepts
observations produced by the browser adapter, validates them, and emits a
small guarded command which the browser operator may execute.  All state that
matters for recovery is kept in SQLite so a CLI process and the local panel
can safely share a session.
"""

from __future__ import annotations

import inspect
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .contracts import (
    ACTION_STATUSES,
    ControlIntent,
    DraftAction,
    DraftObservation,
    DraftRecommendation,
    LeagueConfig,
    LivePlayer,
    RoomIdentity,
    SESSION_STATES,
    timestamp_age,
    utc_now,
)
from .lifecycle import DraftSession


class CoordinatorError(RuntimeError):
    """Base error for a rejected control-plane operation."""


class FreshnessError(CoordinatorError):
    pass


class IdentityError(CoordinatorError):
    pass


class ReadinessError(CoordinatorError):
    pass


class ActionConflict(CoordinatorError):
    pass


# ESPN creates a new league ID for every practice room.  The heading is the
# browser agent's visible proof that the room is a practice draft; the source
# league ID ties that practice room back to the configured live league.
_PRACTICE_HEADING_RE = re.compile(
    r"^ESPN Fantasy Basketball Draft\s*-\s*Practice Draft\s+for\s+(.+)$",
    re.IGNORECASE,
)
# This marker is emitted when recommendation mode intentionally evaluates a
# scorable subset of a larger, known catalog.  It is distinct from unresolved
# feed-health warnings, which must continue to block preview generation.
_RECOMMENDATION_CATALOG_SUBSET_WARNING = "recommendation_catalog_incomplete"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


class Coordinator:
    """Own one or more durable draft sessions.

    ``strategy`` may be a callable or an object exposing ``recommend``.  It is
    injected so the control plane can be tested without importing the strategy
    implementation and so strategy failures cannot bypass the safety gates.
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        *,
        config: LeagueConfig | None = None,
        strategy: Any = None,
        clock: Callable[[], str] = utc_now,
        recover: bool = True,
        promotion_path: Any = None,
        projection_policy: Any = None,
    ) -> None:
        if db_path is None:
            db_path = Path("runtime") / "control.sqlite3"
        self.db_path = str(db_path)
        self.config = config or LeagueConfig()
        self.strategy = strategy
        # The promotion artifact is server-side configuration.  HTTP callers
        # (including the ESPN page) cannot supply or override it.
        self.promotion_path = promotion_path
        # Optional projection staleness policy (default off); see
        # docs/PROJECTION_PROVENANCE.md.  Server-side only, like promotion_path.
        self.projection_policy = projection_policy
        self.clock = clock
        self.csrf_token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._closed = False
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        if self.db_path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._init_schema()
        # Local import: the ownership registry depends on this module.
        from .ownership import SessionOwnership

        self.ownership = SessionOwnership(self)
        if recover:
            self._recover_after_restart()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __enter__(self) -> "Coordinator":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except Exception:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def _init_schema(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    room_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    manual_override INTEGER NOT NULL DEFAULT 0,
                    control_version INTEGER NOT NULL DEFAULT 0,
                    latest_revision INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    session_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, revision),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS recommendations (
                    session_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, revision),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    claim_token TEXT,
                    dispatched INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS intents (
                    session_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS journal (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publish_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    gap_seconds REAL NOT NULL,
                    visibility TEXT
                );
                CREATE TABLE IF NOT EXISTS practice_attestations (
                    session_id TEXT PRIMARY KEY,
                    room_json TEXT NOT NULL,
                    source_league_id TEXT NOT NULL,
                    heading TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS queue_mutations (
                    mutation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    claim_token TEXT,
                    dispatched INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                """
            )
            # Databases made by an early development checkout are upgraded in
            # place; this keeps a service restart from losing its action lock.
            columns = {row["name"] for row in self._db.execute("PRAGMA table_info(actions)").fetchall()}
            if "claim_token" not in columns:
                self._db.execute("ALTER TABLE actions ADD COLUMN claim_token TEXT")
            if "dispatched" not in columns:
                self._db.execute("ALTER TABLE actions ADD COLUMN dispatched INTEGER NOT NULL DEFAULT 0")
            # SQLite enforces the one-action gate across independent CLI and
            # panel processes, rather than relying on an in-memory check.
            try:
                self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_action ON actions(session_id) WHERE status IN ('pending','submitted','uncertain')")
            except sqlite3.IntegrityError:
                # A legacy database may already contain ambiguous unresolved
                # rows. Preserve it for reconciliation; all new databases get
                # the hard uniqueness gate above.
                pass
            try:
                self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_queue_mutation ON queue_mutations(session_id) WHERE status IN ('pending','submitted','uncertain')")
            except sqlite3.IntegrityError:
                # Preserve a legacy ambiguous queue journal for readback; all
                # new mutations are still protected by the unique gate.
                pass
            self._db.commit()

    def _recover_after_restart(self) -> None:
        """Running sessions always resume paused; action history is retained."""
        now = self.clock()
        with self._transaction() as db:
            rows = db.execute("SELECT session_id FROM sessions WHERE state IN ('running','degraded')").fetchall()
            for row in rows:
                db.execute(
                    "UPDATE sessions SET state='paused', reason=?, control_version=control_version+1, updated_at=? WHERE session_id=?",
                    ("paused after coordinator restart", now, row["session_id"]),
                )
                self._set_intent_db(db, row["session_id"], "pause", reason="restart recovery")
                self._journal_db(db, row["session_id"], "restart_pause", {"reason": "restart recovery"}, now)
                pending_queue = db.execute(
                    "SELECT mutation_id,status,payload,dispatched FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain')",
                    (row["session_id"],),
                ).fetchall()
                for mutation in pending_queue:
                    payload = _loads(mutation["payload"])
                    was_dispatched = bool(mutation["dispatched"] or payload.get("dispatched"))
                    payload["status"] = "uncertain" if was_dispatched or mutation["status"] in {"submitted", "uncertain"} else "cancelled"
                    payload["dispatched"] = was_dispatched
                    payload["updated_at"] = now
                    payload["detail"] = (
                        "possible queue mutation after coordinator restart; inspect visible queue before retry"
                        if was_dispatched or mutation["status"] in {"submitted", "uncertain"}
                        else "cancelled by coordinator restart before visible queue mutation"
                    )
                    status = payload["status"]
                    db.execute(
                        "UPDATE queue_mutations SET status=?,payload=?,updated_at=? WHERE mutation_id=?",
                        (status, _json(payload), now, mutation["mutation_id"]),
                    )
                    self._journal_db(db, row["session_id"], f"queue_mutation_{status}", {"mutation_id": mutation["mutation_id"], "detail": payload["detail"]}, now)

    def _journal_db(self, db: sqlite3.Connection, session_id: str, event: str, payload: Any, now: str | None = None) -> None:
        db.execute(
            "INSERT INTO journal(session_id,event,payload,created_at) VALUES (?,?,?,?)",
            (session_id, event, _json(payload), now or self.clock()),
        )

    @staticmethod
    def _normalize_practice_heading(heading: str) -> str:
        """Normalize only presentation differences in a visible heading."""
        if not isinstance(heading, str) or "\n" in heading or "\r" in heading:
            raise IdentityError("observed practice heading must be one visible line")
        # ESPN may expose curly apostrophes or typographic dashes in one
        # accessibility surface and ASCII punctuation in another.  Normalize
        # those presentation differences without accepting a different room
        # title.
        return " ".join(
            heading.replace("\u2019", "'")
            .replace("\u2018", "'")
            .replace("\u2013", "-")
            .replace("\u2014", "-")
            .split()
        )

    @classmethod
    def _valid_practice_heading(cls, heading: str) -> bool:
        normalized = cls._normalize_practice_heading(heading)
        match = _PRACTICE_HEADING_RE.fullmatch(normalized)
        # League names are user data, not a fixed application identity. The
        # attestation binds the room and configured source IDs separately.
        return bool(match and match.group(1).strip())

    def _practice_attestation(self, session_id: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM practice_attestations WHERE session_id=?", (session_id,)
        ).fetchone()

    def _practice_attestation_matches(self, session_id: str, room: RoomIdentity) -> bool:
        row = self._practice_attestation(session_id)
        if row is None:
            return False
        try:
            attested_room = RoomIdentity(**_loads(row["room_json"]))
            heading_valid = self._valid_practice_heading(row["heading"])
        except (TypeError, ValueError, json.JSONDecodeError, IdentityError):
            return False
        return (
            attested_room == room
            and row["source_league_id"] == self.config.league_id
            and heading_valid
        )

    def _set_intent_db(self, db: sqlite3.Connection, session_id: str, kind: str, *, player_id: str | None = None, reason: str = "") -> None:
        intent = ControlIntent(kind, session_id, player_id)
        row = db.execute("SELECT control_version FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        version = int(row["control_version"] if row else 0)
        payload = asdict(intent) | {"reason": reason}
        db.execute(
            "INSERT INTO intents(session_id,payload,version) VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload, version=excluded.version",
            (session_id, _json(payload), version),
        )

    def _session_row(self, session_id: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise CoordinatorError(f"unknown session {session_id}")
        return row

    @staticmethod
    def _room_from_row(row: sqlite3.Row) -> RoomIdentity:
        return RoomIdentity(**_loads(row["room_json"]))

    @staticmethod
    def _observation_from_row(row: sqlite3.Row) -> DraftObservation:
        return DraftObservation.from_dict(_loads(row["payload"]))

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> DraftAction:
        return DraftAction(**_loads(row["payload"]))

    def _latest_observation(self, session_id: str) -> DraftObservation | None:
        row = self._db.execute(
            "SELECT payload FROM observations WHERE session_id=? ORDER BY revision DESC LIMIT 1", (session_id,)
        ).fetchone()
        return DraftObservation.from_dict(_loads(row["payload"])) if row else None

    def _require_fresh(self, observation: DraftObservation | None) -> DraftObservation:
        if observation is None:
            raise FreshnessError("no observation has been published")
        try:
            age = timestamp_age(observation.observed_at)
        except (TypeError, ValueError) as exc:
            raise FreshnessError(f"invalid observation timestamp: {exc}") from exc
        if age < 0:
            raise FreshnessError("future-dated observation rejected")
        if age > self.config.max_observation_age_seconds:
            raise FreshnessError(f"observation is {age:.1f}s old; maximum is {self.config.max_observation_age_seconds:.1f}s")
        return observation

    def _unresolved_work(self, db: sqlite3.Connection, session_id: str) -> bool:
        """True while any action or queue mutation could still reach ESPN."""
        return bool(
            db.execute("SELECT 1 FROM actions WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone()
            or db.execute("SELECT 1 FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone()
        )

    def _pause_if_stale(self, session_id: str) -> None:
        """Take an active session off automation as soon as its board goes stale.

        Staleness never permits a dispatch: every action guard still requires a
        fresh frame.  What changes by state is the way back.  A board that goes
        stale while nothing is unresolved and an opponent is on the clock only
        degrades the session; the next complete, contiguous frame restores it
        (see ``_recover_degraded``).  Any unresolved action, our own turn, or an
        unknown clock team keeps the one-way pause with explicit resume.
        """
        row = self._db.execute("SELECT state FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        obs = self._latest_observation(session_id)
        if not row or row["state"] != "running" or obs is None:
            return
        try:
            age = timestamp_age(obs.observed_at)
            parseable = True
        except (TypeError, ValueError):
            age = self.config.max_observation_age_seconds + 1
            parseable = False
        if 0 <= age <= self.config.max_observation_age_seconds:
            return
        now = self.clock()
        with self._transaction() as db:
            current = db.execute("SELECT state FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if not current or current["state"] != "running":
                return
            opponent_turn = bool(obs.on_clock_team) and obs.on_clock_team != self.config.our_team
            if parseable and opponent_turn and not self._unresolved_work(db, session_id):
                db.execute("UPDATE sessions SET state='degraded',reason='stale observation during an opponent turn; recovers on the next complete contiguous frame',control_version=control_version+1,updated_at=? WHERE session_id=?", (now, session_id))
                self._set_intent_db(db, session_id, "pause", reason="stale observation (degraded)")
                self._journal_db(db, session_id, "stale_degraded", {"age_seconds": age, "from_revision": obs.revision, "from_observed_at": obs.observed_at, "from_pick": obs.current_pick}, now)
                return
            db.execute("UPDATE sessions SET state='paused',reason='stale observation; explicit resume required',control_version=control_version+1,updated_at=? WHERE session_id=?", (now, session_id))
            pending = db.execute("SELECT action_id,payload FROM actions WHERE session_id=? AND status='pending' AND dispatched=0", (session_id,)).fetchall()
            for action in pending:
                payload = _loads(action["payload"])
                payload.update(status="cancelled", updated_at=now, detail="cancelled when observation became stale")
                db.execute("UPDATE actions SET status='cancelled',payload=?,updated_at=? WHERE action_id=?", (_json(payload), now, action["action_id"]))
            self._set_intent_db(db, session_id, "pause", reason="stale observation")
            self._journal_db(db, session_id, "stale_pause", {"age_seconds": age, "cancelled": len(pending)}, now)

    def _recover_degraded(self, obs: DraftObservation) -> None:
        """Return a degraded session to running only when that is provably safe.

        A frame that is merely incomplete or still stale leaves the session
        degraded.  A complete frame that contradicts the last confirmed history,
        fails readiness, or arrives while work is unresolved pauses instead, so
        the operator must resume explicitly.
        """
        session_id = obs.session_id
        row = self._db.execute("SELECT state FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row or row["state"] != "degraded":
            return
        try:
            age = timestamp_age(obs.observed_at)
        except (TypeError, ValueError):
            return
        if not obs.complete or not obs.history_complete or age < 0 or age > self.config.max_observation_age_seconds:
            return
        event = self._db.execute("SELECT payload FROM journal WHERE session_id=? AND event='stale_degraded' ORDER BY seq DESC LIMIT 1", (session_id,)).fetchone()
        baseline_row = self._db.execute("SELECT payload FROM observations WHERE session_id=? AND revision=?", (session_id, _loads(event["payload"], {}).get("from_revision") if event else None)).fetchone()
        baseline = DraftObservation.from_dict(_loads(baseline_row["payload"])) if baseline_row else None
        reasons: list[str] = []
        if baseline is None:
            reasons.append("last confirmed observation is unavailable")
        else:
            known = [(e.pick_number, e.team, e.player_id) for e in baseline.history]
            seen = [(e.pick_number, e.team, e.player_id) for e in obs.history]
            if seen[: len(known)] != known or (obs.current_pick or 0) < (baseline.current_pick or 0):
                reasons.append("history after the gap does not extend the last confirmed history")
        readiness = self.readiness(session_id)
        if not readiness["ready"]:
            reasons.extend(readiness["reasons"])
        now = self.clock()
        with self._transaction() as db:
            current = db.execute("SELECT state FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if not current or current["state"] != "degraded":
                return
            if self._unresolved_work(db, session_id):
                reasons.append("an unresolved action or queue mutation requires explicit resume")
            if db.execute("SELECT session_id FROM sessions WHERE session_id<>? AND state IN ('running','degraded') LIMIT 1", (session_id,)).fetchone():
                reasons.append("another session is active")
            if reasons or baseline is None:
                db.execute("UPDATE sessions SET state='paused',reason=?,control_version=control_version+1,updated_at=? WHERE session_id=?", ("degraded recovery refused: " + "; ".join(reasons), now, session_id))
                self._set_intent_db(db, session_id, "pause", reason="degraded recovery refused")
                self._journal_db(db, session_id, "degraded_pause", {"reasons": reasons, "revision": obs.revision}, now)
                return
            gap = timestamp_age(_loads(event["payload"], {}).get("from_observed_at") or baseline.observed_at) - age
            db.execute("UPDATE sessions SET state='running',reason='recovered from degraded',control_version=control_version+1,updated_at=? WHERE session_id=?", (now, session_id))
            self._set_intent_db(db, session_id, "resume", reason="recovered from degraded")
            self._journal_db(db, session_id, "degraded_recovered", {"gap_seconds": round(gap, 3), "from_revision": baseline.revision, "to_revision": obs.revision, "pick": obs.current_pick}, now)

    @staticmethod
    def _same_board(left: DraftObservation, right: DraftObservation) -> bool:
        """Equal visible board; ignores only revision and clock/timing fields."""
        blank = {"revision": 0, "observed_at": "", "seconds_remaining": None, "warnings": ()}
        return replace(left, **blank) == replace(right, **blank)

    def _room_violation(self, session_id: str) -> None:
        """A frame from another room pauses a degraded session, then is rejected."""
        row = self._db.execute("SELECT state FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row and row["state"] == "degraded":
            self.pause(session_id, reason="room identity changed while degraded; explicit resume required")
        raise IdentityError("observation room does not match connected room")

    def _record_gap_db(self, db: sqlite3.Connection, session_id: str, observed_at: str, visibility: str | None) -> None:
        previous = db.execute("SELECT observed_at FROM publish_gaps WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        gap = 0.0
        if previous is not None:
            gap = max(0.0, timestamp_age(previous["observed_at"]) - timestamp_age(observed_at))
        db.execute("INSERT INTO publish_gaps(session_id,observed_at,gap_seconds,visibility) VALUES(?,?,?,?)", (session_id, observed_at, gap, visibility))

    GAP_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

    def gap_telemetry(self, session_id: str) -> dict[str, Any]:
        """Publish-gap distribution (seconds between consecutive frames)."""
        rows = self._db.execute("SELECT gap_seconds,visibility FROM publish_gaps WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        gaps = sorted(r["gap_seconds"] for r in rows[1:])  # the first frame has no predecessor
        top = f">{self.GAP_BUCKETS[-1]:g}s"
        histogram = {f"<={edge:g}s": 0 for edge in self.GAP_BUCKETS} | {top: 0}
        for gap in gaps:
            histogram[next((f"<={edge:g}s" for edge in self.GAP_BUCKETS if gap <= edge), top)] += 1

        def percentile(fraction: float) -> float | None:
            # Nearest-rank percentile.
            return round(gaps[max(0, math.ceil(fraction * len(gaps)) - 1)], 3) if gaps else None

        return {
            "frames": len(rows),
            "gaps": len(gaps),
            "p50_seconds": percentile(0.5),
            "p95_seconds": percentile(0.95),
            "max_seconds": round(gaps[-1], 3) if gaps else None,
            "over_freshness_limit": sum(1 for gap in gaps if gap > self.config.max_observation_age_seconds),
            "hidden_tab_frames": sum(1 for r in rows if r["visibility"] not in (None, "visible")),
            "histogram": histogram,
        }

    def export_session(self, session_id: str) -> dict[str, Any]:
        """Session snapshot plus the complete journal and publish-gap telemetry."""
        return {
            "status": self.status(session_id),
            "journal": list(reversed(self.journal(session_id, limit=1_000_000))),
            "publish_gaps": self.gap_telemetry(session_id),
        }

    def heartbeat(self, observation: DraftObservation | Mapping[str, Any], *, visibility: str | None = None) -> dict[str, Any]:
        """Accept an in-page liveness frame without disturbing claim revisions.

        A frame whose visible board equals the latest observation only extends
        that observation's freshness (and clock); the revision, and therefore
        every claim bound to it, is unchanged.  A changed board is ingested as
        a new revision numbered by the coordinator, so the page and the agent
        never need to share a counter.  Incomplete frames are ignored and never
        refresh freshness.
        """
        obs = observation if isinstance(observation, DraftObservation) else DraftObservation.from_dict(observation)
        try:
            age = timestamp_age(obs.observed_at)
        except (TypeError, ValueError) as exc:
            raise FreshnessError(f"invalid observation timestamp: {exc}") from exc
        if age < 0:
            raise FreshnessError("future-dated observation rejected")
        if age > self.config.max_observation_age_seconds:
            raise FreshnessError(f"heartbeat frame is {age:.1f}s old; maximum is {self.config.max_observation_age_seconds:.1f}s")
        self._pause_if_stale(obs.session_id)
        row = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (obs.session_id,)).fetchone()
        latest = self._latest_observation(obs.session_id) if row is not None else None
        if row is not None and RoomIdentity(**_loads(row["room_json"])) != obs.room:
            self._room_violation(obs.session_id)
        if not obs.complete:
            return {"status": "ignored", "reason": "incomplete_frame", "state": row["state"] if row else None, "revision": latest.revision if latest else None}
        if latest is not None and self._same_board(latest, obs):
            if timestamp_age(latest.observed_at) <= age:
                return {"status": "ignored", "reason": "not_newer", "state": row["state"], "revision": latest.revision}
            touched = replace(latest, observed_at=obs.observed_at, seconds_remaining=obs.seconds_remaining, warnings=obs.warnings)
            with self._transaction() as db:
                db.execute("UPDATE observations SET observed_at=?,payload=?,received_at=? WHERE session_id=? AND revision=?", (touched.observed_at, _json(touched.to_dict()), self.clock(), obs.session_id, latest.revision))
                self._record_gap_db(db, obs.session_id, obs.observed_at, visibility)
            self._recover_degraded(touched)
            status, revision = "touched", latest.revision
        else:
            revision = max(int(row["latest_revision"] or 0) if row else 0, latest.revision if latest else 0) + 1
            self.ingest_observation(replace(obs, revision=revision), visibility=visibility)
            status = "ingested"
        state = self._session_row(obs.session_id)
        return {"status": status, "state": state["state"], "reason": state["reason"], "revision": revision, "control_version": state["control_version"]}

    def connect(self, url: str, *, practice: bool = False, session_id: str | None = None) -> dict[str, Any]:
        """Parse and remember a room URL.  Connecting never starts a draft."""
        room = RoomIdentity.from_url(url, practice=practice)
        if room.practice and room.league_id == self.config.league_id:
            raise IdentityError("the configured live league cannot be labeled as a practice room")
        sid = session_id or uuid.uuid4().hex
        now = self.clock()
        with self._transaction() as db:
            db.execute(
                "INSERT INTO sessions(session_id,room_json,state,reason,manual_override,control_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (sid, _json(asdict(room)), "observing", "connected; explicit start required", 0, 0, now, now),
            )
            self._set_intent_db(db, sid, "pause", reason="connect does not activate a draft")
            self._journal_db(db, sid, "connect", {"practice": practice, "safe_url": room.safe_url()}, now)
        return self.status(sid)

    def create_session(self, room: RoomIdentity, *, session_id: str | None = None) -> dict[str, Any]:
        return self.connect(room.safe_url(), practice=room.practice, session_id=session_id)

    def attest_practice(self, session_id: str, heading: str, source_league_id: str) -> dict[str, Any]:
        """Record visible proof that this exact session is a practice room.

        ESPN assigns a transient league ID to each practice room.  The agent
        must observe and type the room heading, then explicitly identify the
        configured live league that launched it.  The attestation is immutable
        for the session and is never inferred from a config or observation.
        """
        session = self._session_row(session_id)
        room = self._room_from_row(session)
        if not room.practice:
            raise IdentityError("practice attestation requires a session explicitly connected as practice")
        if room.league_id == self.config.league_id:
            raise IdentityError("the configured live league cannot be labeled as a practice room")
        if room.season_id != self.config.season_id or room.team_id != self.config.team_id:
            raise IdentityError("practice room season and team must match the configured source team")
        if not isinstance(source_league_id, str) or not source_league_id.isdigit():
            raise IdentityError("source league ID must be a numeric string")
        if source_league_id != self.config.league_id:
            raise IdentityError("practice source league does not match the configured live league")
        normalized_heading = self._normalize_practice_heading(heading)
        if not self._valid_practice_heading(normalized_heading):
            raise IdentityError("observed heading must explicitly identify an ESPN Fantasy Basketball practice draft")
        now = self.clock()
        payload = {
            "room": asdict(room),
            "source_league_id": source_league_id,
            "heading": normalized_heading,
        }
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM practice_attestations WHERE session_id=?", (session_id,)
            ).fetchone()
            if existing is not None:
                existing_room = RoomIdentity(**_loads(existing["room_json"]))
                if (
                    existing_room != room
                    or existing["source_league_id"] != source_league_id
                    or existing["heading"] != normalized_heading
                ):
                    raise IdentityError("a different practice attestation is already bound to this session")
            else:
                db.execute(
                    "INSERT INTO practice_attestations(session_id,room_json,source_league_id,heading,created_at) VALUES(?,?,?,?,?)",
                    (session_id, _json(asdict(room)), source_league_id, normalized_heading, now),
                )
            self._journal_db(db, session_id, "practice_attested", payload, now)
        return self.status(session_id)

    # Agent-facing naming for integrations that call this a verification.
    verify_practice = attest_practice

    def ingest_observation(self, observation: DraftObservation | Mapping[str, Any], *, visibility: str | None = None) -> dict[str, Any]:
        obs = observation if isinstance(observation, DraftObservation) else DraftObservation.from_dict(observation)
        try:
            age = timestamp_age(obs.observed_at)
        except (TypeError, ValueError) as exc:
            raise FreshnessError(f"invalid observation timestamp: {exc}") from exc
        if age < 0:
            raise FreshnessError("future-dated observation rejected")
        # Judge the gap against the previous frame before the new one replaces
        # it, so a gap nobody polled during is still degraded or paused.
        self._pause_if_stale(obs.session_id)
        known = self._db.execute("SELECT room_json FROM sessions WHERE session_id=?", (obs.session_id,)).fetchone()
        if known is not None and RoomIdentity(**_loads(known["room_json"])) != obs.room:
            self._room_violation(obs.session_id)
        with self._transaction() as db:
            session = db.execute("SELECT * FROM sessions WHERE session_id=?", (obs.session_id,)).fetchone()
            now = self.clock()
            if session is None:
                db.execute(
                    "INSERT INTO sessions(session_id,room_json,state,reason,manual_override,control_version,latest_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (obs.session_id, _json(asdict(obs.room)), "observing", "observation received; explicit start required", 0, 0, obs.revision, now, now),
                )
                self._set_intent_db(db, obs.session_id, "pause", reason="observation does not activate a draft")
            else:
                room = RoomIdentity(**_loads(session["room_json"]))
                if room != obs.room:
                    raise IdentityError("observation room does not match connected room")
                old = session["latest_revision"]
                if old is not None and obs.revision <= int(old):
                    raise IdentityError(f"observation revision {obs.revision} is not newer than {old}")
                db.execute("UPDATE sessions SET latest_revision=?,updated_at=? WHERE session_id=?", (obs.revision, now, obs.session_id))
            db.execute(
                "INSERT INTO observations(session_id,revision,observed_at,payload,received_at) VALUES(?,?,?,?,?)",
                (obs.session_id, obs.revision, obs.observed_at, _json(obs.to_dict()), now),
            )
            self._journal_db(db, obs.session_id, "observation", {"revision": obs.revision, "age_seconds": age}, now)
            self._record_gap_db(db, obs.session_id, obs.observed_at, visibility)
            if self._is_authoritative_complete(obs):
                db.execute(
                    "UPDATE sessions SET state='completed',reason='authoritative final draft frame',manual_override=0,control_version=control_version+1,updated_at=? WHERE session_id=? AND state != 'completed'",
                    (now, obs.session_id),
                )
                self._set_intent_db(db, obs.session_id, "pause", reason="draft completed")
                self._journal_db(db, obs.session_id, "completed", {"revision": obs.revision, "history": len(obs.history), "roster": len(obs.roster)}, now)
        self._recover_degraded(obs)
        readiness = self.readiness(obs.session_id)
        if self._session_row(obs.session_id)["state"] == "running" and not readiness["ready"]:
            self.pause(obs.session_id, reason="observation requires recovery: " + "; ".join(readiness["reasons"]))
        return self.status(obs.session_id)

    def ingest_or_match_observation(
        self,
        observation: DraftObservation | Mapping[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a frame, or accept one exact retry of the latest frame.

        The raw ``ingest_observation`` path remains strict for CLI/replay
        callers.  Server operations can publish a frame and then carry that
        same frame into their action request; accepting an equal dataclass at
        the same revision makes that handoff idempotent without permitting a
        changed frame to overwrite durable state.
        """
        obs = observation if isinstance(observation, DraftObservation) else DraftObservation.from_dict(observation)
        if session_id is not None and obs.session_id != session_id:
            raise IdentityError("observation session does not match the requested session")
        try:
            age = timestamp_age(obs.observed_at)
        except (TypeError, ValueError) as exc:
            raise FreshnessError(f"invalid observation timestamp: {exc}") from exc
        if age < 0:
            raise FreshnessError("future-dated observation rejected")

        row = self._db.execute("SELECT * FROM sessions WHERE session_id=?", (obs.session_id,)).fetchone()
        if row is not None:
            if RoomIdentity(**_loads(row["room_json"])) != obs.room:
                self._room_violation(obs.session_id)
            latest = self._latest_observation(obs.session_id)
            if latest is not None and obs.revision == latest.revision:
                if obs == latest:
                    return self.status(obs.session_id)
                # A retry of the same board that predates a heartbeat refresh
                # of that revision is still an idempotent retry.
                if self._same_board(obs, latest) and timestamp_age(obs.observed_at) > timestamp_age(latest.observed_at):
                    return self.status(obs.session_id)
                raise IdentityError("observation revision already exists with different visible state")
            if latest is not None and obs.revision < latest.revision and self._same_board(obs, latest):
                # The heartbeat already numbered this board; nothing changed.
                return self.status(obs.session_id)
        return self.ingest_observation(obs)

    @staticmethod
    def _expected_team_for_pick(pick_number: int, pick_order: tuple[str, ...]) -> str | None:
        if not 1 <= pick_number <= 140 or len(pick_order) != 14:
            return None
        round_index, offset = divmod(pick_number - 1, 14)
        seat_index = offset if round_index % 2 == 0 else 13 - offset
        return pick_order[seat_index]

    def _is_authoritative_complete(self, observation: DraftObservation) -> bool:
        if observation.phase.lower() != "complete" or not observation.history_complete:
            return False
        numbers = [event.pick_number for event in observation.history]
        player_ids = [event.player_id for event in observation.history]
        own_history_ids = [event.player_id for event in observation.history if event.team == self.config.our_team]
        roster_ids = [player.player_id for player in observation.roster]
        if numbers != list(range(1, 141)) or len(player_ids) != len(set(player_ids)) or len(observation.roster) != 10:
            return False
        if len(roster_ids) != len(set(roster_ids)) or set(roster_ids) != set(own_history_ids) or len(own_history_ids) != 10:
            return False
        if len(observation.pick_order) != 14 or len(set(observation.pick_order)) != 14:
            return False
        if not all(event.team == self._expected_team_for_pick(event.pick_number, observation.pick_order) for event in observation.history):
            return False
        return self._roster_can_fill_starters(observation.roster)

    @staticmethod
    def _roster_can_fill_starters(roster: tuple[LivePlayer, ...]) -> bool:
        """Return whether ten roster players can legally fill C/G/G/F/F/UTIL."""
        required = ("C", "G", "G", "F", "F", "UTIL")
        eligibilities = [set(player.eligibility) for player in roster]

        def assign(slot_index: int, used: frozenset[int]) -> bool:
            if slot_index == len(required):
                return True
            slot = required[slot_index]
            for index, eligibility in enumerate(eligibilities):
                if index in used or (slot != "UTIL" and slot not in eligibility):
                    continue
                if assign(slot_index + 1, used | {index}):
                    return True
            return False

        return assign(0, frozenset())

    def _empty_player_pool_allowed_after_final_own_pick(self, observation: DraftObservation) -> bool:
        """Allow ESPN's empty Players grid only after our tenth pick.

        ESPN can remove the Players grid while the other teams finish.  An
        empty pool is safe to tolerate only when the frame is otherwise a
        complete, authoritative read-back of our legal ten-player roster and
        the observed snake order has no later turn for us.
        """
        if observation.available_players or not observation.complete or not observation.history_complete:
            return False
        if observation.phase.lower() not in {"draft", "active", "on_clock"}:
            return False
        if len(observation.pick_order) != 14 or len(set(observation.pick_order)) != 14 or observation.pick_order.count(self.config.our_team) != 1:
            return False
        if observation.current_pick is None or not 1 <= observation.current_pick <= 140:
            return False
        expected_turn = self._expected_team_for_pick(observation.current_pick, observation.pick_order)
        if expected_turn is None or expected_turn != observation.on_clock_team or expected_turn == self.config.our_team:
            return False
        if any(
            self._expected_team_for_pick(pick, observation.pick_order) == self.config.our_team
            for pick in range(observation.current_pick, 141)
        ):
            return False

        own_history = [event for event in observation.history if event.team == self.config.our_team]
        own_history_ids = [event.player_id for event in own_history]
        if len(own_history) != 10 or len(set(own_history_ids)) != 10:
            return False
        if not all(
            self._expected_team_for_pick(event.pick_number, observation.pick_order) == self.config.our_team
            for event in own_history
        ):
            return False

        roster_ids = [player.player_id for player in observation.roster]
        if len(roster_ids) != 10 or len(set(roster_ids)) != 10 or set(roster_ids) != set(own_history_ids):
            return False
        return self._roster_can_fill_starters(observation.roster)

    def readiness(self, session_id: str) -> dict[str, Any]:
        row = self._session_row(session_id)
        obs = self._latest_observation(session_id)
        checks: dict[str, bool] = {}
        reasons: list[str] = []
        room = self._room_from_row(row)
        if room.practice:
            # ESPN creates a new league ID for each practice room.  The
            # explicit attestation binds this exact session and room to the
            # configured live league; a practice flag alone is never enough.
            checks["room"] = room.league_id != self.config.league_id and room.season_id == self.config.season_id
            checks["practice_attestation"] = self._practice_attestation_matches(session_id, room)
            if not checks["practice_attestation"]:
                reasons.append("practice room requires a matching visible heading and configured source league attestation")
        else:
            checks["room"] = room.league_id == self.config.league_id and room.season_id == self.config.season_id
        if not checks["room"]:
            reasons.append("connected room does not match configured league and season")
        checks["team"] = room.team_id == self.config.team_id
        if not checks["team"]:
            reasons.append("connected team does not match configured team")
        if obs is None:
            for key in ("fresh", "rules", "order", "phase", "history", "history_teams", "turn_order", "roster", "current_pick", "frame", "clock", "autopick", "available_pool"):
                checks[key] = False
            reasons.append("no observation")
        else:
            try:
                self._require_fresh(obs)
                checks["fresh"] = True
            except FreshnessError as exc:
                checks["fresh"] = False
                reasons.append(str(exc))
            rules = obs.rules or {}
            slots = tuple(rules.get("starting_slots", rules.get("slots", ())))
            scoring = rules.get("scoring")
            checks["rules"] = (
                rules.get("teams") == 14
                and rules.get("rounds") == 10
                and slots == self.config.starting_slots
                and rules.get("bench_slots") == 4
                and rules.get("ir_slots") == 3
                and isinstance(scoring, Mapping)
                and dict(scoring) == dict(self.config.scoring)
            )
            if not checks["rules"]:
                reasons.append("observed rules are not the verified 14-team, ten-round configuration")
            checks["order"] = len(obs.pick_order) == 14 and len(set(obs.pick_order)) == 14 and obs.pick_order.count(self.config.our_team) == 1
            if not checks["order"]:
                reasons.append("observed pick order must contain fourteen unique teams including ours exactly once")
            checks["phase"] = bool(obs.phase) and obs.phase.lower() in {"draft", "active", "on_clock", "pre_draft"}
            if not checks["phase"]:
                reasons.append("draft phase is incomplete or unknown")
            pick_numbers = [p.pick_number for p in obs.history]
            players = [p.player_id for p in obs.history]
            expected_history = list(range(1, obs.current_pick or 1))
            current_pick_valid = isinstance(obs.current_pick, int) and 1 <= obs.current_pick <= 140
            checks["current_pick"] = current_pick_valid
            checks["history"] = bool(obs.complete and obs.history_complete and current_pick_valid and pick_numbers == expected_history and len(players) == len(set(players)))
            checks["history_teams"] = bool(checks["history"] and all(event.team == self._expected_team_for_pick(event.pick_number, obs.pick_order) for event in obs.history))
            checks["turn_order"] = bool(current_pick_valid and obs.on_clock_team == self._expected_team_for_pick(obs.current_pick, obs.pick_order))
            own_history_ids = [event.player_id for event in obs.history if event.team == self.config.our_team]
            roster_ids = [player.player_id for player in obs.roster]
            checks["roster"] = len(roster_ids) == len(set(roster_ids)) and set(roster_ids) == set(own_history_ids)
            checks["frame"] = bool(obs.complete)
            checks["available_pool"] = bool(obs.available_players) or self._empty_player_pool_allowed_after_final_own_pick(obs)
            checks["clock"] = obs.current_pick is not None and obs.seconds_remaining is not None and obs.seconds_remaining >= 0
            checks["autopick"] = obs.autopick is False
            if not checks["history"]:
                reasons.append("pick history must be contiguous, unique, and complete through the current pick")
            if not checks["history_teams"]:
                reasons.append("pick history teams do not match the observed snake order")
            if not checks["turn_order"]:
                reasons.append("on-clock team does not match the observed snake order")
            if not checks["roster"]:
                reasons.append("observed roster must uniquely match our picks in authoritative history")
            if not checks["available_pool"]:
                reasons.append("current available-player candidates are missing")
            if not checks["clock"]:
                reasons.append("draft clock is unknown or negative")
            if not checks["autopick"]:
                reasons.append("autopick must be explicitly false before automation can run")
        freshness = self.projection_freshness()
        if freshness is not None and freshness["enforced"]:
            checks["projection_fresh"] = bool(freshness["ok"])
            if not freshness["ok"]:
                reasons.append("projection data is stale or unverified: " + "; ".join(freshness["warnings"]))
        ready = bool(checks) and all(checks.values())
        return {"ready": ready, "checks": checks, "reasons": reasons, "session_id": session_id, "revision": row["latest_revision"]}

    def projection_freshness(self) -> dict[str, Any] | None:
        """Projection staleness report, or None when no policy is configured."""
        if self.projection_policy is None:
            return None
        return self.projection_policy.evaluate(now=self.clock())

    def start(self, session_id: str) -> dict[str, Any]:
        return self._set_running(session_id, "start")

    def resume(self, session_id: str) -> dict[str, Any]:
        return self._set_running(session_id, "resume")

    def _set_running(self, session_id: str, command: str) -> dict[str, Any]:
        row = self._session_row(session_id)
        if row["state"] not in {"ready", "paused", "observing", "degraded"}:
            raise CoordinatorError(f"{command} requires a ready or paused session")
        ready = self.readiness(session_id)
        if not ready["ready"]:
            raise ReadinessError("; ".join(ready["reasons"]) or "readiness checks failed")
        now = self.clock()
        with self._transaction() as db:
            current = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            other = db.execute("SELECT session_id FROM sessions WHERE session_id<>? AND state IN ('running','degraded') LIMIT 1", (session_id,)).fetchone()
            if other:
                raise ActionConflict(f"pause active session {other['session_id']} before starting another room")
            unresolved_other = db.execute("SELECT session_id FROM actions WHERE session_id<>? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone()
            if unresolved_other:
                raise ActionConflict(f"reconcile outstanding action in session {unresolved_other['session_id']} before starting another room")
            unresolved_queue_other = db.execute("SELECT session_id FROM queue_mutations WHERE session_id<>? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone()
            if unresolved_queue_other:
                raise ActionConflict(f"reconcile outstanding queue mutation in session {unresolved_queue_other['session_id']} before starting another room")
            # A fresh read inside the write transaction prevents two operators
            # from racing a pause/resume command.
            if command in {"start", "resume"} and current["manual_override"]:
                staged = db.execute("SELECT action_id,payload FROM actions WHERE session_id=? AND status='pending' AND dispatched=0", (session_id,)).fetchall()
                for pending in staged:
                    payload = _loads(pending["payload"])
                    if str(payload.get("detail", "")).startswith("manual_stage:"):
                        payload.update(status="cancelled", updated_at=now, detail="manual stage released by explicit resume")
                        db.execute("UPDATE actions SET status='cancelled',payload=?,updated_at=? WHERE action_id=?", (_json(payload), now, pending["action_id"]))
                        self._journal_db(db, session_id, "manual_stage_released", {"action_id": pending["action_id"]}, now)
            db.execute("UPDATE sessions SET state='running',reason=?,manual_override=0,control_version=control_version+1,updated_at=? WHERE session_id=?", (f"explicit {command}", now, session_id))
            self._set_intent_db(db, session_id, "resume", reason=f"explicit {command}")
            self._journal_db(db, session_id, command, {"from": current["state"]}, now)
        return self.status(session_id)

    def pause(self, session_id: str, *, reason: str = "paused by user", manual_override: bool = False) -> dict[str, Any]:
        current = self._session_row(session_id)
        if current["state"] == "completed":
            raise CoordinatorError("completed session is terminal")
        now = self.clock()
        with self._transaction() as db:
            self._pause_db(db, session_id, now, reason=reason, manual_override=manual_override)
        return self.status(session_id)

    def _pause_db(self, db: sqlite3.Connection, session_id: str, now: str, *, reason: str, manual_override: bool = False) -> None:
        """Apply pause effects inside an existing transaction."""
        db.execute("UPDATE sessions SET state='paused',reason=?,manual_override=?,control_version=control_version+1,updated_at=? WHERE session_id=?", (reason, int(manual_override), now, session_id))
        # A browser click already acknowledged as submitted or uncertain
        # cannot be cancelled. Pending claims are safe to cancel now.
        pending = db.execute("SELECT action_id,payload FROM actions WHERE session_id=? AND status='pending' AND dispatched=0", (session_id,)).fetchall()
        for action in pending:
            payload = _loads(action["payload"])
            payload["status"] = "cancelled"
            payload["updated_at"] = now
            payload["detail"] = "cancelled by pause before browser submission"
            db.execute("UPDATE actions SET status='cancelled',payload=?,updated_at=? WHERE action_id=?", (_json(payload), now, action["action_id"]))
            self._journal_db(db, session_id, "action_cancelled", {"action_id": action["action_id"], "reason": reason}, now)
        pending_queue = db.execute(
            "SELECT mutation_id,payload FROM queue_mutations WHERE session_id=? AND status='pending' AND dispatched=0",
            (session_id,),
        ).fetchall()
        for mutation in pending_queue:
            payload = _loads(mutation["payload"])
            payload["status"] = "cancelled"
            payload["updated_at"] = now
            payload["detail"] = "cancelled by pause before visible queue submission"
            db.execute("UPDATE queue_mutations SET status='cancelled',payload=?,updated_at=? WHERE mutation_id=?", (_json(payload), now, mutation["mutation_id"]))
            self._journal_db(db, session_id, "queue_mutation_cancelled", {"mutation_id": mutation["mutation_id"], "reason": reason}, now)
        self._set_intent_db(db, session_id, "pause", reason=reason)
        self._journal_db(db, session_id, "manual_override" if manual_override else "pause", {"cancelled": len(pending), "reason": reason}, now)

    def set_manual_override(self, session_id: str, enabled: bool = True) -> dict[str, Any]:
        if enabled:
            return self.pause(session_id, reason="manual override", manual_override=True)
        with self._transaction() as db:
            db.execute("UPDATE sessions SET manual_override=0,updated_at=? WHERE session_id=?", (self.clock(), session_id))
        return self.status(session_id)

    # Queue fallback has its own durable lifecycle.  It must not reuse
    # DraftAction: queue rows are maintenance intent and never count as one of
    # the ten confirmed Draft selections.
    @staticmethod
    def _queue_player_id(value: Any) -> str:
        if isinstance(value, Mapping):
            for key in ("player_id", "playerId", "data_player_id", "dataPlayerId", "data-player-id", "id"):
                if value.get(key) not in (None, ""):
                    return str(value[key]).strip()
            return ""
        return str(value or "").strip()

    @classmethod
    def _queue_ids(cls, queue: Any) -> list[str]:
        if isinstance(queue, Mapping):
            queue = queue.get("queue", queue.get("queued_players", queue.get("queuedPlayers")))
        if not isinstance(queue, (list, tuple)):
            raise CoordinatorError("visible queue read-back must be an array")
        ids = [cls._queue_player_id(item) for item in queue]
        if any(not item for item in ids):
            raise CoordinatorError("visible queue read-back contains a row without a stable player id")
        return ids

    def _queue_mutation_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = _loads(row["payload"], {})
        payload["mutation_id"] = row["mutation_id"]
        payload["status"] = row["status"]
        payload["dispatched"] = bool(row["dispatched"])
        payload["claim_token"] = bool(row["claim_token"])
        return payload

    def _active_queue_mutation(self, session_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') ORDER BY created_at LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._queue_mutation_from_row(row) if row else None

    def _queue_observation(self, session_id: str, observation: DraftObservation | Mapping[str, Any]) -> DraftObservation:
        """Validate queue-frame identity before touching the durable session."""
        obs = observation if isinstance(observation, DraftObservation) else DraftObservation.from_dict(observation)
        session = self._session_row(session_id)
        room = self._room_from_row(session)
        if obs.session_id != session_id:
            raise IdentityError("queue observation session does not match the requested session")
        if obs.room != room:
            raise IdentityError("queue observation room does not match the requested session")
        self.ingest_or_match_observation(obs, session_id=session_id)
        return self._latest_observation(session_id) or obs

    def _fresh_queue_confirmation_observation(
        self,
        session_id: str,
        observation: DraftObservation | Mapping[str, Any] | None,
    ) -> DraftObservation:
        if observation is None:
            raise ActionConflict("queue confirmation requires a fresh authoritative observation")
        obs = self._queue_observation(session_id, observation)
        # Queue read-back is never interpreted as safe while Autopick is on or
        # unknown.  The browser must visibly turn it off and publish a fresh
        # explicit false frame before resuming reconciliation.
        if obs.autopick is not False:
            raise ActionConflict("Autopick is enabled or unknown; pause and recover explicitly before queue confirmation")
        ready = self.readiness(session_id)
        if not ready["ready"]:
            raise ReadinessError("; ".join(ready["reasons"]) or "queue confirmation readiness checks failed")
        return self._require_fresh(self._latest_observation(session_id))

    def _queue_preflight(
        self,
        session_id: str,
        *,
        operation: str | None = None,
        player_id: str | None = None,
        expected_revision: int | None = None,
        expected_pick: int | None = None,
        expected_team: str | None = None,
        allow_mutation_id: str | None = None,
    ) -> DraftObservation:
        if operation not in {None, "add", "remove"}:
            raise CoordinatorError("queue operation must be add or remove")
        self._pause_if_stale(session_id)
        row = self._session_row(session_id)
        if row["state"] != "running":
            raise ActionConflict("queue maintenance requires an explicitly running session")
        if self._active_unresolved(session_id):
            raise ActionConflict("an unresolved direct Draft action blocks queue maintenance")
        active_queue = self._active_queue_mutation(session_id)
        if active_queue and active_queue.get("mutation_id") != allow_mutation_id:
            raise ActionConflict(f"queue mutation {active_queue['mutation_id']} is unresolved")
        ready = self.readiness(session_id)
        if not ready["ready"]:
            raise ReadinessError("; ".join(ready["reasons"]) or "queue maintenance readiness checks failed")
        obs = self._require_fresh(self._latest_observation(session_id))
        if expected_revision is not None and obs.revision != expected_revision:
            raise IdentityError("observation revision changed")
        if expected_pick is not None and obs.current_pick != expected_pick:
            raise IdentityError("current pick changed")
        if expected_team is not None and obs.on_clock_team != expected_team:
            raise IdentityError("on-clock team changed")
        if obs.session_id != session_id or obs.room != self._room_from_row(row):
            raise IdentityError("observation session or room identity changed")
        if obs.current_pick is None or obs.on_clock_team is None:
            raise FreshnessError("current pick and on-clock team are required for queue maintenance")
        if obs.seconds_remaining is None or obs.seconds_remaining - timestamp_age(obs.observed_at) <= 0:
            raise FreshnessError("draft clock is unknown or expired after observation age")
        if obs.autopick is not False:
            raise ActionConflict("autopick is unknown or enabled; pause and recover explicitly")
        if not isinstance(player_id, str) or not player_id or player_id.startswith("visible:"):
            raise IdentityError("queue mutation requires an explicit stable player id")
        history_ids = {event.player_id for event in obs.history}
        roster_ids = {player.player_id for player in obs.roster}
        if operation == "add":
            player = next((item for item in obs.available_players if item.player_id == player_id), None)
            if player is None:
                raise IdentityError("queue candidate is not in the fresh visible available-player rows")
            if player_id in history_ids or player_id in roster_ids:
                raise IdentityError("queue candidate is already drafted or rostered")
        return obs

    def prepare_queue_mutation(
        self,
        session_id: str,
        operation: str,
        player_id: str,
        *,
        player_name: str | None = None,
        observation: DraftObservation | Mapping[str, Any] | None = None,
        expected_revision: int | None = None,
        expected_pick: int | None = None,
        expected_team: str | None = None,
        queue: Any = None,
    ) -> dict[str, Any]:
        """Reserve one guarded visible queue add/remove mutation.

        The caller supplies a fresh frame and, for removal, the visible queue
        read-back proving that the exact row exists.  This method does not
        click or mutate ESPN.
        """
        if observation is not None:
            obs = self._queue_observation(session_id, observation)
        obs = self._queue_preflight(
            session_id,
            operation=operation,
            player_id=player_id,
            expected_revision=expected_revision,
            expected_pick=expected_pick,
            expected_team=expected_team,
        )
        if queue is None:
            raise IdentityError("queue mutation requires an explicit visible queue read-back")
        visible_queue_ids = self._queue_ids(queue)
        if operation == "remove" and player_id not in visible_queue_ids:
            raise IdentityError("remove requires an existing exact visible queue row")
        if operation == "add" and player_id in visible_queue_ids:
            raise ActionConflict("queue candidate is already present; preserve the visible queue as a no-op")
        player = next((item for item in obs.available_players if item.player_id == player_id), None)
        if player_name is None:
            player_name = player.name if player is not None else player_id
        session_snapshot = self._session_row(session_id)
        expected_control_version = int(session_snapshot["control_version"])
        now = self.clock()
        mutation_id = uuid.uuid4().hex
        payload = {
            "mutation_id": mutation_id,
            "session_id": session_id,
            "room": asdict(obs.room),
            "operation": operation,
            "player_id": player_id,
            "player_name": player_name,
            "revision": obs.revision,
            "expected_pick": obs.current_pick,
            "expected_team": obs.on_clock_team,
            "status": "pending",
            "dispatched": False,
            "created_at": now,
            "updated_at": now,
            "control_version": expected_control_version,
            "detail": "prepared from fresh visible queue state; requires final guard",
        }
        try:
            with self._transaction() as db:
                current = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if not current or current["state"] != "running" or current["control_version"] != expected_control_version or current["latest_revision"] != obs.revision:
                    raise ActionConflict("queue control intent changed before reservation")
                if db.execute("SELECT action_id FROM actions WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone():
                    raise ActionConflict("an unresolved direct Draft action blocks queue maintenance")
                if db.execute("SELECT mutation_id FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone():
                    raise ActionConflict("another queue mutation is already unresolved")
                db.execute(
                    "INSERT INTO queue_mutations(mutation_id,session_id,status,payload,created_at,updated_at,dispatched) VALUES(?,?,?,?,?,?,0)",
                    (mutation_id, session_id, "pending", _json(payload), now, now),
                )
                self._journal_db(db, session_id, "queue_mutation_proposed", payload, now)
        except sqlite3.IntegrityError as exc:
            raise ActionConflict("another queue mutation was reserved for this session") from exc
        return payload

    def claim_queue_mutation(self, mutation_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM queue_mutations WHERE mutation_id=?", (mutation_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown queue mutation")
        mutation = self._queue_mutation_from_row(row)
        if mutation["status"] != "pending":
            raise ActionConflict(f"queue mutation is {mutation['status']}")
        self._queue_preflight(
            mutation["session_id"],
            operation=mutation["operation"],
            player_id=mutation["player_id"],
            expected_revision=mutation["revision"],
            expected_pick=mutation["expected_pick"],
            expected_team=mutation["expected_team"],
            allow_mutation_id=mutation_id,
        )
        token = secrets.token_urlsafe(18)
        with self._transaction() as db:
            current = db.execute("SELECT * FROM queue_mutations WHERE mutation_id=?", (mutation_id,)).fetchone()
            session = db.execute("SELECT state,control_version,latest_revision FROM sessions WHERE session_id=?", (mutation["session_id"],)).fetchone()
            if not current or current["status"] != "pending" or current["claim_token"]:
                raise ActionConflict("queue mutation was claimed by another operator")
            if not session or session["state"] != "running" or session["control_version"] != mutation.get("control_version") or session["latest_revision"] != mutation["revision"]:
                raise ActionConflict("queue control intent changed before claim")
            db.execute("UPDATE queue_mutations SET claim_token=?,updated_at=? WHERE mutation_id=? AND status='pending' AND claim_token IS NULL", (token, self.clock(), mutation_id))
            self._journal_db(db, mutation["session_id"], "queue_mutation_claimed", {"mutation_id": mutation_id, "revision": mutation["revision"]})
        mutation["claim_token"] = True
        return mutation

    def final_queue_guard(self, mutation_id: str, *, queue: Any = None) -> dict[str, Any]:
        """Consume the one-time queue guard immediately before a visible click."""
        row = self._db.execute("SELECT * FROM queue_mutations WHERE mutation_id=?", (mutation_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown queue mutation")
        mutation = self._queue_mutation_from_row(row)
        if mutation["status"] != "pending" or not mutation["claim_token"]:
            raise ActionConflict("queue mutation is not claimable")
        if queue is None:
            raise IdentityError("final queue guard requires a fresh visible queue read-back")
        visible_queue_ids = self._queue_ids(queue)
        if mutation["operation"] == "remove" and mutation["player_id"] not in visible_queue_ids:
            raise IdentityError("final remove guard requires the exact visible queue row")
        if mutation["operation"] == "add" and mutation["player_id"] in visible_queue_ids:
            raise ActionConflict("queue candidate appeared in the fresh read-back; preserve the visible queue as a no-op")
        self._queue_preflight(
            mutation["session_id"],
            operation=mutation["operation"],
            player_id=mutation["player_id"],
            expected_revision=mutation["revision"],
            expected_pick=mutation["expected_pick"],
            expected_team=mutation["expected_team"],
            allow_mutation_id=mutation_id,
        )
        with self._transaction() as db:
            session = db.execute("SELECT state,control_version,latest_revision FROM sessions WHERE session_id=?", (mutation["session_id"],)).fetchone()
            current = db.execute("SELECT status,claim_token,dispatched FROM queue_mutations WHERE mutation_id=?", (mutation_id,)).fetchone()
            if not session or not current or session["state"] != "running" or session["control_version"] != mutation.get("control_version") or session["latest_revision"] != mutation["revision"] or current["status"] != "pending" or not current["claim_token"]:
                raise ActionConflict("queue control intent changed before visible dispatch")
            if current["dispatched"]:
                raise ActionConflict("queue mutation has already been dispatched")
            now = self.clock()
            mutation["dispatched"] = True
            mutation["updated_at"] = now
            db.execute("UPDATE queue_mutations SET dispatched=1,payload=?,updated_at=? WHERE mutation_id=? AND status='pending' AND dispatched=0", (_json(mutation), now, mutation_id))
            self._journal_db(db, mutation["session_id"], "queue_mutation_dispatched", {"mutation_id": mutation_id})
        return {
            "allowed": True,
            "type": "queue_player" if mutation["operation"] == "add" else "remove_queued_player",
            "mutation_id": mutation_id,
            "session_id": mutation["session_id"],
            "room": mutation["room"],
            "player_id": mutation["player_id"],
            "player_name": mutation["player_name"],
            "expected_pick": mutation["expected_pick"],
            "expected_team": mutation["expected_team"],
            "revision": mutation["revision"],
        }

    def ack_queue_mutation(
        self,
        mutation_id: str,
        status: str,
        *,
        detail: str = "",
        queue: Any = None,
        observation: DraftObservation | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"submitted", "uncertain", "confirmed", "failed", "cancelled"}:
            raise CoordinatorError("queue ack status must be submitted, uncertain, confirmed, failed, or cancelled")
        row = self._db.execute("SELECT * FROM queue_mutations WHERE mutation_id=?", (mutation_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown queue mutation")
        mutation = self._queue_mutation_from_row(row)
        if mutation["status"] == "confirmed" or mutation["status"] == status:
            return mutation
        if status == "cancelled" and (mutation["status"] != "pending" or mutation.get("dispatched")):
            raise ActionConflict("only an undispatched pending queue mutation can be cancelled")
        if mutation["status"] == "cancelled" and status != "cancelled":
            raise ActionConflict("cancelled queue mutation cannot be acknowledged")
        if status in {"submitted", "uncertain", "confirmed", "failed"} and (
            not mutation.get("claim_token") or not mutation.get("dispatched")
        ):
            raise ActionConflict("queue mutation must pass its one-time final guard before browser acknowledgement")
        obs = None
        if observation is not None:
            obs = self._queue_observation(mutation["session_id"], observation)
        if status == "confirmed":
            obs = self._fresh_queue_confirmation_observation(mutation["session_id"], obs)
            if queue is None:
                raise ActionConflict("queue confirmation requires an explicit visible queue read-back")
            ids = self._queue_ids(queue)
            present = mutation["player_id"] in ids
            expected = mutation["operation"] == "add"
            history_event = next((event for event in obs.history if event.player_id == mutation["player_id"]), None)
            consumed_by_us = bool(
                history_event
                and history_event.team == self.config.our_team
                and any(player.player_id == mutation["player_id"] for player in obs.roster)
            )
            drafted_by_opponent = bool(history_event and history_event.team != self.config.our_team)
            if present != expected and not (not present and expected and (consumed_by_us or drafted_by_opponent)):
                status = "uncertain"
                detail = detail or "queue read-back did not confirm the requested presence/absence"
        now = self.clock()
        mutation["status"] = status
        mutation["updated_at"] = now
        mutation["detail"] = detail or mutation.get("detail", "")
        with self._transaction() as db:
            db.execute("UPDATE queue_mutations SET status=?,payload=?,updated_at=? WHERE mutation_id=?", (status, _json(mutation), now, mutation_id))
            self._journal_db(db, mutation["session_id"], f"queue_mutation_{status}", {"mutation_id": mutation_id, "detail": mutation["detail"]}, now)
            if status == "uncertain":
                db.execute("UPDATE sessions SET state='paused',reason=?,control_version=control_version+1,updated_at=? WHERE session_id=? AND state != 'completed'", ("paused after uncertain queue mutation; explicit resume required", now, mutation["session_id"]))
                self._set_intent_db(db, mutation["session_id"], "pause", reason="uncertain queue mutation")
                self._journal_db(db, mutation["session_id"], "queue_uncertain_pause", {"mutation_id": mutation_id}, now)
        return mutation

    def reconcile_queue_mutations(
        self,
        session_id: str,
        *,
        queue: Any,
        observation: DraftObservation | Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        obs = self._fresh_queue_confirmation_observation(session_id, observation)
        ids = self._queue_ids(queue)
        history_by_id = {event.player_id: event for event in obs.history}
        roster_ids = {player.player_id for player in obs.roster}
        rows = self._db.execute("SELECT * FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') ORDER BY created_at", (session_id,)).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            mutation = self._queue_mutation_from_row(row)
            present = mutation["player_id"] in ids
            event = history_by_id.get(mutation["player_id"])
            consumed = bool(event and event.team == self.config.our_team and mutation["player_id"] in roster_ids)
            opponent = bool(event and event.team != self.config.our_team)
            if (mutation["operation"] == "add" and (present or consumed or opponent)) or (mutation["operation"] == "remove" and not present):
                detail = "queue presence confirmed" if present else "authoritative history/roster read-back confirmed queue entry outcome"
                resolved.append(self.ack_queue_mutation(mutation["mutation_id"], "confirmed", detail=detail, queue=queue, observation=obs))
        return resolved

    def _active_uncertain(self, session_id: str) -> DraftAction | None:
        row = self._db.execute("SELECT payload FROM actions WHERE session_id=? AND status IN ('submitted','uncertain') ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
        return DraftAction(**_loads(row["payload"])) if row else None

    def _active_unresolved(self, session_id: str) -> DraftAction | None:
        row = self._db.execute("SELECT payload FROM actions WHERE session_id=? AND status IN ('pending','submitted','uncertain') ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
        return DraftAction(**_loads(row["payload"])) if row else None

    def _compute_recommendation(
        self,
        observation: DraftObservation,
        *,
        lookahead_enabled: bool = False,
        promotion_path: Any = None,
    ) -> DraftRecommendation:
        """Run the configured scoring policy without changing coordinator state."""
        if promotion_path is None:
            promotion_path = self.promotion_path
        callback = self.strategy
        if callback is None:
            for module_name in ("draft_assistant.valuation", "draft_assistant.strategy", "draft_assistant.strategy_agent", "strategy_agent"):
                try:
                    module = __import__(module_name, fromlist=["recommend"])
                    callback = getattr(module, "recommend", None)
                    if callback:
                        break
                except ImportError:
                    continue
        if callback is None:
            rec = DraftRecommendation(observation.session_id, observation.revision, self.clock(), (), "unavailable", "No strategy provider is installed")
        else:
            fn = getattr(callback, "recommend", callback)
            try:
                rec = fn(observation, self.config, lookahead_enabled=lookahead_enabled, promotion_path=promotion_path)
            except TypeError:
                rec = fn(observation, self.config)
            if isinstance(rec, Mapping):
                rec = DraftRecommendation.from_dict(rec)
            if not isinstance(rec, DraftRecommendation):
                raise CoordinatorError("strategy provider returned an invalid recommendation")
        if rec.session_id != observation.session_id or rec.revision != observation.revision:
            raise IdentityError("recommendation session or revision does not match the latest observation")
        return rec

    def recommend(self, session_id: str, *, lookahead_enabled: bool = False, promotion_path: Any = None) -> DraftRecommendation:
        self._pause_if_stale(session_id)
        obs = self._require_fresh(self._latest_observation(session_id))
        if self._active_uncertain(session_id):
            raise ActionConflict("an uncertain browser outcome must be reconciled before recommending another pick")
        rec = self._compute_recommendation(obs, lookahead_enabled=lookahead_enabled, promotion_path=promotion_path)
        with self._transaction() as db:
            db.execute("INSERT OR REPLACE INTO recommendations(session_id,revision,payload,created_at) VALUES(?,?,?,?)", (session_id, obs.revision, _json(rec.to_dict()), self.clock()))
            self._journal_db(db, session_id, "recommendation", {"revision": obs.revision, "candidates": len(rec.candidates), "status": rec.lookahead_status, "explanation": rec.explanation[:240]})
        return rec

    def recommend_preview(
        self,
        session_id: str,
        observation: DraftObservation | Mapping[str, Any],
        *,
        lookahead_enabled: bool = False,
        promotion_path: Any = None,
    ) -> DraftRecommendation:
        """Score a fresh in-memory full-catalog frame without persisting it.

        Recommendation-only page attachments use this path so the full
        remaining catalog never becomes the coordinator's actionable
        ``available_players`` observation and no recommendation journal row
        or action lifecycle state is created.  Ownership and CSRF are enforced
        by the page endpoint before this method is reached.
        """
        obs = observation if isinstance(observation, DraftObservation) else DraftObservation.from_dict(observation)
        if obs.session_id != session_id:
            raise IdentityError("preview observation session does not match the requested session")
        row = self._session_row(session_id)
        if self._room_from_row(row) != obs.room:
            self._room_violation(session_id)
        self._require_fresh(obs)
        if not obs.complete or not obs.history_complete:
            raise FreshnessError("recommendation preview requires a complete contiguous feed frame")
        self._validate_recommendation_preview_observation(obs)
        if self._active_uncertain(session_id):
            raise ActionConflict("an uncertain browser outcome must be reconciled before recommending another pick")
        return self._compute_recommendation(obs, lookahead_enabled=lookahead_enabled, promotion_path=promotion_path)

    def _validate_recommendation_preview_observation(self, observation: DraftObservation) -> None:
        """Validate the read-only preview contract without action readiness gates.

        Advisory polling may run while ESPN is paused, with an unknown clock or
        native Autopick value.  It still must carry the verified rules, snake
        order, complete history/roster agreement, and the full preloaded
        catalog so a visible viewport can never silently become the strategy
        universe.
        """
        rules = observation.rules or {}
        slots = tuple(rules.get("starting_slots", rules.get("slots", ())))
        scoring = rules.get("scoring")
        if not (
            rules.get("teams") == 14
            and rules.get("rounds") == 10
            and slots == self.config.starting_slots
            and rules.get("bench_slots") == 4
            and rules.get("ir_slots") == 3
            and isinstance(scoring, Mapping)
            and dict(scoring) == dict(self.config.scoring)
        ):
            raise ReadinessError("recommendation preview rules do not match the verified 14-team, ten-round configuration")
        order = tuple(str(team).strip() for team in observation.pick_order)
        if len(order) != 14 or len(set(order)) != 14 or order.count(self.config.our_team) != 1:
            raise ReadinessError("recommendation preview requires fourteen unique teams including ours exactly once")
        history = tuple(observation.history)
        current_pick = observation.current_pick
        if current_pick is None:
            expected_end = 140 if observation.phase.lower() == "complete" else None
        elif isinstance(current_pick, int) and 1 <= current_pick <= 140:
            expected_end = current_pick - 1
        else:
            expected_end = None
        if expected_end is None or [event.pick_number for event in history] != list(range(1, expected_end + 1)):
            raise ReadinessError("recommendation preview history must be contiguous through the observed pick")
        player_ids = [event.player_id for event in history]
        if any(not player_id for player_id in player_ids) or len(player_ids) != len(set(player_ids)):
            raise ReadinessError("recommendation preview history requires unique stable player IDs")
        if any(event.team != self._expected_team_for_pick(event.pick_number, order) for event in history):
            raise ReadinessError("recommendation preview history teams do not match the observed snake order")
        if current_pick is not None and observation.on_clock_team is not None and observation.on_clock_team != self._expected_team_for_pick(current_pick, order):
            raise ReadinessError("recommendation preview on-clock team does not match the observed snake order")
        own_history_ids = [event.player_id for event in history if event.team == self.config.our_team]
        roster_ids = [player.player_id for player in observation.roster]
        if any(not player_id for player_id in roster_ids) or len(roster_ids) != len(set(roster_ids)) or set(roster_ids) != set(own_history_ids):
            raise ReadinessError("recommendation preview roster must match our authoritative history")
        known_ids = [player.player_id for player in observation.known_players]
        available_ids = [player.player_id for player in observation.available_players]
        if not known_ids or any(not player_id for player_id in known_ids) or len(known_ids) != len(set(known_ids)):
            raise ReadinessError("recommendation preview requires a nonempty unique preloaded catalog")
        catalog_warning = any(str(warning) == _RECOMMENDATION_CATALOG_SUBSET_WARNING for warning in observation.warnings)
        if (not observation.available_pool_complete and not catalog_warning) or len(available_ids) != len(set(available_ids)):
            raise ReadinessError("recommendation preview requires an explicitly complete remaining catalog or an explicit incomplete-catalog warning")
        expected_available = set(known_ids) - set(player_ids)
        if not set(available_ids).issubset(expected_available) or (observation.available_pool_complete and set(available_ids) != expected_available):
            raise ReadinessError("recommendation preview remaining catalog does not match authoritative history")
        unresolved_warnings = [
            str(warning)
            for warning in observation.warnings
            if str(warning) != _RECOMMENDATION_CATALOG_SUBSET_WARNING
            and any(token in str(warning).lower() for token in ("conflict", "gap", "incomplete"))
        ]
        if unresolved_warnings:
            raise ReadinessError("recommendation preview contains an unresolved feed conflict or gap")

    def _preflight_action(self, session_id: str, *, expected_revision: int | None = None, expected_pick: int | None = None, expected_team: str | None = None, player_id: str | None = None) -> DraftObservation:
        self._pause_if_stale(session_id)
        row = self._session_row(session_id)
        if row["state"] != "running":
            raise ActionConflict("automation is not running")
        if self._active_queue_mutation(session_id):
            raise ActionConflict("an unresolved queue mutation blocks direct Draft preparation")
        ready = self.readiness(session_id)
        if not ready["ready"]:
            raise ReadinessError("; ".join(ready["reasons"]) or "readiness checks failed before browser action")
        obs = self._require_fresh(self._latest_observation(session_id))
        if expected_revision is not None and obs.revision != expected_revision:
            raise IdentityError("observation revision changed")
        if expected_pick is not None and obs.current_pick != expected_pick:
            raise IdentityError("current pick changed")
        if expected_team is not None and obs.on_clock_team != expected_team:
            raise IdentityError("on-clock team changed")
        if obs.session_id != session_id or obs.room.team_id != self._room_from_row(row).team_id:
            raise IdentityError("observation session or room identity changed")
        if obs.on_clock_team != self.config.our_team:
            raise ActionConflict("it is not our turn")
        if obs.current_pick is None:
            raise ActionConflict("current pick is unknown")
        if not obs.available_players:
            raise FreshnessError("current available-player candidates are missing")
        if obs.seconds_remaining is None or obs.seconds_remaining - timestamp_age(obs.observed_at) <= 0:
            raise FreshnessError("draft clock is unknown or expired after observation age")
        if obs.autopick is not False:
            raise ActionConflict("autopick is unknown or enabled; pause and recover explicitly")
        if player_id is not None and not any(p.player_id == player_id for p in obs.available_players):
            raise IdentityError("candidate is no longer visible in the fresh available pool")
        if player_id is not None and any(event.player_id == player_id for event in obs.history):
            raise IdentityError("candidate already appears in authoritative pick history")
        return obs

    def next_action(self, session_id: str) -> DraftAction | None:
        obs = self._preflight_action(session_id)
        uncertain = self._active_uncertain(session_id)
        if uncertain:
            raise ActionConflict("uncertain browser outcome locks further actions")
        unresolved = self._active_unresolved(session_id)
        if unresolved and unresolved.status != "pending":
            raise ActionConflict(f"unresolved action is {unresolved.status}; reconcile before another action")
        row = self._db.execute("SELECT action_id FROM actions WHERE session_id=? AND status='pending' LIMIT 1", (session_id,)).fetchone()
        if row:
            return self._action_from_row(self._db.execute("SELECT * FROM actions WHERE action_id=?", (row["action_id"],)).fetchone())
        rec_row = self._db.execute("SELECT payload FROM recommendations WHERE session_id=? AND revision=?", (session_id, obs.revision)).fetchone()
        if rec_row is None:
            raise ActionConflict("a recommendation for the latest observation is required")
        rec = DraftRecommendation.from_dict(_loads(rec_row["payload"]))
        available = {p.player_id: p for p in obs.available_players}
        candidate = next((c for c in rec.candidates if c.player_id in available), None)
        if candidate is None:
            self.pause(session_id, reason="no legal visible recommendation; inspect player pool and roster before resume")
            return None
        now = self.clock()
        action = DraftAction(uuid.uuid4().hex, session_id, obs.revision, obs.current_pick, self.config.our_team, candidate.player_id, candidate.name, created_at=now, updated_at=now, detail="fresh recommendation candidate")
        try:
            with self._transaction() as db:
                # Repeat the unresolved check inside the write transaction;
                # the partial unique index protects independent processes.
                existing = db.execute("SELECT payload FROM actions WHERE session_id=? AND status IN ('pending','submitted','uncertain') ORDER BY created_at LIMIT 1", (session_id,)).fetchone()
                if existing:
                    existing_action = DraftAction(**_loads(existing["payload"]))
                    if existing_action.status == "pending":
                        return existing_action
                    raise ActionConflict(f"unresolved action is {existing_action.status}; reconcile before another action")
                if db.execute("SELECT mutation_id FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone():
                    raise ActionConflict("an unresolved queue mutation blocks direct Draft preparation")
                db.execute("INSERT INTO actions(action_id,session_id,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)", (action.action_id, session_id, action.status, _json(action.to_dict()), now, now))
                self._journal_db(db, session_id, "action_proposed", action.to_dict(), now)
        except sqlite3.IntegrityError as exc:
            raise ActionConflict("another operator reserved an action for this session") from exc
        return action

    def claim_action(self, action_id: str) -> DraftAction:
        row = self._db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown action")
        action = self._action_from_row(row)
        if action.status != "pending":
            raise ActionConflict(f"action is {action.status}")
        if action.action_type == "queue" or action.detail.startswith("manual_stage:"):
            raise ActionConflict("a staged manual pick requires user execution and reconciliation")
        # This is the first half of the two-phase operation. The bridge must
        # call final_guard again immediately before dispatching the click.
        self._preflight_action(action.session_id, expected_revision=action.revision, expected_pick=action.expected_pick, expected_team=action.expected_team, player_id=action.player_id)
        with self._transaction() as db:
            current = db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if current["status"] != "pending" or current["claim_token"]:
                raise ActionConflict("action was claimed by another operator")
            if db.execute("SELECT mutation_id FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (action.session_id,)).fetchone():
                raise ActionConflict("an unresolved queue mutation blocks direct Draft claim")
            token = secrets.token_urlsafe(18)
            db.execute("UPDATE actions SET claim_token=?,updated_at=? WHERE action_id=? AND status='pending' AND claim_token IS NULL", (token, self.clock(), action_id))
            changed = db.execute("SELECT claim_token FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if not changed or changed["claim_token"] != token:
                raise ActionConflict("action was claimed by another operator")
            self._journal_db(db, action.session_id, "action_claimed", {"action_id": action_id, "revision": action.revision})
        return action

    def manual_pick(self, session_id: str, player_id: str) -> DraftAction:
        row = self._session_row(session_id)
        if row["state"] != "paused" or not row["manual_override"]:
            raise ActionConflict("manual picks require an explicit paused manual override")
        if self._active_queue_mutation(session_id):
            raise ActionConflict("an unresolved queue mutation blocks manual staging")
        obs = self._require_fresh(self._latest_observation(session_id))
        if obs.on_clock_team != self.config.our_team or obs.current_pick is None:
            raise ActionConflict("manual pick is not currently ours")
        player = next((p for p in obs.available_players if p.player_id == player_id), None)
        if player is None:
            raise ActionConflict("manual player is not in the fresh available pool")
        if self._active_unresolved(session_id):
            raise ActionConflict("an unresolved action is already reserved")
        now = self.clock()
        action = DraftAction(uuid.uuid4().hex, session_id, obs.revision, obs.current_pick, self.config.our_team, player.player_id, player.name, action_type="draft", created_at=now, updated_at=now, detail="manual_stage: user-requested pick staged; automation remains paused")
        with self._transaction() as db:
            if db.execute("SELECT mutation_id FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (session_id,)).fetchone():
                raise ActionConflict("an unresolved queue mutation blocks manual staging")
            db.execute("INSERT INTO actions(action_id,session_id,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)", (action.action_id, session_id, action.status, _json(action.to_dict()), now, now))
            db.execute("UPDATE sessions SET state='paused',reason='manual pick requested while paused',updated_at=? WHERE session_id=?", (now, session_id))
            self._journal_db(db, session_id, "manual_pick", action.to_dict(), now)
        return action

    def final_guard(self, action_id: str) -> dict[str, Any]:
        """Reread control intent and live state directly before a browser click."""
        row = self._db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown action")
        action = self._action_from_row(row)
        session = self._session_row(action.session_id)
        self._pause_if_stale(action.session_id)
        session = self._session_row(action.session_id)
        intent_row = self._db.execute("SELECT payload,version FROM intents WHERE session_id=?", (action.session_id,)).fetchone()
        intent = _loads(intent_row["payload"]) if intent_row else {}
        allowed = (session["state"] == "running") or (session["state"] == "paused" and session["manual_override"] and action.action_type == "draft")
        if not allowed or action.status != "pending" or action.action_type == "queue" or action.detail.startswith("manual_stage:"):
            raise ActionConflict("control intent no longer permits this browser command")
        if intent.get("kind") == "pause" and session["state"] == "running":
            raise ActionConflict("pause intent supersedes an in-flight action")
        if session["state"] == "running":
            self._preflight_action(action.session_id, expected_revision=action.revision, expected_pick=action.expected_pick, expected_team=action.expected_team, player_id=action.player_id)
        else:
            raise ActionConflict("only running automation may dispatch a browser command")
        with self._transaction() as db:
            live_session = db.execute("SELECT state,control_version FROM sessions WHERE session_id=?", (action.session_id,)).fetchone()
            latest_revision = db.execute("SELECT latest_revision FROM sessions WHERE session_id=?", (action.session_id,)).fetchone()
            if not live_session or live_session["state"] != "running" or live_session["control_version"] != session["control_version"] or not latest_revision or latest_revision["latest_revision"] != action.revision:
                raise ActionConflict("control intent changed before browser dispatch")
            current = db.execute("SELECT claim_token,dispatched,status FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if not current or current["status"] != "pending" or not current["claim_token"]:
                raise ActionConflict("action is no longer claimable")
            if current["dispatched"]:
                raise ActionConflict("action has already been dispatched")
            if db.execute("SELECT mutation_id FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') LIMIT 1", (action.session_id,)).fetchone():
                raise ActionConflict("an unresolved queue mutation blocks direct Draft dispatch")
            db.execute("UPDATE actions SET dispatched=1,updated_at=? WHERE action_id=? AND dispatched=0 AND status='pending'", (self.clock(), action_id))
        return {"allowed": True, "action_id": action_id, "control_version": session["control_version"], "intent": intent, "session_id": action.session_id, "revision": action.revision, "player_id": action.player_id, "player_name": action.player_name}

    def _manual_guard(self, action: DraftAction) -> None:
        obs = self._require_fresh(self._latest_observation(action.session_id))
        if obs.revision != action.revision or obs.current_pick != action.expected_pick or obs.on_clock_team != self.config.our_team:
            raise IdentityError("manual pick preflight changed")
        if not any(p.player_id == action.player_id for p in obs.available_players):
            raise IdentityError("manual player is no longer available")

    def ack_action(self, action_id: str, status: str, *, detail: str = "", observation: DraftObservation | Mapping[str, Any] | None = None) -> DraftAction:
        if status not in {"submitted", "uncertain", "confirmed", "failed", "cancelled"}:
            raise CoordinatorError("ack status must be submitted, uncertain, confirmed, failed, or cancelled")
        row = self._db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown action")
        action = self._action_from_row(row)
        if action.status == "confirmed" or (action.status == status and status != "confirmed"):
            return action
        if status == "cancelled" and (action.status != "pending" or row["dispatched"]):
            raise ActionConflict("only an undispatched pending draft action can be cancelled")
        if action.status == "cancelled" and status != "cancelled":
            raise ActionConflict("cancelled action cannot be acknowledged")
        if status == "confirmed" and not row["dispatched"]:
            if action.detail.startswith("manual_stage:"):
                manual_session = self._session_row(action.session_id)
                if manual_session["state"] != "paused" or not manual_session["manual_override"]:
                    raise ActionConflict("manual confirmation requires the paused manual override")
            else:
                raise ActionConflict("direct Draft confirmation requires a dispatched browser command")
        obs = None
        if observation is not None:
            obs = observation if isinstance(observation, DraftObservation) else DraftObservation.from_dict(observation)
            if obs.session_id != action.session_id:
                raise IdentityError("action observation session does not match the action")
            self.ingest_or_match_observation(obs, session_id=action.session_id)
        if status == "confirmed":
            obs = self._latest_observation(action.session_id)
            if not self._authoritatively_confirmed(action, obs):
                # Confirmation must come from the board history and our roster.
                if action.status != "uncertain":
                    self._set_action_status(action, "uncertain", detail=detail or "browser ack lacked authoritative history and roster")
                raise ActionConflict("cannot confirm without authoritative history and roster")
        return self._set_action_status(action, status, detail=detail)

    def _authoritatively_confirmed(self, action: DraftAction, obs: DraftObservation | None) -> bool:
        if obs is None or obs.session_id != action.session_id or action.action_type != "draft":
            return False
        try:
            self._require_fresh(obs)
            session = self._session_row(action.session_id)
            if obs.room != self._room_from_row(session) or obs.complete is not True or obs.history_complete is not True or obs.autopick is not False:
                return False
        except (CoordinatorError, FreshnessError, IdentityError, ValueError):
            return False
        history = any(p.pick_number == action.expected_pick and p.team == action.expected_team and p.player_id == action.player_id for p in obs.history)
        roster = any(p.player_id == action.player_id for p in obs.roster)
        return history and roster

    def _set_action_status(self, action: DraftAction, status: str, *, detail: str = "") -> DraftAction:
        now = self.clock()
        action.status = status
        action.updated_at = now
        if detail:
            action.detail = detail
        with self._transaction() as db:
            db.execute("UPDATE actions SET status=?,payload=?,updated_at=? WHERE action_id=?", (status, _json(action.to_dict()), now, action.action_id))
            self._journal_db(db, action.session_id, f"action_{status}", {"action_id": action.action_id, "detail": action.detail}, now)
        return action

    def reconcile(self, session_id: str) -> list[DraftAction]:
        obs = self._require_fresh(self._latest_observation(session_id))
        rows = self._db.execute("SELECT * FROM actions WHERE session_id=? AND status IN ('pending','submitted','uncertain') ORDER BY created_at", (session_id,)).fetchall()
        resolved: list[DraftAction] = []
        for row in rows:
            action = self._action_from_row(row)
            if self._authoritatively_confirmed(action, obs):
                has_direct_provenance = bool(row["dispatched"])
                if not has_direct_provenance:
                    resolved.append(self._set_action_status(action, "cancelled", detail="matching history/roster event preceded the guarded dispatch; treated as an external selection"))
                else:
                    resolved.append(self._set_action_status(action, "confirmed", detail="confirmed by authoritative board history and roster after guarded dispatch"))
        return resolved

    def confirm_manual_pick(self, action_id: str, observation: DraftObservation | Mapping[str, Any] | None = None) -> DraftAction:
        row = self._db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise CoordinatorError("unknown action")
        action = self._action_from_row(row)
        if action.action_type != "queue" and not action.detail.startswith("manual_stage:"):
            raise ActionConflict("only a staged manual pick can be confirmed this way")
        session = self._session_row(action.session_id)
        if session["state"] != "paused" or not session["manual_override"]:
            raise ActionConflict("manual confirmation requires the paused manual override")
        return self.ack_action(action_id, "confirmed", observation=observation)

    def pick_timer_seconds(self, session_id: str) -> float | None:
        """The native pick timer read back at this session's first Start, if any."""
        row = self._db.execute(
            "SELECT payload FROM journal WHERE session_id=? AND event='pick_timer_recorded' ORDER BY seq LIMIT 1",
            (session_id,),
        ).fetchone()
        return float(_loads(row["payload"], {})["seconds"]) if row else None

    def record_pick_timer(self, session_id: str, seconds: float) -> float:
        """Record the draft's pick timer once; the first recorded value is kept."""
        with self._transaction() as db:
            existing = db.execute(
                "SELECT payload FROM journal WHERE session_id=? AND event='pick_timer_recorded' LIMIT 1",
                (session_id,),
            ).fetchone()
            if existing:
                return float(_loads(existing["payload"], {})["seconds"])
            self._journal_db(db, session_id, "pick_timer_recorded", {"seconds": float(seconds)})
        return float(seconds)

    def journal(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT seq,event,payload,created_at FROM journal WHERE session_id=? ORDER BY seq DESC LIMIT ?", (session_id, limit)).fetchall()
        return [{"seq": r["seq"], "event": r["event"], "payload": _loads(r["payload"], {}), "created_at": r["created_at"]} for r in rows]

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        if session_id is None:
            rows = self._db.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC").fetchall()
            return {"sessions": [self.status(r["session_id"]) for r in rows]}
        self._pause_if_stale(session_id)
        row = self._session_row(session_id)
        obs = self._latest_observation(session_id)
        attestation = self._practice_attestation(session_id)
        actions = self._db.execute("SELECT payload,dispatched,claim_token FROM actions WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
        action_payloads = []
        for action in actions:
            payload = _loads(action["payload"])
            # Keep the SQL provenance outside DraftAction so legacy action
            # payloads remain constructible while the browser checkpoint can
            # distinguish a dispatched click from an external/native pick.
            payload["dispatched"] = bool(action["dispatched"])
            payload["claimed"] = bool(action["claim_token"])
            action_payloads.append(payload)
        intent = self._db.execute("SELECT payload,version FROM intents WHERE session_id=?", (session_id,)).fetchone()
        return {
            "session_id": session_id,
            "state": row["state"],
            "reason": row["reason"],
            "manual_override": bool(row["manual_override"]),
            "control_version": row["control_version"],
            "room": _loads(row["room_json"]),
            "practice_attestation": (
                {
                    "room": _loads(attestation["room_json"]),
                    "source_league_id": attestation["source_league_id"],
                    "heading": attestation["heading"],
                    "created_at": attestation["created_at"],
                }
                if attestation
                else None
            ),
            "latest_revision": row["latest_revision"],
            "pick_timer_seconds": self.pick_timer_seconds(session_id),
            "owner": self.ownership.describe(session_id),
            "observation": obs.to_dict() if obs else None,
            "observation_age_seconds": timestamp_age(obs.observed_at) if obs else None,
            "projection_freshness": self.projection_freshness(),
            "actions": action_payloads,
            "unresolved_actions": [payload for payload in action_payloads if payload.get("status") in {"pending", "submitted", "uncertain"}],
            "queue_mutations": [self._queue_mutation_from_row(q) for q in self._db.execute("SELECT * FROM queue_mutations WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()],
            "unresolved_queue_mutations": [self._queue_mutation_from_row(q) for q in self._db.execute("SELECT * FROM queue_mutations WHERE session_id=? AND status IN ('pending','submitted','uncertain') ORDER BY created_at", (session_id,)).fetchall()],
            "recommendation": self._latest_recommendation(session_id),
            "intent": _loads(intent["payload"]) if intent else None,
            "journal": self.journal(session_id, limit=20),
            "readiness": self.readiness(session_id),
            "publish_gaps": self.gap_telemetry(session_id),
        }

    def _latest_recommendation(self, session_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT payload FROM recommendations WHERE session_id=? ORDER BY revision DESC LIMIT 1", (session_id,)).fetchone()
        return _loads(row["payload"]) if row else None

    # Friendly names used by the browser adapter and local CLI integrations.
    publish_observation = ingest_observation
    connect_url = connect
    ready = readiness

    def control(self, session_id: str, command: str, *, player_id: str | None = None, action_id: str | None = None, reason: str = "") -> Any:
        command = command.lower().replace("_", "-")
        if command == "start":
            return self.start(session_id)
        if command == "resume":
            return self.resume(session_id)
        if command == "pause":
            return self.pause(session_id, reason=reason or "paused by user")
        if command in {"manual-override", "override"}:
            return self.set_manual_override(session_id, True)
        if command in {"manual-pick", "pick"}:
            if not player_id:
                raise CoordinatorError("manual-pick requires player_id")
            return self.manual_pick(session_id, player_id)
        if command in {"confirm-manual-pick", "confirm-pick"}:
            target = action_id or player_id
            if not target:
                raise CoordinatorError("confirm-manual-pick requires action_id")
            return self.confirm_manual_pick(target)
        raise CoordinatorError(f"unknown control command {command}")

