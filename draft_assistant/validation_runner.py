"""Efficient full-league validation for the live deterministic policy.

This runner is deliberately separate from ``mock_draft.experiments``.  It
invokes the typed live policy at our seat, keeps 13 opponent seats, evaluates
all 14 starting seats, and aggregates bootstrap samples by independent seed
cluster (not by correlated seat rows).
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from mock_draft.experiments import _forecast_shift_players, _score_selected_ids
from mock_draft.fixture import (
    DRAFT_ROUNDS,
    OPPONENT_PROFILES,
    PICKS_PER_DRAFT,
    STARTING_SLOTS,
    TOTAL_TEAMS,
    DraftConfig,
    OpponentProfile,
    PlayerRecord,
    allocate_lineup,
    check_roster_shape,
    make_synthetic_schedule,
    score_stats,
    synthetic_fixture,
)

from .contracts import DraftObservation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity
from .lookahead import apply_lookahead
from .valuation import ALGORITHM_VERSION, OPPONENT_MODEL_VERSION, ROLLOUT_MAX_CANDIDATES, rank_players, strategy_fingerprint

LINEUP_DAYS = 7


def _live(player: PlayerRecord) -> LivePlayer:
    return LivePlayer(
        player_id=player.player_id,
        name=player.name,
        eligibility=tuple(player.eligibility),
        stats=dict(player.stats),
        projected_gp=None,
        availability=player.projected_availability,
        # Synthetic market rank is an explicit fixture field.  It is supplied
        # to exercise the direct-rank channel in lookahead; valuation itself
        # still ranks by custom production and GP/availability.
        draft_rank=player.market_rank,
        adp=None,
        # The team is public schedule structure (teammates share game days);
        # foundation and lookahead ignore it, the marginal-lineup policy uses it.
        nba_team=player.schedule_group or None,
        source=player.source_label,
    )


def _potentially_legal(roster: Sequence[PlayerRecord], available: Sequence[PlayerRecord], remaining_picks: int, config: DraftConfig) -> bool:
    """Cheap conservative feasibility test used during a draft."""

    current = tuple(roster)
    if len(current) == config.roster_size:
        return check_roster_shape(current, config=config)[0]
    remaining_slots = config.roster_size - len(current)
    if remaining_slots > remaining_picks:
        return False
    for position, required in {"C": 1, "G": 2, "F": 2}.items():
        current_count = sum(position in p.eligibility for p in current)
        needed = max(0, required - current_count)
        if needed > remaining_slots:
            return False
        if sum(position in p.eligibility for p in available) < needed:
            return False
    return True


def _position_counts(available: Sequence[PlayerRecord]) -> dict[str, int]:
    return {position: sum(position in player.eligibility for player in available) for position in ("C", "G", "F")}


def _global_position_feasible(
    candidate: PlayerRecord,
    *,
    seat: int,
    rosters: Mapping[int, Sequence[PlayerRecord]],
    available_counts: Mapping[str, int],
    base_deficits: Mapping[str, int] | None = None,
) -> bool:
    required = {"C": 1, "G": 2, "F": 2}
    if base_deficits is None:
        needed = {position: 0 for position in required}
        for roster_seat, roster in rosters.items():
            current = tuple((*roster, candidate)) if roster_seat == seat else tuple(roster)
            for position, count in required.items():
                needed[position] += max(0, count - sum(position in player.eligibility for player in current))
    else:
        needed = dict(base_deficits)
        own = rosters[seat]
        for position, count in required.items():
            before = max(0, count - sum(position in player.eligibility for player in own))
            after = max(0, count - sum(position in player.eligibility for player in (*own, candidate)))
            needed[position] += after - before
    return all(available_counts[position] - int(position in candidate.eligibility) >= needed[position] for position in required)


def _candidate_legal_fast(
    candidate: PlayerRecord,
    roster: Sequence[PlayerRecord],
    available: Sequence[PlayerRecord],
    available_counts: Mapping[str, int],
    remaining_picks: int,
    config: DraftConfig,
    *,
    exact: bool = True,
    all_pattern_counts: Mapping[tuple[str, ...], int] | None = None,
) -> bool:
    current = tuple((*roster, candidate))
    if len(current) == config.roster_size:
        return check_roster_shape(current, config=config)[0]
    remaining_slots = config.roster_size - len(current)
    if remaining_slots > remaining_picks:
        return False
    if exact and len(current) >= 6:
        # Exact starter matching near the roster tail prevents a locally
        # plausible C/G/F count from stranding two inflexible players.
        from mock_draft.fixture import _min_future_assignments

        if all_pattern_counts is None:
            patterns: dict[tuple[str, ...], int] = {}
            for item in available:
                if item.player_id == candidate.player_id:
                    continue
                pattern = tuple(sorted(item.eligibility))
                patterns[pattern] = patterns.get(pattern, 0) + 1
        else:
            patterns = dict(all_pattern_counts)
            candidate_pattern = tuple(sorted(candidate.eligibility))
            patterns[candidate_pattern] = patterns.get(candidate_pattern, 0) - 1
            if patterns[candidate_pattern] <= 0:
                patterns.pop(candidate_pattern, None)
        minimum = _min_future_assignments(current, patterns, config.starting_slots)
        if minimum is None or minimum > remaining_slots:
            return False
    for position, required in {"C": 1, "G": 2, "F": 2}.items():
        needed = max(0, required - sum(position in item.eligibility for item in current))
        if needed > remaining_slots or available_counts[position] - int(position in candidate.eligibility) < needed:
            return False
    return True


def _draft_order() -> list[int]:
    order: list[int] = []
    for round_number in range(1, DRAFT_ROUNDS + 1):
        seats = list(range(1, TOTAL_TEAMS + 1))
        order.extend(seats if round_number % 2 else list(reversed(seats)))
    return order


def _profile_shift(mode: str) -> tuple[OpponentProfile, ...]:
    if mode == "nominal":
        return OPPONENT_PROFILES
    if mode == "market_misspecified":
        return tuple(
            replace(profile, market_weight=profile.market_weight + 0.75, custom_weight=max(0.1, profile.custom_weight * 0.45), noise=min(0.5, profile.noise + 0.04))
            for profile in OPPONENT_PROFILES
        )
    if mode == "custom_misspecified":
        return tuple(
            replace(profile, market_weight=profile.market_weight * 0.35, custom_weight=profile.custom_weight + 0.75, noise=min(0.5, profile.noise + 0.04))
            for profile in OPPONENT_PROFILES
        )
    raise ValueError(f"unknown opponent scenario {mode!r}")


def _opponent_pick(
    available: Sequence[PlayerRecord],
    roster: Sequence[PlayerRecord],
    profile: OpponentProfile,
    *,
    rng: random.Random,
    config: DraftConfig,
    remaining_picks: int,
    point_cache: Mapping[str, float] | None = None,
    seat: int | None = None,
    rosters: Mapping[int, Sequence[PlayerRecord]] | None = None,
) -> PlayerRecord:
    # Keep the fixture's opponent model, including its noise, but make shape
    # feasibility explicit before choosing the top scored candidate.
    scored: list[tuple[float, PlayerRecord]] = []
    available_counts = _position_counts(available)
    base_deficits = {
        position: sum(max(0, required - sum(position in player.eligibility for player in roster_item)) for roster_item in rosters.values())
        for position, required in {"C": 1, "G": 2, "F": 2}.items()
    } if rosters is not None else None
    all_pattern_counts: dict[tuple[str, ...], int] = {}
    for item in available:
        pattern = tuple(sorted(item.eligibility))
        all_pattern_counts[pattern] = all_pattern_counts.get(pattern, 0) + 1
    counts = {position: sum(position in item.eligibility for item in roster) for position in ("C", "G", "F")}
    deficit = sum(max(0, required - counts[position]) for position, required in {"C": 1, "G": 2, "F": 2}.items())
    for candidate in available:
        if seat is not None and rosters is not None and not _global_position_feasible(candidate, seat=seat, rosters=rosters, available_counts=available_counts, base_deficits=base_deficits):
            continue
        if not _candidate_legal_fast(candidate, roster, available, available_counts, remaining_picks, config, exact=False):
            continue
        points = (point_cache or {}).get(candidate.player_id)
        if points is None:
            points = score_stats(candidate.stats, double_double_includes_triple_double=config.double_double_includes_triple_double)
        market = 1.0 / max((candidate.market_rank + candidate.adp) / 2.0, 1.0)
        player_deficit = sum(1 for position in candidate.eligibility if counts.get(position, 0) < {"C": 1, "G": 2, "F": 2}[position])
        fit = player_deficit * 4.0 + (len(candidate.eligibility) - 1) * 0.55 + deficit * 0.15
        big = float("C" in candidate.eligibility) + float("F" in candidate.eligibility)
        features = (
            profile.market_weight * (100.0 * market)
            + profile.custom_weight * points
            + profile.fit_weight * fit
            + profile.availability_weight * candidate.projected_availability * 10.0
            + profile.upside_weight * candidate.upside
            + profile.big_weight * big
        )
        features += rng.uniform(-profile.noise, profile.noise) * max(abs(features), 1.0)
        scored.append((features, candidate))
    if not scored:
        scored = []
        for candidate in available:
            points = (point_cache or {}).get(candidate.player_id)
            if points is None:
                points = score_stats(candidate.stats, double_double_includes_triple_double=config.double_double_includes_triple_double)
            scored.append((float(points), candidate))
    scored.sort(key=lambda item: (-item[0], item[1].player_id))
    # Exact feasibility is needed only for the small number of top choices;
    # running the min-cost matcher for every candidate at every opponent pick
    # overwhelms the validation matrix without changing the policy model.
    for _, candidate in scored[:6]:
        if (seat is None or rosters is None or _global_position_feasible(candidate, seat=seat, rosters=rosters, available_counts=available_counts, base_deficits=base_deficits)) and _candidate_legal_fast(candidate, roster, available, available_counts, remaining_picks, config, exact=True, all_pattern_counts=all_pattern_counts):
            return candidate
    for _, candidate in scored[6:]:
        if (seat is None or rosters is None or _global_position_feasible(candidate, seat=seat, rosters=rosters, available_counts=available_counts, base_deficits=base_deficits)) and _candidate_legal_fast(candidate, roster, available, available_counts, remaining_picks, config, exact=True, all_pattern_counts=all_pattern_counts):
            return candidate
    return scored[0][1]


def _draft_one(
    players: Sequence[PlayerRecord],
    *,
    our_seat: int,
    seed: int,
    opponent_mode: str,
    policy: str,
    policy_params: object | None = None,
) -> tuple[list[PlayerRecord], dict[str, list[PlayerRecord]], list[dict[str, object]]]:
    cfg = DraftConfig()
    live_cfg = LeagueConfig()
    profiles = _profile_shift(opponent_mode)
    rng = random.Random(seed)
    point_cache = {
        player.player_id: score_stats(player.stats, double_double_includes_triple_double=cfg.double_double_includes_triple_double)
        for player in players
    }
    live_by_id = {player.player_id: _live(player) for player in players}
    known_catalog = live_by_id if policy == "lookahead" else None
    base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] = {}
    available: dict[str, PlayerRecord] = {p.player_id: p for p in players}
    rosters = {seat: [] for seat in range(1, TOTAL_TEAMS + 1)}
    history: list[PickEvent] = []
    logs: list[dict[str, object]] = []
    our_name = live_cfg.our_team
    teams = tuple(our_name if seat == our_seat else str(seat) for seat in range(1, TOTAL_TEAMS + 1))
    for pick_number, seat in enumerate(_draft_order(), start=1):
        pool = list(available.values())
        remaining_picks = cfg.roster_size - len(rosters[seat])
        pick_log: dict[str, object] = {}
        if seat == our_seat:
            observation = DraftObservation(
                session_id=f"validation-{seed}-{opponent_mode}-{our_seat}",
                room=RoomIdentity("1000000000", "2027", "15", True),
                observed_at="2026-10-10T00:00:00+00:00",
                revision=pick_number,
                phase="draft",
                current_pick=pick_number,
                on_clock_team=our_name,
                pick_order=teams,
                history=tuple(history),
                roster=tuple(_live(p) for p in rosters[seat]),
                available_players=tuple(live_by_id[p.player_id] for p in pool),
                available_pool_complete=True,
                history_complete=True,
                known_players=tuple(live_by_id.values()),
            )
            if policy == "market_baseline":
                # Synthetic market rank is explicit fixture input.  This
                # baseline is only for paired mechanics validation; the live
                # policy itself never falls back to rendered row order.
                ranked_ids = sorted(
                    pool,
                    key=lambda item: (
                        float(item.market_rank),
                        float(item.adp),
                        item.player_id,
                    ),
                )
                ranked = tuple(
                    type("BaselineCandidate", (), {"player_id": item.player_id}) for item in ranked_ids
                )
            elif policy == "marginal-lineup-v1":
                from .marginal_lineup import rank_marginal

                marginal = rank_marginal(observation, live_cfg, policy_params, _base_cache=base_cache)
                ranked = marginal.candidates
                pick_log = {"elapsed": marginal.elapsed_seconds, "fallback": marginal.status == "fallback"}
            else:
                ranked = rank_players(observation, live_cfg, _base_cache=base_cache)
                if policy == "lookahead":
                    promoted = apply_lookahead(
                        observation,
                        live_cfg,
                        ranked,
                        promote=True,
                        budget_seconds=live_cfg.recommendation_budget_seconds,
                        max_candidates=ROLLOUT_MAX_CANDIDATES,
                        player_catalog=known_catalog,
                        _base_cache=base_cache,
                    )
                    ranked = promoted.candidates
            if not ranked:
                raise RuntimeError("live policy found no legal selection")
            selected_id = ranked[0].player_id
            selected = available[selected_id]
            # The direct policy should retain a legal completion path.  If a
            # malformed/sparse record makes its first choice unsafe, use the
            # next ranked candidate while preserving the same deterministic
            # ordering.
            available_counts = _position_counts(pool)
            base_deficits = {
                position: sum(max(0, required - sum(position in item.eligibility for item in roster_items)) for roster_items in rosters.values())
                for position, required in {"C": 1, "G": 2, "F": 2}.items()
            }
            all_pattern_counts: dict[tuple[str, ...], int] = {}
            for item in pool:
                pattern = tuple(sorted(item.eligibility))
                all_pattern_counts[pattern] = all_pattern_counts.get(pattern, 0) + 1
            for candidate in ranked:
                record = available[candidate.player_id]
                if record.player_id == selected_id:
                    if not _candidate_legal_fast(record, rosters[seat], pool, available_counts, remaining_picks - 1, cfg, all_pattern_counts=all_pattern_counts):
                        raise RuntimeError("live policy selected an infeasible roster continuation")
                    break
        else:
            index = seat - 1 if seat < our_seat else seat - 2
            selected = _opponent_pick(pool, rosters[seat], profiles[index], rng=rng, config=cfg, remaining_picks=remaining_picks - 1, point_cache=point_cache, seat=seat, rosters=rosters)
        available.pop(selected.player_id)
        rosters[seat].append(selected)
        event = PickEvent(pick_number, our_name if seat == our_seat else str(seat), selected.player_id, selected.name)
        history.append(event)
        logs.append({"pick": pick_number, "seat": seat, "player_id": selected.player_id, "policy": policy, **pick_log})
    if len(history) != PICKS_PER_DRAFT or len({event.player_id for event in history}) != PICKS_PER_DRAFT:
        raise RuntimeError("draft did not produce 140 unique picks")
    for seat, roster in rosters.items():
        legal, errors = check_roster_shape(roster, config=cfg)
        if not legal:
            raise RuntimeError(f"seat {seat} illegal roster: {errors}")
    return rosters[our_seat], rosters, logs


def _weekly_points(roster: Sequence[PlayerRecord], players: Sequence[PlayerRecord], *, seed: int) -> float:
    schedule = make_synthetic_schedule(players, days=LINEUP_DAYS, seed=seed)
    realized = _forecast_shift_players(players, mode="noise", seed=seed + 1_000_003)
    realized_by_id = {p.player_id: p for p in realized}
    total = 0.0
    for day in range(LINEUP_DAYS):
        allocation = allocate_lineup(roster, day=day, schedule=schedule, method="dp", config=DraftConfig())
        total += _score_selected_ids(allocation, realized_by_id=realized_by_id, schedule=schedule, config=DraftConfig())
    return total


def _bootstrap_lower(values: Sequence[float], *, seed: int = 90210, samples: int = 2000) -> float:
    if not values:
        return float("nan")
    rng = random.Random(seed)
    clusters = list(values)
    means = []
    for _ in range(samples):
        means.append(statistics.fmean(clusters[rng.randrange(len(clusters))] for _ in clusters))
    means.sort()
    return means[max(0, int(0.025 * len(means)) - 1)]


def run_validation(
    *,
    output_dir: str | Path,
    heldout_seeds: Sequence[int] = tuple(range(20270000, 20270100)),
    scenarios: Sequence[str] = ("nominal", "market_misspecified", "custom_misspecified"),
) -> dict[str, object]:
    started = time.perf_counter()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    # Reuse each seed's player pool and realized noise for the paired policies
    # and 14 seats; only the draft policy changes.
    for scenario in scenarios:
        for seed in heldout_seeds:
            # 150 records leave ten spare players while reducing repeated
            # rollout work; the required league still drafts all 140 unique
            # picks and every seat has a complete legal roster.
            players = synthetic_fixture(player_count=195, seed=seed)
            for seat in range(1, TOTAL_TEAMS + 1):
                baseline, _, _ = _draft_one(players, our_seat=seat, seed=seed, opponent_mode=scenario, policy="foundation")
                policy, _, _ = _draft_one(players, our_seat=seat, seed=seed, opponent_mode=scenario, policy="lookahead")
                baseline_points = _weekly_points(baseline, players, seed=seed + seat * 1000)
                policy_points = _weekly_points(policy, players, seed=seed + seat * 1000)
                rows.append({
                    "scenario": scenario,
                    "seed": seed,
                    "seat": seat,
                    "foundation_weekly_points": baseline_points,
                    "lookahead_weekly_points": policy_points,
                    "delta": policy_points - baseline_points,
                    "foundation_roster_size": len(baseline),
                    "lookahead_roster_size": len(policy),
                })
            print(f"{scenario}: completed seed {seed} ({len(rows)} paired seat rows)", flush=True)
    summaries: list[dict[str, object]] = []
    for scenario in scenarios:
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        by_seed: dict[int, list[dict[str, object]]] = {}
        for row in scenario_rows:
            by_seed.setdefault(int(row["seed"]), []).append(row)
        cluster_means = [statistics.fmean(float(row["delta"]) for row in cluster) for cluster in by_seed.values()]
        by_seat = []
        for seat in range(1, TOTAL_TEAMS + 1):
            seat_rows = [row for row in scenario_rows if int(row["seat"]) == seat]
            baseline = statistics.fmean(float(row["foundation_weekly_points"]) for row in seat_rows)
            delta = statistics.fmean(float(row["delta"]) for row in seat_rows)
            by_seat.append({"seat": seat, "mean_delta": delta, "mean_loss_fraction": delta / baseline if baseline else 0.0})
        lower = _bootstrap_lower(cluster_means, seed=90210 + list(scenarios).index(scenario))
        summaries.append({
            "scenario": scenario,
            "independent_seed_clusters": len(cluster_means),
            "seat_rows": len(scenario_rows),
            "mean_delta": statistics.fmean(cluster_means),
            "paired_bootstrap_95_lower": lower,
            "min_seat_delta": min(float(item["mean_delta"]) for item in by_seat),
            "worst_seat_loss_fraction": min(float(item["mean_loss_fraction"]) for item in by_seat),
            "gate_pass": lower > 0 and min(float(item["mean_loss_fraction"]) for item in by_seat) >= -0.05,
            "by_seat": by_seat,
        })
    metadata = {
        "runner": "draft_assistant.validation_runner",
        "heldout_seed_clusters": len(heldout_seeds),
        "teams": TOTAL_TEAMS,
        "opponents": TOTAL_TEAMS - 1,
        "rounds": DRAFT_ROUNDS,
        "picks_per_draft": PICKS_PER_DRAFT,
        "seats_per_seed": TOTAL_TEAMS,
        "scenarios": list(scenarios),
        "foundation": "custom FP × availability with small coverage tie adjustment",
        "policy": "bounded two-pick lookahead promoted for validation only",
        "foundation_vs_lookahead": True,
        "lookahead_enabled": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_fingerprint": strategy_fingerprint(LeagueConfig()),
        "lookahead_budget_seconds": LeagueConfig().recommendation_budget_seconds,
        "lookahead_max_candidates": ROLLOUT_MAX_CANDIDATES,
        "opponent_model": OPPONENT_MODEL_VERSION,
        "realized_evaluation": "seeded ±10% stat noise after draft selection; no forecast leakage",
        "elapsed_seconds": time.perf_counter() - started,
    }
    payload = {"metadata": metadata, "summaries": summaries, "rows": rows}
    (destination / "heldout_validation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (destination / "heldout_runs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["scenario"])
        writer.writeheader()
        writer.writerows(rows)
    (destination / "validation_summary.json").write_text(json.dumps({"metadata": metadata, "summaries": summaries}, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="draft_assistant/validation")
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--scenarios", nargs="+", default=["nominal", "market_misspecified", "custom_misspecified"])
    args = parser.parse_args()
    result = run_validation(
        output_dir=args.output_dir,
        heldout_seeds=tuple(range(20270000, 20270000 + args.seeds)),
        scenarios=tuple(args.scenarios),
    )
    print(json.dumps(result["summaries"], indent=2))
