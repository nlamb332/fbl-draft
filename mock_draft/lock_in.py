"""Bounded synthetic research engine for Sleeper-style Lock-In scoring.

The module deliberately builds on :mod:`mock_draft.six_team`.  It adds only
the pieces that are specific to Lock-In research: game-level forecast and
realized worlds, a fixed weekly nine-player assignment, and an online
stopping policy.  A policy sees the current completed-game score and forecast
distributions for games that remain.  Future realized games are generated in
advance so counterfactual strategies share a world, but are never passed to a
policy before their game is complete.

This is a research fixture, not a live league configuration or a claim about
real-player projections.  The default output directory is ignored
``runtime/lock_in_research``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .six_team import (
    BENCH_SLOTS,
    DRAFT_ROUNDS,
    LINEUP_DAYS,
    MANDATORY_NATIVE_POSITIONS,
    OPPONENT_PROFILES,
    PICKS_PER_DRAFT,
    ROSTER_SIZE,
    SCORING_WEIGHTS,
    STARTING_SLOTS,
    SixTeamConfig,
    SixTeamError,
    SixTeamPick,
    SixTeamPlayer,
    SixTeamDraftResult,
    _custom_points,
    _deterministic_noise,
    _eligible_for_slot,
    _lineup_aware_score,
    _max_assignable_slots,
    _opponent_score,
    _shape_feasible,
    check_roster_shape,
    default_opponent_profiles,
    generate_synthetic_players,
    score_stats,
    simulate_draft,
)


LOCK_IN_STRATEGIES: tuple[str, ...] = (
    "custom_per_game",
    "availability",
    "legacy_daily_lineup_coverage",
    "replacement_aware",
    "lock_in_value",
    "market_aware_timing",
)
# A short alias keeps callers familiar with six_team.py while the report uses
# the explicit name requested for the legacy baseline.
STRATEGY_ALIASES = {"lineup_aware": "legacy_daily_lineup_coverage", "market": "market_aware_timing", "market_static": "market_aware_timing"}
POLICY_RULES: tuple[str, ...] = ("always_first", "mean_threshold", "forecast_dp")
STRATEGY_NAMES = LOCK_IN_STRATEGIES


def _stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _normal_expected_max(mean: float, deviation: float, floor: float) -> float:
    """E[max(X, floor)] for a normal forecast, with a stable zero-sd case."""

    deviation = max(0.0, float(deviation))
    if deviation <= 1e-12:
        return max(float(mean), float(floor))
    z = (float(mean) - float(floor)) / deviation
    phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    return float(floor) + deviation * phi + (float(mean) - float(floor)) * _normal_cdf(z)


@dataclass(frozen=True)
class GameForecast:
    player_id: str
    week: int
    game_index: int
    mean: float
    stddev: float
    availability_probability: float = 1.0

    def __post_init__(self) -> None:
        if not self.player_id:
            raise SixTeamError("game forecast requires player_id")
        if self.week < 0 or self.game_index < 0:
            raise SixTeamError("week and game_index must be non-negative")
        if self.stddev < 0:
            raise SixTeamError("forecast stddev cannot be negative")
        if not 0.0 <= self.availability_probability <= 1.0:
            raise SixTeamError("availability_probability must be between 0 and 1")


@dataclass(frozen=True)
class GameOutcome:
    player_id: str
    week: int
    game_index: int
    score: float
    available: bool = True
    stats: Mapping[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.stats is None:
            object.__setattr__(self, "stats", {})
        object.__setattr__(self, "score", float(self.score))


@dataclass(frozen=True)
class PlayerWeekGames:
    forecasts: tuple[GameForecast, ...]
    outcomes: tuple[GameOutcome, ...]

    def __post_init__(self) -> None:
        if len(self.forecasts) != len(self.outcomes):
            raise SixTeamError("forecast and outcome game counts must match")
        for index, (forecast, outcome) in enumerate(zip(self.forecasts, self.outcomes)):
            if forecast.game_index != index or outcome.game_index != index:
                raise SixTeamError("games must be ordered by game_index")
            if forecast.player_id != outcome.player_id or forecast.week != outcome.week:
                raise SixTeamError("forecast/outcome identity mismatch")


@dataclass(frozen=True)
class LockInWorld:
    world_id: str
    pool_seed: int
    scenario: str
    weeks: int
    games: Mapping[tuple[str, int], PlayerWeekGames]
    forecast_bias: float = 0.0
    forecast_variance_error: float = 0.0
    injury_shift: float = 0.0

    def player_week(self, player_id: str, week: int) -> PlayerWeekGames:
        return self.games.get((player_id, week), PlayerWeekGames((), ()))


@dataclass(frozen=True)
class WeeklyAssignment:
    slots: Mapping[str, str]
    bench: tuple[str, ...]

    @property
    def starter_ids(self) -> tuple[str, ...]:
        return tuple(self.slots.values())


@dataclass(frozen=True)
class PlayerLockResult:
    player_id: str
    slot: str | None
    score: float
    locked: bool
    locked_game_index: int | None
    observed_games: int
    zero_fallback: bool = False


@dataclass(frozen=True)
class LockInWeekResult:
    week: int
    weekly_points: float
    assignment: WeeklyAssignment
    player_results: tuple[PlayerLockResult, ...]
    hindsight_upper_bound: float

    @property
    def locked_players(self) -> int:
        return sum(result.locked for result in self.player_results)

    @property
    def locked_starter_slots(self) -> int:
        return self.locked_players

    @property
    def unfilled_zero_count(self) -> int:
        return sum(result.zero_fallback for result in self.player_results)


@dataclass(frozen=True)
class DraftResearchContext:
    world: LockInWorld
    config: SixTeamConfig
    policy_rule: str = "forecast_dp"
    lock_values: Mapping[str, float] | None = None
    per_game_values: Mapping[str, float] | None = None
    available_values: Mapping[str, float] | None = None


def _forecast_stopping_value(forecasts: Sequence[GameForecast]) -> float:
    """Expected value of a sequential stopping policy under forecast models."""

    value = 0.0
    ordered = tuple(forecasts)
    for position, forecast in reversed(list(enumerate(ordered))):
        # DNPs pass the opportunity without consuming it.  This mixture also
        # preserves negative forecast values: a final played negative can be
        # the correct lock, while a final DNP defaults to zero.
        played_value = float(forecast.mean) if position == len(ordered) - 1 else _normal_expected_max(forecast.mean, forecast.stddev, value)
        value = forecast.availability_probability * played_value + (1.0 - forecast.availability_probability) * value
    return value


def _forecast_mean_value(forecasts: Sequence[GameForecast]) -> float:
    value = 0.0
    for forecast in reversed(tuple(forecasts)):
        value = forecast.availability_probability * forecast.mean + (1.0 - forecast.availability_probability) * value
    return value


def forecast_threshold(rule: str, remaining: Sequence[GameForecast]) -> float:
    """Return the dynamic threshold before the current game is locked."""

    if not remaining:
        return -math.inf
    if rule == "mean_threshold":
        return _forecast_mean_value(remaining)
    if rule == "forecast_dp":
        return _forecast_stopping_value(remaining)
    if rule == "always_first":
        return -math.inf
    raise SixTeamError(f"unknown lock policy {rule!r}; expected {POLICY_RULES}")


class LockInPolicyManager:
    """Shared online decision manager for all draft strategies."""

    def __init__(self, rule: str = "forecast_dp") -> None:
        if rule not in POLICY_RULES:
            raise SixTeamError(f"unknown lock policy {rule!r}; expected {POLICY_RULES}")
        self.rule = rule

    def decide(
        self,
        current_score: float,
        remaining_forecasts: Sequence[GameForecast],
        *,
        game_index: int,
        games_remaining: int | None = None,
    ) -> bool:
        """Decide from current score plus future *forecasts* only.

        The method intentionally has no outcomes argument.  This shape makes
        it difficult for a caller to accidentally leak future realized games.
        """

        if games_remaining is not None and games_remaining != len(remaining_forecasts):
            raise SixTeamError("games_remaining must equal remaining forecast count")
        if not remaining_forecasts:
            return True
        if self.rule == "always_first":
            # "First" means first completed/played opportunity.  The caller
            # skips nonfinal DNPs before invoking decide().
            return True
        return float(current_score) >= forecast_threshold(self.rule, remaining_forecasts)

    def run_player_week(
        self,
        player_id: str,
        slot: str | None,
        games: PlayerWeekGames,
    ) -> PlayerLockResult:
        locked = False
        observed = 0
        for index, outcome in enumerate(games.outcomes):
            observed += 1
            # A nonfinal DNP advances the opportunity without presenting a
            # lockable zero.  A played zero is a real, legal observation.
            if not outcome.available:
                if index < len(games.outcomes) - 1:
                    continue
                break
            # This is the only realized value presented to decide().
            current_score = float(outcome.score)
            remaining = games.forecasts[index + 1 :]
            if self.decide(current_score, remaining, game_index=index, games_remaining=len(remaining)):
                return PlayerLockResult(player_id, slot, current_score, True, index, observed)
        # A final scheduled DNP has no score to lock.  The explicit zero is
        # the platform default and never falls back to a previous score.
        return PlayerLockResult(player_id, slot, 0.0, False, None, observed, zero_fallback=True)


def _assignment_for_roster(
    roster: Sequence[SixTeamPlayer],
    *,
    player_values: Mapping[str, float],
    config: SixTeamConfig,
) -> WeeklyAssignment:
    """Choose a fixed maximum-weight legal nine-player basis once per week."""

    if not roster or len(roster) < len(config.starting_slots):
        raise SixTeamError("roster cannot fill fixed weekly starter slots")
    selected: list[SixTeamPlayer] = []
    rank = 0
    for player in sorted(roster, key=lambda item: (-player_values.get(item.player_id, 0.0), item.player_id)):
        trial = selected + [player]
        trial_rank = _max_assignable_slots(trial, config.starting_slots)
        if trial_rank > rank:
            selected.append(player)
            rank = trial_rank
        if rank == len(config.starting_slots):
            break
    if rank < len(config.starting_slots):
        raise SixTeamError("fixed weekly assignment has no legal nine-player matching")
    selected_ids = {player.player_id for player in selected}
    slot_order = sorted(config.starting_slots, key=lambda slot: sum(_eligible_for_slot(p, slot) for p in selected))
    assigned: dict[str, str] = {}
    used: set[str] = set()

    def assign(position: int) -> bool:
        if position == len(slot_order):
            return True
        slot = slot_order[position]
        occurrence = sum(key.startswith(slot) for key in assigned)
        key = f"{slot}{occurrence + 1}" if config.starting_slots.count(slot) > 1 else slot
        for player in sorted(selected, key=lambda item: item.player_id):
            if player.player_id in used or not _eligible_for_slot(player, slot):
                continue
            used.add(player.player_id)
            assigned[key] = player.player_id
            if assign(position + 1):
                return True
            used.remove(player.player_id)
            del assigned[key]
        return False

    if not assign(0):
        raise SixTeamError("could not reconstruct fixed weekly assignment")
    bench = tuple(player.player_id for player in roster if player.player_id not in selected_ids)
    # Return slots in the canonical order, while retaining duplicate UTIL keys.
    ordered = {key: assigned[key] for key in assigned}
    return WeeklyAssignment(ordered, bench)


def assign_weekly_starters(
    roster: Sequence[SixTeamPlayer],
    world: LockInWorld,
    *,
    week: int,
    config: SixTeamConfig | None = None,
) -> WeeklyAssignment:
    cfg = config or SixTeamConfig()
    values: dict[str, float] = {}
    for player in roster:
        games = world.player_week(player.player_id, week).forecasts
        values[player.player_id] = _forecast_stopping_value(games)
    return _assignment_for_roster(roster, player_values=values, config=cfg)


def evaluate_lock_in_week(
    roster: Sequence[SixTeamPlayer],
    world: LockInWorld,
    *,
    week: int,
    policy_manager: LockInPolicyManager,
    config: SixTeamConfig | None = None,
) -> LockInWeekResult:
    """Assign nine players before games, then process each player's games online."""

    cfg = config or SixTeamConfig()
    legal, errors = check_roster_shape(roster, config=cfg)
    if not legal:
        raise SixTeamError("; ".join(errors))
    assignment = assign_weekly_starters(roster, world, week=week, config=cfg)
    slot_for_player = {player_id: slot for slot, player_id in assignment.slots.items()}
    results: list[PlayerLockResult] = []
    hindsight = 0.0
    for player_id in assignment.starter_ids:
        games = world.player_week(player_id, week)
        result = policy_manager.run_player_week(player_id, slot_for_player[player_id], games)
        results.append(result)
        outcomes = [outcome.score if outcome.available else 0.0 for outcome in games.outcomes]
        hindsight += max(outcomes, default=0.0)
    # Bench players do not participate.  In particular, do not sum all bench
    # games into a legacy "benched points" metric.
    return LockInWeekResult(week, sum(item.score for item in results), assignment, tuple(results), hindsight)


