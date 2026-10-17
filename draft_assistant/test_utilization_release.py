"""Release-artifact contract checks for the bounded utilization review."""

from __future__ import annotations

import json
from pathlib import Path


ARTIFACT = Path(__file__).parent / "validation" / "draft-utilization-release"


def test_release_artifact_keeps_policy_advisory_and_promotion_gate_closed() -> None:
    payload = json.loads((ARTIFACT / "analysis.json").read_text(encoding="utf-8"))

    assert payload["artifact_version"] == "draft-utilization-release-v1"
    policy = payload["policy_evaluation"]
    assert policy["mode"] == "advisory_only"
    assert policy["teams"] == 14
    assert policy["opponents"] == 13
    assert policy["seats"] == 14
    assert policy["promotion_gate_pass"] is False
    assert policy["development_seed_clusters"] < 100
    assert policy["heldout_seed_clusters"] < 100


def test_schedule_capture_has_no_duplicate_team_day_and_is_subject_to_change() -> None:
    payload = json.loads((ARTIFACT / "schedule_capture.json").read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    for day, team_one, team_two in payload["games"]:
        assert (day, team_one) not in seen
        assert (day, team_two) not in seen
        seen.add((day, team_one))
        seen.add((day, team_two))
    assert payload["subject_to_change"] is True
    assert payload["window"]["days"] == [f"2026-10-{day:02d}" for day in range(20, 27)]


def test_opening_week_review_reports_exact_assignment_and_unknown_health() -> None:
    payload = json.loads((ARTIFACT / "analysis.json").read_text(encoding="utf-8"))
    review = payload["actual_roster"]["opening_week"]

    assert review["legal_final_roster"] is True
    assert review["eligibility_counts"] == {"C": 7, "G": 2, "F": 3}
    assert review["center_only_count"] == 6
    assert review["days_analyzed"] == 7
    assert review["active_starter_rate"] == 25 / 42
    assert review["active_starter_rate_of_scheduled_games"] == 25 / 31
    assert review["scheduled_games_by_player"]["jrue-holiday"] == 2
    assert payload["sources"]["health"]["draft_date_status"].startswith("unknown")
