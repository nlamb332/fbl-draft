"""Reusable, deterministic mock fantasy basketball draft fixture.

This module intentionally uses only the Python standard library.  The fixture is
synthetic and is labelled as such throughout the public API.  Real player
projections can be supplied with :func:`load_player_records`; this module never
creates projections for real players.

The public contract is deliberately small enough for a simulation runner to use
without depending on a particular data vendor:

* ``PlayerRecord`` stores expected per-game stats, eligibility, availability,
  market rank and ADP.
* ``MockDraft`` runs a 14-team, 10-round snake draft (140 unique picks).
* ``make_synthetic_schedule`` creates a reproducible schedule shared by paired
  strategy comparisons.
* ``allocate_lineup`` provides exact dynamic-programming and greedy roster
  allocation for C, G, G, F, F, UTIL plus a four-player bench.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence


TOTAL_TEAMS = 14
DRAFT_ROUNDS = 10
PICKS_PER_DRAFT = TOTAL_TEAMS * DRAFT_ROUNDS
STARTING_SLOTS: tuple[str, ...] = ("C", "G", "G", "F", "F", "UTIL")
ROSTER_SIZE = len(STARTING_SLOTS) + 4
BENCH_SLOTS = 4
IR_SLOTS = 3
MIN_POSITION_COUNTS: Mapping[str, int] = {"C": 1, "G": 2, "F": 2}

# The scoring map is intentionally explicit.  DD/TD overlap is handled by the
# ``double_double_includes_triple_double`` assumption in DraftConfig.
SCORING_WEIGHTS: Mapping[str, float] = {
    "PTS": 1,
    "FGM": 2,
    "FGA": -1,
    "FTM": 1,
    "FTA": -1,
    "3PM": 1,
    "REB": 2,
    "AST": 2,
    "STL": 4,
    "BLK": 4,
    "TO": -2,
    "PF": -1,
    "TF": -2,
    "EJ": -5,
    "DD": 3,
    "TD": 5,
    "QD": 100,
}


class FixtureError(ValueError):
    """Base error for malformed fixture inputs or impossible draft states."""


class RosterShapeError(FixtureError):
    """Raised when a roster cannot legally fill the configured starting slots."""


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise FixtureError(f"expected numeric value, got {value!r}") from exc


def _normalise_eligibility(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.replace("|", ",").replace("/", ",").replace(";", ",").split(",")
    else:
        values = value or ()
    positions = tuple(dict.fromkeys(str(item).strip().upper() for item in values if str(item).strip()))
    invalid = set(positions) - {"C", "G", "F"}
    if invalid:
        raise FixtureError(f"unsupported eligibility position(s): {sorted(invalid)}")
    if not positions:
        raise FixtureError("a player must have at least one of C, G or F eligibility")
    return positions


def _normalise_stats(value: Any) -> dict[str, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise FixtureError(f"stats must be a JSON object, got {value!r}") from exc
    if not isinstance(value, Mapping):
        raise FixtureError("stats must be a mapping of stat name to expected per-game value")
    result: dict[str, float] = {}
    for key, raw in value.items():
        name = str(key).upper().strip()
        if name:
            result[name] = _as_float(raw)
    return result


@dataclass(frozen=True)
class PlayerRecord:
    """Expected per-game player input used by the draft and lineup engines.

    ``projected_availability`` is a probability in [0, 1].  ``upside`` is an
    optional expected ceiling used only by the upside strategy.  A source label
    is required in practice (the synthetic fixture sets it to
    ``synthetic_fixture``); retaining it in the record makes provenance visible
    to downstream reports.  ``schedule_group`` lets real inputs share a team
    game/off-day pattern in a synthetic schedule.
    """

    player_id: str
    name: str
    eligibility: tuple[str, ...]
    stats: Mapping[str, float]
    projected_availability: float
    market_rank: float
    adp: float
    upside: float = 0.0
    source_label: str = "external_input"
    schedule_group: str = ""

    def __post_init__(self) -> None:
        if not self.player_id or not self.name:
            raise FixtureError("player_id and name are required")
        object.__setattr__(self, "eligibility", _normalise_eligibility(self.eligibility))
        object.__setattr__(self, "stats", _normalise_stats(self.stats))
        availability = float(self.projected_availability)
        if not 0.0 <= availability <= 1.0:
            raise FixtureError("projected_availability must be between 0 and 1")
        object.__setattr__(self, "projected_availability", availability)
        object.__setattr__(self, "market_rank", float(self.market_rank))
        object.__setattr__(self, "adp", float(self.adp))
        object.__setattr__(self, "upside", float(self.upside))
        object.__setattr__(self, "schedule_group", str(self.schedule_group or ""))

    @classmethod
    def from_mapping(cls, record: Mapping[str, Any], *, source_label: str | None = None) -> "PlayerRecord":
        """Build a record from JSON-like input.

        Stats may be supplied under ``stats`` as a mapping/JSON string, or as
        individual columns such as ``PTS``, ``REB`` and ``3PM``.
        """
        stats = record.get("stats")
        if stats is None or stats == "":
            stats = {
                key: value
                for key, value in record.items()
                if str(key).upper() in SCORING_WEIGHTS
            }
        return cls(
            player_id=str(record.get("player_id", record.get("id", ""))),
            name=str(record.get("name", "")),
            eligibility=_normalise_eligibility(record.get("eligibility", record.get("positions", ()))),
            stats=_normalise_stats(stats),
            projected_availability=_as_float(
                record.get("projected_availability", record.get("availability", 1.0)), 1.0
            ),
            market_rank=_as_float(record.get("market_rank", record.get("rank", 9999)), 9999),
            adp=_as_float(record.get("adp", record.get("ADP", record.get("market_rank", 9999))), 9999),
            upside=_as_float(record.get("upside", record.get("ceiling", 0.0)), 0.0),
            source_label=str(record.get("source_label", source_label or "external_input")),
            schedule_group=str(record.get("schedule_group", record.get("team", "")) or ""),
        )


def load_player_records(path: str | Path) -> list[PlayerRecord]:
    """Load player records from a CSV or JSON file using only stdlib parsers.

    JSON accepts either a list of records or ``{"players": [...]}``.  CSV
    accepts ``eligibility`` as ``C|F``/``C,F`` and ``stats`` as a JSON object;
    stat columns are also accepted directly.  The loader does not infer or
    fabricate real-player projections.
    """
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix == ".json":
        with source_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload.get("players", payload) if isinstance(payload, Mapping) else payload
        if not isinstance(records, list):
            raise FixtureError("JSON player input must be a list or an object with a players list")
        return [PlayerRecord.from_mapping(row, source_label="json_input") for row in records]
    if suffix == ".csv":
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return [PlayerRecord.from_mapping(row, source_label="csv_input") for row in rows]
    raise FixtureError(f"unsupported player input format {suffix!r}; use .csv or .json")


def score_stats(
    stats: Mapping[str, Any],
    *,
    double_double_includes_triple_double: bool = True,
) -> float:
    """Calculate custom fantasy points from expected per-game stats.

    The default assumption is that a triple-double earns both the DD and TD
    bonuses (``+3 DD +5 TD``).  Pass ``False`` to make the bonuses mutually
    exclusive; this explicit switch keeps the unresolved platform convention
    visible in sensitivity tests.
    """
    values = {str(key).upper(): _as_float(value) for key, value in stats.items()}
    total = 0.0
    for key, weight in SCORING_WEIGHTS.items():
        if key == "DD" and double_double_includes_triple_double:
            # A source may provide TD without a separate DD indicator.  Under
            # the overlap assumption, a TD therefore implies the DD bonus.
            total += weight * max(values.get("DD", 0.0), values.get("TD", 0.0))
        elif key == "DD" and not double_double_includes_triple_double:
            total += weight * max(0.0, values.get("DD", 0.0) - values.get("TD", 0.0))
        else:
            total += weight * values.get(key, 0.0)
    return total


@dataclass(frozen=True)
class DraftConfig:
    """Draft and roster settings; defaults encode the requested league shape."""

    total_teams: int = TOTAL_TEAMS
    rounds: int = DRAFT_ROUNDS
    starting_slots: tuple[str, ...] = STARTING_SLOTS
    bench_slots: int = BENCH_SLOTS
    ir_slots: int = IR_SLOTS
    double_double_includes_triple_double: bool = True

    def __post_init__(self) -> None:
        if self.total_teams != TOTAL_TEAMS:
            raise FixtureError(f"mock draft requires exactly {TOTAL_TEAMS} total teams")
        if self.rounds != DRAFT_ROUNDS:
            raise FixtureError(f"mock draft requires exactly {DRAFT_ROUNDS} rounds")
        if tuple(self.starting_slots) != STARTING_SLOTS:
            raise FixtureError(f"starting slots must be exactly {STARTING_SLOTS}")
        if self.bench_slots != BENCH_SLOTS or self.ir_slots != IR_SLOTS:
            raise FixtureError("roster shape must have four bench slots and three IR slots")

    @property
    def picks(self) -> int:
        return self.total_teams * self.rounds

    @property
    def roster_size(self) -> int:
        return len(self.starting_slots) + self.bench_slots


def _eligible_for_slot(player: PlayerRecord, slot: str) -> bool:
    return slot == "UTIL" or slot in player.eligibility


def _can_fill_slot_pattern(players: Sequence[PlayerRecord], slots: Sequence[str]) -> bool:
    """Small bipartite matching check used for shape validation."""
    if len(players) < len(slots):
        return False

    @lru_cache(maxsize=None)
    def visit(slot_index: int, used_mask: int) -> bool:
        if slot_index == len(slots):
            return True
        slot = slots[slot_index]
        for idx, player in enumerate(players):
            if used_mask & (1 << idx):
                continue
            if _eligible_for_slot(player, slot) and visit(slot_index + 1, used_mask | (1 << idx)):
                return True
        return False

    return visit(0, 0)


def check_roster_shape(players: Sequence[PlayerRecord], *, config: DraftConfig | None = None) -> tuple[bool, tuple[str, ...]]:
    """Return ``(is_legal, errors)`` for a team roster.

    IR slots are extra capacity and therefore do not count toward the ten
    drafted players.  A roster can carry multi-position players; matching is
    used rather than simply counting eligibility labels.
    """
    cfg = config or DraftConfig()
    errors: list[str] = []
    if len(players) != cfg.roster_size:
        errors.append(f"roster has {len(players)} players; expected {cfg.roster_size}")
    if not _can_fill_slot_pattern(players, cfg.starting_slots):
        errors.append(f"cannot assign starting slots {cfg.starting_slots}")
    return (not errors, tuple(errors))


def assert_roster_shape(players: Sequence[PlayerRecord], *, config: DraftConfig | None = None) -> None:
    legal, errors = check_roster_shape(players, config=config)
    if not legal:
        raise RosterShapeError("; ".join(errors))


def check_player_pool_shape(
    players: Sequence[PlayerRecord], *, config: DraftConfig | None = None
) -> tuple[bool, tuple[str, ...]]:
    """Check necessary pool-level depth before a draft begins.

    Counts are deliberately conservative: every one of the 14 teams needs one
    C, two G and two F eligible players, in addition to the 140 total picks.
    This catches an under-specified real-data input before a late-round pick,
    while per-roster matching remains the final legality check.
    """
    cfg = config or DraftConfig()
    errors: list[str] = []
    if len(players) < cfg.picks:
        errors.append(f"pool has {len(players)} players; expected at least {cfg.picks}")
    for position, per_team in MIN_POSITION_COUNTS.items():
        available = sum(position in player.eligibility for player in players)
        required = cfg.total_teams * per_team
        if available < required:
            errors.append(f"pool has {available} {position}-eligible players; expected at least {required}")
    return (not errors, tuple(errors))


def assert_player_pool_shape(players: Sequence[PlayerRecord], *, config: DraftConfig | None = None) -> None:
    legal, errors = check_player_pool_shape(players, config=config)
    if not legal:
        raise RosterShapeError("; ".join(errors))


@dataclass(frozen=True)
class OpponentProfile:
    """A diverse market/custom/fit/noise profile for an opposing team."""

    name: str
    style: str
    market_weight: float = 0.0
    custom_weight: float = 1.0
    fit_weight: float = 1.0
    availability_weight: float = 0.0
    upside_weight: float = 0.0
    noise: float = 0.0
    big_weight: float = 0.0


OPPONENT_PROFILES: tuple[OpponentProfile, ...] = (
    OpponentProfile("market-anchor", "market", market_weight=1.0, custom_weight=0.12, fit_weight=0.20, noise=0.05),
    OpponentProfile("market-noisy", "market-noisy", market_weight=0.85, custom_weight=0.10, fit_weight=0.20, noise=0.35),
    OpponentProfile("custom-points", "custom", market_weight=0.10, custom_weight=1.0, fit_weight=0.35, noise=0.08),
    OpponentProfile("custom-aggressive", "custom-aggressive", market_weight=0.05, custom_weight=1.1, fit_weight=0.30, noise=0.25),
    OpponentProfile("positional-fit", "fit", market_weight=0.10, custom_weight=0.45, fit_weight=1.35, noise=0.08),
    OpponentProfile("flexibility", "flexibility", market_weight=0.12, custom_weight=0.60, fit_weight=1.10, noise=0.15),
    OpponentProfile("big-heavy", "big-heavy", market_weight=0.08, custom_weight=0.65, fit_weight=0.55, big_weight=1.0, noise=0.12),
    OpponentProfile("big-value", "big-value", market_weight=0.20, custom_weight=0.70, fit_weight=0.60, big_weight=0.75, noise=0.18),
    OpponentProfile("availability", "availability", market_weight=0.15, custom_weight=0.55, fit_weight=0.35, availability_weight=1.1, noise=0.08),
    OpponentProfile("upside", "upside", market_weight=0.08, custom_weight=0.65, fit_weight=0.30, upside_weight=1.2, noise=0.18),
    OpponentProfile("upside-noisy", "upside-noisy", market_weight=0.05, custom_weight=0.55, fit_weight=0.25, upside_weight=1.0, noise=0.40),
    OpponentProfile("balanced", "balanced", market_weight=0.30, custom_weight=0.65, fit_weight=0.75, availability_weight=0.35, noise=0.12),
    OpponentProfile("contrarian", "contrarian", market_weight=-0.15, custom_weight=0.75, fit_weight=0.30, upside_weight=0.35, noise=0.28),
)


STRATEGY_NAMES: tuple[str, ...] = (
    "baseline_market",
    "custom_value",
    "flexibility_balanced",
    "big_heavy",
    "availability",
    "upside",
    "marginal_usable_lineup",
)


_POSITION_DEMAND: Mapping[str, int] = {"C": 1, "G": 2, "F": 2, "UTIL": 1}


def _eligibility_pattern(player: PlayerRecord) -> tuple[str, ...]:
    return tuple(sorted(player.eligibility))


def _min_future_assignments(
    current_roster: Sequence[PlayerRecord],
    future_pattern_counts: Mapping[tuple[str, ...], int],
    slots: Sequence[str],
) -> int | None:
    """Return the minimum future players needed to fill ``slots`` exactly.

    This is a tiny min-cost bipartite flow: current roster players have cost 0,
    future players (grouped by identical eligibility pattern) have cost 1.
    Grouping future players is exact because only eligibility matters for this
    feasibility question.  ``None`` means fewer than all slots can be matched.
    """
    source = 0
    current_start = 1
    pattern_start = current_start + len(current_roster)
    slot_start = pattern_start + len(future_pattern_counts)
    sink = slot_start + len(slots)
    node_count = sink + 1
    graph: list[list[list[int]]] = [[] for _ in range(node_count)]

    def add_edge(left: int, right: int, capacity: int, cost: int) -> None:
        graph[left].append([right, len(graph[right]), capacity, cost])
        graph[right].append([left, len(graph[left]) - 1, 0, -cost])

    for index, player in enumerate(current_roster):
        player_node = current_start + index
        add_edge(source, player_node, 1, 0)
        for slot_index, slot in enumerate(slots):
            if _eligible_for_slot(player, slot):
                add_edge(player_node, slot_start + slot_index, 1, 0)

    patterns = tuple(future_pattern_counts)
    for index, pattern in enumerate(patterns):
        pattern_node = pattern_start + index
        add_edge(source, pattern_node, int(future_pattern_counts[pattern]), 1)
        for slot_index, slot in enumerate(slots):
            if slot == "UTIL" or slot in pattern:
                add_edge(pattern_node, slot_start + slot_index, 1, 0)

    for slot_index in range(len(slots)):
        add_edge(slot_start + slot_index, sink, 1, 0)

    total_cost = 0
    for _ in slots:
        # Bellman-Ford is sufficient for this graph and handles the reverse
        # edges introduced by the min-cost augmentation directly.
        distance = [10**9] * node_count
        previous: list[tuple[int, int] | None] = [None] * node_count
        distance[source] = 0
        for _ in range(node_count - 1):
            changed = False
            for left in range(node_count):
                if distance[left] == 10**9:
                    continue
                for edge_index, edge in enumerate(graph[left]):
                    right, _, capacity, cost = edge
                    if capacity and distance[right] > distance[left] + cost:
                        distance[right] = distance[left] + cost
                        previous[right] = (left, edge_index)
                        changed = True
            if not changed:
                break
        if distance[sink] == 10**9:
            return None
        total_cost += distance[sink]
        node = sink
        while node != source:
            left, edge_index = previous[node]  # type: ignore[misc]
            edge = graph[left][edge_index]
            edge[2] -= 1
            graph[node][edge[1]][2] += 1
            node = left
    return total_cost


def _points(player: PlayerRecord, config: DraftConfig) -> float:
    return score_stats(
        player.stats,
        double_double_includes_triple_double=config.double_double_includes_triple_double,
    )


def _fit_score(player: PlayerRecord, roster: Sequence[PlayerRecord]) -> float:
    counts = {position: sum(position in item.eligibility for item in roster) for position in MIN_POSITION_COUNTS}
    deficit = sum(max(0, MIN_POSITION_COUNTS[position] - counts[position]) for position in MIN_POSITION_COUNTS)
    player_deficit = sum(1 for position in player.eligibility if counts.get(position, 0) < MIN_POSITION_COUNTS[position])
    flexibility = len(player.eligibility) - 1
    return player_deficit * 4.0 + flexibility * 0.55 + deficit * 0.15


def _marginal_usable_coverage(player: PlayerRecord, roster: Sequence[PlayerRecord]) -> float:
    """Return slot-demand coverage gained by adding ``player``.

    The demand is the starting shape C1/G2/F2/UTIL1.  This is a small,
    transparent coverage signal; it is not a fitted flex-position bonus.
    """
    before = {
        position: sum(position in item.eligibility for item in roster)
        for position in ("C", "G", "F")
    }
    after = dict(before)
    for position in player.eligibility:
        after[position] += 1
    gain = sum(
        max(0, min(_POSITION_DEMAND[position], after[position]) - min(_POSITION_DEMAND[position], before[position]))
        for position in ("C", "G", "F")
    )
    if not roster:
        gain += 1  # first drafted player can cover UTIL1
    return float(gain)


def _candidate_score(
    player: PlayerRecord,
    roster: Sequence[PlayerRecord],
    profile: OpponentProfile,
    *,
    config: DraftConfig,
    rng: random.Random,
    force_strategy: str | None = None,
) -> float:
    points = _points(player, config)
    # Treat rank and ADP as two market signals; from_mapping defaults a missing
    # one to the other, so external inputs never receive an artificial penalty.
    market = 1.0 / max((player.market_rank + player.adp) / 2.0, 1.0)
    fit = _fit_score(player, roster)
    big = float("C" in player.eligibility) + float("F" in player.eligibility)
    features = (
        profile.market_weight * (100.0 * market)
        + profile.custom_weight * points
        + profile.fit_weight * fit
        + profile.availability_weight * player.projected_availability * 10.0
        + profile.upside_weight * player.upside
        + profile.big_weight * big
    )
    if force_strategy:
        if force_strategy == "baseline_market":
            # Strict market baseline: rank/ADP only.  Shape feasibility is
            # enforced by the caller, rather than folded into this value.
            features = -((player.market_rank + player.adp) / 2.0)
        elif force_strategy == "custom_value":
            features = points + 0.8 * fit + 0.1 * 100.0 * market
        elif force_strategy == "flexibility_balanced":
            features = points + 1.9 * fit + 1.2 * (len(player.eligibility) - 1) + 0.3 * 100.0 * market
        elif force_strategy == "big_heavy":
            features = points + 0.8 * fit + 6.0 * big + 0.2 * 100.0 * market
        elif force_strategy == "availability":
            features = points * player.projected_availability
        elif force_strategy == "upside":
            features = points + 0.6 * fit + 1.8 * player.upside + 0.1 * 100.0 * market
        elif force_strategy == "marginal_usable_lineup":
            features = points * player.projected_availability + _marginal_usable_coverage(player, roster)
        else:
            raise FixtureError(f"unknown strategy {force_strategy!r}; expected one of {STRATEGY_NAMES}")
    return features + rng.uniform(-profile.noise, profile.noise) * max(abs(features), 1.0)


@dataclass(frozen=True)
class DraftPick:
    pick_number: int
    round_number: int
    seat: int
    team: str
    player: PlayerRecord


@dataclass
class DraftResult:
    picks: list[DraftPick]
    rosters: dict[str, list[PlayerRecord]]
    seat_by_team: dict[str, int]
    strategy: str
    seed: int

    def roster(self, team: str = "our-team") -> list[PlayerRecord]:
        return list(self.rosters[team])

    def validate(self, *, config: DraftConfig | None = None) -> None:
        cfg = config or DraftConfig()
        if len(self.picks) != cfg.picks:
            raise FixtureError(f"draft produced {len(self.picks)} picks; expected {cfg.picks}")
        if len({pick.player.player_id for pick in self.picks}) != cfg.picks:
            raise FixtureError("draft contains duplicate player picks")
        if sorted(self.seat_by_team.values()) != list(range(1, cfg.total_teams + 1)):
            raise FixtureError("draft seats must cover every seat from 1 through 14 exactly once")
        for team, roster in self.rosters.items():
            assert_roster_shape(roster, config=cfg)


class MockDraft:
    """Run a reproducible snake draft with feasibility-aware pick selection."""

    def __init__(
        self,
        players: Sequence[PlayerRecord],
        *,
        strategy: str = "custom_value",
        seat: int = 1,
        seed: int = 0,
        config: DraftConfig | None = None,
        opponent_profiles: Sequence[OpponentProfile] = OPPONENT_PROFILES,
    ) -> None:
        self.players = list(players)
        self.strategy = strategy
        self.seat = seat
        self.seed = seed
        self.config = config or DraftConfig()
        if strategy not in STRATEGY_NAMES:
            raise FixtureError(f"unknown strategy {strategy!r}; expected one of {STRATEGY_NAMES}")
        if not 1 <= seat <= self.config.total_teams:
            raise FixtureError(f"seat must be between 1 and {self.config.total_teams}")
        if len(opponent_profiles) != self.config.total_teams - 1:
            raise FixtureError("exactly 13 opponent profiles are required")
        if len({player.player_id for player in self.players}) != len(self.players):
            raise FixtureError("player_id values must be unique before drafting")
        assert_player_pool_shape(self.players, config=self.config)
        self.opponent_profiles = tuple(opponent_profiles)

    def _draft_order(self) -> list[int]:
        order: list[int] = []
        for round_number in range(1, self.config.rounds + 1):
            seats = range(1, self.config.total_teams + 1)
            if round_number % 2 == 0:
                seats = reversed(tuple(seats))
            order.extend(seats)
        return order

    def _teams(self) -> tuple[str, ...]:
        return tuple("our-team" if seat == self.seat else f"team-{seat:02d}" for seat in range(1, self.config.total_teams + 1))

    def _profile_for(self, seat: int) -> OpponentProfile:
        if seat == self.seat:
            return OpponentProfile("our-strategy", self.strategy)
        index = seat - 1 if seat < self.seat else seat - 2
        return self.opponent_profiles[index]

    def _candidate_preserves_shape(
        self,
        candidate: PlayerRecord,
        *,
        team: str,
        rosters: Mapping[str, Sequence[PlayerRecord]],
        available: Mapping[str, PlayerRecord],
        available_counts: Mapping[str, int] | None = None,
        other_deficits: Mapping[str, int] | None = None,
        future_pattern_counts: Mapping[tuple[str, ...], int] | None = None,
    ) -> bool:
        """Keep both the picking team and global position quotas feasible.

        The global check reserves enough eligible records for every team's
        outstanding C/G/F minimums.  It is intentionally a conservative count
        check; the final per-roster matching check remains authoritative.
        """
        current_roster = tuple(rosters[team]) + (candidate,)
        current_remaining = self.config.roster_size - len(current_roster)
        if future_pattern_counts is None:
            future_pattern_counts = {}
            for player_id, player in available.items():
                if player_id == candidate.player_id:
                    continue
                pattern = _eligibility_pattern(player)
                future_pattern_counts[pattern] = future_pattern_counts.get(pattern, 0) + 1
        min_future = _min_future_assignments(
            current_roster,
            future_pattern_counts,
            self.config.starting_slots,
        )
        if min_future is None or min_future > current_remaining:
            return False
        current_deficits = {
            position: max(0, required - sum(position in item.eligibility for item in current_roster))
            for position, required in MIN_POSITION_COUNTS.items()
        }
        if sum(current_deficits.values()) > current_remaining:
            return False
        if available_counts is None:
            available_counts = {
                position: sum(position in player.eligibility for player in available.values())
                for position in MIN_POSITION_COUNTS
            }
        if other_deficits is None:
            other_deficits = {
                position: sum(
                    max(0, required - sum(position in item.eligibility for item in roster))
                    for roster_team, roster in rosters.items()
                    if roster_team != team
                )
                for position, required in MIN_POSITION_COUNTS.items()
            }
        if future_pattern_counts is None:
            future_pattern_counts = {}
        for position, required in MIN_POSITION_COUNTS.items():
            needed = current_deficits[position] + other_deficits[position]
            if available_counts[position] - int(position in candidate.eligibility) < needed:
                return False
        return True

    def run(self) -> DraftResult:
        rng = random.Random(self.seed)
        available = {player.player_id: player for player in self.players}
        teams = self._teams()
        rosters: dict[str, list[PlayerRecord]] = {team: [] for team in teams}
        seat_by_team = {team: seat for seat, team in enumerate(teams, start=1)}
        picks: list[DraftPick] = []
        order = self._draft_order()
        for pick_number, seat in enumerate(order, start=1):
            team = "our-team" if seat == self.seat else f"team-{seat:02d}"
            roster = rosters[team]
            profile = self._profile_for(seat)
            candidates = list(available.values())
            available_counts = {
                position: sum(position in player.eligibility for player in candidates)
                for position in MIN_POSITION_COUNTS
            }
            other_deficits = {
                position: sum(
                    max(0, required - sum(position in item.eligibility for item in other_roster))
                    for other_team, other_roster in rosters.items()
                    if other_team != team
                )
                for position, required in MIN_POSITION_COUNTS.items()
            }
            future_pattern_counts: dict[tuple[str, ...], int] = {}
            for player in candidates:
                pattern = _eligibility_pattern(player)
                future_pattern_counts[pattern] = future_pattern_counts.get(pattern, 0) + 1
            def feasible_candidates(*, enforce_global_counts: bool) -> list[PlayerRecord]:
                shape_cache: dict[tuple[str, ...], bool] = {}
                feasible: list[PlayerRecord] = []
                for player in candidates:
                    pattern = _eligibility_pattern(player)
                    if pattern not in shape_cache:
                        future_after = {
                            pool_pattern: count - int(pool_pattern == pattern)
                            for pool_pattern, count in future_pattern_counts.items()
                            if count - int(pool_pattern == pattern) > 0
                        }
                        shape_cache[pattern] = self._candidate_preserves_shape(
                            player,
                            team=team,
                            rosters=rosters,
                            available=available,
                            available_counts=available_counts if enforce_global_counts else {position: 10**9 for position in MIN_POSITION_COUNTS},
                            other_deficits=other_deficits if enforce_global_counts else {position: 0 for position in MIN_POSITION_COUNTS},
                            future_pattern_counts=future_after,
                        )
                    if shape_cache[pattern]:
                        feasible.append(player)
                return feasible

            legal = feasible_candidates(enforce_global_counts=True)
            if not legal:
                # Global category counts are deliberately conservative for
                # multi-position players.  Fall back to the exact picking-team
                # feasibility check, and fail loudly if even that is impossible.
                legal = feasible_candidates(enforce_global_counts=False)
            if not legal:
                raise RosterShapeError(f"no legal shape-preserving candidate remains for {team} at pick {pick_number}")
            scored = [
                (_candidate_score(p, roster, profile, config=self.config, rng=rng, force_strategy=self.strategy if seat == self.seat else None), p)
                for p in legal
            ]
            _, player = max(scored, key=lambda pair: pair[0])
            del available[player.player_id]
            roster.append(player)
            picks.append(DraftPick(pick_number, (pick_number - 1) // self.config.total_teams + 1, seat, team, player))
        result = DraftResult(picks, rosters, seat_by_team, self.strategy, self.seed)
        result.validate(config=self.config)
        return result


@dataclass(frozen=True)
class SyntheticSchedule:
    """Seeded games plus health availability; ``source_label`` is synthetic.

    ``availability`` is the conjunction of ``game_schedule`` and
    ``health_availability``.  Keeping those matrices separate prevents a
    player's health probability from being mistaken for an NBA game every day.
    """

    availability: Mapping[str, tuple[bool, ...]]
    days: int
    seed: int
    source_label: str = "synthetic_schedule"
    game_schedule: Mapping[str, tuple[bool, ...]] = ()
    health_availability: Mapping[str, tuple[bool, ...]] = ()

    def is_available(self, player_id: str, day: int) -> bool:
        if day < 0 or day >= self.days:
            raise FixtureError(f"day must be between 0 and {self.days - 1}")
        try:
            return bool(self.availability[player_id][day])
        except KeyError as exc:
            raise FixtureError(f"schedule has no player {player_id!r}") from exc

    def is_game_day(self, player_id: str, day: int) -> bool:
        """Return whether the player's synthetic team schedule has a game."""
        if day < 0 or day >= self.days:
            raise FixtureError(f"day must be between 0 and {self.days - 1}")
        matrix = self.game_schedule if self.game_schedule else self.availability
        try:
            return bool(matrix[player_id][day])
        except KeyError as exc:
            raise FixtureError(f"schedule has no player {player_id!r}") from exc


