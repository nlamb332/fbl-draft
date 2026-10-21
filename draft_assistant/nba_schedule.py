"""Real NBA game days for the marginal-lineup model (flag-gated, issue #8).

The checked-in schedule file is produced by ``scripts/ingest_nba_schedule.py``.
Representative weeks are full Monday-Sunday weeks spread evenly across the
season; weeks with almost no games (the All-Star break) are skipped.  Everything
here is a pure function of the schedule file, so outputs are deterministic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

DEFAULT_SCHEDULE_PATH = Path(__file__).resolve().parent / "data" / "nba_schedule_2026-27.json"
MIN_AVG_GAMES = 2.0  # weeks below this league-wide team average are not representative
_ALIASES = {
    "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH", "PHO": "PHX",
    "GSW": "GS", "NOP": "NO", "NOR": "NO", "BRK": "BKN", "CHO": "CHA",
}


def normalize_team(raw: str | None) -> str:
    code = str(raw or "").strip().upper()
    return _ALIASES.get(code, code)


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_schedule(path: Path | str | None = None) -> dict[str, tuple[date, ...]] | None:
    """Team -> sorted game dates, or ``None`` if the file is missing or invalid."""

    try:
        payload = _load(str(path or DEFAULT_SCHEDULE_PATH))
        games: dict[str, list[date]] = {}
        for day, home, away in payload["games"]:
            parsed = date.fromisoformat(day)
            for team in (home, away):
                games.setdefault(normalize_team(team), []).append(parsed)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {team: tuple(sorted(days)) for team, days in games.items()} if games else None


def representative_weeks(schedule: dict[str, tuple[date, ...]], weeks: int) -> tuple[date, ...]:
    """Monday starts of ``weeks`` evenly spaced, full, non-break weeks."""

    first = min(days[0] for days in schedule.values())
    last = max(days[-1] for days in schedule.values())
    start = first - timedelta(days=first.weekday())
    if start < first:
        start += timedelta(days=7)  # drop the partial opening week
    candidates: list[date] = []
    while start + timedelta(days=6) <= last:
        end = start + timedelta(days=7)
        total = sum(1 for days in schedule.values() for d in days if start <= d < end)
        if total / len(schedule) >= MIN_AVG_GAMES:
            candidates.append(start)
        start = end
    if not candidates:
        return ()
    count = len(candidates)
    return tuple(candidates[(index * count) // weeks] for index in range(weeks))


def real_team_games(
    team: str | None, weeks: int, path: Path | str | None = None
) -> tuple[bool, ...] | None:
    """Day flags for ``weeks`` representative weeks, or ``None`` to fall back."""

    schedule = load_schedule(path)
    key = normalize_team(team)
    if schedule is None or key not in schedule:
        return None
    starts = representative_weeks(schedule, weeks)
    if not starts:
        return None
    played = set(schedule[key])
    return tuple((start + timedelta(days=offset)) in played for start in starts for offset in range(7))
