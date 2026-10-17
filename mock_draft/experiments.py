"""Reproducible paired mock-draft experiments.

The draft fixture is deliberately synthetic.  This runner keeps the experiment
design separate from the engine: every strategy uses the same player pool,
opponent profiles, draft seed, and availability schedule for a given
scenario/seat pair.  Consequently the strategy columns can be compared as
paired observations rather than as independent Monte Carlo samples.

Run from the repository root with::

    python -m mock_draft.experiments

The command writes JSON/CSV run-level data and a short Markdown report under
``mock_draft/results``.  The report describes synthetic sensitivity results;
it is not a calibrated probability of winning a fantasy league.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mock_draft.fixture import (
    DRAFT_ROUNDS,
    PICKS_PER_DRAFT,
    ROSTER_SIZE,
    STRATEGY_NAMES,
    TOTAL_TEAMS,
    DraftConfig,
    DraftResult,
    MockDraft,
    PlayerRecord,
    RosterShapeError,
    SyntheticSchedule,
    allocate_lineup,
    check_roster_shape,
    make_synthetic_schedule,
    load_player_records,
    score_stats,
    synthetic_fixture,
)


BASELINE = "baseline_market"
LINEUP_DAYS = 7


@dataclass(frozen=True)
class Scenario:
    """One paired draft/schedule condition.

    ``split`` identifies observations used for development versus held-out
    sensitivity checks.  ``availability_mode`` applies only to the generated
    schedule and is common to every strategy in the scenario.
    """

    name: str
    split: str
    draft_seed: int
    schedule_seed: int
    availability_mode: str = "base"
    overlap: bool = True
    market_mode: str = "fixture"
    forecast_mode: str = "none"


SCENARIOS: tuple[Scenario, ...] = tuple(
    [Scenario(f"development_market_agree_{seed}", "development", 7100 + seed, 8100 + seed, "base", True, "agree", "none") for seed in (1, 2, 3)]
    + [Scenario(f"development_market_mismatch_{seed}", "development", 7200 + seed, 8200 + seed, "base", True, "mismatch", "none") for seed in (1, 2, 3)]
    + [Scenario(f"heldout_standard_{seed}", "heldout", 9100 + seed, 10100 + seed, "base", True, "fixture", "noise") for seed in (1, 2, 3)]
    + [Scenario(f"heldout_standard_overlap_off_{seed}", "heldout", 9100 + seed, 10100 + seed, "base", False, "fixture", "noise") for seed in (1, 2, 3)]
    + [Scenario(f"heldout_availability_stress_{seed}", "heldout", 9200 + seed, 10200 + seed, "stress", True, "fixture", "noise") for seed in (1, 2, 3)]
)


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return float(statistics.fmean(values)) if values else 0.0


def _stdev(values: Iterable[float]) -> float:
    values = list(values)
    return float(statistics.stdev(values)) if len(values) > 1 else 0.0


def _percentile_rank(points: Mapping[str, float]) -> dict[str, int]:
    ordered = sorted(points, key=lambda player_id: (-points[player_id], player_id))
    return {player_id: rank for rank, player_id in enumerate(ordered, start=1)}


def _market_mismatch(
    roster: Sequence[PlayerRecord],
    players: Sequence[PlayerRecord],
    *,
    config: DraftConfig,
) -> float:
    """Mean normalized distance between market and custom-value ranks.

    A value near zero means the selected players tend to agree with the
    market ordering.  Higher values represent a larger market mismatch.  This
    is a descriptive covariate, not a claim that either ordering is correct.
    """

    custom = {
        player.player_id: score_stats(
            player.stats,
            double_double_includes_triple_double=config.double_double_includes_triple_double,
        )
        for player in players
    }
    custom_rank = _percentile_rank(custom)
    denominator = max(len(players) - 1, 1)
    return _mean(
        abs((player.market_rank - custom_rank[player.player_id]) / denominator)
        for player in roster
    )


def _apply_availability_mode(
    schedule: SyntheticSchedule,
    *,
    mode: str,
    seed: int,
) -> SyntheticSchedule:
    """Apply a common, seeded schedule shift to a base schedule.

    The stress condition removes roughly 15% of otherwise active player-days,
    with an additional small day-level cluster.  It is intentionally a
    scenario shift applied equally to all strategies, not a player-specific
    injury prediction.
    """

    if mode == "base":
        return schedule
    if mode != "stress":
        raise ValueError(f"unknown availability mode: {mode}")
    rng = random.Random(seed)
    shifted: dict[str, tuple[bool, ...]] = {}
    shifted_health: dict[str, tuple[bool, ...]] = {}
    for player_id, days in schedule.availability.items():
        revised = []
        revised_health = []
        for day, active in enumerate(days):
            cluster = day in {2, 5} and rng.random() < 0.12
            # Preserve the fixture's team-game matrix.  Stress is an extra
            # health shock layered on top, rather than a fake game/off-day.
            game_day = schedule.is_game_day(player_id, day)
            health = bool(schedule.health_availability.get(player_id, (active,) * schedule.days)[day])
            health = bool(health and not (rng.random() < 0.15 or cluster))
            revised_health.append(health)
            revised.append(bool(game_day and health))
        shifted_health[player_id] = tuple(revised_health)
        shifted[player_id] = tuple(revised)
    return SyntheticSchedule(
        shifted,
        schedule.days,
        schedule.seed,
        schedule.source_label + ":stress",
        schedule.game_schedule,
        shifted_health,
    )


def _market_ordered_players(
    players: Sequence[PlayerRecord],
    *,
    mode: str,
    config: DraftConfig,
) -> list[PlayerRecord]:
    """Return a copy with an explicit market-agreement/mismatch ordering.

    ``agree`` sorts the synthetic market by custom points; ``mismatch`` sorts
    it in the opposite direction.  The experiment therefore changes the
    information condition itself, rather than merely labeling observations by
    a post-draft correlation statistic.
    """

    if mode == "fixture":
        return list(players)
    if mode not in {"agree", "mismatch"}:
        raise ValueError(f"unknown market mode: {mode}")
    points = {
        player.player_id: score_stats(
            player.stats,
            double_double_includes_triple_double=config.double_double_includes_triple_double,
        )
        for player in players
    }
    reverse = mode == "agree"
    order = sorted(players, key=lambda player: ((-points[player.player_id] if reverse else points[player.player_id]), player.player_id))
    return [replace(player, market_rank=float(rank), adp=float(rank)) for rank, player in enumerate(order, start=1)]


def _forecast_shift_players(
    players: Sequence[PlayerRecord],
    *,
    mode: str,
    seed: int,
) -> list[PlayerRecord]:
    """Create a realized-stat evaluation copy for forecast-uncertainty runs.

    Draft decisions still use the forecast records.  The lineup scorer uses
    this separate realized copy, with a deterministic multiplicative shock,
    so held-out rows are not simply forecast-as-truth replays.
    """

    if mode == "none":
        return list(players)
    if mode != "noise":
        raise ValueError(f"unknown forecast mode: {mode}")
    rng = random.Random(seed)
    shifted: list[PlayerRecord] = []
    for player in players:
        stats = {
            key: max(0.0, float(value) * (1.0 + rng.uniform(-0.10, 0.10)))
            for key, value in player.stats.items()
        }
        shifted.append(replace(player, stats=stats, source_label=player.source_label + ":realized_noise"))
    return shifted


def _active_count(roster: Sequence[PlayerRecord], schedule: SyntheticSchedule, day: int) -> int:
    return sum(schedule.is_available(player.player_id, day) for player in roster)


def _score_selected_ids(
    allocation: Any,
    *,
    realized_by_id: Mapping[str, PlayerRecord],
    schedule: SyntheticSchedule,
    config: DraftConfig,
) -> float:
    """Score forecast-selected slots with realized stats.

    Keeping selection and evaluation separate prevents held-out stat noise
    from leaking into the daily lineup optimizer.  Availability is treated as
    known at lineup time in this synthetic exercise.
    """

    total = 0.0
    for player_id in allocation.slots.values():
        if schedule.is_available(player_id, allocation.day):
            total += score_stats(
                realized_by_id[player_id].stats,
                double_double_includes_triple_double=config.double_double_includes_triple_double,
            )
    return total


def _run_one(
    players: Sequence[PlayerRecord],
    evaluation_players: Sequence[PlayerRecord],
    scenario: Scenario,
    *,
    strategy: str,
    seat: int,
    config: DraftConfig,
    schedule: SyntheticSchedule,
) -> dict[str, Any]:
    """Run one strategy/seat pair and return scalar run metrics."""

    draft = MockDraft(
        players,
        strategy=strategy,
        seat=seat,
        seed=scenario.draft_seed,
        config=config,
    ).run()
    roster = draft.roster()
    evaluation_by_id = {player.player_id: player for player in evaluation_players}
    evaluation_roster = [evaluation_by_id[player.player_id] for player in roster]
    legal, shape_errors = check_roster_shape(roster, config=config)
    dp_allocations = [
        allocate_lineup(roster, day=day, schedule=schedule, method="dp", config=config)
        for day in range(LINEUP_DAYS)
    ]
    greedy_allocations = []
    greedy_assignment_failures = 0
    for day in range(LINEUP_DAYS):
        try:
            greedy_allocations.append(
                allocate_lineup(roster, day=day, schedule=schedule, method="greedy", config=config)
            )
        except RosterShapeError:
            # A greedy slot order can paint itself into a corner even when the
            # exact DP assignment is legal.  Keep that approximation failure
            # observable and leave the exact DP result authoritative.
            greedy_assignment_failures += 1
    active_player_games = sum(_active_count(evaluation_roster, schedule, day) for day in range(LINEUP_DAYS))
    busy_collisions = sum(max(0, _active_count(evaluation_roster, schedule, day) - len(config.starting_slots)) for day in range(LINEUP_DAYS))
    inactive_starters = sum(len(allocation.inactive_starters) for allocation in dp_allocations)
    realized_by_id = {player.player_id: player for player in evaluation_roster}
    dp_realized_scores = [
        _score_selected_ids(allocation, realized_by_id=realized_by_id, schedule=schedule, config=config)
        for allocation in dp_allocations
    ]
    greedy_realized_scores = [
        _score_selected_ids(allocation, realized_by_id=realized_by_id, schedule=schedule, config=config)
        for allocation in greedy_allocations
    ]
    market_rank_mean = _mean(player.market_rank for player in roster)
    forecast_points = _mean(score_stats(player.stats, double_double_includes_triple_double=config.double_double_includes_triple_double) for player in roster)
    realized_points = _mean(score_stats(player.stats, double_double_includes_triple_double=config.double_double_includes_triple_double) for player in evaluation_roster)
    all_pick_ids = [pick.player.player_id for pick in draft.picks]
    shape_error_text = "; ".join(shape_errors)
    return {
        "scenario": scenario.name,
        "split": scenario.split,
        "availability_mode": scenario.availability_mode,
        "double_double_includes_triple_double": scenario.overlap,
        "strategy": strategy,
        "our_seat": seat,
        "draft_seed": scenario.draft_seed,
        "schedule_seed": scenario.schedule_seed,
        "player_source": players[0].source_label if players else "unknown",
        "schedule_source": schedule.source_label,
        "draft_picks": len(draft.picks),
        "simulation_runs": LINEUP_DAYS,
        "weekly_dp_points": sum(dp_realized_scores),
        "weekly_greedy_points": sum(greedy_realized_scores),
        "dp_greedy_gap": sum(dp_realized_scores) - sum(greedy_realized_scores),
        "greedy_assignment_failures": greedy_assignment_failures,
        "usable_starter_games": len(config.starting_slots) * LINEUP_DAYS - inactive_starters,
        "active_player_games": active_player_games,
        "busy_day_collisions": busy_collisions,
        "inactive_starters": inactive_starters,
        "roster_availability_rate": active_player_games / float(ROSTER_SIZE * LINEUP_DAYS),
        "forecast_custom_points_per_player": forecast_points,
        "realized_custom_points_per_player": realized_points,
        "market_rank_mean": market_rank_mean,
        "market_mismatch": _market_mismatch(roster, players, config=config),
        "roster_size": len(roster),
        "legal_roster": legal,
        "shape_errors": shape_error_text,
        "unique_pick_count": len(set(all_pick_ids)),
        "all_seats_present": sorted(draft.seat_by_team.values()) == list(range(1, TOTAL_TEAMS + 1)),
        "roster_player_ids": [player.player_id for player in roster],
    }


def _validate_run_matrix(rows: Sequence[Mapping[str, Any]]) -> None:
    """Fail fast if the paired design is incomplete or malformed."""

    expected_per_scenario = len(STRATEGY_NAMES) * TOTAL_TEAMS
    by_scenario: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(str(row["scenario"]), []).append(row)
        if row["draft_picks"] != PICKS_PER_DRAFT:
            raise AssertionError(f"{row['scenario']} produced {row['draft_picks']} picks")
        if row["unique_pick_count"] != PICKS_PER_DRAFT:
            raise AssertionError(f"{row['scenario']} has duplicate picks")
        if not row["all_seats_present"] or not row["legal_roster"]:
            raise AssertionError(f"illegal draft or roster in {row['scenario']} seat {row['our_seat']}")
    if set(by_scenario) != {scenario.name for scenario in SCENARIOS}:
        raise AssertionError("scenario set does not match the declared experiment design")
    for scenario in SCENARIOS:
        scenario_rows = by_scenario[scenario.name]
        if len(scenario_rows) != expected_per_scenario:
            raise AssertionError(f"{scenario.name} has {len(scenario_rows)} rows; expected {expected_per_scenario}")
        keys = {(row["strategy"], row["our_seat"]) for row in scenario_rows}
        expected = {(strategy, seat) for strategy in STRATEGY_NAMES for seat in range(1, TOTAL_TEAMS + 1)}
        if keys != expected:
            raise AssertionError(f"{scenario.name} is missing strategy/seat pairs")


def _paired_deltas(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    baseline = {
        (row["scenario"], row["our_seat"]): row
        for row in rows
        if row["strategy"] == BASELINE
    }
    deltas: list[dict[str, Any]] = []
    for row in rows:
        if row["strategy"] == BASELINE:
            continue
        base = baseline[(row["scenario"], row["our_seat"])]
        deltas.append({
            "scenario": row["scenario"],
            "split": row["split"],
            "strategy": row["strategy"],
            "our_seat": row["our_seat"],
            "weekly_dp_delta": row["weekly_dp_points"] - base["weekly_dp_points"],
            "usable_starter_games_delta": row["usable_starter_games"] - base["usable_starter_games"],
            "busy_day_collisions_delta": row["busy_day_collisions"] - base["busy_day_collisions"],
            "inactive_starters_delta": row["inactive_starters"] - base["inactive_starters"],
            "dp_greedy_gap_delta": row["dp_greedy_gap"] - base["dp_greedy_gap"],
            "market_mismatch": row["market_mismatch"],
        })
    return deltas


def _summary(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deltas = _paired_deltas(rows)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault((str(row["split"]), str(row["strategy"])), []).append(row)
    delta_groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in deltas:
        delta_groups.setdefault((str(row["split"]), str(row["strategy"])), []).append(row)
    summaries: list[dict[str, Any]] = []
    for (split, strategy), items in sorted(groups.items()):
        paired = delta_groups.get((split, strategy), [])
        seat_means = {
            seat: _mean(row["weekly_dp_points"] for row in items if row["our_seat"] == seat)
            for seat in range(1, TOTAL_TEAMS + 1)
        }
        summaries.append({
            "split": split,
            "strategy": strategy,
            "runs": len(items),
            "draft_picks": sum(row["draft_picks"] for row in items),
            "simulation_runs": sum(row["simulation_runs"] for row in items),
            "weekly_dp_points_mean": _mean(row["weekly_dp_points"] for row in items),
            "weekly_dp_points_sd": _stdev(row["weekly_dp_points"] for row in items),
            "weekly_greedy_points_mean": _mean(row["weekly_greedy_points"] for row in items),
            "dp_greedy_gap_mean": _mean(row["dp_greedy_gap"] for row in items),
            "greedy_assignment_failures_mean": _mean(row["greedy_assignment_failures"] for row in items),
            "dp_greedy_gap_complete_mean": _mean(row["dp_greedy_gap"] for row in items if row["greedy_assignment_failures"] == 0),
            "dp_greedy_gap_complete_runs": sum(row["greedy_assignment_failures"] == 0 for row in items),
            "usable_starter_games_mean": _mean(row["usable_starter_games"] for row in items),
            "busy_day_collisions_mean": _mean(row["busy_day_collisions"] for row in items),
            "inactive_starters_mean": _mean(row["inactive_starters"] for row in items),
            "roster_availability_rate_mean": _mean(row["roster_availability_rate"] for row in items),
            "market_mismatch_mean": _mean(row["market_mismatch"] for row in items),
            "paired_delta_vs_market_mean": 0.0 if strategy == BASELINE else _mean(row["weekly_dp_delta"] for row in paired),
            "paired_delta_vs_market_sd": 0.0 if strategy == BASELINE else _stdev(row["weekly_dp_delta"] for row in paired),
            "paired_usable_games_delta_mean": 0.0 if strategy == BASELINE else _mean(row["usable_starter_games_delta"] for row in paired),
            "seat_mean_min": min(seat_means.values()),
            "seat_mean_max": max(seat_means.values()),
            "seat_mean_spread": max(seat_means.values()) - min(seat_means.values()),
            "worst_seat_by_mean": min(seat_means, key=seat_means.get),
        })
    return summaries, deltas


def _agreement_summary(rows: Sequence[Mapping[str, Any]], deltas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Summarize paired deltas by market agreement/mismatch bins."""

    # Bins are intentionally descriptive and use the selected roster's
    # normalized rank distance; they are not a post-hoc claim of causality.
    bins = (("aligned", 0.0, 0.15), ("mixed", 0.15, 0.30), ("mismatch", 0.30, math.inf))
    output: list[dict[str, Any]] = []
    for strategy in STRATEGY_NAMES:
        for split in ("development", "heldout"):
            strategy_deltas = [row for row in deltas if row["strategy"] == strategy and row["split"] == split]
            for label, lower, upper in bins:
                selected = [row for row in strategy_deltas if lower <= row["market_mismatch"] < upper]
                output.append({
                    "split": split,
                    "strategy": strategy,
                    "agreement_bin": label,
                    "observations": len(selected),
                    "paired_weekly_dp_delta_mean": _mean(row["weekly_dp_delta"] for row in selected),
                    "paired_usable_games_delta_mean": _mean(row["usable_starter_games_delta"] for row in selected),
                })
    return output