def make_synthetic_schedule(
    players: Sequence[PlayerRecord],
    *,
    days: int = 7,
    seed: int = 0,
) -> SyntheticSchedule:
    """Create a deterministic, explicitly synthetic availability matrix.

    The same schedule object should be passed to each paired strategy so that
    strategy differences are not confounded by independent availability draws.
    """
    if days <= 0:
        raise FixtureError("days must be positive")
    rng = random.Random(seed)
    groups: dict[str, list[PlayerRecord]] = {}
    for index, player in enumerate(players):
        # External inputs can provide a real team/group.  Synthetic records use
        # stable groups so teammates share game/off-day patterns.
        group = player.schedule_group or f"SYNTH-GROUP-{index % 30:02d}"
        groups.setdefault(group, []).append(player)
    games_by_group: dict[str, tuple[bool, ...]] = {}
    target_games = max(1, min(days, (3 if days <= 7 else round(days * 0.5))))
    for group in sorted(groups):
        count = target_games
        if days >= 7:
            count = min(days, target_games + rng.randrange(2))
        game_days = set(rng.sample(range(days), count))
        games_by_group[group] = tuple(day in game_days for day in range(days))
    game_schedule: dict[str, tuple[bool, ...]] = {}
    health_availability: dict[str, tuple[bool, ...]] = {}
    availability: dict[str, tuple[bool, ...]] = {}
    for group, group_players in groups.items():
        games = games_by_group[group]
        for player in group_players:
            health = tuple(rng.random() < player.projected_availability for _ in range(days))
            game_schedule[player.player_id] = games
            health_availability[player.player_id] = health
            availability[player.player_id] = tuple(games[day] and health[day] for day in range(days))
    return SyntheticSchedule(availability, days, seed, "synthetic_schedule", game_schedule, health_availability)


