"""Read-only Sleeper API client with injectable fetcher and file caches."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_ROOT = "https://api.sleeper.app/v1"
PROJECTIONS_ROOT = "https://api.sleeper.app/projections/nba"
USER_AGENT = "sleeper-advisor/1.0 (+local draft helper)"
TIMEOUT_SECONDS = 15

PLAYERS_TTL = 24 * 3600
PROJECTIONS_TTL = 6 * 3600

POSITIONS = ("PG", "SG", "SF", "PF", "C")

_DRAFT_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?sleeper\.(?:com|app)/draft/(?:[A-Za-z]+/)?(\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)

FetchJson = Callable[[str], Any]


def parse_draft_id(url_or_id: str) -> str:
    """Return the draft id from a Sleeper draft URL or a bare numeric id."""
    text = str(url_or_id or "").strip()
    if text.isdigit():
        return text
    match = _DRAFT_URL_RE.match(text)
    if match:
        return match.group(1)
    raise ValueError(f"Not a Sleeper draft URL or numeric draft id: {url_or_id!r}")


def default_fetch_json(url: str) -> Any:
    """GET ``url`` and decode the body as UTF-8 JSON."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
    return json.loads(body.decode("utf-8"))


def projections_url(season: str | int) -> str:
    positions = "&".join(f"position[]={p}" for p in POSITIONS)
    return f"{PROJECTIONS_ROOT}/{season}?season_type=regular&{positions}"


class SleeperClient:
    def __init__(
        self,
        fetch_json: FetchJson | None = None,
        cache_dir: str | os.PathLike = Path("runtime/sleeper"),
        now: Callable[[], float] = time.time,
    ) -> None:
        self.fetch_json: FetchJson = fetch_json or default_fetch_json
        self.cache_dir = Path(cache_dir)
        self.now = now

    # -- live endpoints -------------------------------------------------
    def draft(self, draft_id: str) -> dict:
        return self.fetch_json(f"{API_ROOT}/draft/{draft_id}")

    def picks(self, draft_id: str) -> list:
        return self.fetch_json(f"{API_ROOT}/draft/{draft_id}/picks") or []

    def traded_picks(self, draft_id: str) -> list:
        return self.fetch_json(f"{API_ROOT}/draft/{draft_id}/traded_picks") or []

    def league(self, league_id: str) -> dict:
        return self.fetch_json(f"{API_ROOT}/league/{league_id}")

    def user(self, username: str) -> dict:
        return self.fetch_json(f"{API_ROOT}/user/{urllib.parse.quote(str(username), safe='')}")

    # -- cached endpoints -----------------------------------------------
    def players(self) -> dict:
        return self._cached(
            "players_nba.json", PLAYERS_TTL, lambda: self.fetch_json(f"{API_ROOT}/players/nba")
        ) or {}

    def projections(self, season: str | int) -> list:
        return self._cached(
            f"projections_{season}.json",
            PROJECTIONS_TTL,
            lambda: self.fetch_json(projections_url(season)),
        ) or []

    def _cached(self, name: str, ttl: float, loader: Callable[[], Any]) -> Any:
        path = self.cache_dir / name
        cached = self._read_cache(path)
        now = self.now()
        if cached is not None:
            age = now - cached["fetched_at"]
            if 0 <= age < ttl:
                return cached["data"]
        try:
            data = loader()
        except Exception:
            if cached is not None:  # stale beats nothing
                return cached["data"]
            raise
        self._write_cache(path, {"fetched_at": now, "data": data})
        return data

    @staticmethod
    def _read_cache(path: Path) -> dict | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("fetched_at"), (int, float)) and "data" in payload:
                return payload
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def _write_cache(path: Path, payload: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # caching is best-effort