def _sample_stats(base: Mapping[str, float], rng: random.Random, *, injury_shift: float) -> dict[str, float]:
    """Sample primitive game stats and derive inclusive bonus events."""

    stats: dict[str, float] = {}
    for key in ("PTS", "REB", "AST", "STL", "BLK", "TO"):
        mean = max(0.0, float(base.get(key, 0.0)))
        spread = max(0.25, abs(mean) * (0.12 + 0.04 * injury_shift))
        value = rng.gauss(mean, spread)
        stats[key] = float(max(0, round(value)))
    # Three-pointers cannot exceed one made three per three points scored.
    three_mean = max(0.0, float(base.get("3PM", 0.0)))
    three_draw = round(rng.gauss(three_mean, max(0.25, three_mean * 0.20)))
    stats["3PM"] = float(min(max(0, three_draw), int(stats["PTS"] // 3)))
    # Technical and flagrant fouls are rare Bernoulli events, rather than
    # fractional Gaussian rates.  Their base values are forecast rates.
    stats["TF"] = 1.0 if rng.random() < min(1.0, max(0.0, float(base.get("TF", 0.0)))) else 0.0
    stats["FLAGRANT_FOUL"] = 1.0 if rng.random() < min(1.0, max(0.0, float(base.get("FLAGRANT_FOUL", 0.0)))) else 0.0
    # A coherent sampled stat line derives event bonuses from the primitive
    # values.  DD/TD and 40+/50+ are therefore inclusive rather than independent
    # random rates, and score_stats applies the configured stacking convention.
    tens = int(stats["PTS"] >= 10) + int(stats["REB"] >= 10) + int(stats["AST"] >= 10) + int(stats["STL"] >= 10) + int(stats["BLK"] >= 10)
    stats["TD"] = float(tens >= 3)
    stats["DD"] = float(tens >= 2)
    stats["POINTS_40_PLUS"] = float(stats["PTS"] >= 40)
    stats["POINTS_50_PLUS"] = float(stats["PTS"] >= 50)
    return stats


def generate_lock_in_world(
    players: Sequence[SixTeamPlayer],
    *,
    pool_seed: int,
    world_index: int = 0,
    weeks: int = 8,
    scenario: str = "nominal",
    forecast_bias: float = 0.0,
    forecast_variance_error: float = 0.0,
    injury_shift: float = 0.0,
) -> LockInWorld:
    """Generate a stable pool/game/world fixture with separate forecasts/outcomes."""

    if weeks <= 0:
        raise SixTeamError("weeks must be positive")
    games: dict[tuple[str, int], PlayerWeekGames] = {}
    scenario_injury = max(0.0, injury_shift)
    for player in players:
        # Forecast the player distribution once from an independent, larger
        # development sample. Reusing it across games avoids treating Monte
        # Carlo estimator noise as a new forecast for each scheduled game.
        forecast_rng = random.Random(_stable_seed("forecast-model", pool_seed, world_index, player.player_id))
        forecast_scores = [score_stats(_sample_stats(player.stats, forecast_rng, injury_shift=scenario_injury)) for _ in range(256)]
        conditional_mean = statistics.fmean(forecast_scores)
        conditional_sd = statistics.stdev(forecast_scores)
        for week in range(weeks):
            schedule_rng = random.Random(_stable_seed("schedule", pool_seed, world_index, player.player_id, week))
            game_count = schedule_rng.randint(1, 4)
            forecasts: list[GameForecast] = []
            outcomes: list[GameOutcome] = []
            for game_index in range(game_count):
                game_seed = _stable_seed("game", pool_seed, world_index, player.player_id, week, game_index)
                outcome_rng = random.Random(_stable_seed("outcome", game_seed))
                availability_probability = max(0.25, min(0.995, player.projected_availability - scenario_injury * 0.18))
                # Estimate the forecast distribution from a separate seeded
                # Monte Carlo sample of the same primitive stat generator.
                # This keeps nominal forecast and realized scoring families
                # coherent while preserving a clean forecast/outcome split.
                mean = conditional_mean * max(0.55, 1.0 + forecast_bias)
                stddev = max(0.5, conditional_sd * max(0.55, 1.0 + forecast_variance_error))
                forecast = GameForecast(player.player_id, week, game_index, mean, stddev, availability_probability)
                stats = _sample_stats(player.stats, outcome_rng, injury_shift=scenario_injury)
                available = outcome_rng.random() < availability_probability
                # DNP score is zero; available scores are derived from the
                # sampled primitive stats with both bonus pairs stacked.
                score = score_stats(stats) if available else 0.0
                forecasts.append(forecast)
                outcomes.append(GameOutcome(player.player_id, week, game_index, score, available, stats))
            games[(player.player_id, week)] = PlayerWeekGames(tuple(forecasts), tuple(outcomes))
    return LockInWorld(
        world_id=f"{scenario}-{pool_seed}-{world_index}",
        pool_seed=pool_seed,
        scenario=scenario,
        weeks=weeks,
        games=games,
        forecast_bias=forecast_bias,
        forecast_variance_error=forecast_variance_error,
        injury_shift=injury_shift,
    )


def realized_player_score(outcome: GameOutcome) -> float:
    """Score an outcome from its stats, retaining an audit check for fixtures."""

    if not outcome.available:
        return 0.0
    return score_stats(outcome.stats)


def hindsight_weekly_upper_bound(
    roster: Sequence[SixTeamPlayer],
    world: LockInWorld,
    *,
    week: int,
    config: SixTeamConfig | None = None,
) -> float:
    """Report-only upper bound; never passed to draft or online policy logic."""

    assignment = assign_weekly_starters(roster, world, week=week, config=config)
    return sum(
        max((outcome.score if outcome.available else 0.0 for outcome in world.player_week(player_id, week).outcomes), default=0.0)
        for player_id in assignment.starter_ids
    )


def _canonical_strategy(strategy: str) -> str:
    return STRATEGY_ALIASES.get(strategy, strategy)


def estimate_lock_in_value(player: SixTeamPlayer, world: LockInWorld | None = None, *, weeks: int = 8) -> float:
    """Forecast expected sequential stopping reward used by draft ranking."""

    if world is None:
        # Draft-time callers without a generated world still receive a stable
        # approximation from the player's per-game forecast score.
        return _custom_points(player, SixTeamConfig()) * player.projected_availability
    values = []
    for week in range(min(weeks, world.weeks)):
        values.append(_forecast_stopping_value(world.player_week(player.player_id, week).forecasts))
    return statistics.fmean(values) if values else 0.0


def _next_own_pick(pick_number: int, seat: int, config: SixTeamConfig) -> int | None:
    for future_pick in range(pick_number + 1, config.picks + 1):
        round_number = (future_pick - 1) // config.total_teams + 1
        index = (future_pick - 1) % config.total_teams
        future_seat = index + 1 if round_number % 2 else config.total_teams - index
        if future_seat == seat:
            return future_pick
    return None


def _common_forecast_values(player: SixTeamPlayer, world: LockInWorld) -> tuple[float, float]:
    forecasts = [forecast for week in range(world.weeks) for forecast in world.player_week(player.player_id, week).forecasts]
    if not forecasts:
        mean = _custom_points(player, SixTeamConfig())
        return mean, mean * player.projected_availability
    return (statistics.fmean(item.mean for item in forecasts),
            statistics.fmean(item.mean * item.availability_probability for item in forecasts))


def draft_strategy_score(
    player: SixTeamPlayer,
    roster: Sequence[SixTeamPlayer],
    strategy: str,
    *,
    config: SixTeamConfig,
    context: DraftResearchContext | None = None,
    available: Mapping[str, SixTeamPlayer] | None = None,
    pick_number: int = 1,
    our_seat: int = 1,
) -> float:
    """Research score injection for the copied, bounded draft loop."""

    strategy = _canonical_strategy(strategy)
    custom = _custom_points(player, config)
    common, available_value = custom, custom * player.projected_availability
    if context:
        if context.per_game_values is not None and context.available_values is not None:
            common = context.per_game_values[player.player_id]
            available_value = context.available_values[player.player_id]
        else:
            common, available_value = _common_forecast_values(player, context.world)
    if strategy == "custom_per_game":
        return common
    if strategy == "availability":
        return available_value
    if strategy == "legacy_daily_lineup_coverage":
        # This is the old daily coverage heuristic, retained as the baseline
        # for a fixed-week Lock-In comparison.
        return available_value + _lineup_aware_score(player, roster, config) - custom * player.projected_availability
    if strategy == "replacement_aware":
        # Estimate the undrafted pool by removing the remaining league picks
        # in market order. This is a declared depletion assumption, not a
        # measured waiver pool or a transaction simulation.
        pool = [item for item in (available or {}).values() if item.player_id != player.player_id]
        lock_values = context.lock_values if context and context.lock_values is not None else {}
        def value(item: SixTeamPlayer) -> float:
            return lock_values[item.player_id] if item.player_id in lock_values else estimate_lock_in_value(item, context.world if context else None)
        market_order = sorted(pool, key=lambda item: ((item.market_rank + item.adp) / 2.0, item.player_id))
        undrafted = market_order[max(0, config.picks - pick_number):]
        floors = [max((value(item) for item in undrafted if position in item.eligibility), default=0.0)
                  for position in player.eligibility if position in MANDATORY_NATIVE_POSITIONS]
        replacement = min(floors, default=0.0)
        candidate_value = value(player)
        return candidate_value + 0.5 * (candidate_value - replacement)
    if strategy == "lock_in_value":
        if context and context.lock_values and player.player_id in context.lock_values:
            return context.lock_values[player.player_id]
        return estimate_lock_in_value(player, context.world if context else None)
    if strategy == "market_aware_timing":
        # Estimate the opportunity cost of waiting until the next own pick from
        # the currently available pool.  Opponents are assumed to remove the
        # best market-ranked alternatives during that interval.  This is an
        # explicit counterfactual depletion proxy, not observed market data.
        lock_values = context.lock_values if context and context.lock_values is not None else {}
        timing = lock_values[player.player_id] if player.player_id in lock_values else estimate_lock_in_value(player, context.world if context else None)
        pool = [item for item in (available or {}).values() if item.player_id != player.player_id]
        next_pick = _next_own_pick(pick_number, our_seat, config)
        if next_pick is None:
            return timing
        wait = max(0, (next_pick - pick_number - 1) if next_pick is not None else 0)
        market_order = sorted(pool, key=lambda item: ((item.market_rank + item.adp) / 2.0, item.player_id))
        depleted_ids = {item.player_id for item in market_order[:wait]}
        def future_value(item: SixTeamPlayer) -> float:
            return lock_values[item.player_id] if item.player_id in lock_values else estimate_lock_in_value(item, context.world if context else None)
        future_option = next((future_value(item)
                              for item in sorted(pool, key=lambda item: (-future_value(item), item.player_id))
                              if item.player_id not in depleted_ids
                              and _max_assignable_slots(tuple(roster) + (player, item), config.starting_slots)
                                  + config.roster_size - len(roster) - 2 >= len(config.starting_slots)), 0.0)
        return timing + future_option
    raise SixTeamError(f"unknown Lock-In draft strategy {strategy!r}; expected {LOCK_IN_STRATEGIES}")


def simulate_lock_in_draft(
    players: Sequence[SixTeamPlayer],
    *,
    strategy: str,
    our_seat: int,
    seed: int,
    context: DraftResearchContext | None = None,
    config: SixTeamConfig | None = None,
) -> SixTeamDraftResult:
    """Copy only the old bounded snake loop so score injection stays explicit."""

    cfg = config or SixTeamConfig()
    strategy = _canonical_strategy(strategy)
    if strategy not in LOCK_IN_STRATEGIES:
        raise SixTeamError(f"unknown Lock-In draft strategy {strategy!r}; expected {LOCK_IN_STRATEGIES}")
    if not 1 <= our_seat <= cfg.total_teams:
        raise SixTeamError("our_seat must be between 1 and 6")
    if len(players) < cfg.picks:
        raise SixTeamError(f"player pool must contain at least {cfg.picks} records")
    if len({player.player_id for player in players}) != len(players):
        raise SixTeamError("player IDs must be unique")
    if context is not None and context.lock_values is None:
        context = replace(context, lock_values={player.player_id: estimate_lock_in_value(player, context.world) for player in players})
    if context is not None and (context.per_game_values is None or context.available_values is None):
        values = {player.player_id: _common_forecast_values(player, context.world) for player in players}
        context = replace(context, per_game_values={key: pair[0] for key, pair in values.items()},
                          available_values={key: pair[1] for key, pair in values.items()})
    profiles = default_opponent_profiles(seed=seed)
    profile_by_seat: dict[int, Any] = {}
    profile_index = 0
    for seat in range(1, cfg.total_teams + 1):
        if seat != our_seat:
            profile_by_seat[seat] = profiles[profile_index]
            profile_index += 1
    available = {player.player_id: player for player in players}
    rosters: dict[int, list[SixTeamPlayer]] = {seat: [] for seat in range(1, cfg.total_teams + 1)}
    picks: list[SixTeamPick] = []
    for pick_number in range(1, cfg.picks + 1):
        round_number = (pick_number - 1) // cfg.total_teams + 1
        index = (pick_number - 1) % cfg.total_teams
        seat = index + 1 if round_number % 2 else cfg.total_teams - index
        roster = rosters[seat]
        if seat == our_seat:
            def ranking(player: SixTeamPlayer) -> float:
                return draft_strategy_score(player, roster, strategy, config=cfg, context=context, available=available, pick_number=pick_number, our_seat=seat) + _deterministic_noise(seed, pick_number, seat, player.player_id) * 0.000001
        else:
            profile = profile_by_seat[seat]
            def ranking(player: SixTeamPlayer) -> float:
                # Preserve opponent styles while giving their custom-production
                # component the same forecast source as our draft baselines.
                base = _opponent_score(player, roster, profile, cfg)
                if context and context.per_game_values:
                    common_mean = context.per_game_values[player.player_id]
                    base += profile.custom_weight * (common_mean - _custom_points(player, cfg))
                    if common_mean > 0 and context.available_values:
                        probability = context.available_values[player.player_id] / common_mean
                        base += profile.availability_weight * (probability - player.projected_availability) * 10.0
                return base + _deterministic_noise(seed, pick_number, seat, player.player_id) * profile.noise * max(abs(_custom_points(player, cfg)), 1.0)
        selected = next(
            (player for player in sorted(available.values(), key=lambda item: (ranking(item), -item.market_rank, item.player_id), reverse=True)
             if _shape_feasible(player, roster, available, rosters, cfg)),
            None,
        )
        if selected is None:
            raise SixTeamError(f"no legal candidate remains at pick {pick_number} for seat {seat}")
        del available[selected.player_id]
        roster.append(selected)
        picks.append(SixTeamPick(pick_number, round_number, seat, selected))
    result = SixTeamDraftResult(picks, rosters, strategy, our_seat, seed, profiles)
    result.validate(config=cfg)
    return result


def _evaluate_roster(
    roster: Sequence[SixTeamPlayer],
    world: LockInWorld,
    *,
    policy_rule: str,
    config: SixTeamConfig,
) -> dict[str, Any]:
    policy = LockInPolicyManager(policy_rule)
    weeks = [evaluate_lock_in_week(roster, world, week=week, policy_manager=policy, config=config) for week in range(world.weeks)]
    points = [week.weekly_points for week in weeks]
    upper = [week.hindsight_upper_bound for week in weeks]
    return {
        "weekly_points": statistics.fmean(points) if points else 0.0,
        "weekly_points_sd": statistics.stdev(points) if len(points) > 1 else 0.0,
        "hindsight_upperbound": statistics.fmean(upper) if upper else 0.0,
        "hindsight_gap": (statistics.fmean(upper) - statistics.fmean(points)) if points else 0.0,
        "locked_starter_slots": statistics.fmean(week.locked_starter_slots for week in weeks) if weeks else 0.0,
        "locked_players": statistics.fmean(week.locked_players for week in weeks) if weeks else 0.0,
        "lock_rate": (statistics.fmean(week.locked_starter_slots for week in weeks) / len(STARTING_SLOTS)) if weeks else 0.0,
        "unfilled_zero_count": statistics.fmean(week.unfilled_zero_count for week in weeks) if weeks else 0.0,
        "bench_points": 0.0,
        "bench_points_definition": "zero: fixed weekly nine-player assignment; bench games are never scored or summed",
        "weeks": weeks,
    }


def _win_fraction(points: float, opponent_points: Sequence[float]) -> float:
    if not opponent_points:
        return 0.0
    return statistics.fmean(1.0 if points > value else 0.5 if points == value else 0.0 for value in opponent_points)


def _scenario_params(scenario: str) -> dict[str, float]:
    if scenario == "nominal":
        return {"forecast_bias": 0.0, "forecast_variance_error": 0.0, "injury_shift": 0.0}
    if scenario == "injury_stress":
        return {"forecast_bias": -0.015, "forecast_variance_error": 0.04, "injury_shift": 1.0}
    if scenario == "forecast_shift":
        return {"forecast_bias": 0.07, "forecast_variance_error": 0.10, "injury_shift": 0.0}
    raise SixTeamError(f"unknown Lock-In scenario {scenario!r}")


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return float(statistics.fmean(values)) if values else 0.0


def _source_fingerprints(profile_path: Path | None = None) -> dict[str, str]:
    paths = [Path(__file__), Path(__file__).with_name("six_team.py")]
    if profile_path and profile_path.exists():
        paths.append(profile_path)
    fingerprints: dict[str, str] = {}
    for path in paths:
        fingerprints[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprints


def load_lock_in_profile(path: str | Path | None = None) -> tuple[dict[str, Any], str]:
    profile_path = Path(path) if path else Path(__file__).parents[1] / "profiles" / "six_team_lock_in.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    return profile, digest


def load_profile(path: str | Path | None = None) -> tuple[dict[str, Any], SixTeamConfig, str]:
    """Load and strictly validate the research profile consumed by this engine."""

    profile, digest = load_lock_in_profile(path)
    required_top = {"profile_id", "status", "mode", "daily_accumulation", "settings", "scoring", "strategies", "lock_policies"}
    missing_top = required_top - set(profile)
    if missing_top:
        raise SixTeamError(f"Lock-In profile missing fields: {sorted(missing_top)}")
    if profile["mode"] != "lock_in" or profile["daily_accumulation"] is not False:
        raise SixTeamError("Lock-In profile must set mode=lock_in and daily_accumulation=false")
    settings = profile.get("settings", {})
    required_settings = {
        "total_teams", "rounds", "picks", "starting_slots", "bench_slots", "roster_size", "ir_slots",
        "ir_transactions_modeled", "snake_draft", "keeper", "fixed_weekly_starter_assignment", "bench_swapping",
        "one_lock_per_player_per_week", "negative_scores_legal", "final_scheduled_dnp_defaults_zero",
        "double_double_includes_triple_double", "threshold_bonuses_stack",
    }
    missing_settings = required_settings - set(settings)
    if missing_settings:
        raise SixTeamError(f"Lock-In profile missing settings: {sorted(missing_settings)}")
    if set(settings) - required_settings:
        raise SixTeamError(f"Lock-In profile has unknown settings: {sorted(set(settings) - required_settings)}")
    if tuple(settings["starting_slots"]) != STARTING_SLOTS:
        raise SixTeamError("Lock-In profile starting slots do not match six_team.py")
    if int(settings["total_teams"]) != 6 or int(settings["rounds"]) != 14 or int(settings["picks"]) != 84:
        raise SixTeamError("Lock-In profile must describe six teams and fourteen rounds")
    if int(settings["bench_slots"]) != BENCH_SLOTS or int(settings["roster_size"]) != ROSTER_SIZE:
        raise SixTeamError("Lock-In profile roster shape does not match six_team.py")
    if int(settings["ir_slots"]) != 1 or settings["ir_transactions_modeled"] is not False:
        raise SixTeamError("Lock-In profile must record one IR slot with transactions unmodeled")
    if settings["double_double_includes_triple_double"] is not True or settings["threshold_bonuses_stack"] is not True:
        raise SixTeamError("Lock-In profile must stack DD/TD and 40+/50+ bonuses")
    if set(profile["scoring"]) != set(SCORING_WEIGHTS) or any(float(profile["scoring"][key]) != float(value) for key, value in SCORING_WEIGHTS.items()):
        raise SixTeamError("Lock-In profile scoring weights do not match six_team.py")
    if set(profile["lock_policies"]) != set(POLICY_RULES):
        raise SixTeamError("Lock-In profile lock policies do not match engine")
    if set(profile["strategies"]) != set(LOCK_IN_STRATEGIES):
        raise SixTeamError("Lock-In profile strategies do not match engine")
    return profile, SixTeamConfig(), digest


def _row_for_strategy(
    players: Sequence[SixTeamPlayer],
    world: LockInWorld,
    *,
    split: str,
    strategy: str,
    seat: int,
    pool_seed: int,
    policy_rule: str,
    config: SixTeamConfig,
    draft_result: SixTeamDraftResult | None = None,
) -> dict[str, Any]:
    context = DraftResearchContext(world, config, policy_rule)
    draft = draft_result or simulate_lock_in_draft(players, strategy=strategy, our_seat=seat, seed=_stable_seed("draft", pool_seed), context=context, config=config)
    roster_results = {
        roster_seat: _evaluate_roster(roster, world, policy_rule=policy_rule, config=config)
        for roster_seat, roster in draft.rosters.items()
    }
    ours = roster_results[seat]
    own_weeks = ours["weeks"]
    opponent_weekly_points = {
        str(opponent): [week.weekly_points for week in roster_results[opponent]["weeks"]]
        for opponent in roster_results if opponent != seat
    }
    opponent_fraction = _mean(
        _win_fraction(
            own_week.weekly_points,
            [roster_results[opponent]["weeks"][week].weekly_points for opponent in roster_results if opponent != seat],
        )
        for week, own_week in enumerate(own_weeks)
    )
    return {
        "split": split,
        "pool_seed": pool_seed,
        "strategy": _canonical_strategy(strategy),
        "our_seat": seat,
        "scenario": world.scenario,
        "policy_rule": policy_rule,
        "weeks": world.weeks,
        "weekly_realized_points": ours["weekly_points"],
        "weekly_points_sd": ours["weekly_points_sd"],
        "own_weekly_points": [week.weekly_points for week in own_weeks],
        "opponent_weekly_points": opponent_weekly_points,
        "weekly_opponent_pair_win_fraction": opponent_fraction,
        "hindsight_weekly_points": ours["hindsight_upperbound"],
        "hindsight_upperbound": ours["hindsight_upperbound"],
        "hindsight_gap": ours["hindsight_gap"],
        "locked_starter_slots": ours["locked_starter_slots"],
        "locked_players": ours["locked_players"],
        "lock_rate": ours["lock_rate"],
        "unfilled_zero_count": ours["unfilled_zero_count"],
        "bench_points": ours["bench_points"],
        "bench_points_definition": ours["bench_points_definition"],
        "draft_picks": len(draft.picks),
        "draft_unique_players": len({pick.player.player_id for pick in draft.picks}),
        "roster_player_ids": [player.player_id for player in draft.rosters[seat]],
        "forecast_bias": world.forecast_bias,
        "forecast_variance_error": world.forecast_variance_error,
        "injury_shift": world.injury_shift,
    }


def _summaries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (str(row["split"]), str(row["scenario"]), str(row["strategy"]), str(row["policy_rule"]))
        groups.setdefault(key, []).append(row)
    summaries: list[dict[str, Any]] = []
    for (split, scenario, strategy, policy_rule), items in sorted(groups.items()):
        pool_means: dict[int, float] = {}
        for row in items:
            pool_means.setdefault(int(row["pool_seed"]), 0.0)
            pool_means[int(row["pool_seed"])] += float(row["weekly_realized_points"])
        # The six seats are the unit of comparison within a balanced pool; the
        # pool-cluster mean exposes the independent world replication.
        for pool_seed in list(pool_means):
            pool_means[pool_seed] /= max(1, sum(int(row["pool_seed"]) == pool_seed for row in items))
        summaries.append({
            "split": split,
            "scenario": scenario,
            "strategy": strategy,
            "policy_rule": policy_rule,
            "rows": len(items),
            "pool_worlds": len(pool_means),
            "weekly_realized_points_mean": _mean(float(row["weekly_realized_points"]) for row in items),
            "weekly_opponent_pair_win_fraction_mean": _mean(float(row["weekly_opponent_pair_win_fraction"]) for row in items),
            "locked_starter_slots_mean": _mean(float(row["locked_starter_slots"]) for row in items),
            "lock_rate_mean": _mean(float(row["lock_rate"]) for row in items),
            "unfilled_zero_count_mean": _mean(float(row["unfilled_zero_count"]) for row in items),
            "hindsight_weekly_points_mean": _mean(float(row["hindsight_weekly_points"]) for row in items),
            "hindsight_gap_mean": _mean(float(row["hindsight_gap"]) for row in items),
            "pool_cluster_mean": _mean(pool_means.values()),
        })
    return summaries


def _select_strategy(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    candidates = [
        row for row in summaries
        if row["split"] == "development" and row["scenario"] == "nominal" and row["policy_rule"] == "forecast_dp"
    ]
    selected = max(candidates, key=lambda row: (float(row["weekly_opponent_pair_win_fraction_mean"]), float(row["weekly_realized_points_mean"]))) if candidates else None
    return {
        "development_selected_strategy": selected["strategy"] if selected else None,
        "development_selected_policy_rule": selected["policy_rule"] if selected else "forecast_dp",
        "selection_metric": "weekly_opponent_pair_win_fraction_mean, tie-break weekly_realized_points_mean",
        "selection_scope": "development nominal only; heldout nominal is an audit",
        "interpretation": "best within tested synthetic worlds, not a calibrated win probability or globally optimal policy",
    }


def _validate_research_matrix(
    rows: Sequence[Mapping[str, Any]],
    *,
    development_pool_seeds: Sequence[int],
    heldout_pool_seeds: Sequence[int],
    scenarios: Sequence[str],
    strategies: Sequence[str],
    policy_rules: Sequence[str],
) -> None:
    dev = {int(seed) for seed in development_pool_seeds}
    hold = {int(seed) for seed in heldout_pool_seeds}
    if len(dev) != len(tuple(development_pool_seeds)) or len(hold) != len(tuple(heldout_pool_seeds)):
        raise SixTeamError("pool seeds must be unique within each split")
    if dev & hold:
        raise SixTeamError("development and heldout pool seeds must be disjoint")
    if len(set(scenarios)) != len(tuple(scenarios)):
        raise SixTeamError("scenarios must be unique")
    if len(set(_canonical_strategy(strategy) for strategy in strategies)) != len(tuple(strategies)):
        raise SixTeamError("strategies must be unique after aliases")
    if len(set(policy_rules)) != len(tuple(policy_rules)):
        raise SixTeamError("policy rules must be unique")
    expected = {
        ("development" if seed in dev else "heldout", int(seed), scenario, _canonical_strategy(strategy), seat, rule)
        for seed in tuple(dev) + tuple(hold)
        for scenario in scenarios
        for strategy in strategies
        for seat in range(1, 7)
        for rule in policy_rules
    }
    observed = {
        (str(row["split"]), int(row["pool_seed"]), str(row["scenario"]), str(row["strategy"]), int(row["our_seat"]), str(row["policy_rule"]))
        for row in rows
    }
    if observed != expected or len(rows) != len(expected):
        raise SixTeamError(f"research matrix mismatch: expected {len(expected)} rows, observed {len(observed)}")
    if any(int(row["draft_picks"]) != PICKS_PER_DRAFT or int(row["draft_unique_players"]) != PICKS_PER_DRAFT for row in rows):
        raise SixTeamError("every matrix row must contain an 84-pick unique-player draft")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, *, metadata: Mapping[str, Any], summaries: Sequence[Mapping[str, Any]], selection: Mapping[str, Any]) -> None:
    lines = [
        "# Lock-In research results",
        "",
        "This bounded study uses synthetic game-level worlds and a fixed weekly assignment of nine roster players to the native PG/SG/G/SF/PF/F/C/UTIL/UTIL slots. The assignment is made before games and there is no bench swapping.",
        "",
        "Each player may lock at most one completed game score per week. A nonfinal DNP advances to the next opportunity; a final scheduled DNP with no prior lock contributes an explicit zero. Negative played scores are legal and are never replaced by a best prior score.",
        "",
        f"Primary policy: `{metadata['primary_policy_rule']}` shared across all draft strategies. Secondary rules: `{', '.join(metadata['secondary_policy_rules'])}`. Development selection uses nominal worlds only; heldout nominal rows are an audit.",
        "",
        "The hindsight upper bound is report-only: it sums the best realized game for each fixed starter. It is never supplied to a draft scorer or online policy. `bench_points` is zero by definition because fixed weekly bench players are never scored; their games are not summed.",
        "",
        "Forecast bias, forecast variance error, and injury stress are conditional synthetic sensitivity regimes. They describe how this fixture responds to those assumptions and are not calibrated injury rates, market timing, or real-player projections.",
        "",
        "## Aggregated results",
        "",
        "| split | scenario | strategy | lock rule | weekly points | paired win fraction | locked slots | unfilled zeros | hindsight upper bound |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append("| {split} | {scenario} | {strategy} | {policy_rule} | {points:.2f} | {win:.3f} | {locked:.2f} | {zeros:.2f} | {upper:.2f} |".format(
            split=row["split"], scenario=row["scenario"], strategy=row["strategy"], policy_rule=row["policy_rule"],
            points=float(row["weekly_realized_points_mean"]), win=float(row["weekly_opponent_pair_win_fraction_mean"]),
            locked=float(row["locked_starter_slots_mean"]), zeros=float(row["unfilled_zero_count_mean"]), upper=float(row["hindsight_weekly_points_mean"]),
        ))
    lines.extend([
        "",
        f"Development selected `{selection.get('development_selected_strategy')}` under `{selection.get('development_selected_policy_rule')}`. The selection is a within-fixture comparison and should be re-evaluated if forecast scoring bias or coverage assumptions change.",
        "",
        "Raw balanced world rows are in `rows.csv`; `pool_seed` identifies the independent world cluster and `our_seat` gives all six seat replications. `summary.csv` groups the rows by split, scenario, strategy, and lock rule.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    *,
    development_pool_seeds: Sequence[int] = (101, 102, 103, 104),
    heldout_pool_seeds: Sequence[int] = tuple(range(201, 209)),
    player_count: int = 96,
    weeks: int = 8,
    scenarios: Sequence[str] = ("nominal", "injury_stress", "forecast_shift"),
    strategies: Sequence[str] = LOCK_IN_STRATEGIES,
    policy_rules: Sequence[str] = POLICY_RULES,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the bounded development/heldout crossed matrix."""

    profile, cfg, profile_digest = load_profile()
    if not development_pool_seeds or not heldout_pool_seeds:
        raise SixTeamError("development and heldout pool sets must be nonempty")
    if player_count < PICKS_PER_DRAFT:
        raise SixTeamError(f"player_count must be at least {PICKS_PER_DRAFT}")
    for rule in policy_rules:
        if rule not in POLICY_RULES:
            raise SixTeamError(f"unknown lock policy {rule!r}")
    for strategy in strategies:
        if _canonical_strategy(strategy) not in LOCK_IN_STRATEGIES:
            raise SixTeamError(f"unknown Lock-In strategy {strategy!r}")
    for scenario in scenarios:
        _scenario_params(scenario)
    rows: list[dict[str, Any]] = []
    pool_specs = [("development", int(seed)) for seed in development_pool_seeds] + [("heldout", int(seed)) for seed in heldout_pool_seeds]
    for split, pool_seed in pool_specs:
        players = generate_synthetic_players(player_count=player_count, seed=pool_seed)
        for scenario in scenarios:
            params = _scenario_params(scenario)
            world = generate_lock_in_world(players, pool_seed=pool_seed, world_index=0, weeks=weeks, scenario=scenario, **params)
            for strategy in strategies:
                for seat in range(1, cfg.total_teams + 1):
                    # Draft once per strategy/seat/world.  Crossed lock rules
                    # then evaluate the identical fixed roster, isolating the
                    # stopping policy from draft strategy effects.
                    draft = simulate_lock_in_draft(
                        players,
                        strategy=strategy,
                        our_seat=seat,
                        seed=_stable_seed("draft", pool_seed),
                        context=DraftResearchContext(world, cfg, "forecast_dp"),
                        config=cfg,
                    )
                    for rule in policy_rules:
                        rows.append(_row_for_strategy(players, world, split=split, strategy=strategy, seat=seat, pool_seed=pool_seed, policy_rule=rule, config=cfg, draft_result=draft))
    _validate_research_matrix(
        rows,
        development_pool_seeds=development_pool_seeds,
        heldout_pool_seeds=heldout_pool_seeds,
        scenarios=scenarios,
        strategies=strategies,
        policy_rules=policy_rules,
    )
    summaries = _summaries(rows)
    selection = _select_strategy(summaries)
    metadata = {
        "study": "sleeper_lock_in_research",
        "engine": "mock_draft.lock_in",
        "model": "synthetic box-score worlds with normal forecast approximation and explicit availability",
        "agent_configuration": "GPT-5.6 Luna at XHIGH for assessment, implementation and independent review",
        "status": "research_only_advisory",
        "teams": 6,
        "rounds": 14,
        "picks_per_draft": PICKS_PER_DRAFT,
        "roster_size": ROSTER_SIZE,
        "starting_slots": list(STARTING_SLOTS),
        "bench_slots": BENCH_SLOTS,
        "ir_slots": 1,
        "ir_transactions_modeled": False,
        "weeks": weeks,
        "development_pool_seeds": list(development_pool_seeds),
        "heldout_pool_seeds": list(heldout_pool_seeds),
        "pool_world_count": len(pool_specs),
        "scenario_count": len(scenarios),
        "strategy_count": len(strategies),
        "policy_rule_count": len(policy_rules),
        "seats_per_world": 6,
        "rows": len(rows),
        "unique_drafts": len(rows) // len(policy_rules),
        "player_count": player_count,
        "common_randomness_across_scenarios": True,
        "scoring_weights": dict(SCORING_WEIGHTS),
        "double_double_includes_triple_double": True,
        "threshold_bonuses_stack": True,
        "assignment": "fixed weekly nine starter assignment before games; no bench swapping",
        "primary_policy_rule": "forecast_dp",
        "secondary_policy_rules": [rule for rule in policy_rules if rule != "forecast_dp"],
        "bench_points_definition": "zero: bench games are never scored or summed under fixed assignment",
        "replacement_aware_definition": "forecast Lock-In value plus 0.5 times its excess over the best native-position undrafted alternative under market-order depletion",
        "market_aware_timing_definition": "counterfactual next-own-pick depletion using market rank and forecast lock-in option value; no live market observations",
        "forecast_outcomes_pregenerated": True,
        "draft_forecast_source": "common conditional game-score means; availability and stopping policies transform the same forecast distribution",
        "forecast_model_samples_per_player": 256,
        "stopping_distribution": "normal approximation fitted to independent synthetic score samples, with explicit DNP probability mixture",
        "policy_future_outcome_access": False,
        "profile_digest": profile_digest,
        "source_fingerprints": _source_fingerprints(Path(__file__).parents[1] / "profiles" / "six_team_lock_in.json"),
        "policy_selection": selection,
    }
    result = {"rows": rows, "summary": summaries, "metadata": metadata, "selection": selection, "profile": profile}
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        (destination / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
        (destination / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (destination / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
        (destination / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
        (destination / "source_fingerprints.json").write_text(json.dumps(metadata["source_fingerprints"], indent=2), encoding="utf-8")
        _write_csv(destination / "rows.csv", rows)
        _write_csv(destination / "summary.csv", summaries)
        _write_report(destination / "REPORT.md", metadata=metadata, summaries=summaries, selection=selection)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(Path(__file__).parents[1] / "runtime" / "lock_in_research"))
    parser.add_argument("--player-count", type=int, default=96)
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--quick", action="store_true", help="one development and one heldout pool for smoke testing")
    args = parser.parse_args(argv)
    kwargs: dict[str, Any] = {"player_count": args.player_count, "weeks": args.weeks, "output_dir": args.output_dir}
    if args.quick:
        kwargs.update(development_pool_seeds=(101,), heldout_pool_seeds=(201,), scenarios=("nominal",))
    result = run_research(**kwargs)
    print(json.dumps(result["metadata"], indent=2))
    return 0


__all__ = [
    "GameForecast", "GameOutcome", "LockInWorld", "LockInPolicyManager", "LockInWeekResult",
    "PlayerLockResult", "PlayerWeekGames", "WeeklyAssignment", "DraftResearchContext", "LOCK_IN_STRATEGIES", "STRATEGY_NAMES", "POLICY_RULES",
    "assign_weekly_starters", "draft_strategy_score", "estimate_lock_in_value", "evaluate_lock_in_week",
    "forecast_threshold", "generate_lock_in_world", "hindsight_weekly_upper_bound", "load_lock_in_profile",
    "load_profile", "realized_player_score", "run_research", "simulate_lock_in_draft",
]


if __name__ == "__main__":
    raise SystemExit(main())


