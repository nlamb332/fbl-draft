"""Advisory roster-utilization analysis for the 14-team points league.

This module is deliberately separate from the live draft scorer.  It answers a
post-draft question: given a legal ten-player roster and a dated availability
matrix, how many of the six daily slots can the roster actually fill?  It also
provides a schedule-aware candidate comparison for research and review.  No
function here changes the live policy or the frozen validation artifacts.

Schedule matrices are mappings from player id to a sequence of booleans, one
entry per day.  A missing schedule is never silently converted into a forecast:
``analyze_roster`` reports structural legality only, while
``marginal_usable_value`` and ``rank_advisory_candidates`` require a matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

STARTING_SLOTS: tuple[str, ...] = ("C", "G", "G", "F", "F", "UTIL")
ROSTER_SIZE = len(STARTING_SLOTS) + 4
POSITION_TARGETS: Mapping[str, int] = {"C": 2, "G": 3, "F": 3}
SOFT_CENTER_ELIGIBILITY_CAP = 4
SOFT_CENTER_ONLY_CAP = 2
POLICY_VERSION = "utilization-review-v1"


@dataclass(frozen=True)
class DayAllocation:
    """Best legal assignment for one day, prioritizing active starts."""

    day: int
    slots: Mapping[str, str]
    starter_ids: tuple[str, ...]
    bench_ids: tuple[str, ...]
    active_starter_count: int
    active_roster_count: int
    score: float
    inactive_starters: tuple[str, ...] = ()


@dataclass(frozen=True)
class RosterUtilizationReport:
    """Structural and, when supplied, daily utilization metrics."""

    roster_size: int
    legal_final_roster: bool
    eligibility_counts: Mapping[str, int]
    center_only_count: int
    structural_start_capacity: int
    schedule_available: bool
    days_analyzed: int
    active_starter_rate: float | None
    active_starter_rate_of_scheduled_games: float | None
    active_roster_rate: float | None
    mean_busy_collisions: float | None
    mean_inactive_starters: float | None
    daily: tuple[DayAllocation, ...] = ()
    errors: tuple[str, ...] = ()
    scheduled_games_by_player: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "roster_size": self.roster_size,
            "legal_final_roster": self.legal_final_roster,
            "eligibility_counts": dict(self.eligibility_counts),
            "center_only_count": self.center_only_count,
            "structural_start_capacity": self.structural_start_capacity,
            "schedule_available": self.schedule_available,
            "days_analyzed": self.days_analyzed,
            "active_starter_rate": self.active_starter_rate,
            "active_starter_rate_of_scheduled_games": self.active_starter_rate_of_scheduled_games,
            "active_roster_rate": self.active_roster_rate,
            "mean_busy_collisions": self.mean_busy_collisions,
            "mean_inactive_starters": self.mean_inactive_starters,
            "daily": [
                {
                    "day": item.day,
                    "slots": dict(item.slots),
                    "starter_ids": list(item.starter_ids),
                    "bench_ids": list(item.bench_ids),
                    "active_starter_count": item.active_starter_count,
                    "active_roster_count": item.active_roster_count,
                    "score": item.score,
                    "inactive_starters": list(item.inactive_starters),
                }
                for item in self.daily
            ],
            "errors": list(self.errors),
            "scheduled_games_by_player": dict(self.scheduled_games_by_player),
        }


@dataclass(frozen=True)
class ScheduleMatrix:
    """A dated, reproducible player-game matrix used by the advisory review.

    ``available`` describes scheduled team games only unless an explicit
    player health matrix is supplied.  Health is intentionally separate from
    the schedule so a missing injury forecast cannot become a fabricated
    ``False`` or ``True`` value.
    """

    dates: tuple[str, ...]
    available: Mapping[str, tuple[bool, ...]]
    source: str
    captured_at: str
    health_applied: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dates": list(self.dates),
            "available": {key: list(value) for key, value in self.available.items()},
            "source": self.source,
            "captured_at": self.captured_at,
            "health_applied": self.health_applied,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class AdvisoryCandidate:
    """Transparent candidate comparison; advisory only."""

    player_id: str
    name: str
    production_value: float
    marginal_usable_value: float
    marginal_active_starts: float
    balance_delta: float
    score: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "production_value": self.production_value,
            "marginal_usable_value": self.marginal_usable_value,
            "marginal_active_starts": self.marginal_active_starts,
            "balance_delta": self.balance_delta,
            "score": self.score,
            "warnings": list(self.warnings),
        }


def _field(player: Any, key: str, default: Any = None) -> Any:
    if isinstance(player, Mapping):
        return player.get(key, default)
    return getattr(player, key, default)


def _id(player: Any) -> str:
    value = _field(player, "player_id", _field(player, "id", ""))
    return str(value or "")


def _name(player: Any) -> str:
    return str(_field(player, "name", _id(player)) or _id(player))


def _eligibility(player: Any) -> tuple[str, ...]:
    raw = _field(player, "eligibility", _field(player, "positions", ()))
    if isinstance(raw, str):
        raw = raw.replace("|", ",").replace("/", ",").split(",")
    values = tuple(dict.fromkeys(str(item).strip().upper() for item in (raw or ()) if str(item).strip()))
    invalid = set(values) - {"C", "G", "F"}
    if not values or invalid:
        raise ValueError(f"{_name(player)!r} has invalid eligibility {values!r}")
    return values


def _value(player: Any, values: Mapping[str, float] | None = None) -> float:
    """Return an explicit value, or known custom FP from supplied stats.

    Missing scoring fields are omitted and a warning is attached by candidate
    ranking.  This fallback is useful for structural review only; it is not a
    complete player projection.
    """

    if values is not None and _id(player) in values:
        return float(values[_id(player)])
    direct = _field(player, "value", _field(player, "production_value", None))
    if direct is not None:
        return float(direct)
    stats = _field(player, "stats", {}) or {}
    weights = {
        "PTS": 1, "FGM": 2, "FGA": -1, "FTM": 1, "FTA": -1,
        "3PM": 1, "REB": 2, "AST": 2, "STL": 4, "BLK": 4,
        "TO": -2, "PF": -1, "TF": -2, "EJ": -5, "DD": 3,
        "TD": 5, "QD": 100,
    }
    total = 0.0
    for key, weight in weights.items():
        try:
            total += float(stats.get(key, stats.get(key.lower(), 0.0)) or 0.0) * weight
        except (TypeError, ValueError):
            continue
    return total


def _missing_warning(player: Any) -> str | None:
    missing = _field(player, "missing_stats", ()) or ()
    if missing:
        return "production value omits missing scoring fields: " + ", ".join(str(item) for item in missing)
    return None


def _health_warnings(player: Any) -> tuple[str, ...]:
    warnings: list[str] = []
    status = _field(player, "injury_status", None)
    if status:
        warnings.append(
            f"injury status at capture: {status}; this does not establish draft-date availability"
        )
    if bool(_field(player, "health_status_unknown", False)):
        warnings.append("player health at draft date is unknown; schedule-only value is advisory")
    return tuple(warnings)


def _can_fill(players: Sequence[Any], slots: Sequence[str] = STARTING_SLOTS) -> bool:
    if len(players) < len(slots):
        return False
    used: set[int] = set()

    def visit(slot_index: int) -> bool:
        if slot_index == len(slots):
            return True
        slot = slots[slot_index]
        for index, player in enumerate(players):
            if index in used or (slot != "UTIL" and slot not in _eligibility(player)):
                continue
            used.add(index)
            if visit(slot_index + 1):
                return True
            used.remove(index)
        return False

    return visit(0)


def _schedule_days(schedule: Mapping[str, Sequence[bool]]) -> int:
    lengths = {len(values) for values in schedule.values()}
    if not lengths or len(lengths) != 1 or next(iter(lengths)) <= 0:
        raise ValueError("schedule must contain equally sized, non-empty day sequences")
    return next(iter(lengths))


_TEAM_ALIASES = {
    "NYK": "NY", "NY": "NY", "SAS": "SA", "SA": "SA", "UTA": "UTAH",
    "UTAH": "UTAH", "WAS": "WSH", "WSH": "WSH", "PHX": "PHX",
    "GSW": "GS", "GS": "GS", "NOP": "NO", "NO": "NO", "BKN": "BKN",
    "LAL": "LAL", "LAC": "LAC", "MIL": "MIL", "MIA": "MIA",
}


def _team(player: Any) -> str:
    """Read the captured team code without inventing one when it is absent."""

    raw = _field(player, "nba_team", _field(player, "team", _field(player, "schedule_group", "")))
    return str(raw or "").strip().upper()


def _date_strings(values: Sequence[str], *, sort_values: bool = True) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values if str(value).strip())
    if not normalized:
        raise ValueError("dates must contain at least one ISO date")
    try:
        if len(set(normalized)) != len(normalized):
            raise ValueError("dates must be unique")
        ordered = tuple(sorted(normalized, key=date.fromisoformat)) if sort_values else normalized
    except ValueError as exc:
        raise ValueError("dates must use ISO YYYY-MM-DD values") from exc
    return ordered


def build_schedule_matrix(
    players: Sequence[Any],
    *,
    dates: Sequence[str],
    team_games: Mapping[str, Sequence[str]],
    player_teams: Mapping[str, str] | None = None,
    health: Mapping[str, Sequence[bool]] | None = None,
    source: str = "",
    captured_at: str = "",
) -> ScheduleMatrix:
    """Build a dated player matrix from official team games and optional health.

    Team-game dates are facts from a captured schedule.  A health matrix is an
    independent, explicit input; when it is absent, this function reports
    schedule-only availability and emits a warning.  Missing player/team
    mapping or incomplete health rows fail loudly rather than turning unknown
    availability into a guessed value.
    """

    raw_dates = tuple(str(value).strip() for value in dates)
    if health is None:
        ordered_dates = _date_strings(raw_dates)
    else:
        if len(set(raw_dates)) != len(raw_dates) or tuple(sorted(raw_dates, key=date.fromisoformat)) != raw_dates:
            raise ValueError("health matrices require ordered unique ISO dates")
        ordered_dates = _date_strings(raw_dates, sort_values=False)
    date_set = set(ordered_dates)
    normalized_games: dict[str, set[str]] = {}
    for raw_team, raw_dates in team_games.items():
        team = _TEAM_ALIASES.get(str(raw_team).strip().upper(), str(raw_team).strip().upper())
        game_dates = _date_strings(tuple(raw_dates)) if raw_dates else ()
        normalized_games.setdefault(team, set()).update(date_set.intersection(game_dates))

    player_teams = player_teams or {}
    matrix: dict[str, tuple[bool, ...]] = {}
    warnings: list[str] = []
    for player in players:
        player_id = _id(player)
        if not player_id:
            raise ValueError("schedule player ids must be non-empty")
        raw_team = player_teams.get(player_id, _team(player))
        team = _TEAM_ALIASES.get(str(raw_team).strip().upper(), str(raw_team).strip().upper())
        if not team or team not in normalized_games:
            raise ValueError(f"no captured team schedule for player {player_id!r} ({raw_team!r})")
        team_row = tuple(day in normalized_games[team] for day in ordered_dates)
        if health is None:
            row = team_row
        else:
            if player_id not in health or len(health[player_id]) != len(ordered_dates):
                raise ValueError(f"health row for {player_id!r} must match captured dates")
            health_row = tuple(health[player_id])
            if any(type(flag) is not bool for flag in health_row):
                raise ValueError(f"health row for {player_id!r} must contain only boolean entries")
            row = tuple(team_active and health_row[index] for index, team_active in enumerate(team_row))
        matrix[player_id] = row

    if health is None:
        warnings.append("schedule-only matrix: player health/availability was not supplied")
    return ScheduleMatrix(
        dates=ordered_dates,
        available=matrix,
        source=source,
        captured_at=captured_at,
        health_applied=health is not None,
        warnings=tuple(warnings),
    )


def repeat_schedule_pattern(schedule: ScheduleMatrix, repeats: int) -> ScheduleMatrix:
    """Repeat a captured week for mechanics sensitivity, clearly labeled synthetic."""

    repeats = int(repeats)
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if repeats == 1:
        return schedule
    dates = tuple(
        f"cycle-{cycle + 1:02d}:{value}"
        for cycle in range(repeats)
        for value in schedule.dates
    )
    available = {
        player_id: tuple(value for _ in range(repeats) for value in row)
        for player_id, row in schedule.available.items()
    }
    warnings = tuple(dict.fromkeys((*schedule.warnings, "repeated fixture is synthetic and is not a future NBA forecast")))
    return ScheduleMatrix(
        dates=dates,
        available=available,
        source=f"{schedule.source} (repeated fixture sensitivity)",
        captured_at=schedule.captured_at,
        health_applied=schedule.health_applied,
        warnings=warnings,
    )


def _active(player: Any, day: int, schedule: Mapping[str, Sequence[bool]] | None) -> bool:
    if schedule is None:
        return True
    player_id = _id(player)
    if player_id not in schedule:
        raise ValueError(f"schedule has no player {player_id!r}")
    return bool(schedule[player_id][day])


def allocate_day(
    roster: Sequence[Any],
    *,
    day: int = 0,
    schedule: Mapping[str, Sequence[bool]] | None = None,
    values: Mapping[str, float] | None = None,
) -> DayAllocation:
    """Allocate one legal six-slot lineup by active starts, then value."""

    if len(roster) < len(STARTING_SLOTS):
        raise ValueError("at least six players are required to allocate a lineup")
    if schedule is not None and day >= _schedule_days(schedule):
        raise ValueError("day is outside the supplied schedule")
    ids = [_id(player) for player in roster]
    if len(set(ids)) != len(ids) or any(not player_id for player_id in ids):
        raise ValueError("roster player ids must be unique and non-empty")
    active = [_active(player, day, schedule) for player in roster]
    score_values = [_value(player, values) for player in roster]
    slots = ("C", "G1", "G2", "F1", "F2", "UTIL")

    # tuple ordering is intentional: active starts are primary, then points,
    # then lexicographically stable player ids for reproducible reports.
    best: tuple[int, float, tuple[int, ...]] | None = None

    def visit(slot_index: int, used: frozenset[int], chosen: tuple[int, ...], count: int, score: float) -> None:
        nonlocal best
        if slot_index == len(slots):
            result = (count, score, chosen)
            if best is None or result[:2] > best[:2] or (result[:2] == best[:2] and tuple(ids[i] for i in chosen) < tuple(ids[i] for i in best[2])):
                best = result
            return
        slot = slots[slot_index]
        position = slot[0] if slot != "UTIL" else "UTIL"
        for index, player in enumerate(roster):
            if index in used or (position != "UTIL" and position not in _eligibility(player)):
                continue
            visit(
                slot_index + 1,
                used | {index},
                chosen + (index,),
                count + int(active[index]),
                score + (score_values[index] if active[index] else 0.0),
            )

    visit(0, frozenset(), (), 0, 0.0)
    if best is None:
        raise ValueError("roster cannot fill C/G/G/F/F/UTIL")
    count, score, chosen = best
    slot_map = {slot: ids[index] for slot, index in zip(slots, chosen)}
    starters = tuple(ids[index] for index in chosen)
    bench = tuple(player_id for player_id in ids if player_id not in set(starters))
    inactive = tuple(player_id for index, player_id in zip(chosen, starters) if not active[index])
    return DayAllocation(
        day=day,
        slots=slot_map,
        starter_ids=starters,
        bench_ids=bench,
        active_starter_count=count,
        active_roster_count=sum(active),
        score=score,
        inactive_starters=inactive,
    )


def analyze_roster(
    roster: Sequence[Any],
    *,
    schedule: Mapping[str, Sequence[bool]] | None = None,
    values: Mapping[str, float] | None = None,
) -> RosterUtilizationReport:
    """Measure legality and daily active-start utilization.

    Without a schedule the report intentionally returns ``None`` for daily
    rates.  This protects the review from turning roster legality into an
    unsupported season or week forecast.
    """

    counts = {position: sum(position in _eligibility(player) for player in roster) for position in ("C", "G", "F")}
    center_only = sum("C" in _eligibility(player) and not ({"G", "F"} & set(_eligibility(player))) for player in roster)
    legal = len(roster) == ROSTER_SIZE and _can_fill(roster)
    errors: list[str] = []
    if len(roster) != ROSTER_SIZE:
        errors.append(f"expected {ROSTER_SIZE} active-roster players, found {len(roster)}")
    if not legal:
        errors.append("roster cannot fill C/G/G/F/F/UTIL")
    if schedule is None:
        return RosterUtilizationReport(
            roster_size=len(roster), legal_final_roster=legal,
            eligibility_counts=counts, center_only_count=center_only,
            structural_start_capacity=len(STARTING_SLOTS), schedule_available=False,
            days_analyzed=0, active_starter_rate=None, active_roster_rate=None,
            active_starter_rate_of_scheduled_games=None,
            mean_busy_collisions=None, mean_inactive_starters=None, errors=tuple(errors),
            scheduled_games_by_player={},
        )
    days = _schedule_days(schedule)
    allocations = tuple(allocate_day(roster, day=day, schedule=schedule, values=values) for day in range(days)) if legal else ()
    if not allocations:
        return RosterUtilizationReport(
            roster_size=len(roster), legal_final_roster=legal,
            eligibility_counts=counts, center_only_count=center_only,
            structural_start_capacity=len(STARTING_SLOTS), schedule_available=True,
            days_analyzed=days, active_starter_rate=0.0, active_roster_rate=0.0,
            active_starter_rate_of_scheduled_games=0.0,
            mean_busy_collisions=0.0, mean_inactive_starters=float(len(STARTING_SLOTS)),
            errors=tuple(errors), scheduled_games_by_player={_id(player): 0 for player in roster},
        )
    active_starters = sum(item.active_starter_count for item in allocations)
    active_roster = sum(item.active_roster_count for item in allocations)
    scheduled_games = sum(active_roster for active_roster in (
        sum(1 for active_day in schedule[_id(player)] if active_day) for player in roster
    ))
    return RosterUtilizationReport(
        roster_size=len(roster), legal_final_roster=legal,
        eligibility_counts=counts, center_only_count=center_only,
        structural_start_capacity=len(STARTING_SLOTS), schedule_available=True,
        days_analyzed=days,
        active_starter_rate=active_starters / float(len(STARTING_SLOTS) * days),
        active_starter_rate_of_scheduled_games=(active_starters / float(scheduled_games)) if scheduled_games else 0.0,
        active_roster_rate=active_roster / float(len(roster) * days),
        mean_busy_collisions=sum(max(0, item.active_roster_count - len(STARTING_SLOTS)) for item in allocations) / float(days),
        mean_inactive_starters=sum(len(STARTING_SLOTS) - item.active_starter_count for item in allocations) / float(days),
        daily=allocations, errors=tuple(errors),
        scheduled_games_by_player={
            _id(player): sum(1 for active_day in schedule[_id(player)] if active_day)
            for player in roster
        },
    )


def _balance_score(roster: Sequence[Any]) -> float:
    counts = {position: sum(position in _eligibility(player) for player in roster) for position in POSITION_TARGETS}
    shortfall = sum(max(0, POSITION_TARGETS[position] - counts[position]) for position in POSITION_TARGETS)
    center_excess = max(0, counts["C"] - SOFT_CENTER_ELIGIBILITY_CAP)
    center_only = sum("C" in _eligibility(player) and not ({"G", "F"} & set(_eligibility(player))) for player in roster)
    center_only_excess = max(0, center_only - SOFT_CENTER_ONLY_CAP)
    # Higher is better.  The two center terms are soft congestion penalties;
    # they do not make a candidate ineligible and are intentionally modest.
    return -float(shortfall + center_excess + center_only_excess)


def marginal_usable_value(
    candidate: Any,
    roster: Sequence[Any],
    *,
    schedule: Mapping[str, Sequence[bool]],
    values: Mapping[str, float] | None = None,
    drop_player_id: str | None = None,
) -> tuple[float, float]:
    """Return (daily-score delta, active-starter delta) for a candidate.

    A full ten-player roster requires ``drop_player_id``.  The score is the
    exact daily assignment score over the supplied schedule; it is a modeled
    marginal usable value, not a real-game forecast.
    """

    days = _schedule_days(schedule)
    baseline = list(roster)
    candidate_id = _id(candidate)
    if candidate_id in {_id(player) for player in baseline}:
        raise ValueError("candidate already appears in roster")
    if len(baseline) >= ROSTER_SIZE:
        if drop_player_id is None:
            raise ValueError("drop_player_id is required when roster is full")
        kept = [player for player in baseline if _id(player) != drop_player_id]
        if len(kept) != len(baseline) - 1:
            raise ValueError(f"drop player {drop_player_id!r} is not in roster")
        comparison = kept + [candidate]
    elif drop_player_id is not None:
        # A partial-roster comparison may still name a planned replacement.
        comparison = [player for player in baseline if _id(player) != drop_player_id] + [candidate]
    else:
        comparison = baseline + [candidate]
    if len(baseline) < len(STARTING_SLOTS) or len(comparison) < len(STARTING_SLOTS):
        raise ValueError("marginal lineup comparison requires at least six current players")
    before = tuple(allocate_day(baseline, day=day, schedule=schedule, values=values) for day in range(days))
    after = tuple(allocate_day(comparison, day=day, schedule=schedule, values=values) for day in range(days))
    score_delta = sum(item.score for item in after) - sum(item.score for item in before)
    start_delta = sum(item.active_starter_count for item in after) - sum(item.active_starter_count for item in before)
    return float(score_delta), float(start_delta)


def rank_advisory_candidates(
    candidates: Sequence[Any],
    roster: Sequence[Any],
    *,
    schedule: Mapping[str, Sequence[bool]],
    values: Mapping[str, float] | None = None,
    drop_player_id: str | None = None,
    production_weight: float = 1.0,
    marginal_weight: float = 1.0,
    balance_weight: float = 2.0,
) -> tuple[AdvisoryCandidate, ...]:
    """Rank candidates by production plus schedule-aware marginal utility.

    This policy is an advisory research candidate.  It is purposefully not
    imported by ``valuation.py`` or the coordinator.  The balance term is a
    soft preference for guard/forward coverage and against center congestion.
    """

    before_balance = _balance_score(roster)
    ranked: list[AdvisoryCandidate] = []
    for candidate in candidates:
        warnings: list[str] = []
        warning = _missing_warning(candidate)
        if warning:
            warnings.append(warning)
        warnings.extend(_health_warnings(candidate))
        delta, starts = marginal_usable_value(
            candidate, roster, schedule=schedule, values=values, drop_player_id=drop_player_id,
        )
        candidate_balance = _balance_score([*roster, candidate] if drop_player_id is None else [
            *[player for player in roster if _id(player) != drop_player_id], candidate,
        ])
        production = _value(candidate, values)
        balance_delta = candidate_balance - before_balance
        score = production_weight * production + marginal_weight * delta + balance_weight * balance_delta
        ranked.append(AdvisoryCandidate(
            player_id=_id(candidate), name=_name(candidate), production_value=production,
            marginal_usable_value=delta, marginal_active_starts=starts,
            balance_delta=balance_delta, score=score, warnings=tuple(warnings),
        ))
    ranked.sort(key=lambda item: (-item.score, item.player_id))
    return tuple(ranked)


__all__ = [
    "AdvisoryCandidate", "DayAllocation", "POLICY_VERSION", "RosterUtilizationReport",
    "ScheduleMatrix", "STARTING_SLOTS", "allocate_day", "analyze_roster",
    "build_schedule_matrix", "marginal_usable_value", "rank_advisory_candidates",
    "repeat_schedule_pattern",
]
