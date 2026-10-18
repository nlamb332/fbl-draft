"""Reusable test doubles: a fake fetcher and a fixture-backed SleeperClient."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..api import API_ROOT, PROJECTIONS_ROOT, SleeperClient

FIXTURES = Path(__file__).parent / "fixtures"

DRAFT_ID = "900000000000000001"
LEAGUE_ID = "800000000000000001"
USERNAME = "user_a"
USER_ID = "700000000000000003"
SCENARIOS = ("pre", "drafting", "mid", "traded")


def load_fixture(name: str) -> Any:
    """Load ``fixtures/<name>`` (``.json`` appended if missing) as UTF-8 JSON."""
    if not name.endswith(".json"):
        name += ".json"
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fake_fetch(routes: dict) -> Callable[[str], Any]:
    """Build a ``fetch_json`` from ``{url: value}``.

    Keys match the URL exactly, otherwise as a prefix (longest wins). A value
    may be data, a callable ``f(url)``, or an Exception instance (raised).
    Unknown URLs raise ``KeyError``. The returned function has a ``calls``
    list recording every requested URL.
    """
    calls: list[str] = []

    def fetch(url: str) -> Any:
        calls.append(url)
        if url in routes:
            key = url
        else:
            matches = [k for k in routes if url.startswith(k)]
            if not matches:
                raise KeyError(f"no fake route for {url}")
            key = max(matches, key=len)
        value = routes[key]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(url)
        return json.loads(json.dumps(value))  # fresh copy per call

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def fixture_routes(scenario: str = "mid") -> dict:
    """Routes for a scenario: pre | drafting | mid (picks, no trades) | traded."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
    draft = load_fixture("draft_pre" if scenario == "pre" else "draft_drafting")
    picks = load_fixture("picks_mid") if scenario in ("mid", "traded") else []
    traded = load_fixture("traded_picks") if scenario == "traded" else []
    return {
        f"{API_ROOT}/draft/{DRAFT_ID}/picks": picks,
        f"{API_ROOT}/draft/{DRAFT_ID}/traded_picks": traded,
        f"{API_ROOT}/draft/{DRAFT_ID}": draft,
        f"{API_ROOT}/league/{LEAGUE_ID}": load_fixture("league"),
        f"{API_ROOT}/user/{USERNAME}": load_fixture("user"),
        f"{API_ROOT}/players/nba": load_fixture("players"),
        f"{PROJECTIONS_ROOT}/2026": load_fixture("projections"),
    }


def fixture_client(tmp_path, scenario: str = "mid", now: Callable[[], float] | None = None) -> SleeperClient:
    """SleeperClient wired to fixtures; caches under ``tmp_path/sleeper``.

    The injected fetcher is available as ``client.fetch_json`` (``.calls``).
    """
    kwargs = {} if now is None else {"now": now}
    return SleeperClient(
        fetch_json=fake_fetch(fixture_routes(scenario)),
        cache_dir=Path(tmp_path) / "sleeper",
        **kwargs,
    )
