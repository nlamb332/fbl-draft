"""``marginal-lineup-v1``: expected marginal optimized weekly lineup points.

The foundation scorer values a player as ``custom FP/game x GP/82``.  That has
no replacement level and no notion that a C/G/G/F/F/UTIL lineup can start only
so many players of one eligibility class on a given day.  This policy instead
scores a candidate as the expected *increase* in optimized weekly starting
lineup points from adding the candidate to the current roster, after the
remaining roster has been filled with the players we would expect to draft
(a bench opportunity cost).  There is no position cap, quota, or soft bonus;
position counts are diagnostics only.

Method, in the order the code applies it:

1. Forecast-gap imputation (``impute_forecast_gaps``).  PF is estimated from a
   per-minute rate by eligibility class and DD/TD from the projected
   PTS/REB/AST.  Only absent fields are filled, every fill is tagged, and a
   supplied value is never overwritten.
2. Replacement level (``replacement_levels``).  Per position, the expected
   per-game production of the k-th best available player, where k is the
   14-team starter demand (slots plus a UTIL share) for that position that is
   still unfilled.  It is recomputed from the current pool at every call.
3. Marginal lineup value.  Representative weeks give each player a dated
   activity matrix (team game days x availability).  Each day's lineup is the
   exact maximum-weight C/G/G/F/F/UTIL assignment.  Because the assignable
   sets form a transversal matroid and every active value is positive, a
   descending-value greedy that keeps a player only while the set remains
   matchable is exactly optimal, and is fast enough for the 2.0 s budget.
4. Bench opportunity cost.  The remaining roster spots are filled by phantom
   players drawn from the current pool at the depth the league will have
   reached by each of our next picks; each phantom's eligibility is the one
   that adds the most lineup points.  A candidate's value is measured against
   a replacement-level pick made now, with the same expected completion.

Everything is deterministic: the representative schedules are seeded from
constant strings, never from an evaluation seed.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

# Owner-accepted held-out promotion (#44).  `--foundation-only` opts out.
DEFAULT_PROMOTION_PATH = Path(__file__).resolve().parent / "validation" / "marginal-lineup-v1" / "promotion_artifact.json"

from .contracts import (
    CandidateScore,
    DraftObservation,
    LeagueConfig,
    LivePlayer,
    snake_pick_numbers,
)
from .injury import injury_factor
from .valuation import _base_games_factor, custom_fantasy_points, rank_players

ALGORITHM_VERSION = "marginal-lineup-v1"
IMPUTATION_VERSION = "forecast-gap-imputation-v1"
ROSTER_CAPACITY = 10
STARTER_SLOTS = ("C", "G", "G", "F", "F", "UTIL")
_BIT = {"C": 1, "G": 2, "F": 4}
GAMES_PER_WEEK = 3.5
DAYS_PER_WEEK = 7
_BUDGET_SAFETY = 0.85

# --- imputation constants ---------------------------------------------------
# Personal fouls per 36 minutes by eligibility class.  These are analyst
# estimates anchored on the NBA league-average of roughly 2.8 PF/36 and the
# well-known guard < wing < forward < center ordering.  No external dataset was
# fetched when they were written; they are therefore flagged as imputed on every
# use, and the validation report sweeps +-25 percent (``MarginalParams.pf_scale``) as a sensitivity band.
PF_PER_36 = {
    ("G",): 2.5,
    ("F", "G"): 2.6,
    ("F",): 2.8,
    ("C", "F"): 3.2,
    ("C",): 3.4,
    ("C", "G"): 3.0,
    ("C", "F", "G"): 3.0,
}
PF_DEFAULT_PER_36 = 2.8
# Minutes per game when the forecast carries no MIN.  Least-squares fit on the
# 195-player ESPN catalog snapshot (R^2 0.67, MAE 1.9 minutes).
MIN_FIT = (17.80, 0.520, 0.176, 0.303, 0.590)  # const, PTS, REB, AST, STL+BLK
# Game-to-game spread of a stat with mean mu is sd = a + b * mu.  The values are
# round numbers matched to typical NBA box-score variability.
_SPREAD = {"PTS": (2.0, 0.24), "REB": (1.0, 0.28), "AST": (1.0, 0.33)}


@dataclass(frozen=True)
class MarginalParams:
    """Tunable knobs.  They are frozen before any held-out run."""

    weeks: int = 12
    completion_days: int = 14
    vor_weight: float = 0.5
    depth_scale: float = 1.25
    opponent_market_weight: float = 0.25
    use_imputation: bool = True
    pf_scale: float = 1.0
    util_share: float = 1.0
    max_candidates: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "weeks": self.weeks,
            "completion_days": self.completion_days,
            "vor_weight": self.vor_weight,
            "depth_scale": self.depth_scale,
            "opponent_market_weight": self.opponent_market_weight,
            "use_imputation": self.use_imputation,
            "pf_scale": self.pf_scale,
            "util_share": self.util_share,
            "max_candidates": self.max_candidates,
        }


DEFAULT_PARAMS = MarginalParams()


def marginal_fingerprint(config: LeagueConfig, params: MarginalParams | None = None) -> str:
    """Stable fingerprint bound into validation artifacts and promotions."""

    p = params or DEFAULT_PARAMS
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "imputation_version": IMPUTATION_VERSION,
        "teams": config.teams,
        "rounds": config.rounds,
        "starting_slots": list(config.starting_slots),
        "bench_slots": config.bench_slots,
        "scoring": dict(sorted(config.scoring.items())),
        "double_double_includes_triple_double": config.double_double_includes_triple_double,
        "budget_seconds": config.recommendation_budget_seconds,
        "params": p.as_dict(),
        "pf_per_36": sorted((",".join(k), v) for k, v in PF_PER_36.items()),
        "min_fit": list(MIN_FIT),
        "games_per_week": GAMES_PER_WEEK,
    }
    if config.injury_policy.enabled:
        payload["injury_policy"] = config.injury_policy.as_dict()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# --- forecast-gap imputation ------------------------------------------------

def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stat_map(player: LivePlayer) -> dict[str, float]:
    values: dict[str, float] = {}
    for key, raw in (player.stats or {}).items():
        number = _num(raw)
        if number is not None:
            values[str(key).upper().strip()] = number
    return values


def _estimated_minutes(stats: Mapping[str, float]) -> float:
    const, pts, reb, ast, stocks = MIN_FIT
    value = const + pts * stats.get("PTS", 0.0) + reb * stats.get("REB", 0.0) + ast * stats.get("AST", 0.0) + stocks * (stats.get("STL", 0.0) + stats.get("BLK", 0.0))
    return max(10.0, min(40.0, value))


def _tail_probability(mean: float, stat: str, threshold: float = 10.0) -> float:
    """P(stat >= threshold) with a continuity-corrected normal."""

    base, slope = _SPREAD[stat]
    sd = max(0.5, base + slope * mean)
    z = (threshold - 0.5 - mean) / sd
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def double_triple_probabilities(stats: Mapping[str, float]) -> tuple[float, float]:
    """Per-game P(double-double, incl. triple) and P(triple-double).

    The three categories are treated as independent, which understates the
    correlation between rebounds and assists for big men; the effect is a mild
    underestimate of triple-doubles for the very top passers.
    """

    p1 = _tail_probability(stats.get("PTS", 0.0), "PTS")
    p2 = _tail_probability(stats.get("REB", 0.0), "REB")
    p3 = _tail_probability(stats.get("AST", 0.0), "AST")
    triple = p1 * p2 * p3
    double = p1 * p2 + p1 * p3 + p2 * p3 - 2.0 * triple
    return max(0.0, min(1.0, double)), max(0.0, min(1.0, triple))


def impute_forecast_gaps(
    player: LivePlayer,
    *,
    pf_scale: float | None = None,
) -> tuple[LivePlayer, tuple[str, ...], dict[str, float]]:
    """Fill absent PF/DD/TD forecasts; return (player, warnings, imputed values).

    A stat present in ``player.stats`` is never changed, including an explicit
    zero.  The result is deterministic and identical inputs give identical
    outputs.  TF, EJ and QD are left missing (their expected value is tiny and
    no defensible per-player rate is available), so the foundation missing-stat
    warning for them remains.
    """

    stats = _stat_map(player)
    imputed: dict[str, float] = {}
    scale = 1.0 if pf_scale is None else pf_scale
    if "PF" not in stats:
        key = tuple(sorted(player.eligibility))
        rate = PF_PER_36.get(key, PF_DEFAULT_PER_36)
        minutes = stats.get("MIN")
        source = "projected MIN"
        if minutes is None or minutes <= 0:
            minutes = _estimated_minutes(stats)
            source = "estimated MIN"
        imputed["PF"] = round(rate * scale * minutes / 36.0, 4)
        imputed_source_pf = source
    else:
        imputed_source_pf = ""
    if "DD" not in stats and "TD" not in stats and {"PTS", "REB", "AST"} <= set(stats):
        double, triple = double_triple_probabilities(stats)
        imputed["DD"] = round(double, 4)
        imputed["TD"] = round(triple, 4)
    elif "DD" not in stats and "TD" in stats and {"PTS", "REB", "AST"} <= set(stats):
        imputed["DD"] = round(double_triple_probabilities(stats)[0], 4)
    elif "TD" not in stats and "DD" in stats and {"PTS", "REB", "AST"} <= set(stats):
        imputed["TD"] = round(double_triple_probabilities(stats)[1], 4)
    if not imputed:
        return player, (), {}
    merged = dict(player.stats)
    merged.update(imputed)
    remaining_missing = tuple(item for item in player.missing_stats if str(item).upper().strip() not in imputed)
    filled = replace(player, stats=merged, missing_stats=remaining_missing)
    tags = []
    if "PF" in imputed:
        tags.append(f"imputed PF={imputed['PF']:.2f}/game ({IMPUTATION_VERSION}: eligibility-class rate x {imputed_source_pf})")
    if "DD" in imputed or "TD" in imputed:
        tags.append(
            f"imputed DD={imputed.get('DD', stats.get('DD', 0.0)):.3f} TD={imputed.get('TD', stats.get('TD', 0.0)):.3f} per game "
            f"({IMPUTATION_VERSION}: independent PTS/REB/AST tails)"
        )
    return filled, tuple(tags), imputed


# --- replacement level ------------------------------------------------------

BASE_DEMAND = {"C": 1, "G": 2, "F": 2}


def starter_demand(config: LeagueConfig, params: MarginalParams = DEFAULT_PARAMS) -> dict[str, float]:
    """League-wide starter demand per position, UTIL spread proportionally."""

    total_slots = sum(BASE_DEMAND.values())
    util = sum(1 for slot in config.starting_slots if slot == "UTIL") * params.util_share
    return {
        position: config.teams * (count + util * count / total_slots)
        for position, count in BASE_DEMAND.items()
    }


def replacement_levels(
    expected: Mapping[str, float],
    eligibility: Mapping[str, Sequence[str]],
    demand: Mapping[str, float],
    *,
    picks_made: int = 0,
    starter_picks: int | None = None,
) -> dict[str, tuple[float, str | None]]:
    """Per-position replacement level on the *current* pool.

    ``expected`` is expected per-game production (FP x availability) for each
    available player.  ``demand[pos]`` is the league's starter demand for the
    position; it is reduced pro rata by the share of league starter slots
    already consumed by picks, then the replacement level is the production of
    the player at that depth in the current pool.  The function is monotone:
    more demand means a deeper (weakly lower) level, and a depleted pool can
    only lower a level.
    """

    total_demand = sum(demand.values())
    consumed = min(1.0, max(0.0, (picks_made if starter_picks is None else starter_picks) / total_demand)) if total_demand else 0.0
    levels: dict[str, tuple[float, str | None]] = {}
    for position, slots in demand.items():
        ranked = sorted(
            ((value, pid) for pid, value in expected.items() if position in eligibility[pid]),
            key=lambda item: (-item[0], item[1]),
        )
        depth = max(1, int(math.ceil(slots * (1.0 - consumed))))
        if not ranked:
            levels[position] = (0.0, None)
            continue
        value, pid = ranked[min(depth, len(ranked)) - 1]
        levels[position] = (value, pid)
    return levels


# --- exact lineup (transversal-matroid greedy) ------------------------------

_SLOT_MASKS = (1, 2, 2, 4, 4, 7)  # C G G F F UTIL; mask bit set = eligible


def _matchable_counts(key: int) -> bool:
    """Can every player in the multiset be given a distinct eligible slot?"""

    masks = [mask for mask in range(1, 8) for _ in range((key >> (3 * mask)) & 7)]
    if len(masks) > len(_SLOT_MASKS):
        return False
    used = [False] * len(_SLOT_MASKS)

    def place(player_index: int) -> bool:
        if player_index == len(masks):
            return True
        for slot_index, slot in enumerate(_SLOT_MASKS):
            if used[slot_index] or not (masks[player_index] & slot):
                continue
            used[slot_index] = True
            if place(player_index + 1):
                return True
            used[slot_index] = False
        return False

    return place(0)


@lru_cache(maxsize=None)
def _feasible(key: int) -> bool:
    return _matchable_counts(key)


def lineup_points(entries: Sequence[tuple[float, int]]) -> float:
    """Exact optimal one-day lineup value; ``entries`` sorted by value desc."""

    key = 0
    total = 0.0
    taken = 0
    for value, mask in entries:
        if value <= 0.0:
            break
        trial = key + (1 << (3 * mask))
        if _feasible(trial):
            key = trial
            total += value
            taken += 1
            if taken == 6:
                break
    return total


def _lineup_plus(entries: Sequence[tuple[float, int]], extra_value: float, extra_mask: int) -> float:
    key = 0
    total = 0.0
    taken = 0
    index = 0
    size = len(entries)
    placed = False
    while True:
        if not placed and (index >= size or extra_value >= entries[index][0]):
            value, mask = extra_value, extra_mask
            placed = True
        elif index < size:
            value, mask = entries[index]
            index += 1
        else:
            break
        if value <= 0.0:
            break
        trial = key + (1 << (3 * mask))
        if _feasible(trial):
            key = trial
            total += value
            taken += 1
            if taken == 6:
                break
    return total


# --- representative schedules ----------------------------------------------

_GAME_CACHE: dict[tuple[str, int], tuple[bool, ...]] = {}
_UNIFORM_CACHE: dict[tuple[str, int], tuple[float, ...]] = {}


def _team_games(team_key: str, weeks: int) -> tuple[bool, ...]:
    """Dated game days: alternating 3/4 games per week, random day placement.

    Every team gets the same game count so that no candidate is favoured by a
    lucky draw; only the *placement* (and hence overlap with the rest of the
    roster) differs between teams.
    """

    cached = _GAME_CACHE.get((team_key, weeks))
    if cached is not None:
        return cached
    offset = random.Random(f"{ALGORITHM_VERSION}:offset:{team_key}").randrange(2)
    days: list[bool] = []
    for week in range(weeks):
        rng = random.Random(f"{ALGORITHM_VERSION}:games:{team_key}:{week}")
        chosen = set(rng.sample(range(DAYS_PER_WEEK), 3 + (week + offset) % 2))
        days.extend(day in chosen for day in range(DAYS_PER_WEEK))
    result = tuple(days)
    if len(_GAME_CACHE) > 4096:
        _GAME_CACHE.clear()
    _GAME_CACHE[(team_key, weeks)] = result
    return result


def _uniforms(player_id: str, days: int) -> tuple[float, ...]:
    """Stratified uniforms so the healthy-day share matches availability."""

    cached = _UNIFORM_CACHE.get((player_id, days))
    if cached is None:
        rng = random.Random(f"{ALGORITHM_VERSION}:health:{player_id}")
        order = list(range(days))
        rng.shuffle(order)
        cached = tuple((slot + rng.random()) / days for slot in order)
        if len(_UNIFORM_CACHE) > 4096:
            _UNIFORM_CACHE.clear()
        _UNIFORM_CACHE[(player_id, days)] = cached
    return cached


def game_days(player: LivePlayer, weeks: int) -> tuple[bool, ...]:
    team_key = f"team:{player.nba_team}" if player.nba_team else f"solo:{player.player_id}"
    return _team_games(team_key, weeks)


def activity_vector(player: LivePlayer, availability: float, weeks: int) -> tuple[bool, ...]:
    """Dated activity: team game days AND a stratified health draw."""

    games = game_days(player, weeks)
    draws = _uniforms(player.player_id, weeks * DAYS_PER_WEEK)
    return tuple(bool(games[day]) and draws[day] < availability for day in range(weeks * DAYS_PER_WEEK))


# --- scoring ----------------------------------------------------------------

@dataclass(frozen=True)
class _Entry:
    player_id: str
    mask: int
    value: float
    expected: float
    activity: tuple[bool, ...]
    market: float
    games: tuple[bool, ...] = ()
    availability: float = 1.0


@dataclass(frozen=True)
class MarginalResult:
    candidates: tuple[CandidateScore, ...]
    status: str  # "ranked" or "fallback"
    explanation: str
    elapsed_seconds: float = 0.0
    fallback_reason: str = ""
    evaluated: int = 0


def _mask(eligibility: Sequence[str]) -> int:
    mask = 0
    for position in eligibility:
        mask |= _BIT.get(position, 0)
    return mask


def _day_lists(entries: Sequence[_Entry], days: int) -> list[list[tuple[float, int]]]:
    lists: list[list[tuple[float, int]]] = []
    for day in range(days):
        active = [(entry.value, entry.mask) for entry in entries if entry.activity[day]]
        active.sort(key=lambda item: -item[0])
        lists.append(active)
    return lists


def _lineup_total(day_lists: Sequence[Sequence[tuple[float, int]]]) -> float:
    return sum(lineup_points(day) for day in day_lists)


def _depths(observation: DraftObservation, config: LeagueConfig, remaining: int, scale: float) -> list[int]:
    """Other-team picks before each of our next ``remaining`` own picks."""

    current = observation.current_pick or (len(observation.history) + 1)
    own: list[int] = []
    order = observation.pick_order
    if len(order) == config.teams and config.our_team in order:
        seat = order.index(config.our_team) + 1
        own = [pick for pick in snake_pick_numbers(seat, config.teams, config.rounds) if pick > current]
    depths = []
    for index in range(1, remaining + 1):
        if index <= len(own):
            others = own[index - 1] - current - index
        else:
            others = index * (config.teams - 1)
        depths.append(max(0, int(round(others * scale))))
    return depths


def _complete(
    base: Sequence[_Entry],
    market_pool: Sequence[_Entry],
    depths: Sequence[int],
    exclude: set[str],
    days: int,
) -> list[_Entry]:
    """Greedy expected completion: best-fit phantom at each future depth."""

    roster = list(base)
    used = set(exclude)
    for depth in depths:
        remaining = [entry for entry in market_pool if entry.player_id not in used]
        if not remaining:
            break
        available = remaining[depth:] or remaining[-1:]
        best_by_mask: dict[int, _Entry] = {}
        for entry in available:
            current = best_by_mask.get(entry.mask)
            if current is None or (entry.expected, entry.player_id) > (current.expected, current.player_id):
                best_by_mask[entry.mask] = entry
        best_choice: tuple[float, int, _Entry] | None = None
        for mask in sorted(best_by_mask):
            phantom = best_by_mask[mask]
            total = _lineup_total(_day_lists([*roster, phantom], days))
            if best_choice is None or total > best_choice[0] + 1e-9:
                best_choice = (total, mask, phantom)
        assert best_choice is not None
        roster.append(best_choice[2])
        used.add(best_choice[2].player_id)
    return roster


def _foundation_fallback(
    observation: DraftObservation,
    config: LeagueConfig,
    reason: str,
    started: float,
    foundation: Sequence[CandidateScore] | None = None,
) -> MarginalResult:
    ranked = tuple(foundation) if foundation is not None else rank_players(observation, config)
    return MarginalResult(
        ranked,
        "fallback",
        f"{ALGORITHM_VERSION} fallback to foundation: {reason}",
        time.perf_counter() - started,
        reason,
    )


def rank_marginal(
    observation: DraftObservation,
    config: LeagueConfig,
    params: MarginalParams | None = None,
    *,
    budget_seconds: float | None = None,
    limit: int | None = None,
    _clock: Any = None,
    _base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> MarginalResult:
    """Rank feasible candidates by expected marginal weekly lineup points.

    On any budget overrun the unmodified foundation ranking is returned with
    ``status == "fallback"`` and the reason, so the caller can journal it.
    """

    p = params or DEFAULT_PARAMS
    _clock = _clock or time.perf_counter
    started = _clock()
    budget = float(budget_seconds if budget_seconds is not None else config.recommendation_budget_seconds)
    deadline = started + max(0.001, budget * _BUDGET_SAFETY)
    foundation = rank_players(observation, config, _base_cache=_base_cache)
    if not foundation:
        return MarginalResult((), "ranked", "no feasible candidates", _clock() - started)
    roster_now = tuple(observation.roster)
    remaining_after = ROSTER_CAPACITY - len(roster_now) - 1
    if remaining_after < 0:
        return _foundation_fallback(observation, config, "roster already full", started, foundation)

    drafted = {event.player_id for event in observation.history} | {item.player_id for item in roster_now}
    by_id: dict[str, LivePlayer] = {}
    for item in observation.available_players:
        if item.player_id and item.player_id not in drafted and item.player_id not in by_id:
            by_id[item.player_id] = item
    weeks = max(1, int(p.weeks))
    days = weeks * DAYS_PER_WEEK
    foundation_rank = {item.player_id: index for index, item in enumerate(foundation, start=1)}

    tags: dict[str, tuple[str, ...]] = {}
    imputed_values: dict[str, dict[str, float]] = {}
    injury_weekly: dict[str, tuple[float, float]] = {}

    def entry_for(player: LivePlayer, market_hint: float) -> _Entry:
        source = player
        if p.use_imputation:
            source, notes, filled = impute_forecast_gaps(player, pf_scale=p.pf_scale)
            if notes:
                tags[player.player_id] = notes
                imputed_values[player.player_id] = filled
        fp, _, _ = custom_fantasy_points(source, config)
        base_gp, _ = _base_games_factor(player)
        haircut, _ = injury_factor(player, config.injury_policy)
        gp_factor = base_gp * haircut
        if config.injury_policy.enabled:
            # Weekly points lost to the haircut, surfaced as an audit line.
            injury_weekly[player.player_id] = (haircut, fp * (gp_factor - base_gp) * GAMES_PER_WEEK)
        return _Entry(
            player.player_id,
            _mask(player.eligibility),
            fp,
            fp * gp_factor,
            activity_vector(player, gp_factor, weeks),
            market_hint,
            game_days(player, weeks),
            gp_factor,
        )

    pool_entries: dict[str, _Entry] = {}
    for item in foundation:
        player = by_id.get(item.player_id)
        if player is None:
            continue
        hint = player.draft_rank if player.draft_rank is not None else (player.adp if player.adp is not None else 10_000.0 + foundation_rank[item.player_id])
        pool_entries[item.player_id] = entry_for(player, float(hint))
    # Players beyond the feasible foundation list still deplete the pool for
    # depth and replacement purposes.
    for pid, player in by_id.items():
        if pid not in pool_entries:
            hint = player.draft_rank if player.draft_rank is not None else (player.adp if player.adp is not None else 10_000.0)
            pool_entries[pid] = entry_for(player, float(hint))
    roster_entries = [entry_for(item, float(item.draft_rank or 0.0)) for item in roster_now]
    # Opponents are modeled as drafting by a blend of draft rank and expected
    # production (ordinal positions within the current pool); the weight is a
    # tuned parameter and the validation matrix stresses it with market-only
    # and custom-only opponent scenarios.
    by_market = {e.player_id: i for i, e in enumerate(sorted(pool_entries.values(), key=lambda e: (e.market, e.player_id)))}
    by_value = {e.player_id: i for i, e in enumerate(sorted(pool_entries.values(), key=lambda e: (-e.expected, e.player_id)))}
    weight = min(1.0, max(0.0, p.opponent_market_weight))
    market_pool = sorted(
        pool_entries.values(),
        key=lambda e: (weight * by_market[e.player_id] + (1.0 - weight) * by_value[e.player_id], e.player_id),
    )
    if _clock() > deadline:
        return _foundation_fallback(observation, config, "budget exhausted while preparing the pool", started, foundation)

    elig = {pid: by_id[pid].eligibility for pid in pool_entries}
    expected = {pid: entry.expected for pid, entry in pool_entries.items()}
    levels = replacement_levels(
        expected,
        elig,
        starter_demand(config, p),
        picks_made=len(observation.history),
    )

    def level_for(eligibility: Sequence[str]) -> float:
        values = [levels[pos][0] for pos in eligibility if pos in levels]
        return min(values) if values else 0.0

    depths = _depths(observation, config, remaining_after, p.depth_scale)
    completion_days = min(days, max(DAYS_PER_WEEK, int(p.completion_days)))
    # Baseline: replacement-level pick now (the real pool player at the
    # replacement depth for the best-fit position), same expected completion.
    candidate_masks = sorted({entry.mask for entry in pool_entries.values() if entry.player_id in foundation_rank})
    base_completion_depths = depths
    replacement_options: dict[int, _Entry] = {}
    for position, (_, pid) in levels.items():
        if pid is not None:
            option = pool_entries[pid]
            replacement_options.setdefault(option.mask, option)
    best_base: tuple[float, list[_Entry]] | None = None
    for mask in sorted(replacement_options):
        option = replacement_options[mask]
        roster = _complete([*roster_entries, option], market_pool, base_completion_depths, {option.player_id}, completion_days)
        total = _lineup_total(_day_lists(roster, completion_days))
        if best_base is None or total > best_base[0] + 1e-9:
            best_base = (total, roster)
        if _clock() > deadline:
            return _foundation_fallback(observation, config, "budget exhausted while building the replacement baseline", started, foundation)
    if best_base is None:
        return _foundation_fallback(observation, config, "no replacement baseline available", started, foundation)
    baseline_total = _lineup_total(_day_lists(best_base[1], days))
    baseline_weekly = baseline_total / weeks

    # Per-mask completion with a strong same-class proxy for the candidate.
    best_expected_by_mask: dict[int, _Entry] = {}
    for item in foundation:
        entry = pool_entries.get(item.player_id)
        if entry is None:
            continue
        current = best_expected_by_mask.get(entry.mask)
        if current is None or entry.expected > current.expected:
            best_expected_by_mask[entry.mask] = entry
    completions: dict[int, tuple[list[list[tuple[float, int]]], list[float], set[str]]] = {}
    for mask in candidate_masks:
        proxy = best_expected_by_mask[mask]
        phantoms = _complete([*roster_entries, proxy], market_pool, base_completion_depths, {proxy.player_id}, completion_days)
        fills = phantoms[len(roster_entries) + 1 :]
        base_roster = [*roster_entries, *fills]
        lists = _day_lists(base_roster, days)
        completions[mask] = (lists, [lineup_points(day) for day in lists], {entry.player_id for entry in fills})
        if _clock() > deadline:
            return _foundation_fallback(observation, config, "budget exhausted while completing expected rosters", started, foundation)

    scored: list[tuple[float, CandidateScore]] = []
    evaluated = 0
    cap = p.max_candidates if p.max_candidates is not None else len(foundation)
    for item in foundation[:cap]:
        entry = pool_entries.get(item.player_id)
        player = by_id.get(item.player_id)
        if entry is None or player is None:
            continue
        evaluated += 1
        if evaluated % 12 == 0 and _clock() > deadline:
            return _foundation_fallback(observation, config, f"budget exhausted after {evaluated} candidates", started, foundation)
        lists, base_points, fill_ids = completions[entry.mask]
        if entry.player_id in fill_ids:
            # The candidate is also one of the expected future fills of its
            # class: rebuild the completion with the candidate itself removed
            # from the pool, so it is never counted twice.
            phantoms = _complete(
                [*roster_entries, entry], market_pool, base_completion_depths, {entry.player_id}, completion_days
            )
            lists_c = _day_lists([*roster_entries, *phantoms[len(roster_entries) + 1 :]], days)
            base_c = [lineup_points(day) for day in lists_c]
        else:
            lists_c, base_c = lists, base_points
        # The candidate's own health is integrated analytically (game day x
        # availability), so the estimate carries no idiosyncratic draw noise.
        total = 0.0
        games = entry.games
        chance = entry.availability
        value = entry.value
        mask = entry.mask
        for day in range(days):
            if games[day]:
                total += chance * _lineup_plus(lists_c[day], value, mask) + (1.0 - chance) * base_c[day]
            else:
                total += base_c[day]
        with_weekly = total / weeks
        marginal = with_weekly - baseline_weekly
        # Raw, replacement and imputation components explain the score.
        raw_weekly = entry.expected * GAMES_PER_WEEK
        level = level_for(player.eligibility)
        vor_weekly = (entry.expected - level) * GAMES_PER_WEEK
        score = marginal + p.vor_weight * vor_weekly
        c_only = float(player.eligibility == ("C",))
        warnings = tuple(dict.fromkeys((*item.warnings, *tags.get(entry.player_id, ()))))
        filled = imputed_values.get(entry.player_id, {})
        components = {
            "custom_fp": entry.value,
            "expected_fp_per_game": entry.expected,
            "raw_weekly_value": raw_weekly,
            "replacement_level_fp_per_game": level,
            "value_over_replacement_weekly": vor_weekly,
            "lineup_with_candidate_weekly": with_weekly,
            "lineup_baseline_weekly": baseline_weekly,
            "marginal_lineup_weekly": marginal,
            "c_only": c_only,
            "center_eligible": float("C" in player.eligibility),
            "imputed_pf": float(filled.get("PF", 0.0)),
            "imputed_dd": float(filled.get("DD", 0.0)),
            "imputed_td": float(filled.get("TD", 0.0)),
            "imputed_field_count": float(len(filled)),
            "foundation_score": item.score,
            "foundation_rank": float(foundation_rank[entry.player_id]),
        }
        if entry.player_id in injury_weekly:
            components["injury_factor"], components["injury_adjustment"] = injury_weekly[entry.player_id]
        explanation = (
            f"marginal lineup {marginal:+.1f} weekly pts "
            f"(standalone {raw_weekly:.1f}; replacement {level:.1f} FP/g; "
            f"over-replacement {vor_weekly:+.1f}"
            + ("; C-only" if c_only else "")
            + (f"; imputed {','.join(sorted(filled))}" if filled else "")
            + (f"; injury adjustment {components['injury_adjustment']:+.1f} weekly pts" if components.get("injury_factor", 1.0) != 1.0 else "")
            + ")"
        )
        scored.append((score, CandidateScore(entry.player_id, player.name, score, components, explanation, warnings)))
    scored.sort(key=lambda row: (-row[0], -row[1].components["foundation_score"], row[1].player_id))
    ranked: list[CandidateScore] = []
    for index, (_, candidate) in enumerate(scored, start=1):
        components = dict(candidate.components)
        components["rank"] = float(index)
        ranked.append(replace(candidate, components=components))
    if limit is not None:
        ranked = ranked[: max(0, int(limit))]
    return MarginalResult(
        tuple(ranked),
        "ranked",
        f"{ALGORITHM_VERSION}: expected marginal optimized weekly lineup points over {weeks} representative weeks",
        _clock() - started,
        "",
        evaluated,
    )


# --- promotion artifact -----------------------------------------------------

PROMOTION_CATALOGS = ("synthetic", "real")


def load_promotion_payload(promotion_path: Any) -> Mapping[str, Any] | None:
    """Return the artifact mapping when it declares this policy, else None."""

    if promotion_path is None:
        return None
    try:
        if isinstance(promotion_path, Mapping):
            payload: Any = promotion_path
        else:
            from pathlib import Path

            path = Path(str(promotion_path))
            if not path.is_file():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping) or payload.get("policy") != ALGORITHM_VERSION:
        return None
    return payload


def params_from_payload(payload: Mapping[str, Any]) -> MarginalParams | None:
    try:
        return MarginalParams(**dict(payload.get("params", {})))
    except (TypeError, ValueError):
        return None


def promotion_valid(payload: Mapping[str, Any], config: LeagueConfig) -> bool:
    """Gate: positive paired 95% lower bound and no seat mean loss over 5%.

    Both catalogs (synthetic and real ESPN) must pass on at least 100
    independent held-out seed clusters with the exact league shape, and the
    artifact must bind the algorithm version and the parameter/config
    fingerprint that the live recommender will actually run.
    """

    if payload.get("status") not in ("valid", "promotable", "approved"):
        return False
    params = params_from_payload(payload)
    if params is None:
        return False
    if payload.get("algorithm_version") != ALGORITHM_VERSION or payload.get("config_fingerprint") != marginal_fingerprint(config, params):
        return False
    metadata = payload.get("metadata", {})
    summaries = payload.get("summaries", ())
    if not isinstance(metadata, Mapping) or not isinstance(summaries, Sequence) or isinstance(summaries, (str, bytes)):
        return False
    try:
        if int(metadata.get("teams", 0)) != 14 or int(metadata.get("opponents", -1)) != 13:
            return False
        if int(metadata.get("picks_per_draft", 0)) != 140 or int(metadata.get("seats_per_seed", 0)) != 14:
            return False
        if metadata.get("foundation_vs_marginal_lineup") is not True:
            return False
        for catalog in PROMOTION_CATALOGS:
            rows = [
                item for item in summaries
                if isinstance(item, Mapping) and item.get("catalog") == catalog and item.get("scenario") == "nominal"
                and item.get("variant") == "marginal"
            ]
            if len(rows) != 1:
                return False
            row = rows[0]
            clusters = int(row.get("independent_seed_clusters", 0))
            lower = float(row.get("paired_bootstrap_95_lower", float("nan")))
            loss = float(row.get("worst_seat_loss_fraction", float("nan")))
            if clusters < 100 or int(row.get("seat_rows", 0)) != 14 * clusters:
                return False
            if not (math.isfinite(lower) and math.isfinite(loss)) or lower <= 0.0 or loss < -0.05:
                return False
    except (TypeError, ValueError, OverflowError):
        return False
    expires = payload.get("expires_at")
    if expires is not None:
        try:
            from .contracts import timestamp_age

            if timestamp_age(str(expires)) >= 0:
                return False
        except (ValueError, TypeError):
            return False
    return True


__all__ = [
    "ALGORITHM_VERSION",
    "load_promotion_payload",
    "params_from_payload",
    "promotion_valid",
    "DEFAULT_PARAMS",
    "MarginalParams",
    "MarginalResult",
    "double_triple_probabilities",
    "impute_forecast_gaps",
    "lineup_points",
    "marginal_fingerprint",
    "rank_marginal",
    "replacement_levels",
    "starter_demand",
]
