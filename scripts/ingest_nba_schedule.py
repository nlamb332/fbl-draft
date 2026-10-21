"""Fetch the 2026-27 NBA regular-season schedule into a checked-in data file.

Source: ESPN public team-schedule endpoint (one call per team).  Game dates use
the US Eastern calendar day.  Output is deterministic apart from ``retrieved_at``.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parents[1] / "draft_assistant" / "data" / "nba_schedule_2026-27.json"
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
EASTERN = ZoneInfo("America/New_York")


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    teams = sorted(
        item["team"]["abbreviation"].upper()
        for item in _get(f"{BASE}/teams?limit=40")["sports"][0]["leagues"][0]["teams"]
    )
    games: dict[str, dict] = {}
    for team in teams:
        for event in _get(f"{BASE}/teams/{team.lower()}/schedule?season=2027")["events"]:
            if event["seasonType"]["type"] != 2:
                continue
            day = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(EASTERN).date().isoformat()
            sides = {c["homeAway"]: c["team"]["abbreviation"].upper() for c in event["competitions"][0]["competitors"]}
            games[event["id"]] = {"date": day, "home": sides["home"], "away": sides["away"]}
    ordered = sorted(games.values(), key=lambda g: (g["date"], g["home"], g["away"]))
    payload = {
        "season": "2026-27",
        "source": f"ESPN public API {BASE}/teams/{{team}}/schedule?season=2027 (regular season, type 2)",
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_basis": "US Eastern calendar day of tip-off",
        "teams": teams,
        "games": [[g["date"], g["home"], g["away"]] for g in ordered],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(ordered)} games, {len(teams)} teams -> {args.out}")


if __name__ == "__main__":
    main()