@dataclass(frozen=True)
class LineupAllocation:
    day: int
    method: str
    slots: Mapping[str, str]
    starters: tuple[PlayerRecord, ...]
    bench: tuple[PlayerRecord, ...]
    score: float
    inactive_starters: tuple[str, ...] = ()


def _allocation_score(player: PlayerRecord, config: DraftConfig) -> float:
    return _points(player, config)


def allocate_lineup(
    roster: Sequence[PlayerRecord],
    *,
    day: int = 0,
    schedule: SyntheticSchedule | None = None,
    method: str = "dp",
    config: DraftConfig | None = None,
) -> LineupAllocation:
    """Allocate a legal six-slot lineup with exact DP or greedy selection.

    If no all-active assignment exists for a day, the allocator still returns
    the legal shape and marks zero-scoring unavailable starters in
    ``inactive_starters``.  This makes injury/availability sensitivity runs
    reproducible without silently dropping a roster slot.
    """
    cfg = config or DraftConfig()
    if len(roster) != cfg.roster_size:
        raise RosterShapeError(f"roster has {len(roster)} players; expected {cfg.roster_size}")
    assert_roster_shape(roster, config=cfg)
    if method not in {"dp", "greedy"}:
        raise FixtureError("method must be 'dp' or 'greedy'")
    active = [
        schedule is None or schedule.is_available(player.player_id, day)
        for player in roster
    ]
    values = [_allocation_score(player, cfg) for player in roster]

    def solve(require_active: bool, greedy: bool = False) -> tuple[dict[str, str], float] | None:
        indexed_slots = tuple(enumerate(cfg.starting_slots))
        candidates = [
            [idx for idx, player in enumerate(roster) if _eligible_for_slot(player, slot) and (not require_active or active[idx])]
            for _, slot in indexed_slots
        ]
        if greedy:
            slots: dict[str, str] = {}
            used: set[int] = set()
            total = 0.0
            for slot_index, slot in indexed_slots:
                options = [idx for idx in candidates[slot_index] if idx not in used]
                if not options:
                    return None
                idx = max(options, key=lambda item: values[item])
                used.add(idx)
                key = f"{slot}{slot_index + 1}" if slot in {"G", "F"} else slot
                slots[key] = roster[idx].player_id
                total += values[idx] if active[idx] else 0.0
            return slots, total

        @lru_cache(maxsize=None)
        def best(slot_index: int, used_mask: int) -> tuple[float, tuple[int, ...]] | None:
            if slot_index == len(indexed_slots):
                return (0.0, ())
            best_result: tuple[float, tuple[int, ...]] | None = None
            for idx in candidates[slot_index]:
                if used_mask & (1 << idx):
                    continue
                result = best(slot_index + 1, used_mask | (1 << idx))
                if result is None:
                    continue
                score, chosen = result
                score += values[idx] if active[idx] else 0.0
                candidate = (score, (idx,) + chosen)
                if best_result is None or candidate[0] > best_result[0]:
                    best_result = candidate
            return best_result

        result = best(0, 0)
        if result is None:
            return None
        score, chosen = result
        slots = {}
        for (slot_index, slot), idx in zip(indexed_slots, chosen):
            key = f"{slot}{slot_index + 1}" if slot in {"G", "F"} else slot
            slots[key] = roster[idx].player_id
        return slots, score

    selected: tuple[dict[str, str], float] | None
    if method == "dp":
        selected = solve(require_active=True)
        if selected is None:
            selected = solve(require_active=False)
    else:
        selected = solve(require_active=True, greedy=True)
        if selected is None:
            selected = solve(require_active=False, greedy=True)
    if selected is None:
        raise RosterShapeError(f"no assignment exists for slots {cfg.starting_slots}")
    slots, score = selected
    starter_ids = set(slots.values())
    starters = tuple(roster[next(idx for idx, player in enumerate(roster) if player.player_id == player_id)] for player_id in slots.values())
    bench = tuple(player for player in roster if player.player_id not in starter_ids)
    inactive = tuple(player_id for player_id in starter_ids if schedule is not None and not schedule.is_available(player_id, day))
    return LineupAllocation(day, method, slots, starters, bench, score, inactive)


