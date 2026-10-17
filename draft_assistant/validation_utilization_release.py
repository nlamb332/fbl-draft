"""Build the bounded schedule-aware utilization release artifact.

The inputs are checked-in captures: the October 10 ESPN rendered projection
catalog and the NBA's August 13, 2026 schedule-by-date PDF.  This script does
not fetch data or alter the live draft scorer.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utilization_review import (
    analyze_roster,
    build_schedule_matrix,
    rank_advisory_candidates,
    repeat_schedule_pattern,
)


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "mock_draft" / "data" / "players_2026-27_espn_projection.csv"
OUT = Path(__file__).resolve().parent / "validation" / "draft-utilization-release"
ROSTER_CAPTURED_AT = "2026-10-11T15:16:04.271Z"
SCHEDULE_CAPTURED_AT = "2026-10-11"
DATES = tuple(f"2026-10-{day:02d}" for day in range(20, 27))


# Transcribed from the NBA schedule-by-date PDF (as of August 13, 2026).
# This is an opening-week date window for bounded review, not a whole-season
# forecast.  Team aliases are normalized by utilization_review.py.
GAMES = (
    ("2026-10-20", "BOS", "DET"), ("2026-10-20", "PHI", "NY"), ("2026-10-20", "OKC", "SA"),
    ("2026-10-21", "ATL", "ORL"), ("2026-10-21", "MIL", "WSH"), ("2026-10-21", "CHA", "BKN"),
    ("2026-10-21", "MIN", "MIA"), ("2026-10-21", "CHI", "TOR"), ("2026-10-21", "UTAH", "MEM"),
    ("2026-10-21", "IND", "NO"), ("2026-10-21", "DAL", "HOU"), ("2026-10-21", "GS", "LAL"),
    ("2026-10-21", "PHX", "POR"), ("2026-10-21", "SAC", "LAC"),
    ("2026-10-22", "CLE", "PHI"), ("2026-10-22", "DEN", "OKC"),
    ("2026-10-23", "NY", "BOS"), ("2026-10-23", "ATL", "CHA"), ("2026-10-23", "DAL", "IND"),
    ("2026-10-23", "MIN", "ORL"), ("2026-10-23", "TOR", "WSH"), ("2026-10-23", "DET", "MIA"),
    ("2026-10-23", "CHI", "MIL"), ("2026-10-23", "HOU", "SA"), ("2026-10-23", "NO", "UTAH"),
    ("2026-10-23", "MEM", "GS"), ("2026-10-23", "LAC", "LAL"), ("2026-10-23", "POR", "SAC"),
    ("2026-10-24", "MIA", "CHA"), ("2026-10-24", "BKN", "CLE"), ("2026-10-24", "MIL", "PHI"),
    ("2026-10-24", "HOU", "ATL"), ("2026-10-24", "WSH", "CHI"), ("2026-10-24", "SA", "DAL"),
    ("2026-10-24", "NO", "DEN"), ("2026-10-24", "GS", "PHX"),
    ("2026-10-25", "LAL", "UTAH"), ("2026-10-25", "IND", "BKN"), ("2026-10-25", "ORL", "NY"),
    ("2026-10-25", "TOR", "MIN"), ("2026-10-25", "LAC", "OKC"), ("2026-10-25", "DET", "PHI"),
    ("2026-10-25", "MEM", "SAC"),
    ("2026-10-26", "WSH", "CHA"), ("2026-10-26", "MIN", "CLE"), ("2026-10-26", "CHI", "BOS"),
    ("2026-10-26", "DAL", "MIA"), ("2026-10-26", "IND", "MIL"), ("2026-10-26", "PHX", "OKC"),
    ("2026-10-26", "ATL", "HOU"), ("2026-10-26", "MEM", "UTAH"), ("2026-10-26", "GS", "DEN"),
)

ROSTER_NAMES = (
    "Karl-Anthony Towns", "Jalen Duren", "Jarrett Allen", "Julius Randle",
    "Walker Kessler", "Kel'el Ware", "Nikola Vucevic", "John Collins",
    "Kyshawn George", "Jrue Holiday",
)


def _known_value(stats: dict[str, Any]) -> float:
    weights = {
        "PTS": 1, "FGM": 2, "FGA": -1, "FTM": 1, "FTA": -1,
        "3PM": 1, "REB": 2, "AST": 2, "STL": 4, "BLK": 4,
        "TO": -2,
    }
    total = 0.0
    for key, weight in weights.items():
        if key not in stats or stats[key] is None:
            raise ValueError(f"catalog row is missing required known component {key}")
        number = float(stats[key])
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"catalog row has non-finite component {key}")
        total += number * weight
    return total


def _row(raw: dict[str, str]) -> dict[str, Any]:
    stats = json.loads(raw["stats"])
    missing = [key for key in ("EJ", "PF", "TF", "DD", "TD", "QD") if not raw.get(key)]
    status = raw.get("injury_status") or None
    return {
        "player_id": raw["player_id"], "name": raw["name"], "team": raw["team"],
        "eligibility": tuple(raw["eligibility"].split("|")), "stats": stats,
        "value": _known_value(stats), "missing_stats": missing,
        "injury_status": status, "health_status_unknown": True,
        "projected_gp": float(raw["gp_projected"]) if raw.get("gp_projected") else None,
        "projected_availability": float(raw["projected_availability"]),
        "forecast_source": raw["source_label"], "market_rank": int(raw["market_rank"]),
    }


def _load_catalog() -> list[dict[str, Any]]:
    with CATALOG.open(newline="", encoding="utf-8") as handle:
        return [_row(raw) for raw in csv.DictReader(handle)]


def _team_games() -> dict[str, list[str]]:
    games: dict[str, list[str]] = {}
    for day, home, away in GAMES:
        games.setdefault(home, []).append(day)
        games.setdefault(away, []).append(day)
    return games


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def build() -> dict[str, Any]:
    catalog = _load_catalog()
    by_name = {item["name"]: item for item in catalog}
    roster = [dict(by_name[name]) for name in ROSTER_NAMES]
    # These are the statuses visible in the October 10 ESPN capture.  They
    # are evidence at capture time only; the review intentionally lacks a
    # draft-date health probability matrix.
    capture_status = {item["name"]: item.get("injury_status") for item in roster if item.get("injury_status")}
    team_games = _team_games()
    schedule = build_schedule_matrix(
        roster, dates=DATES, team_games=team_games,
        source="NBA 2026-27 Regular Season Schedule By Date PDF (as of 2026-08-13)",
        captured_at=SCHEDULE_CAPTURED_AT,
    )
    values = {item["player_id"]: item["value"] for item in roster}
    week_report = analyze_roster(roster, schedule=schedule.available, values=values)
    repeated = repeat_schedule_pattern(schedule, 4)
    repeated_report = analyze_roster(roster, schedule=repeated.available, values=values)

    roster_ids = {item["player_id"] for item in roster}
    candidates = [
        item for item in catalog
        if item["player_id"] not in roster_ids
        and ("G" in item["eligibility"] or "F" in item["eligibility"])
        and 80 <= item["market_rank"] <= 180
    ]
    candidate_schedule = build_schedule_matrix(
        [*roster, *candidates], dates=DATES, team_games=team_games,
        source=schedule.source, captured_at=SCHEDULE_CAPTURED_AT,
    )
    candidate_values = {item["player_id"]: item["value"] for item in [*roster, *candidates]}
    ranked = rank_advisory_candidates(
        candidates, roster, schedule=candidate_schedule.available,
        values=candidate_values, drop_player_id="kel-el-ware",
        production_weight=1.0, marginal_weight=1.0, balance_weight=2.0,
    )

    excerpt_path = OUT / "nba_schedule_excerpt.txt"
    excerpt_hash = hashlib.sha256(excerpt_path.read_bytes()).hexdigest() if excerpt_path.exists() else None
    schedule_capture = {
        "source": "https://cdn.nba.com/manage/2026/08/2026-27-NBA-Regular-Season-Schedule-By-Date.pdf",
        "source_index": "https://www.nba.com/news/2026-27-schedule-team-by-team-index",
        "released_as_of": "2026-08-13",
        "captured_at": SCHEDULE_CAPTURED_AT,
        "subject_to_change": True,
        "evidence": {"excerpt_file": "nba_schedule_excerpt.txt", "pdf_pages": "0-1", "pdf_line_ranges": ["p0 L0-L50", "p1 L53-L108"], "content_sha256": excerpt_hash},
        "window": {"start": DATES[0], "end": DATES[-1], "days": list(DATES)},
        "games": [list(game) for game in GAMES],
        "team_games": team_games,
    }
    top = [item.to_dict() for item in ranked[:12]]
    for item in top:
        item["schedule_source"] = schedule.source
        item["health_status"] = "unknown at draft date; schedule-only matrix"
    return {
        "artifact_version": "draft-utilization-release-v1",
        "status": "advisory_only",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "league": {"teams": 14, "opponents": 13, "seats": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4},
        "sources": {
            "schedule": schedule_capture,
            "forecast": {"source": "mock_draft/data/players_2026-27_espn_projection.csv", "captured_at": "2026-10-10", "source_label": "ESPN rendered projections UI snapshot 2026-10-10", "limitations": ["market rank is rendered row order proxy, not measured ADP", "PF/TF/EJ/DD/TD/QD forecasts are missing", "availability is a rough implied-GP proxy and is not used as health in this review"]},
            "health": {"capture_statuses": capture_status, "capture_status_note": "DTD is a capture-time label only", "draft_date_status": "unknown; no dated health probability matrix supplied"},
        },
        "actual_roster": {"players": [item["name"] for item in roster], "eligibility_counts": dict(week_report.eligibility_counts), "center_only_count": week_report.center_only_count, "roster_capture_observed_at": ROSTER_CAPTURED_AT, "opening_week": week_report.to_dict(), "repeatable_fixture_4x": repeated_report.to_dict(), "schedule_warnings": list(schedule.warnings)},
        "candidate_comparison": {"drop_player_id": "kel-el-ware", "candidate_filter": "same captured ESPN catalog; G/F eligible; market proxy ranks 80..180", "top_candidates": top, "candidate_count": len(candidates), "scoring_note": "known custom components only; missing fields remain warnings"},
        "policy_evaluation": {"policy": "schedule-aware marginal usable value + soft balance adjustment", "mode": "advisory_only", "teams": 14, "opponents": 13, "seats": 14, "development_seed_clusters": 0, "heldout_seed_clusters": 0, "promotion_gate_pass": False, "gate_reasons": ["bounded review is not a 100-seed development/100-seed held-out policy validation", "player health at draft date is unknown", "the schedule window is one captured opening week; repeated fixture is synthetic", "ESPN custom scoring inputs omit PF/TF/EJ/DD/TD/QD"], "foundation_decision": "retain frozen foundation scorer and lookahead benchmark; use this policy only as an advisory review column"},
        "frozen_artifacts": {"changed": False, "paths": ["draft_assistant/valuation.py", "draft_assistant/validation/heldout-v2", "mock_draft/results/synthetic", "mock_draft/results/real_verified"]},
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = build()
    (OUT / "analysis.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    capture = payload["sources"]["schedule"]
    (OUT / "schedule_capture.json").write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")
    rows = payload["candidate_comparison"]["top_candidates"]
    fields = ["player_id", "name", "production_value", "marginal_usable_value", "marginal_active_starts", "balance_delta", "score", "warnings"]
    with (OUT / "candidate_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row[field]) if field == "warnings" else row[field] for field in fields})
    print(json.dumps({"output": str(OUT), "opening_week_active_start_rate": payload["actual_roster"]["opening_week"]["active_starter_rate"], "repeatable_fixture_active_start_rate": payload["actual_roster"]["repeatable_fixture_4x"]["active_starter_rate"], "candidates": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
