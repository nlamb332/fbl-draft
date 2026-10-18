"""Bounded, research-only six-team points-draft engine.

This module is deliberately isolated from :mod:`mock_draft.fixture` and from
the live draft advisor.  It models a six-team, fourteen-round (84-pick) room
with native NBA position eligibility, nine starters, and five bench spots.
The default output directory is ignored ``runtime/`` so generated artifacts do
not become part of the minimal download archive.
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
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TOTAL_TEAMS = 6
DRAFT_ROUNDS = 14
STARTING_SLOTS: tuple[str, ...] = (
    "PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL",
)
BENCH_SLOTS = 5
ROSTER_SIZE = len(STARTING_SLOTS) + BENCH_SLOTS
PICKS_PER_DRAFT = TOTAL_TEAMS * DRAFT_ROUNDS
LINEUP_DAYS = 7
BASELINE_STRATEGY = "custom_per_game"
STRATEGY_NAMES: tuple[str, ...] = (
    "custom_per_game",
    "availability",
    "market",
    "lineup_aware",
    "big_preference",
)
NATIVE_POSITIONS = frozenset({"PG", "SG", "SF", "PF", "C", "G", "F"})
MANDATORY_NATIVE_POSITIONS = ("PG", "SG", "SF", "PF", "C")
ROSTER_CATEGORY_REQUIREMENTS: Mapping[str, int] = {
    "PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1,
    "GUARD": 3, "FORWARD": 3,
}

SCORING_WEIGHTS: Mapping[str, float] = {
    "PTS": 0.5,
    "REB": 1.0,
    "AST": 1.0,
    "STL": 2.0,
    "BLK": 2.0,
    "TO": -1.0,
    "DD": 1.0,
    "TD": 2.0,
    "TF": -2.0,
    "FLAGRANT_FOUL": -2.0,
    "3PM": 0.5,
    "POINTS_40_PLUS": 2.0,
    "POINTS_50_PLUS": 2.0,
}


class SixTeamError(ValueError):
    """Malformed research profile, player input, or impossible draft state."""


@dataclass(frozen=True)
class SixTeamConfig:
    """Explicit research configuration; never used as live LeagueConfig."""

    total_teams: int = TOTAL_TEAMS
    rounds: int = DRAFT_ROUNDS
    starting_slots: tuple[str, ...] = STARTING_SLOTS
    bench_slots: int = BENCH_SLOTS
    double_double_includes_triple_double: bool = True
    threshold_bonuses_stack: bool = True

    def __post_init__(self) -> None:
        if self.total_teams != TOTAL_TEAMS or self.rounds != DRAFT_ROUNDS:
            raise SixTeamError("six-team research config must be exactly 6 teams and 14 rounds")
        if tuple(self.starting_slots) != STARTING_SLOTS:
            raise SixTeamError(f"starting slots must be exactly {STARTING_SLOTS}")
        if self.bench_slots != BENCH_SLOTS:
            raise SixTeamError(f"roster must have exactly {BENCH_SLOTS} bench spots")

    @property
    def picks(self) -> int:
        return self.total_teams * self.rounds

    @property
    def roster_size(self) -> int:
        return len(self.starting_slots) + self.bench_slots


def _normalise_positions(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = values.replace("|", ",").replace("/", ",").split(",")
    positions = tuple(dict.fromkeys(str(value).strip().upper() for value in (values or ()) if str(value).strip()))
    if not positions or set(positions) - NATIVE_POSITIONS:
        raise SixTeamError(f"eligibility must use native positions {sorted(NATIVE_POSITIONS)}")
    return positions


def _normalise_stats(values: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in values.items():
        name = str(key).upper().strip()
        if name:
            try:
                result[name] = float(value)
            except (TypeError, ValueError) as exc:
                raise SixTeamError(f"stat {name!r} must be numeric") from exc
    return result


@dataclass(frozen=True)
class SixTeamPlayer:
    player_id: str
    name: str
    eligibility: tuple[str, ...]
    stats: Mapping[str, float]
    projected_availability: float
    market_rank: float
    adp: float
    upside: float = 0.0
    schedule_group: str = ""
    source_label: str = "synthetic_six_team_research"

    def __post_init__(self) -> None:
        if not self.player_id or not self.name:
            raise SixTeamError("player_id and name are required")
        object.__setattr__(self, "eligibility", _normalise_positions(self.eligibility))
        object.__setattr__(self, "stats", _normalise_stats(self.stats))
        availability = float(self.projected_availability)
        if not 0.0 <= availability <= 1.0:
            raise SixTeamError("projected_availability must be between 0 and 1")
        object.__setattr__(self, "projected_availability", availability)
        object.__setattr__(self, "market_rank", float(self.market_rank))
        object.__setattr__(self, "adp", float(self.adp))
        object.__setattr__(self, "upside", float(self.upside))


def score_stats(
    stats: Mapping[str, Any],
    *,
    double_double_includes_triple_double: bool = True,
    threshold_bonuses_stack: bool = True,
) -> float:
    """Score expected per-game primitives and synthetic event rates.

    ``DD``, ``TD``, ``POINTS_40_PLUS``, and ``POINTS_50_PLUS`` are rates in
    generated forecast/realized records.  The default research assumption
    stacks both bonus pairs; switches remain explicit for sensitivity checks.
    """
    values = {str(key).upper(): float(value) for key, value in stats.items()}
    total = sum(weight * values.get(name, 0.0) for name, weight in SCORING_WEIGHTS.items())
    dd = values.get("DD", 0.0)
    td = values.get("TD", 0.0)
    if not double_double_includes_triple_double:
        total -= SCORING_WEIGHTS["DD"] * min(dd, td)
    if not threshold_bonuses_stack:
        total -= SCORING_WEIGHTS["POINTS_50_PLUS"] * min(
            values.get("POINTS_40_PLUS", 0.0), values.get("POINTS_50_PLUS", 0.0)
        )
    return total


def _eligible_for_slot(player: SixTeamPlayer, slot: str) -> bool:
    positions = set(player.eligibility)
    if slot == "PG":
        return "PG" in positions
    if slot == "SG":
        return "SG" in positions
    if slot == "G":
        return bool(positions & {"PG", "SG", "G"})
    if slot == "SF":
        return "SF" in positions
    if slot == "PF":
        return "PF" in positions
    if slot == "F":
        return bool(positions & {"SF", "PF", "F"})
    if slot == "C":
        return "C" in positions
    if slot == "UTIL":
        return True
    raise SixTeamError(f"unknown lineup slot {slot!r}")


def _eligible_for_category(player: SixTeamPlayer, category: str) -> bool:
    if category == "GUARD":
        return bool(set(player.eligibility) & {"PG", "SG", "G"})
    if category == "FORWARD":
        return bool(set(player.eligibility) & {"SF", "PF", "F"})
    return category in player.eligibility


def check_roster_shape(roster: Sequence[SixTeamPlayer], *, config: SixTeamConfig | None = None) -> tuple[bool, tuple[str, ...]]:
    cfg = config or SixTeamConfig()
    errors: list[str] = []
    if len(roster) != cfg.roster_size:
        errors.append(f"roster has {len(roster)} players; expected {cfg.roster_size}")
    if not _can_assign_slots(roster, cfg.starting_slots):
        errors.append(f"cannot assign starting slots {cfg.starting_slots}")
    return not errors, tuple(errors)


def _can_assign_slots(players: Sequence[SixTeamPlayer], slots: Sequence[str]) -> bool:
    if len(players) < len(slots):
        return False

    @lru_cache(maxsize=None)
    def visit(slot_index: int, used_mask: int) -> bool:
        if slot_index == len(slots):
            return True
        for index, player in enumerate(players):
            if used_mask & (1 << index):
                continue
            if _eligible_for_slot(player, slots[slot_index]) and visit(slot_index + 1, used_mask | (1 << index)):
                return True
        return False

    return visit(0, 0)


def _max_assignable_slots(players: Sequence[SixTeamPlayer], slots: Sequence[str]) -> int:
    """Return maximum bipartite matching cardinality in O(slots * players)."""
    matched_player: list[int] = [-1] * len(players)

    def augment(slot_index: int, seen: set[int]) -> bool:
        slot = slots[slot_index]
        for player_index, player in enumerate(players):
            if player_index in seen or not _eligible_for_slot(player, slot):
                continue
            seen.add(player_index)
            if matched_player[player_index] == -1 or augment(matched_player[player_index], seen):
                matched_player[player_index] = slot_index
                return True
        return False

    return sum(augment(slot_index, set()) for slot_index in range(len(slots)))


@dataclass(frozen=True)
class OpponentProfile:
    name: str
    market_weight: float
    custom_weight: float
    availability_weight: float = 0.0
    upside_weight: float = 0.0
    big_weight: float = 0.0
    noise: float = 0.0


OPPONENT_PROFILES: tuple[OpponentProfile, ...] = (
    OpponentProfile("market_anchor", 1.00, 0.15, noise=0.04),
    OpponentProfile("market_noisy", 0.82, 0.12, noise=0.30),
    OpponentProfile("custom_points", 0.10, 1.00, noise=0.07),
    OpponentProfile("custom_aggressive", 0.05, 1.15, noise=0.22),
    OpponentProfile("availability", 0.12, 0.62, availability_weight=1.10, noise=0.07),
    OpponentProfile("upside", 0.10, 0.66, upside_weight=1.20, noise=0.16),
    OpponentProfile("big_heavy", 0.10, 0.70, big_weight=1.10, noise=0.12),
    OpponentProfile("balanced", 0.28, 0.72, availability_weight=0.30, noise=0.10),
    OpponentProfile("contrarian", -0.12, 0.78, upside_weight=0.35, noise=0.25),
    OpponentProfile("flexibility", 0.18, 0.64, big_weight=0.30, noise=0.14),
    OpponentProfile("defense", 0.16, 0.72, noise=0.13),
    OpponentProfile("scarcity_first", 0.20, 0.58, big_weight=0.72, noise=0.18),
    OpponentProfile("late_value", 0.42, 0.46, availability_weight=0.20, noise=0.20),
)


def default_opponent_profiles(*, seed: int) -> tuple[OpponentProfile, ...]:
    """Sample five distinct profiles from all thirteen for one draft seed."""
    return tuple(random.Random(seed ^ 0x6A09E667).sample(OPPONENT_PROFILES, TOTAL_TEAMS - 1))


@dataclass(frozen=True)
class SixTeamPick:
    pick_number: int
    round_number: int
    seat: int
    player: SixTeamPlayer


@dataclass
class SixTeamDraftResult:
    picks: list[SixTeamPick]
    rosters: dict[int, list[SixTeamPlayer]]
    strategy: str
    our_seat: int
    seed: int
    opponent_profiles: tuple[OpponentProfile, ...]

    def roster(self) -> list[SixTeamPlayer]:
        return list(self.rosters[self.our_seat])

    def validate(self, *, config: SixTeamConfig | None = None) -> None:
        cfg = config or SixTeamConfig()
        if len(self.picks) != cfg.picks:
            raise SixTeamError(f"draft produced {len(self.picks)} picks; expected {cfg.picks}")
        if len({pick.player.player_id for pick in self.picks}) != cfg.picks:
            raise SixTeamError("draft contains duplicate player picks")
        if set(self.rosters) != set(range(1, cfg.total_teams + 1)):
            raise SixTeamError("draft must include every seat 1 through 6")
        for roster in self.rosters.values():
            legal, errors = check_roster_shape(roster, config=cfg)
            if not legal:
                raise SixTeamError("; ".join(errors))


def _custom_points(player: SixTeamPlayer, config: SixTeamConfig) -> float:
    return score_stats(
        player.stats,
        double_double_includes_triple_double=config.double_double_includes_triple_double,
        threshold_bonuses_stack=config.threshold_bonuses_stack,
    )


def _big_score(player: SixTeamPlayer) -> float:
    return float(bool(set(player.eligibility) & {"PF", "C", "F"}))


def _lineup_aware_score(player: SixTeamPlayer, roster: Sequence[SixTeamPlayer], config: SixTeamConfig) -> float:
    """Transparent marginal slot coverage heuristic for research comparisons."""
    deficits = {position: int(not any(position in item.eligibility for item in roster)) for position in MANDATORY_NATIVE_POSITIONS}
    gain = sum(deficits[position] for position in player.eligibility if position in deficits)
    if not roster:
        gain += 1
    flex_gain = len(set(player.eligibility) & {"PG", "SG", "SF", "PF", "G", "F"})
    return _custom_points(player, config) * player.projected_availability + gain + 0.25 * flex_gain


def _strategy_score(player: SixTeamPlayer, roster: Sequence[SixTeamPlayer], strategy: str, config: SixTeamConfig) -> float:
    custom = _custom_points(player, config)
    if strategy == "custom_per_game":
        return custom
    if strategy == "availability":
        return custom * player.projected_availability
    if strategy == "market":
        return -((player.market_rank + player.adp) / 2.0)
    if strategy == "lineup_aware":
        return _lineup_aware_score(player, roster, config)
    if strategy == "big_preference":
        return custom + 2.25 * _big_score(player)
    raise SixTeamError(f"unknown strategy {strategy!r}; expected {STRATEGY_NAMES}")


def _opponent_score(player: SixTeamPlayer, roster: Sequence[SixTeamPlayer], profile: OpponentProfile, config: SixTeamConfig) -> float:
    custom = _custom_points(player, config)
    market = -((player.market_rank + player.adp) / 2.0)
    flexibility = 0.45 * len(player.eligibility)
    return (
        profile.market_weight * market
        + profile.custom_weight * custom
        + profile.availability_weight * player.projected_availability * 10.0
        + profile.upside_weight * player.upside
        + profile.big_weight * _big_score(player)
        + flexibility
    )


def _deterministic_noise(seed: int, pick_number: int, seat: int, player_id: str) -> float:
    """Return common, reproducible noise independent of candidate iteration."""
    payload = f"{seed}:{pick_number}:{seat}:{player_id}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return unit * 2.0 - 1.0


def _shape_feasible(candidate: SixTeamPlayer, team_roster: Sequence[SixTeamPlayer], available: Mapping[str, SixTeamPlayer], all_rosters: Mapping[int, Sequence[SixTeamPlayer]], config: SixTeamConfig) -> bool:
    current_roster = tuple(team_roster) + (candidate,)
    remaining = [player for player_id, player in available.items() if player_id != candidate.player_id]
    remaining_team_picks = config.roster_size - len(current_roster)
    # A team's current roster must be extendable by at most its remaining
    # picks. This catches flexible-player conflicts where simple category
    # counts (for example, three guards) are insufficient for PG/SG/G.
    if _max_assignable_slots(current_roster, config.starting_slots) + remaining_team_picks < len(config.starting_slots):
        return False
    # Once a team has its full 14-player roster, only that roster may satisfy
    # the exact starter assignment. Earlier picks use the remaining pool as a
    # conservative reachability check.
    if len(current_roster) == config.roster_size and not _can_assign_slots(current_roster, config.starting_slots):
        return False
    # Category reservations are conservative shared-pool safeguards, not a
    # proof of simultaneous completion for every team. Exact pool matching
    # adds a late-round check; current-roster capacity is checked above.
    if len(current_roster) >= config.roster_size - 2 and not _can_assign_slots(current_roster + tuple(remaining), config.starting_slots):
        return False
    rosters_after = [
        tuple(roster) + (candidate,) if roster is team_roster else tuple(roster)
        for roster in all_rosters.values()
    ]
    for category, required in ROSTER_CATEGORY_REQUIREMENTS.items():
        current_count = sum(_eligible_for_category(item, category) for item in current_roster)
        if current_count + (config.roster_size - len(current_roster)) < required:
            return False
        deficit = sum(
            max(0, required - sum(_eligible_for_category(item, category) for item in roster))
            for roster in rosters_after
        )
        if sum(_eligible_for_category(item, category) for item in remaining) < deficit:
            return False
    return True


def simulate_draft(
    players: Sequence[SixTeamPlayer],
    *,
    strategy: str,
    our_seat: int,
    seed: int,
    config: SixTeamConfig | None = None,
) -> SixTeamDraftResult:
    cfg = config or SixTeamConfig()
    if strategy not in STRATEGY_NAMES:
        raise SixTeamError(f"unknown strategy {strategy!r}; expected {STRATEGY_NAMES}")
    if not 1 <= our_seat <= cfg.total_teams:
        raise SixTeamError("our_seat must be between 1 and 6")
    if len(players) < cfg.picks:
        raise SixTeamError(f"player pool must contain at least {cfg.picks} records")
    if len({player.player_id for player in players}) != len(players):
        raise SixTeamError("player IDs must be unique")
    profiles = default_opponent_profiles(seed=seed)
    profile_by_seat = {}
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
                return _strategy_score(player, roster, strategy, cfg) + _deterministic_noise(seed, pick_number, seat, player.player_id) * 0.000001
        else:
            profile = profile_by_seat[seat]
            def ranking(player: SixTeamPlayer) -> float:
                return _opponent_score(player, roster, profile, cfg) + _deterministic_noise(seed, pick_number, seat, player.player_id) * profile.noise * max(abs(_custom_points(player, cfg)), 1.0)
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


@dataclass(frozen=True)
class SixTeamSchedule:
    availability: Mapping[str, tuple[bool, ...]]
    days: int
    seed: int

    def is_available(self, player_id: str, day: int) -> bool:
        if not 0 <= day < self.days:
            raise SixTeamError(f"day must be between 0 and {self.days - 1}")
        try:
            return bool(self.availability[player_id][day])
        except KeyError as exc:
            raise SixTeamError(f"schedule has no player {player_id!r}") from exc


def make_synthetic_schedule(players: Sequence[SixTeamPlayer], *, days: int = LINEUP_DAYS, seed: int = 0) -> SixTeamSchedule:
    if days <= 0:
        raise SixTeamError("days must be positive")
    rng = random.Random(seed)
    groups: dict[str, list[SixTeamPlayer]] = {}
    for index, player in enumerate(players):
        groups.setdefault(player.schedule_group or f"SYNTH-GROUP-{index % 30:02d}", []).append(player)
    availability: dict[str, tuple[bool, ...]] = {}
    for group, group_players in sorted(groups.items()):
        game_days = set(rng.sample(range(days), min(days, max(1, min(4, days)))))
        for player in group_players:
            availability[player.player_id] = tuple(day in game_days and rng.random() < player.projected_availability for day in range(days))
    return SixTeamSchedule(availability, days, seed)


@dataclass(frozen=True)
class LineupAllocation:
    day: int
    slots: Mapping[str, str]
    score: float
    inactive_starters: tuple[str, ...]


def allocate_lineup(roster: Sequence[SixTeamPlayer], *, day: int, schedule: SixTeamSchedule, config: SixTeamConfig | None = None) -> LineupAllocation:
    cfg = config or SixTeamConfig()
    legal, errors = check_roster_shape(roster, config=cfg)
    if not legal:
        raise SixTeamError("; ".join(errors))
    active = {player.player_id: schedule.is_available(player.player_id, day) for player in roster}
    values = {player.player_id: _custom_points(player, cfg) for player in roster}

    def solve(require_active: bool) -> tuple[dict[str, str], float] | None:
        candidates = [
            player for player in roster
            if not require_active or active[player.player_id]
        ]
        # Player weights are independent of the slot they fill. The eligible
        # player sets therefore form a transversal matroid: sorting by weight
        # and retaining a player whenever matching rank increases gives the
        # exact maximum-weight basis in O(players * slots * players).
        selected: list[SixTeamPlayer] = []
        rank = 0
        for player in sorted(candidates, key=lambda item: (-values[item.player_id] if active[item.player_id] else 0.0, item.player_id)):
            trial = selected + [player]
            trial_rank = _max_assignable_slots(trial, cfg.starting_slots)
            if trial_rank > rank:
                selected.append(player)
                rank = trial_rank
            if rank == len(cfg.starting_slots):
                break
        if rank < len(cfg.starting_slots):
            return None
        selected = sorted(selected, key=lambda item: item.player_id)
        assignment: list[SixTeamPlayer | None] = [None] * len(cfg.starting_slots)
        slot_order = sorted(range(len(cfg.starting_slots)), key=lambda index: sum(_eligible_for_slot(player, cfg.starting_slots[index]) for player in selected))

        def assign(order_index: int, used: set[str]) -> bool:
            if order_index == len(slot_order):
                return True
            slot_index = slot_order[order_index]
            slot = cfg.starting_slots[slot_index]
            for player in selected:
                if player.player_id in used or not _eligible_for_slot(player, slot):
                    continue
                used.add(player.player_id)
                assignment[slot_index] = player
                if assign(order_index + 1, used):
                    return True
                used.remove(player.player_id)
                assignment[slot_index] = None
            return False

        if not assign(0, set()):
            raise SixTeamError("matching rank reported a lineup that could not be reconstructed")
        occurrence: dict[str, int] = {}
        slots: dict[str, str] = {}
        for slot, player in zip(cfg.starting_slots, assignment):
            if player is None:
                raise SixTeamError("lineup assignment omitted a required slot")
            occurrence[slot] = occurrence.get(slot, 0) + 1
            key = f"{slot}{occurrence[slot]}" if cfg.starting_slots.count(slot) > 1 else slot
            slots[key] = player.player_id
        score = sum(values[player_id] if active[player_id] else 0.0 for player_id in slots.values())
        return slots, score

    selected = solve(True) or solve(False)
    if selected is None:
        raise SixTeamError("no exact lineup assignment exists")
    slots, score = selected
    inactive = tuple(player_id for player_id in slots.values() if not active[player_id])
    return LineupAllocation(day, slots, score, inactive)


def generate_synthetic_players(*, player_count: int = 126, seed: int = 0) -> list[SixTeamPlayer]:
    """Generate clearly labelled native-position research records."""
    if player_count < PICKS_PER_DRAFT:
        raise SixTeamError(f"synthetic pool requires at least {PICKS_PER_DRAFT} players")
    rng = random.Random(seed)
    patterns = (("PG",), ("SG",), ("SF",), ("PF",), ("C",), ("PG", "SG"), ("SG", "SF"), ("SF", "PF"), ("PF", "C"))
    players: list[SixTeamPlayer] = []
    for index in range(1, player_count + 1):
        eligibility = patterns[(index - 1) % len(patterns)]
        ppg = round(rng.uniform(14.0, 29.0) + (2.0 if "C" in eligibility else 0.0), 2)
        rebounds = round(rng.uniform(3.0, 11.0) + (2.0 if "C" in eligibility else 0.0), 2)
        assists = round(rng.uniform(1.5, 8.5) + (1.0 if "PG" in eligibility else 0.0), 2)
        dd_rate = round(min(0.42, (0.18 if "C" in eligibility or "PF" in eligibility else 0.07) + rng.uniform(0.0, 0.10)), 4)
        td_rate = round(min(dd_rate, 0.08 if "PG" in eligibility else 0.035), 4)
        points40 = round(min(0.40, max(0.01, (ppg - 18.0) / 85.0 + rng.uniform(0.0, 0.06))), 4)
        points50 = round(min(points40, points40 * rng.uniform(0.05, 0.30)), 4)
        stats = {
            "PTS": ppg, "REB": rebounds, "AST": assists,
            "STL": round(rng.uniform(0.4, 2.0), 2), "BLK": round(rng.uniform(0.1, 1.8), 2),
            "TO": round(rng.uniform(0.8, 3.6), 2), "3PM": round(rng.uniform(0.2, 3.5), 2),
            "DD": dd_rate, "TD": td_rate, "TF": round(rng.uniform(0.0, 0.015), 4),
            "FLAGRANT_FOUL": round(rng.uniform(0.0, 0.01), 4),
            "POINTS_40_PLUS": points40, "POINTS_50_PLUS": points50,
        }
        players.append(SixTeamPlayer(
            player_id=f"SIX-SYNTH-{index:03d}", name=f"Six Team Synthetic Player {index:03d}",
            eligibility=eligibility, stats=stats,
            projected_availability=round(rng.uniform(0.78, 0.98), 4),
            market_rank=float(index), adp=float(index) + rng.uniform(-4.0, 4.0),
            upside=round(rng.uniform(0.0, 12.0), 2), schedule_group=f"SIX-GROUP-{(index - 1) % 30:02d}",
        ))
    # Make the synthetic market a noisy, reproducible proxy for custom value
    # rather than an accidental index ordering unrelated to the generated
    # stats. The runner still treats it as a model assumption, not ADP data.
    market_rng = random.Random(seed ^ 0xC0FFEE)
    ordered = sorted(
        players,
        key=lambda player: (-_custom_points(player, SixTeamConfig()), market_rng.random()),
    )
    return [replace(player, market_rank=float(rank), adp=float(rank) + market_rng.uniform(-4.0, 4.0)) for rank, player in enumerate(ordered, start=1)]


def realize_players(players: Sequence[SixTeamPlayer], *, seed: int) -> list[SixTeamPlayer]:
    """Create a separate realized-stat world after forecast drafting decisions."""
    rng = random.Random(seed)
    realized: list[SixTeamPlayer] = []
    for player in players:
        stats: dict[str, float] = {}
        for key, value in player.stats.items():
            shock = rng.uniform(-0.18, 0.18)
            revised = max(0.0, float(value) * (1.0 + shock))
            if key in {"DD", "TD", "TF", "FLAGRANT_FOUL", "POINTS_40_PLUS", "POINTS_50_PLUS"}:
                revised = min(1.0, revised)
            stats[key] = revised
        # Inclusive event probabilities must remain coherent after shocks.
        stats["TD"] = min(stats.get("TD", 0.0), stats.get("DD", 0.0))
        stats["POINTS_50_PLUS"] = min(stats.get("POINTS_50_PLUS", 0.0), stats.get("POINTS_40_PLUS", 0.0))
        realized.append(replace(player, stats=stats, source_label="synthetic_six_team_realized"))
    return realized


def _score_forecast_selected(allocation: LineupAllocation, realized_by_id: Mapping[str, SixTeamPlayer], schedule: SixTeamSchedule, config: SixTeamConfig) -> float:
    return sum(
        _custom_points(realized_by_id[player_id], config)
        for player_id in allocation.slots.values()
        if schedule.is_available(player_id, allocation.day)
    )


def _weekly_roster_score(roster: Sequence[SixTeamPlayer], realized_by_id: Mapping[str, SixTeamPlayer], schedule: SixTeamSchedule, config: SixTeamConfig) -> tuple[float, list[LineupAllocation], float]:
    allocations = [allocate_lineup(roster, day=day, schedule=schedule, config=config) for day in range(schedule.days)]
    selected_score = sum(_score_forecast_selected(allocation, realized_by_id, schedule, config) for allocation in allocations)
    active_roster_score = sum(
        _custom_points(realized_by_id[player.player_id], config)
        for player in roster
        for day in range(schedule.days)
        if schedule.is_available(player.player_id, day)
    )
    return selected_score, allocations, active_roster_score


def _coverage(roster: Sequence[SixTeamPlayer]) -> dict[str, int]:
    return {position: sum(position in player.eligibility for player in roster) for position in MANDATORY_NATIVE_POSITIONS}


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return float(statistics.fmean(values)) if values else 0.0


def _stdev(values: Iterable[float]) -> float:
    values = list(values)
    return float(statistics.stdev(values)) if len(values) > 1 else 0.0


def _run_one(players: Sequence[SixTeamPlayer], realized: Sequence[SixTeamPlayer], schedule: SixTeamSchedule, *, strategy: str, seat: int, seed: int, split: str, config: SixTeamConfig) -> dict[str, Any]:
    draft = simulate_draft(players, strategy=strategy, our_seat=seat, seed=seed, config=config)
    roster = draft.roster()
    realized_by_id = {player.player_id: player for player in realized}
    weekly_realized, allocations, active_roster_score = _weekly_roster_score(roster, realized_by_id, schedule, config)
    weekly_forecast = sum(allocation.score for allocation in allocations)
    opponent_scores: list[float] = []
    for opponent_seat, opponent_roster in draft.rosters.items():
        if opponent_seat == seat:
            continue
        opponent_score, _, _ = _weekly_roster_score(opponent_roster, realized_by_id, schedule, config)
        opponent_scores.append(opponent_score)
    opponent_pair_win_fraction = _mean(
        1.0 if weekly_realized > opponent_score else 0.5 if weekly_realized == opponent_score else 0.0
        for opponent_score in opponent_scores
    )
    legal, errors = check_roster_shape(roster, config=config)
    picks = [pick.player.player_id for pick in draft.picks]
    return {
        "split": split, "pool_seed": seed, "strategy": strategy, "our_seat": seat,
        "draft_picks": len(draft.picks), "unique_pick_count": len(set(picks)),
        "all_seats_present": set(draft.rosters) == set(range(1, TOTAL_TEAMS + 1)),
        "roster_size": len(roster), "legal_roster": legal, "shape_errors": "; ".join(errors),
        "coverage": _coverage(roster), "coverage_complete": all(_coverage(roster)[position] >= 1 for position in MANDATORY_NATIVE_POSITIONS),
        "weekly_forecast_points": weekly_forecast, "weekly_realized_points": weekly_realized,
        "weekly_benched_points": active_roster_score - weekly_realized,
        "weekly_opponent_pair_win_fraction": opponent_pair_win_fraction,
        "usable_starter_slots": sum(len(config.starting_slots) - len(allocation.inactive_starters) for allocation in allocations),
        "inactive_starters": sum(len(allocation.inactive_starters) for allocation in allocations),
        "roster_availability_rate": sum(schedule.is_available(player.player_id, day) for player in roster for day in range(schedule.days)) / float(len(roster) * schedule.days),
        "opponent_profiles": [profile.name for profile in draft.opponent_profiles],
    }


def _validate_rows(rows: Sequence[Mapping[str, Any]], *, seeds: Sequence[int], config: SixTeamConfig) -> None:
    expected = {(seed, strategy, seat) for seed in seeds for strategy in STRATEGY_NAMES for seat in range(1, config.total_teams + 1)}
    actual = {(int(row["pool_seed"]), str(row["strategy"]), int(row["our_seat"])) for row in rows}
    if actual != expected:
        raise SixTeamError(f"evaluation matrix mismatch: got {len(actual)} rows, expected {len(expected)}")
    for row in rows:
        if row["draft_picks"] != config.picks or row["unique_pick_count"] != config.picks:
            raise SixTeamError("incomplete or duplicate-pick draft in evaluation rows")
        if not row["all_seats_present"] or not row["legal_roster"] or not row["coverage_complete"]:
            raise SixTeamError(f"invalid six-team row: {row}")


def _summaries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    baseline = {(row["split"], row["pool_seed"], row["our_seat"]): row for row in rows if row["strategy"] == BASELINE_STRATEGY}
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["split"]), str(row["strategy"])), []).append(row)
    summaries: list[dict[str, Any]] = []
    for (split, strategy), items in sorted(grouped.items()):
        deltas = [
            row["weekly_realized_points"]
            - baseline[(split, row["pool_seed"], row["our_seat"])] ["weekly_realized_points"]
            for row in items
        ]
        summaries.append({
            "split": split, "strategy": strategy, "runs": len(items),
            "weekly_realized_points_mean": _mean(row["weekly_realized_points"] for row in items),
            "weekly_realized_points_sd": _stdev(row["weekly_realized_points"] for row in items),
            "weekly_forecast_points_mean": _mean(row["weekly_forecast_points"] for row in items),
            "paired_delta_vs_custom_per_game_mean": _mean(deltas), "paired_delta_vs_custom_per_game_sd": _stdev(deltas),
            "weekly_benched_points_mean": _mean(row["weekly_benched_points"] for row in items),
            "weekly_opponent_pair_win_fraction_mean": _mean(row["weekly_opponent_pair_win_fraction"] for row in items),
            "coverage_rate": _mean(float(row["coverage_complete"]) for row in items),
            "legal_roster_rate": _mean(float(row["legal_roster"]) for row in items),
            "usable_starter_slots_mean": _mean(row["usable_starter_slots"] for row in items),
            "inactive_starters_mean": _mean(row["inactive_starters"] for row in items),
            "roster_availability_rate_mean": _mean(row["roster_availability_rate"] for row in items),
        })
    return summaries


def _validate_profile(profile: Mapping[str, Any]) -> SixTeamConfig:
    if profile.get("status") != "research_only_advisory" or profile.get("live_consumption") is not False:
        raise SixTeamError("six-team profile must be marked research_only_advisory with live_consumption=false")
    settings = profile.get("settings")
    scoring = profile.get("scoring")
    if not isinstance(settings, Mapping) or not isinstance(scoring, Mapping):
        raise SixTeamError("six-team profile requires settings and scoring objects")
    config = SixTeamConfig(
        total_teams=int(settings.get("total_teams", 0)), rounds=int(settings.get("rounds", 0)),
        starting_slots=tuple(settings.get("starting_slots", ())), bench_slots=int(settings.get("bench_slots", 0)),
        double_double_includes_triple_double=bool(settings.get("double_double_includes_triple_double", True)),
        threshold_bonuses_stack=bool(settings.get("threshold_bonuses_stack", True)),
    )
    if int(settings.get("picks", 0)) != config.picks or settings.get("ir_slots", "missing") is not None:
        raise SixTeamError("profile must declare 84 picks and null/unverified IR slots")
    expected = dict(SCORING_WEIGHTS)
    if set(scoring) != set(expected):
        raise SixTeamError("profile scoring keys do not match the isolated six-team engine")
    for name, weight in expected.items():
        if float(scoring.get(name, math.nan)) != weight:
            raise SixTeamError(f"profile scoring mismatch for {name}")
    if set(profile.get("strategies", ())) != set(STRATEGY_NAMES):
        raise SixTeamError("profile strategies do not match the isolated six-team engine")
    rate_semantics = profile.get("forecast_rate_semantics")
    if not isinstance(rate_semantics, Mapping) or set(rate_semantics) != {"DD", "TD", "POINTS_40_PLUS", "POINTS_50_PLUS"}:
        raise SixTeamError("profile must label inclusive DD/TD and 40+/50+ forecast rate semantics")
    return config


def load_profile(path: str | Path | None = None) -> tuple[dict[str, Any], SixTeamConfig, str]:
    profile_path = Path(path) if path else Path(__file__).resolve().parents[1] / "profiles" / "six_team_points.json"
    raw = profile_path.read_bytes()
    profile = json.loads(raw.decode("utf-8"))
    if not isinstance(profile, dict):
        raise SixTeamError("profile JSON must be an object")
    return profile, _validate_profile(profile), hashlib.sha256(raw).hexdigest()


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, *, metadata: Mapping[str, Any], summaries: Sequence[Mapping[str, Any]], selection: Mapping[str, Any]) -> None:
    lines = [
        "# Six-team points strategy research",
        "",
        "This report is a bounded synthetic sensitivity study. It is advisory research and is not live LeagueConfig or production advisor validation.",
        "",
        f"Profile: `{metadata['profile_id']}`; teams: {metadata['teams']}; rounds: {metadata['rounds']}; picks per draft: {metadata['picks_per_draft']}; development seeds: {metadata['development_seeds']}; held-out seeds: {metadata['heldout_seeds']}.",
        "",
        "Draft decisions and daily starter selection use forecast records. The selected starter IDs are then scored with a separate realized-stat world; realized values are not available to draft ranking or lineup selection.",
        "",
        "`lineup_aware` uses this engine's transparent slot-coverage heuristic. It is not production marginal-lineup-v1.",
        "",
        "## Paired strategy summary",
        "",
        "| Split | Strategy | Runs | Realized points | Paired delta vs custom | Pair win fraction | Benched points | Coverage | Legal rosters |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(f"| {row['split']} | {row['strategy']} | {row['runs']} | {row['weekly_realized_points_mean']:.2f} | {row['paired_delta_vs_custom_per_game_mean']:.2f} | {row['weekly_opponent_pair_win_fraction_mean']:.3f} | {row['weekly_benched_points_mean']:.2f} | {row['coverage_rate']:.3f} | {row['legal_roster_rate']:.3f} |")
    lines.extend([
        "", f"Development-selected strategy: `{selection['development_selected_strategy']}`.",
        f"Held-out audit of that fixed choice: {selection.get('selected_strategy_heldout_paired_delta_mean', 0.0):.2f} points per weekly roster versus `custom_per_game`.",
        "", "## Limits", "",
        "- Pools, schedules, opponent profiles, availability, and realized shocks are generated assumptions; no private input data or live actions are used.",
        "- Six-team scarcity and 14-round timing differ from the legacy 14-team fixture. This module does not validate live ESPN behavior.",
        "- Bonus rates are synthetic expected rates. DD+TD and 40+50+ stacking follows the user's final correction; event rates remain model assumptions.",
        "- Small seed counts support reproducibility and sensitivity checks, not calibrated win probabilities.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_evaluation(*, development_seeds: Sequence[int] = (101, 102, 103, 104), heldout_seeds: Sequence[int] = tuple(range(201, 209)), player_count: int = 126, output_dir: str | Path | None = None, profile_path: str | Path | None = None) -> dict[str, Any]:
    profile, config, profile_digest = load_profile(profile_path)
    development_seeds = tuple(int(seed) for seed in development_seeds)
    heldout_seeds = tuple(int(seed) for seed in heldout_seeds)
    if not development_seeds or not heldout_seeds or set(development_seeds) & set(heldout_seeds):
        raise SixTeamError("development and held-out seed sets must be nonempty and disjoint")
    rows: list[dict[str, Any]] = []
    for split, seeds in (("development", development_seeds), ("heldout", heldout_seeds)):
        for pool_seed in seeds:
            players = generate_synthetic_players(player_count=player_count, seed=pool_seed)
            realized = realize_players(players, seed=pool_seed ^ 0xA5A5A5A5)
            schedule = make_synthetic_schedule(players, days=LINEUP_DAYS, seed=pool_seed ^ 0x13579BDF)
            for strategy in STRATEGY_NAMES:
                for seat in range(1, config.total_teams + 1):
                    rows.append(_run_one(players, realized, schedule, strategy=strategy, seat=seat, seed=pool_seed, split=split, config=config))
    _validate_rows([row for row in rows if row["split"] == "development"], seeds=development_seeds, config=config)
    _validate_rows([row for row in rows if row["split"] == "heldout"], seeds=heldout_seeds, config=config)
    summaries = _summaries(rows)
    development = [row for row in summaries if row["split"] == "development"]
    heldout = [row for row in summaries if row["split"] == "heldout"]
    selected = max(development, key=lambda row: (row["paired_delta_vs_custom_per_game_mean"], -row["inactive_starters_mean"]))
    selected_holdout = next(row for row in heldout if row["strategy"] == selected["strategy"])
    holdout_best = max(heldout, key=lambda row: row["paired_delta_vs_custom_per_game_mean"])
    selection = {
        "development_selected_strategy": selected["strategy"],
        "development_paired_delta_mean": selected["paired_delta_vs_custom_per_game_mean"],
        "selected_strategy_heldout_paired_delta_mean": selected_holdout["paired_delta_vs_custom_per_game_mean"],
        "heldout_best_strategy_in_tested_set": holdout_best["strategy"],
        "heldout_best_paired_delta_mean": holdout_best["paired_delta_vs_custom_per_game_mean"],
        "selection_basis": "highest mean paired realized weekly points delta on development seeds; tie-break fewer inactive starters",
    }
    metadata = {
        "profile_id": profile["profile_id"], "profile_sha256": profile_digest,
        "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "teams": config.total_teams, "rounds": config.rounds, "picks_per_draft": config.picks,
        "roster_size": config.roster_size, "starting_slots": list(config.starting_slots), "bench_slots": config.bench_slots,
        "strategies": list(STRATEGY_NAMES), "development_seeds": list(development_seeds), "heldout_seeds": list(heldout_seeds),
        "runs": len(rows), "player_count": player_count, "schedule_days": LINEUP_DAYS,
        "forecast_source": "synthetic_six_team_research", "realized_source": "synthetic_six_team_realized",
        "forecast_rate_semantics": profile["forecast_rate_semantics"],
        "opponent_profile_library": len(OPPONENT_PROFILES), "opponent_profiles_per_draft": TOTAL_TEAMS - 1,
        "selection": selection,
    }
    result = {"metadata": metadata, "rows": rows, "summary": summaries, "selection": selection}
    if output_dir is not None:
        destination = Path(output_dir)
    else:
        destination = Path(__file__).resolve().parents[1] / "runtime" / "six_team_research"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (destination / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    (destination / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
    (destination / "rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    _write_rows(destination / "rows.csv", rows)
    _write_report(destination / "REPORT.md", metadata=metadata, summaries=summaries, selection=selection)
    result["output_dir"] = str(destination.resolve())
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--profile", dest="profile_path", default=None)
    parser.add_argument("--development-seeds", nargs="+", type=int, default=(101, 102, 103, 104))
    parser.add_argument("--heldout-seeds", nargs="+", type=int, default=tuple(range(201, 209)))
    parser.add_argument("--player-count", type=int, default=126)
    args = parser.parse_args(argv)
    result = run_evaluation(
        development_seeds=args.development_seeds, heldout_seeds=args.heldout_seeds,
        player_count=args.player_count, output_dir=args.output_dir, profile_path=args.profile_path,
    )
    print(json.dumps(result["metadata"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASELINE_STRATEGY", "BENCH_SLOTS", "DRAFT_ROUNDS", "LINEUP_DAYS", "MANDATORY_NATIVE_POSITIONS",
    "OPPONENT_PROFILES", "PICKS_PER_DRAFT", "ROSTER_SIZE", "SCORING_WEIGHTS", "STARTING_SLOTS",
    "STRATEGY_NAMES", "SixTeamConfig", "SixTeamDraftResult", "SixTeamError", "SixTeamPick",
    "SixTeamPlayer", "SixTeamSchedule", "allocate_lineup", "check_roster_shape", "default_opponent_profiles",
    "generate_synthetic_players", "load_profile", "make_synthetic_schedule", "realize_players", "run_evaluation",
    "score_stats", "simulate_draft",
]