def synthetic_fixture(
    *,
    player_count: int = 210,
    seed: int = 20261010,
) -> list[PlayerRecord]:
    """Return a deterministic pool of explicitly synthetic player records.

    The pool is intentionally larger than the 140 picks and has ample C/G/F
    depth so that every team can finish with a legal shape.  Names are generated
    identifiers (``SYNTH-001`` etc.), never real player projections.
    """
    if player_count < PICKS_PER_DRAFT:
        raise FixtureError(f"synthetic fixture requires at least {PICKS_PER_DRAFT} players")
    rng = random.Random(seed)
    players: list[PlayerRecord] = []
    # A synthetic position mix with realistic basketball roles: guards, wings,
    # forwards, G/F wings and F/C bigs.  C/G and C/G/F multi-eligibility is
    # intentionally rare so centers do not become universal guards.
    position_patterns = (
        ("G",), ("F",), ("G",), ("F",), ("G", "F"),
        ("F", "C"), ("F",), ("G", "F"), ("C",), ("F", "C"),
    )
    for index in range(1, player_count + 1):
        pattern = position_patterns[(index - 1) % len(position_patterns)]
        rank = float(index)
        # Generated synthetic expected stats are deliberately modest and varied;
        # the source label makes them unsuitable for accidental real-player use.
        # Build makes/attempts coherently: FGA >= FGM, FTA >= FTM and PTS is
        # exactly 2*(FGM-3PM) + 3PM*3 + FTM.
        fgm = round(2.4 + (player_count - index) * 0.006 + rng.uniform(0.0, 2.8), 2)
        three_pm = round(min(fgm, rng.uniform(0.0, 2.5 if "G" in pattern else 1.5)), 2)
        fga = round(fgm + rng.uniform(2.0, 7.0), 2)
        ftm = round(rng.uniform(0.5, 3.3), 2)
        fta = round(ftm + rng.uniform(0.2, 2.4), 2)
        stats = {
            "PTS": round(2.0 * (fgm - three_pm) + 3.0 * three_pm + ftm, 2),
            "FGM": fgm,
            "FGA": fga,
            "FTM": ftm,
            "FTA": fta,
            "3PM": three_pm,
            "REB": round(2.0 + (3.0 if "C" in pattern else 0.0) + rng.uniform(0.0, 5.0), 2),
            "AST": round(1.0 + (1.5 if "G" in pattern else 0.0) + rng.uniform(0.0, 4.8), 2),
            "STL": round(rng.uniform(0.1, 1.8), 2),
            "BLK": round((0.45 if "C" in pattern else 0.1) + rng.uniform(0.0, 1.4), 2),
            "TO": round(rng.uniform(0.2, 2.2), 2),
            "PF": round(rng.uniform(0.3, 2.6), 2),
            "TF": 0.0,
            "EJ": 0.0,
            "DD": 1.0 if "C" in pattern and index % 7 == 0 else 0.0,
            "TD": 1.0 if "G" in pattern and index % 31 == 0 else 0.0,
            "QD": 0.0,
        }
        availability = min(0.98, max(0.76, 0.97 - (index % 9) * 0.018))
        upside = round(rng.uniform(0.0, 12.0) + (8.0 if index % 17 == 0 else 0.0), 2)
        players.append(
            PlayerRecord(
                player_id=f"SYNTH-{index:03d}",
                name=f"Synthetic Player {index:03d}",
                eligibility=pattern,
                stats=stats,
                projected_availability=availability,
                market_rank=rank,
                adp=rank + rng.uniform(-4.0, 4.0),
                upside=upside,
                source_label="synthetic_fixture",
                schedule_group=f"SYNTH-TEAM-{(index - 1) % 30:02d}",
            )
        )
    return players