def _policy_selection(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Select a best-in-tested-set policy on development rows and audit holdout."""

    # Include the market baseline as a legitimate candidate.  If every tested
    # alternative loses on development rows, the measured recommendation is
    # to retain the baseline rather than force a heuristic improvement.
    development = [row for row in summaries if row["split"] == "development"]
    heldout = [row for row in summaries if row["split"] == "heldout"]
    if not development:
        return {"development_selected_strategy": None, "selection_basis": "no development rows"}
    selected = max(development, key=lambda row: (row["paired_delta_vs_market_mean"], -row["seat_mean_spread"]))
    selected_name = selected["strategy"]
    selected_holdout = next((row for row in heldout if row["strategy"] == selected_name), None)
    holdout_best = max(heldout, key=lambda row: row["paired_delta_vs_market_mean"]) if heldout else None
    return {
        "development_selected_strategy": selected_name,
        "development_paired_delta_mean": selected["paired_delta_vs_market_mean"],
        "development_seat_spread": selected["seat_mean_spread"],
        "selected_strategy_holdout_paired_delta_mean": selected_holdout["paired_delta_vs_market_mean"] if selected_holdout else None,
        "heldout_best_strategy_in_tested_set": holdout_best["strategy"] if holdout_best else None,
        "heldout_best_paired_delta_mean": holdout_best["paired_delta_vs_market_mean"] if holdout_best else None,
        "selection_basis": "highest mean paired weekly DP delta versus baseline across development scenarios; tie-break lower seat spread",
        "interpretation": "best within tested synthetic/source-input conditions, not globally optimal and not a calibrated win probability",
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _representative_draft_rows(
    players: Sequence[PlayerRecord],
    *,
    scenario: Scenario,
    strategy: str,
    seat: int,
    config: DraftConfig,
) -> list[dict[str, Any]]:
    """Return one complete 140-pick league log for auditability."""

    draft = MockDraft(players, strategy=strategy, seat=seat, seed=scenario.draft_seed, config=config).run()
    return [
        {
            "pick_number": pick.pick_number,
            "round_number": pick.round_number,
            "seat": pick.seat,
            "team": pick.team,
            "player_id": pick.player.player_id,
            "player_name": pick.player.name,
            "eligibility": "|".join(pick.player.eligibility),
            "market_rank": pick.player.market_rank,
            "projected_availability": pick.player.projected_availability,
            "scenario": scenario.name,
            "our_strategy": strategy,
            "our_seat": seat,
        }
        for pick in draft.picks
    ]


def _fmt(value: Any, digits: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _write_report(
    path: Path,
    *,
    metadata: Mapping[str, Any],
    summaries: Sequence[Mapping[str, Any]],
    agreement: Sequence[Mapping[str, Any]],
) -> None:
    synthetic_input = bool(metadata.get("synthetic_input", False))
    provenance_sentence = (
        "These results come from an explicitly synthetic player pool and synthetic availability schedules. They are sensitivity tests of the fixture and strategy rules, not real-player projections or calibrated fantasy win probabilities."
        if synthetic_input
        else "These results use the supplied player snapshot with synthetic team game/off-day schedules. They are projection sensitivity tests, not calibrated fantasy win probabilities or a claim that the displayed market proxy is ADP."
    )
    lines = [
        "# Mock draft experiment results",
        "",
        provenance_sentence,
        "",
        f"Experiment design: **{metadata['scenario_count']} scenarios × {metadata['strategy_count']} strategies × {metadata['seats_per_scenario']} seats = {metadata['simulation_runs']} draft simulations**. Each simulation has **{metadata['draft_picks_per_run']} draft picks** and **{metadata['lineup_runs_per_draft']} daily lineup allocation runs**. The 140 picks and lineup runs are reported separately.",
        "",
        f"Player source: `{metadata['player_source']}`. Schedule source: `{metadata['schedule_source']}`. The schedule matrix is reused for every paired strategy in each scenario. Opponents are the fixture's 13 named profiles.",
        "",
        f"Market conditions exercised: `{', '.join(metadata['market_modes'])}`. Forecast evaluation modes: `{', '.join(metadata['forecast_modes'])}`; held-out noise rows draft from forecast records and score lineups against a separate seeded realized-stat copy.",
        "",
        f"Within the tested set, development selects `{metadata.get('policy_selection', {}).get('development_selected_strategy')}` by paired weekly DP delta; that policy's held-out delta is `{_fmt(metadata.get('policy_selection', {}).get('selected_strategy_holdout_paired_delta_mean'))}`. This is a within-experiment selection result, not a globally optimal policy claim.",
        "",
        "## Paired strategy deltas",
        "",
        "`weekly Δ` is exact dynamic-programming lineup points relative to the market baseline for the same scenario and seat. Positive values indicate more modeled points under that scenario's supplied records and synthetic schedule; they are not win probabilities.",
        "",
        "| split | strategy | runs | weekly DP | weekly Δ vs market | usable starter games | busy collisions | inactive starters | greedy fails | seat spread |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append("| {split} | {strategy} | {runs} | {points} | {delta} | {usable} | {busy} | {inactive} | {greedy_fail} | {spread} |".format(
            split=row["split"], strategy=row["strategy"], runs=row["runs"], points=_fmt(row["weekly_dp_points_mean"]),
            delta=_fmt(row["paired_delta_vs_market_mean"]), usable=_fmt(row["usable_starter_games_mean"]),
            busy=_fmt(row["busy_day_collisions_mean"]), inactive=_fmt(row["inactive_starters_mean"]), greedy_fail=_fmt(row["greedy_assignment_failures_mean"]), spread=_fmt(row["seat_mean_spread"]),
        ))
    lines.extend([
        "",
        "A seat spread is the range of per-seat means within the split. It describes draft-position sensitivity and should not be read as a probability of winning.",
        "",
        "## Market agreement and mismatch",
        "",
        "`market_mismatch` is the normalized distance between each drafted player's market rank and custom-points rank. The bins below are descriptive covariates; sample sizes are shown so sparse bins are visible.",
        "",
        "| split | strategy | bin | observations | weekly Δ vs market | usable-games Δ |",
        "|---|---|---|---:|---:|---:|",
    ])
    for row in agreement:
        if row["observations"]:
            lines.append("| {split} | {strategy} | {bin} | {n} | {delta} | {usable} |".format(
                split=row["split"], strategy=row["strategy"], bin=row["agreement_bin"], n=row["observations"],
                delta=_fmt(row["paired_weekly_dp_delta_mean"]), usable=_fmt(row["paired_usable_games_delta_mean"]),
            ))
    lines.extend([
        "",
        "## Assumptions and limitations",
        "",
        "- Daily lineups are `C, G, G, F, F, UTIL`; four bench spots are drafted and three IR slots are extra capacity, not draft slots.",
        "- The exact DP allocator and greedy allocator both preserve legal roster shape. `dp_greedy_gap` quantifies the assignment approximation gap.",
        "- A greedy assignment failure is reported separately; its missing day is not interpreted as an optimizer improvement in the exact DP score.",
        "- The held-out rows use new draft/schedule seeds and include an availability-stress shift. The overlap-off rows reuse held-out base seeds to isolate the DD/TD bonus convention.",
        "- Forecast-noise rows separate draft inputs from realized evaluation stats with a seeded ±10% stat perturbation; this is a synthetic robustness regime, not an uncertainty calibration.",
        "- The daily allocator sees the synthetic active/inactive schedule, while realized stat noise is used only after slot selection; late injury information is therefore not modeled.",
        "- Triple-double overlap is controlled by `double_double_includes_triple_double`. The report does not claim which platform convention is correct.",
        ("- No ESPN transaction, roster, or real-player projection was used; re-run with an explicitly sourced player file through `load_player_records` before drawing domain conclusions." if synthetic_input else "- The supplied ESPN snapshot has a rendered row-order market proxy (% rostered), no true ADP, and rough implied availability. Missing PF/DD/TD/EJ fields remain unknown rather than forecasts of zero; draw domain conclusions only after sourcing those inputs."),
        "",
        "Raw paired observations are in `experiment_runs.csv`; aggregate tables are in `experiment_summary.csv` and `agreement_summary.csv`. `policy_selection.json` records the development-selected tested policy and its held-out audit. `representative_draft.csv` is one complete 140-pick league log (illustrative custom-value seat 7) for auditability.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_experiments(
    *,
    players: Sequence[PlayerRecord] | None = None,
    scenarios: Sequence[Scenario] = SCENARIOS,
    output_dir: str | Path | None = None,
    player_seed: int = 20261010,
) -> dict[str, Any]:
    """Run the full paired matrix and optionally persist results.

    ``players`` is injectable for tests and for a future sourced player file.
    With no input, the runner uses 210 generated records labelled
    ``synthetic_fixture``.
    """

    config = DraftConfig()
    player_pool = list(players) if players is not None else synthetic_fixture(seed=player_seed)
    if len(player_pool) < PICKS_PER_DRAFT:
        raise ValueError(f"need at least {PICKS_PER_DRAFT} players")
    if len(scenarios) == 0:
        raise ValueError("at least one scenario is required")
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_config = DraftConfig(double_double_includes_triple_double=scenario.overlap)
        draft_players = _market_ordered_players(player_pool, mode=scenario.market_mode, config=scenario_config)
        evaluation_players = _forecast_shift_players(
            draft_players,
            mode=scenario.forecast_mode,
            seed=scenario.draft_seed + scenario.schedule_seed,
        )
        base_schedule = make_synthetic_schedule(draft_players, days=LINEUP_DAYS, seed=scenario.schedule_seed)
        schedule = _apply_availability_mode(base_schedule, mode=scenario.availability_mode, seed=scenario.schedule_seed + 17)
        for strategy in STRATEGY_NAMES:
            for seat in range(1, TOTAL_TEAMS + 1):
                rows.append(_run_one(draft_players, evaluation_players, scenario, strategy=strategy, seat=seat, config=scenario_config, schedule=schedule))
    # The validator is deliberately run before writing anything, so a partial
    # result directory cannot be mistaken for a complete experiment.
    original_scenarios = globals()["SCENARIOS"]
    try:
        globals()["SCENARIOS"] = tuple(scenarios)
        _validate_run_matrix(rows)
    finally:
        globals()["SCENARIOS"] = original_scenarios
    summaries, deltas = _summary(rows)
    agreement = _agreement_summary(rows, deltas)
    selection = _policy_selection(summaries)
    metadata = {
        "player_source": player_pool[0].source_label if player_pool else "unknown",
        "schedule_source": "synthetic_schedule",
        "scenario_count": len(scenarios),
        "strategy_count": len(STRATEGY_NAMES),
        "seats_per_scenario": TOTAL_TEAMS,
        "simulation_runs": len(rows),
        "draft_picks_per_run": PICKS_PER_DRAFT,
        "total_draft_picks": len(rows) * PICKS_PER_DRAFT,
        "lineup_runs_per_draft": LINEUP_DAYS,
        "total_lineup_simulation_runs": len(rows) * LINEUP_DAYS,
        "rounds": DRAFT_ROUNDS,
        "teams": TOTAL_TEAMS,
        "roster_size": ROSTER_SIZE,
        "strategies": list(STRATEGY_NAMES),
        "scenarios": [asdict(scenario) for scenario in scenarios],
        "player_count": len(player_pool),
        "synthetic_input": bool(player_pool and player_pool[0].source_label == "synthetic_fixture"),
        "market_modes": sorted({scenario.market_mode for scenario in scenarios}),
        "forecast_modes": sorted({scenario.forecast_mode for scenario in scenarios}),
        "reproducibility": {"player_seed": player_seed, "draft_and_schedule_seeds_in_scenarios": True},
        "representative_draft": {"strategy": "custom_value", "our_seat": 7, "complete_picks": PICKS_PER_DRAFT},
        "policy_selection": selection,
    }
    result = {"metadata": metadata, "runs": rows, "summary": summaries, "deltas": deltas, "agreement": agreement, "policy_selection": selection}
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "experiment_runs.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        (destination / "experiment_summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
        (destination / "agreement_summary.json").write_text(json.dumps(agreement, indent=2), encoding="utf-8")
        (destination / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (destination / "policy_selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
        _write_csv(destination / "experiment_runs.csv", rows)
        _write_csv(destination / "experiment_summary.csv", summaries)
        _write_csv(destination / "agreement_summary.csv", agreement)
        representative = next((scenario for scenario in scenarios if scenario.split == "heldout"), scenarios[0])
        representative_config = DraftConfig(double_double_includes_triple_double=representative.overlap)
        representative_players = _market_ordered_players(player_pool, mode=representative.market_mode, config=representative_config)
        representative_rows = _representative_draft_rows(
            representative_players,
            scenario=representative,
            strategy="custom_value",
            seat=7,
            config=representative_config,
        )
        _write_csv(destination / "representative_draft.csv", representative_rows)
        _write_report(destination / "RESULTS.md", metadata=metadata, summaries=summaries, agreement=agreement)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(Path(__file__).with_name("results")))
    parser.add_argument("--player-seed", type=int, default=20261010)
    parser.add_argument("--player-file", "--players", dest="player_file", help="Optional CSV/JSON input; omit to use the explicitly synthetic pool")
    args = parser.parse_args(argv)
    players = load_player_records(args.player_file) if args.player_file else None
    result = run_experiments(output_dir=args.output_dir, player_seed=args.player_seed, players=players)
    print(json.dumps(result["metadata"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

