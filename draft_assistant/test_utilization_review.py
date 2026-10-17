"""Counterexamples and advisory-policy tests for utilization-review-v1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .utilization_review import (
    STARTING_SLOTS,
    allocate_day,
    analyze_roster,
    build_schedule_matrix,
    marginal_usable_value,
    rank_advisory_candidates,
    repeat_schedule_pattern,
)


def player(player_id: str, eligibility: tuple[str, ...], value: float = 10.0, **extra: object) -> dict[str, object]:
    return {"player_id": player_id, "name": player_id, "eligibility": eligibility, "value": value, **extra}


def legal_center_heavy_roster() -> list[dict[str, object]]:
    return [
        player("sab", ("C", "F"), 60), player("ga", ("G",), 20),
        player("kg", ("G", "F"), 35), player("jaren", ("C", "F"), 40),
        player("hart", ("F", "G"), 40), player("duren", ("C",), 54),
        player("allen", ("C",), 47), player("clingan", ("C",), 45),
        player("zubac", ("C",), 47), player("jabari", ("F", "C"), 34),
    ]


def test_actual_shape_is_legal_but_schedule_absence_blocks_daily_claims() -> None:
    report = analyze_roster(legal_center_heavy_roster())

    assert report.legal_final_roster is True
    assert report.eligibility_counts == {"C": 7, "G": 3, "F": 5}
    assert report.center_only_count == 4
    assert report.structural_start_capacity == 6
    assert report.schedule_available is False
    assert report.active_starter_rate is None
    assert report.mean_inactive_starters is None


def test_legal_center_heavy_roster_can_have_only_two_active_starts() -> None:
    roster = legal_center_heavy_roster()
    # Explicit stress case: only the four C-only players have games.  The
    # roster remains legal, but C plus UTIL is the maximum active assignment.
    active_center_only = {p["player_id"]: (p["player_id"] in {"duren", "allen", "clingan", "zubac"},) for p in roster}

    report = analyze_roster(roster, schedule=active_center_only)
    allocation = report.daily[0]

    assert report.legal_final_roster is True
    assert allocation.active_roster_count == 4
    assert allocation.active_starter_count == 2
    assert report.active_starter_rate == pytest.approx(2 / 6)
    assert len(allocation.inactive_starters) == 4


def test_legal_center_heavy_roster_can_have_only_three_active_starts_when_centers_off() -> None:
    roster = legal_center_heavy_roster()
    # The three non-center-only players are active; four required categories
    # still cannot be supplied by reusing one player in multiple slots.
    active_noncenters = {p["player_id"]: (p["player_id"] in {"ga", "kg", "hart"},) for p in roster}

    report = analyze_roster(roster, schedule=active_noncenters)
    allocation = report.daily[0]

    assert report.legal_final_roster is True
    assert allocation.active_roster_count == 3
    assert allocation.active_starter_count == 3
    assert report.active_starter_rate == pytest.approx(3 / 6)
    assert len(allocation.inactive_starters) == 3


def test_all_active_day_uses_exact_six_starts_and_four_bench_players() -> None:
    roster = legal_center_heavy_roster()
    schedule = {p["player_id"]: (True,) for p in roster}
    allocation = allocate_day(roster, schedule=schedule, values={p["player_id"]: p["value"] for p in roster})

    assert allocation.active_starter_count == len(STARTING_SLOTS)
    assert len(allocation.starter_ids) == 6
    assert len(allocation.bench_ids) == 4
    assert allocation.inactive_starters == ()
    assert set(allocation.starter_ids).isdisjoint(allocation.bench_ids)


def test_schedule_aware_marginal_value_penalizes_congested_center_candidate() -> None:
    roster = legal_center_heavy_roster()[:6]
    roster += [player("bench-g", ("G",), 18), player("bench-f", ("F",), 18), player("bench-c", ("C",), 18), player("bench-c2", ("C",), 18)]
    center_candidate = player("new-center", ("C",), 80)
    wing_candidate = player("new-wing", ("G", "F"), 45)
    all_active = {p["player_id"]: (True, True) for p in [*roster, center_candidate, wing_candidate]}
    values = {p["player_id"]: float(p["value"]) for p in [*roster, center_candidate, wing_candidate]}

    center_delta, center_starts = marginal_usable_value(center_candidate, roster, schedule=all_active, values=values, drop_player_id="bench-c2")
    wing_delta, wing_starts = marginal_usable_value(wing_candidate, roster, schedule=all_active, values=values, drop_player_id="bench-c2")

    assert center_starts == 0
    assert wing_starts == 0
    # The center's raw value can still improve the total score when it
    # replaces a low-value bench player, but it does not create a new start.
    assert center_delta > wing_delta


def test_advisory_policy_can_choose_lower_raw_value_wing_for_usable_depth() -> None:
    roster = [
        player("c1", ("C",), 50), player("c2", ("C",), 49),
        player("gf1", ("G", "F"), 30), player("gf2", ("G", "F"), 29),
        player("g1", ("G",), 28), player("f1", ("F",), 28),
    ]
    # Four current players are active on day 0; the candidate wing is the
    # seventh eligible player and creates another active starter.  The center
    # is higher raw value but duplicates C depth already present.
    center = player("candidate-center", ("C",), 70)
    wing = player("candidate-wing", ("G", "F"), 40)
    schedule = {p["player_id"]: ((p["player_id"] != "c2"), True) for p in roster}
    schedule["c2"] = (True, False)
    schedule["candidate-center"] = (True, False)
    schedule["candidate-wing"] = (False, True)
    values = {p["player_id"]: float(p["value"]) for p in [*roster, center, wing]}

    ranked = rank_advisory_candidates(
        [center, wing], roster, schedule=schedule, values=values,
        production_weight=0.5, marginal_weight=3.0, balance_weight=2.0,
    )

    assert ranked[0].player_id == "candidate-wing"
    assert ranked[0].marginal_active_starts > ranked[1].marginal_active_starts
    assert ranked[0].balance_delta >= ranked[1].balance_delta


def test_schedule_requires_complete_equal_length_matrix() -> None:
    roster = legal_center_heavy_roster()
    schedule = {p["player_id"]: (True,) for p in roster}
    schedule["ga"] = (True, False)

    with pytest.raises(ValueError, match="equally sized"):
        analyze_roster(roster, schedule=schedule)


def test_actual_observation_file_has_expected_roster_and_unknown_forecasts() -> None:
    path = Path(__file__).parent / "validation" / "browser-practice-2026-10-10" / "final_observation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    roster = payload["roster"]
    report = analyze_roster(roster)

    assert len(roster) == 10
    assert report.legal_final_roster is True
    assert report.eligibility_counts == {"C": 7, "G": 3, "F": 5}
    assert sum(bool(item.get("missing_stats")) for item in roster) == 10
    assert sum(item.get("projected_gp") is not None for item in roster) == 7


def test_build_schedule_matrix_uses_team_dates_and_keeps_health_unknown() -> None:
    roster = [player("g", ("G",), 10, team="AAA"), player("f", ("F",), 10, team="BBB")]
    captured = build_schedule_matrix(
        roster,
        dates=("2026-10-21", "2026-10-20", "2026-10-22"),
        team_games={"AAA": ("2026-10-20", "2026-10-22"), "BBB": ("2026-10-21",)},
        source="official schedule test capture",
        captured_at="2026-10-11T00:00:00Z",
    )

    assert captured.dates == ("2026-10-20", "2026-10-21", "2026-10-22")
    assert captured.available == {"g": (True, False, True), "f": (False, True, False)}
    assert captured.health_applied is False
    assert "health/availability" in captured.warnings[0]


def test_health_matrix_is_explicit_and_combined_with_schedule() -> None:
    roster = [player("g", ("G",), 10, team="AAA")]
    captured = build_schedule_matrix(
        roster,
        dates=("2026-10-20", "2026-10-21"),
        team_games={"AAA": ("2026-10-20", "2026-10-21")},
        health={"g": (True, False)},
        source="official schedule test capture",
        captured_at="2026-10-11T00:00:00Z",
    )
    assert captured.available["g"] == (True, False)
    assert captured.health_applied is True


def test_health_matrix_rejects_unordered_dates_and_non_boolean_entries() -> None:
    roster = [player("g", ("G",), 10, team="AAA")]
    with pytest.raises(ValueError, match="ordered unique"):
        build_schedule_matrix(
            roster,
            dates=("2026-10-21", "2026-10-20"),
            team_games={"AAA": ("2026-10-20", "2026-10-21")},
            health={"g": (True, False)},
        )
    with pytest.raises(ValueError, match="only boolean"):
        build_schedule_matrix(
            roster,
            dates=("2026-10-20", "2026-10-21"),
            team_games={"AAA": ("2026-10-20", "2026-10-21")},
            health={"g": (True, None)},
        )


def test_repeat_schedule_pattern_is_labeled_synthetic() -> None:
    roster = [player("g", ("G",), 10, team="AAA")]
    captured = build_schedule_matrix(
        roster,
        dates=("2026-10-20", "2026-10-21"),
        team_games={"AAA": ("2026-10-20",)},
        source="official schedule test capture",
        captured_at="2026-10-11T00:00:00Z",
    )
    repeated = repeat_schedule_pattern(captured, 2)
    assert repeated.available["g"] == (True, False, True, False)
    assert repeated.dates[0].startswith("cycle-01:")
    assert any("synthetic" in warning for warning in repeated.warnings)


def test_health_warning_distinguishes_capture_status_from_draft_date() -> None:
    roster = legal_center_heavy_roster()
    candidate = player("candidate", ("G",), 11, injury_status="DTD", health_status_unknown=True)
    schedule = {item["player_id"]: (True,) for item in roster}
    schedule["candidate"] = (True,)
    ranked = rank_advisory_candidates(
        [candidate], roster, schedule=schedule, values={"g": 10.0, "candidate": 11.0},
        drop_player_id="zubac",
    )
    assert ranked[0].warnings == (
        "injury status at capture: DTD; this does not establish draft-date availability",
        "player health at draft date is unknown; schedule-only value is advisory",
    )