def load_config(path: str | Path | None = None) -> DraftConfig:
    """Load ``DraftConfig`` from JSON, defaulting to the checked-in config."""
    config_path = Path(path) if path else Path(__file__).with_name("config.json")
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return DraftConfig(
        total_teams=int(payload.get("total_teams", TOTAL_TEAMS)),
        rounds=int(payload.get("rounds", DRAFT_ROUNDS)),
        starting_slots=tuple(payload.get("starting_slots", STARTING_SLOTS)),
        bench_slots=int(payload.get("bench_slots", BENCH_SLOTS)),
        ir_slots=int(payload.get("ir_slots", IR_SLOTS)),
        double_double_includes_triple_double=bool(payload.get("double_double_includes_triple_double", True)),
    )


# Friendly aliases keep the runner contract readable and preserve a small
# migration path if a caller used these descriptive names in an early draft.
generate_synthetic_players = synthetic_fixture
load_players = load_player_records
build_schedule = make_synthetic_schedule
optimize_daily_lineup = allocate_lineup


def run_mock_draft(players: Sequence[PlayerRecord], **kwargs: Any) -> DraftResult:
    """Convenience wrapper around ``MockDraft(...).run()``."""
    return MockDraft(players, **kwargs).run()


__all__ = [
    "BENCH_SLOTS", "DRAFT_ROUNDS", "DraftConfig", "DraftPick", "DraftResult",
    "FixtureError", "IR_SLOTS", "LineupAllocation", "MIN_POSITION_COUNTS",
    "MockDraft", "OPPONENT_PROFILES", "OpponentProfile", "PICKS_PER_DRAFT",
    "PlayerRecord", "ROSTER_SIZE", "RosterShapeError", "SCORING_WEIGHTS",
    "STARTING_SLOTS", "STRATEGY_NAMES", "SyntheticSchedule", "TOTAL_TEAMS",
    "allocate_lineup", "assert_player_pool_shape", "assert_roster_shape", "build_schedule",
    "check_player_pool_shape", "check_roster_shape", "generate_synthetic_players", "load_config",
    "load_player_records", "load_players", "make_synthetic_schedule", "optimize_daily_lineup",
    "run_mock_draft", "score_stats", "synthetic_fixture",
]
