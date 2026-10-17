"""Deterministic campaign Monte Carlo draft and season evaluator.

This module is deliberately a small, standard-library orchestration layer.  It
adapts the existing typed draft policies to a repeatable 14-team/10-round
simulation and keeps forecast lineups separate from realized outcomes.  The
campaign data contract is duck typed so that the data loader can evolve without
creating an import cycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import date, timedelta
from types import SimpleNamespace
from collections import OrderedDict
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from mock_draft.fixture import (
    DRAFT_ROUNDS,
    PICKS_PER_DRAFT,
    SCORING_WEIGHTS,
    STARTING_SLOTS,
    TOTAL_TEAMS,
    DraftConfig,
    PlayerRecord,
    check_roster_shape,
    score_stats,
)

from .contracts import DraftObservation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity
from .marginal_lineup import DEFAULT_PARAMS, rank_marginal
from .valuation import rank_players
from .validation_runner import _candidate_legal_fast, _global_position_feasible

if TYPE_CHECKING:  # pragma: no cover - only for static checkers
    from .campaign_data import SeasonDataset


POLICIES = ("marginal", "foundation", "custom_ppg", "custom_total", "positional_vor", "market", "espn_rank")
OPPONENT_MODES = ("nominal", "market", "custom")
_REQUIRED = {"C": 1, "G": 2, "F": 2}
_RANK_CACHE_LIMIT = 1024
_RANK_CACHE: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()
_FOUNDATION_BASE_CACHE: OrderedDict[str, dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]]] = OrderedDict()
_RANK_CACHE_ENABLED = True


def clear_policy_cache() -> None:
    """Clear bounded ranking and production-feature caches between campaigns."""

    _RANK_CACHE.clear()
    _FOUNDATION_BASE_CACHE.clear()


def set_policy_cache_enabled(enabled: bool) -> None:
    """Enable/disable exact ranking memoization for audited benchmarks."""

    global _RANK_CACHE_ENABLED
    _RANK_CACHE_ENABLED = bool(enabled)
    if not _RANK_CACHE_ENABLED:
        _RANK_CACHE.clear()


def _catalog_fingerprint(players: Sequence[Any]) -> str:
    payload = []
    for item in sorted(players, key=lambda player: str(player.player_id)):
        payload.append({
            "player_id": str(item.player_id),
            "name": str(getattr(item, "name", "")),
            "eligibility": tuple(str(value) for value in item.eligibility),
            "stats": {str(key): float(value) for key, value in sorted(_stats(item).items())},
            "availability": float(getattr(item, "projected_availability", 1.0)),
            "market_rank": float(getattr(item, "market_rank", 9999.0)),
            "adp": float(getattr(item, "adp", 9999.0)),
            "upside": float(getattr(item, "upside", 0.0)),
            "schedule_group": str(getattr(item, "schedule_group", "") or ""),
            "espn_draft_rank": getattr(item, "espn_draft_rank", None),
        })
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _state_fingerprint(roster: Sequence[Any], history: Sequence[Any]) -> tuple[tuple[str, ...], tuple[tuple[int, str, str], ...]]:
    roster_ids = tuple(sorted(str(item.player_id) for item in roster))
    events = []
    for index, event in enumerate(history, 1):
        if isinstance(event, Mapping):
            pick = int(event.get("pick_number", event.get("pick", index)))
            team = str(event.get("team", ""))
            player_id = str(event.get("player_id", event.get("id", "")))
        else:
            pick = int(getattr(event, "pick_number", index))
            team = str(getattr(event, "team", ""))
            player_id = str(getattr(event, "player_id", ""))
        events.append((pick, team, player_id))
    return roster_ids, tuple(events)


def _event_player_id(event: Any) -> str:
    if isinstance(event, Mapping):
        return str(event.get("player_id", event.get("id", "")))
    return str(getattr(event, "player_id", ""))


def _base_cache_for(catalog_fp: str) -> dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]]:
    base = _FOUNDATION_BASE_CACHE.pop(catalog_fp, None)
    if base is None:
        base = {}
    _FOUNDATION_BASE_CACHE[catalog_fp] = base
    while len(_FOUNDATION_BASE_CACHE) > 16:
        _FOUNDATION_BASE_CACHE.popitem(last=False)
    return base


def _clone_ranked(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"player_id": str(row["player_id"]), "score": float(row["score"]), "components": dict(row.get("components", {}))}
        for row in rows
    ]


def _team_name(seat: int, our_seat: int, config: LeagueConfig | None = None) -> str:
    return (config or LeagueConfig()).our_team if seat == our_seat else str(seat)


def _snake_order() -> tuple[int, ...]:
    return tuple(
        seat
        for round_number in range(DRAFT_ROUNDS)
        for seat in (range(1, TOTAL_TEAMS + 1) if round_number % 2 == 0 else range(TOTAL_TEAMS, 0, -1))
    )


def _hash_int(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _hash_unit(*parts: object) -> float:
    return (_hash_int(*parts) + 0.5) / (2**64)


def _hash_normal(*parts: object) -> float:
    # Box-Muller using two independent hash-derived uniforms.
    u1 = max(_hash_unit(*parts, "u1"), 1e-12)
    u2 = _hash_unit(*parts, "u2")
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _stats(player: Any) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in (getattr(player, "stats", {}) or {}).items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result[str(key).upper().strip()] = number
    return result


def _player_ppg(player: Any) -> float:
    """Forecast custom points per game; missing forecast fields contribute zero."""

    return float(score_stats(_stats(player)))


def _live(player: Any) -> LivePlayer:
    stats = _stats(player)
    # Preserve missingness for the production marginal imputer.  A zero value
    # is a supplied forecast; an absent key remains explicitly unknown.
    missing = tuple(key for key in SCORING_WEIGHTS if key not in stats)
    return LivePlayer(
        player_id=str(player.player_id),
        name=str(player.name),
        eligibility=tuple(player.eligibility),
        stats=stats,
        projected_gp=None,
        draft_rank=max(1.0, float(getattr(player, "market_rank", 9999.0))),
        adp=max(1.0, float(getattr(player, "adp", getattr(player, "market_rank", 9999.0)))),
        availability=float(getattr(player, "projected_availability", 1.0)),
        nba_team=str(getattr(player, "schedule_group", "") or "") or None,
        source=str(getattr(player, "source_label", "campaign")),
        missing_stats=missing,
    )


def _observation(
    players: Sequence[Any],
    roster: Sequence[Any],
    history: Sequence[PickEvent],
    our_seat: int,
    config: LeagueConfig | None = None,
) -> DraftObservation:
    cfg = config or LeagueConfig()
    normalized_history = tuple(event if isinstance(event, PickEvent) else _coerce_event(event) for event in history)
    drafted = {event.player_id for event in normalized_history}
    available = [item for item in players if item.player_id not in drafted]
    team_order = tuple(_team_name(seat, our_seat, cfg) for seat in range(1, TOTAL_TEAMS + 1))
    current = len(history) + 1
    return DraftObservation(
        session_id="campaign-engine",
        room=RoomIdentity("1", "2027", str(our_seat), True),
        observed_at="2026-01-01T00:00:00+00:00",
        revision=current,
        phase="draft",
        current_pick=current,
        on_clock_team=_team_name(_snake_order()[len(history)], our_seat, cfg) if len(history) < PICKS_PER_DRAFT else None,
        pick_order=team_order,
        history=normalized_history,
        roster=tuple(_live(item) for item in roster),
        available_players=tuple(_live(item) for item in available),
        known_players=tuple(_live(item) for item in players),
        history_complete=True,
        available_pool_complete=True,
    )


def _candidate_dict(candidate: Any) -> dict[str, Any]:
    return {
        "player_id": str(candidate.player_id),
        "score": float(candidate.score),
        "components": {str(key): float(value) for key, value in dict(getattr(candidate, "components", {})).items()},
    }


def _foundation_rank(
    players: Sequence[Any],
    roster: Sequence[Any],
    history: Sequence[PickEvent],
    our_seat: int,
    config: LeagueConfig,
    base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> list[dict[str, Any]]:
    observation = _observation(players, roster, history, our_seat, config)
    return [_candidate_dict(item) for item in rank_players(observation, config, _base_cache=base_cache)]


def _marginal_rank(
    players: Sequence[Any],
    roster: Sequence[Any],
    history: Sequence[PickEvent],
    our_seat: int,
    config: LeagueConfig,
    base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> list[dict[str, Any]]:
    observation = _observation(players, roster, history, our_seat, config)
    # DEFAULT_PARAMS is passed unchanged intentionally: campaign comparisons
    # must reflect the promoted policy's own parameters.
    result = rank_marginal(observation, config, DEFAULT_PARAMS, _base_cache=base_cache)
    rows = [_candidate_dict(item) for item in result.candidates]
    if result.status == "fallback":
        for row in rows:
            row["components"]["policy_fallback"] = 1.0
    return rows


def _custom_rank(players: Sequence[Any], roster: Sequence[Any], policy: str) -> list[dict[str, Any]]:
    values = {item.player_id: _player_ppg(item) for item in players}
    if policy == "custom_ppg":
        rows = [
            (values[item.player_id], item, {"custom_ppg": values[item.player_id]})
            for item in players
        ]
    elif policy == "custom_total":
        rows = [
            (
                values[item.player_id] * float(getattr(item, "projected_availability", 1.0)) * 82.0,
                item,
                {
                    "custom_ppg": values[item.player_id],
                    "availability": float(getattr(item, "projected_availability", 1.0)),
                    "season_total": values[item.player_id] * float(getattr(item, "projected_availability", 1.0)) * 82.0,
                },
            )
            for item in players
        ]
    else:
        replacement: dict[str, float] = {}
        for position, demand in _REQUIRED.items():
            ranked = sorted((values[item.player_id] for item in players if position in item.eligibility), reverse=True)
            replacement[position] = ranked[min(len(ranked) - 1, TOTAL_TEAMS * demand - 1)] if ranked else 0.0
        rows = []
        for item in players:
            level = min((replacement.get(position, 0.0) for position in item.eligibility), default=0.0)
            score = values[item.player_id] - level
            rows.append((score, item, {"custom_ppg": values[item.player_id], "replacement_ppg": level, "vor_ppg": score}))
    rows.sort(key=lambda row: (-row[0], row[1].player_id))
    return [{"player_id": item.player_id, "score": float(score), "components": components} for score, item, components in rows]


def _market_rank(players: Sequence[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in players:
        market = float(getattr(item, "market_rank", 9999.0))
        adp = float(getattr(item, "adp", market))
        score = 100.0 / max(1.0, (market + adp) / 2.0)
        rows.append((score, item, {"market_rank": market, "adp": adp, "market_score": score}))
    rows.sort(key=lambda row: (-row[0], row[1].player_id))
    return [{"player_id": item.player_id, "score": float(score), "components": components} for score, item, components in rows]


def rank_policy(
    players: Sequence[Any],
    roster: Sequence[Any],
    history: Sequence[PickEvent],
    our_seat: int,
    policy: str,
    config: LeagueConfig | None = None,
) -> list[dict[str, Any]]:
    """Rank currently available players and retain auditable score components."""

    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; expected one of {POLICIES}")
    drafted = {_event_player_id(event) for event in history} | {item.player_id for item in roster}
    available = [item for item in players if item.player_id not in drafted]
    cfg = config or LeagueConfig()
    catalog_fp = _catalog_fingerprint(players)
    roster_ids, history_key = _state_fingerprint(roster, history)
    config_key = (
        cfg.teams,
        cfg.rounds,
        tuple(cfg.starting_slots),
        cfg.bench_slots,
        tuple(sorted((str(key), float(value)) for key, value in cfg.scoring.items())),
        bool(cfg.double_double_includes_triple_double),
    )
    cache_key = (catalog_fp, our_seat, policy, roster_ids, history_key, config_key)
    if _RANK_CACHE_ENABLED:
        cached = _RANK_CACHE.pop(cache_key, None)
        if cached is not None:
            _RANK_CACHE[cache_key] = cached
            return _clone_ranked(cached)
    base_cache = _base_cache_for(catalog_fp)
    if policy == "foundation":
        ranked = _foundation_rank(players, roster, history, our_seat, cfg, base_cache)
    elif policy == "marginal":
        ranked = _marginal_rank(players, roster, history, our_seat, cfg, base_cache)
    elif policy in {"custom_ppg", "custom_total", "positional_vor"}:
        ranked = _custom_rank(available, roster, policy)
    elif policy == "espn_rank":
        ranked = rank_espn_only(available)
    else:
        ranked = _market_rank(available)
    # A marginal timeout is wall-clock dependent.  Do not memoize a fallback
    # result that could poison a later continuation with a fresh deadline.
    fallback = any(float(row.get("components", {}).get("policy_fallback", 0.0)) for row in ranked)
    if _RANK_CACHE_ENABLED and not fallback:
        _RANK_CACHE[cache_key] = _clone_ranked(ranked)
        while len(_RANK_CACHE) > _RANK_CACHE_LIMIT:
            _RANK_CACHE.popitem(last=False)
    return ranked


def rank_espn_only(players: Sequence[Any]) -> list[dict[str, Any]]:
    """Use an explicitly sourced ESPN RK, never market order or custom value."""
    ranked = []
    for player in players:
        rank = getattr(player, "espn_draft_rank", None)
        if isinstance(rank, bool) or not isinstance(rank, (float, int)) or not math.isfinite(rank) or rank < 1:
            raise ValueError(f"missing or invalid explicit ESPN RK for {player.player_id}")
        ranked.append({"player_id": player.player_id, "score": -float(rank), "components": {"espn_draft_rank": float(rank)}})
    return sorted(ranked, key=lambda row: (-row["score"], row["player_id"]))


def _position_counts(players: Sequence[Any]) -> dict[str, int]:
    return {position: sum(position in item.eligibility for item in players) for position in _REQUIRED}


def _legal_candidate(candidate: Any, seat: int, rosters: Mapping[int, Sequence[Any]], available: Sequence[Any]) -> bool:
    config = DraftConfig()
    team_roster = rosters[seat]
    remaining = config.roster_size - len(team_roster) - 1
    available_counts = _position_counts(available)
    current = tuple((*team_roster, candidate))
    if len(current) > config.roster_size or remaining < 0:
        return False
    patterns: dict[tuple[str, ...], int] = {}
    for item in available:
        pattern = tuple(sorted(item.eligibility))
        patterns[pattern] = patterns.get(pattern, 0) + 1
    if not _candidate_legal_fast(
        candidate,
        team_roster,
        available,
        available_counts,
        remaining,
        config,
        exact=True,
        all_pattern_counts=patterns,
    ):
        return False
    base_deficits = {
        position: sum(max(0, required - sum(position in item.eligibility for item in team_roster)) for team_roster in rosters.values())
        for position, required in _REQUIRED.items()
    }
    return _global_position_feasible(
        candidate,
        seat=seat,
        rosters=rosters,
        available_counts=available_counts,
        base_deficits=base_deficits,
    )


def _opponent_rank(
    available: Sequence[Any],
    seat: int,
    pick_number: int,
    rosters: Mapping[int, Sequence[Any]],
    mode: str,
    seed: int,
) -> list[tuple[float, Any]]:
    if mode not in OPPONENT_MODES:
        raise ValueError(f"unknown opponent mode {mode!r}; expected one of {OPPONENT_MODES}")
    current = rosters[seat]
    deficits = {position: max(0, required - sum(position in item.eligibility for item in current)) for position, required in _REQUIRED.items()}
    # Normalize utilities by value range rather than ordinal percentiles.  A
    # 16-point production edge among elite players must remain material after
    # the pool is expanded with replacement-level players.
    production = {item.player_id: _player_ppg(item) for item in available}
    season_total = {item.player_id: production[item.player_id] * float(getattr(item, "projected_availability", 1.0)) for item in available}

    def value_range(values: Mapping[str, float]) -> dict[str, float]:
        if not values:
            return {}
        low = min(values.values())
        high = max(values.values())
        span = high - low
        return {key: (value - low) / span if span > 1e-12 else 1.0 for key, value in values.items()}

    custom_utility = value_range(production)
    total_utility = value_range(season_total)
    market_order = sorted(available, key=lambda item: (float(getattr(item, "market_rank", 9999.0)), float(getattr(item, "adp", 9999.0)), item.player_id))
    production_curve = sorted(production.values(), reverse=True)
    market_curve_raw = {item.player_id: production_curve[index] for index, item in enumerate(market_order)}
    market_utility = value_range(market_curve_raw)

    rows = []
    for item in available:
        ppg = production[item.player_id]
        market = market_utility[item.player_id]
        custom = custom_utility[item.player_id]
        total = total_utility[item.player_id]
        fit = sum(deficits.get(position, 0) for position in item.eligibility)
        if mode == "market":
            market_weight, custom_weight, total_weight = 0.78, 0.10, 0.08
        elif mode == "custom":
            market_weight, custom_weight, total_weight = 0.10, 0.78, 0.08
        else:
            profile = seat % 4
            if profile == 0:  # market loyalist
                market_weight, custom_weight, total_weight = 0.64, 0.12, 0.14
            elif profile == 1:  # custom production drafter
                market_weight, custom_weight, total_weight = 0.12, 0.64, 0.14
            elif profile == 2:  # availability/season-total drafter
                market_weight, custom_weight, total_weight = 0.18, 0.22, 0.52
            else:  # balanced drafter
                market_weight, custom_weight, total_weight = 0.35, 0.35, 0.20
        fit_weight = 0.0 if len(current) < 2 else min(0.08, 0.02 * (len(current) - 1))
        base = market_weight * market + custom_weight * custom + total_weight * total + fit_weight * min(1.0, fit / 3.0)
        noise = (_hash_unit(seed, seat, pick_number, item.player_id) * 2.0 - 1.0) * 0.02
        rows.append((base + noise, item))
    rows.sort(key=lambda row: (-row[0], row[1].player_id))
    return rows


def _coerce_event(value: Any) -> PickEvent:
    if isinstance(value, PickEvent):
        return value
    if isinstance(value, Mapping):
        return PickEvent(int(value["pick_number"]), str(value["team"]), str(value["player_id"]), str(value.get("player_name", "")))
    raise TypeError("prefix entries must be PickEvent or mapping values")


def simulate_draft(
    players: Sequence[Any],
    our_seat: int,
    seed: int,
    opponent_mode: str,
    policy: str,
    prefix: Sequence[PickEvent] = (),
    forced: Mapping[int, str] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Run one legal snake draft, optionally replaying a prefix/counterfactual."""

    if not 1 <= int(our_seat) <= TOTAL_TEAMS:
        raise ValueError("our_seat must be in 1..14")
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    if opponent_mode not in OPPONENT_MODES:
        raise ValueError(f"unknown opponent mode {opponent_mode!r}")
    records = tuple(players)
    by_id = {item.player_id: item for item in records}
    if len(by_id) != len(records):
        raise ValueError("players must have unique player_id values")
    order = _snake_order()
    config = LeagueConfig()
    rosters: dict[int, list[Any]] = {seat: [] for seat in range(1, TOTAL_TEAMS + 1)}
    history: list[PickEvent] = []
    trace: list[dict[str, Any]] = []
    available: dict[str, Any] = dict(by_id)
    forced_map = {int(key): str(value) for key, value in (forced or {}).items()}

    for raw in prefix:
        event = _coerce_event(raw)
        expected_number = len(history) + 1
        if event.pick_number < 1 or event.pick_number > PICKS_PER_DRAFT:
            raise ValueError(f"prefix pick_number {event.pick_number} is outside 1..{PICKS_PER_DRAFT}")
        if event.pick_number != expected_number:
            raise ValueError(f"prefix pick_number {event.pick_number} does not replay expected {expected_number}")
        seat = order[event.pick_number - 1]
        if event.team != _team_name(seat, our_seat, config):
            raise ValueError(f"prefix team {event.team!r} does not match seat {seat}")
        player = available.get(event.player_id)
        if player is None or not _legal_candidate(player, seat, rosters, tuple(available.values())):
            raise ValueError(f"prefix player {event.player_id!r} is unavailable or illegal at pick {event.pick_number}")
        if event.player_name and event.player_name != player.name:
            raise ValueError(f"prefix player_name {event.player_name!r} does not match {player.name!r}")
        available.pop(event.player_id)
        rosters[seat].append(player)
        history.append(PickEvent(event.pick_number, event.team, player.player_id, player.name))

    status = "complete"
    for pick_number in range(len(history) + 1, PICKS_PER_DRAFT + 1):
        if deadline is not None and time.monotonic() >= float(deadline):
            status = "incomplete"
            break
        seat = order[pick_number - 1]
        pool = tuple(available.values())
        candidates: list[dict[str, Any]] = []
        selected: Any | None = None
        first_rejected: dict[str, Any] | None = None
        if pick_number in forced_map:
            target = forced_map[pick_number]
            target_player = available.get(target)
            if target_player is None or not _legal_candidate(target_player, seat, rosters, pool):
                status = "unavailable"
                break
            selected = target_player
            candidates = [{"player_id": target_player.player_id, "score": float(_player_ppg(target_player)), "components": {"forced": 1.0}}]
        elif seat == our_seat:
            ranked = rank_policy(records, rosters[seat], history, our_seat, policy, config)
            for row in ranked:
                item = available.get(row["player_id"])
                if item is None:
                    continue
                feasible = _legal_candidate(item, seat, rosters, pool)
                candidate_row = {
                    "player_id": row["player_id"],
                    "score": row["score"],
                    "components": {**row.get("components", {}), "feasible_final_path": 1.0 if feasible else 0.0},
                }
                candidates.append(candidate_row)
                if not feasible and first_rejected is None:
                    first_rejected = {"player_id": row["player_id"], "score": row["score"], "components": dict(row.get("components", {}))}
                if feasible and selected is None:
                    selected = item
        else:
            ranked = _opponent_rank(pool, seat, pick_number, rosters, opponent_mode, int(seed))
            for score, item in ranked:
                if _legal_candidate(item, seat, rosters, pool):
                    selected = item
                    candidates = [{"player_id": item.player_id, "score": float(score), "components": {"opponent": float(score)}}]
                    break
        if selected is None:
            status = "incomplete"
            break
        available.pop(selected.player_id)
        rosters[seat].append(selected)
        event = PickEvent(pick_number, _team_name(seat, our_seat, config), selected.player_id, selected.name)
        history.append(event)
        trace_row = {"pick": pick_number, "seat": seat, "team": event.team, "player_id": selected.player_id, "candidates": candidates}
        if first_rejected is not None:
            trace_row["first_rejected_candidate"] = first_rejected
            trace_row["first_rejected_reason"] = "unsafe_final_roster_path"
        trace_row["policy_fallback"] = bool(candidates and candidates[0].get("components", {}).get("policy_fallback", 0.0))
        trace.append(trace_row)

    if len(history) == PICKS_PER_DRAFT:
        for seat, roster in rosters.items():
            if not check_roster_shape(roster)[0]:
                status = "incomplete"
                break
    return {
        "roster": list(rosters[int(our_seat)]),
        "rosters": {seat: list(roster) for seat, roster in rosters.items()},
        "history": list(history),
        "trace": trace,
        "status": status,
    }


def _score_actual(stats: Mapping[str, Any]) -> float:
    values = {str(key).upper(): float(value) for key, value in stats.items() if isinstance(value, (int, float)) and math.isfinite(float(value))}
    # DDTD are observed from primitive stat lines when not explicitly present.
    tens = sum(values.get(key, 0.0) >= 10.0 for key in ("PTS", "REB", "AST", "STL", "BLK"))
    values.setdefault("DD", 1.0 if tens >= 2 else 0.0)
    values.setdefault("TD", 1.0 if tens >= 3 else 0.0)
    values.setdefault("QD", 1.0 if tens >= 4 else 0.0)
    return score_stats(values)


def _daily_allocation(roster: Sequence[Any], available_ids: set[str], value_fn: Any) -> tuple[tuple[str, ...], tuple[str, ...], float]:
    """Exact maximum-weight matching using a 64-state slot occupancy DP.

    Repeated G/F slots are represented by occupancy counts and explored via
    their first free canonical bit.  A skipped player is equivalent to an empty
    starter slot, so inactive and non-positive players never need branches.
    """

    if len(roster) != 10:
        raise ValueError("roster must contain exactly 10 players")
    # Slot bits: C=0, G=1/2, F=3/4, UTIL=5.
    indexed = tuple((index, item) for index, item in enumerate(roster))
    states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, ())}
    for index, item in indexed:
        if item.player_id not in available_ids:
            continue
        value = float(value_fn(item))
        if value <= 0.0:
            continue
        next_states = dict(states)  # skip the player: leave a legal empty slot
        # Occupancy is state dependent for repeated position slots.
        for mask, (base_score, chosen) in states.items():
            if "C" in item.eligibility and not (mask & 1):
                bit = 1
                candidate = (base_score + value, chosen + (index,))
                previous = next_states.get(mask | bit)
                if previous is None or candidate[0] > previous[0] + 1e-12 or (abs(candidate[0] - previous[0]) <= 1e-12 and candidate[1] < previous[1]):
                    next_states[mask | bit] = candidate
            if "G" in item.eligibility:
                free = 1 if not (mask & (1 << 1)) else (2 if not (mask & (1 << 2)) else 0)
                if free:
                    candidate = (base_score + value, chosen + (index,))
                    previous = next_states.get(mask | (1 << free))
                    if previous is None or candidate[0] > previous[0] + 1e-12 or (abs(candidate[0] - previous[0]) <= 1e-12 and candidate[1] < previous[1]):
                        next_states[mask | (1 << free)] = candidate
            if "F" in item.eligibility:
                free = 3 if not (mask & (1 << 3)) else (4 if not (mask & (1 << 4)) else 0)
                if free:
                    candidate = (base_score + value, chosen + (index,))
                    previous = next_states.get(mask | (1 << free))
                    if previous is None or candidate[0] > previous[0] + 1e-12 or (abs(candidate[0] - previous[0]) <= 1e-12 and candidate[1] < previous[1]):
                        next_states[mask | (1 << free)] = candidate
            if not (mask & (1 << 5)):
                candidate = (base_score + value, chosen + (index,))
                previous = next_states.get(mask | (1 << 5))
                if previous is None or candidate[0] > previous[0] + 1e-12 or (abs(candidate[0] - previous[0]) <= 1e-12 and candidate[1] < previous[1]):
                    next_states[mask | (1 << 5)] = candidate
        states = next_states
    if not states:
        raise ValueError("roster cannot fill starting slots")
    result = max(states.values(), key=lambda row: (row[0], tuple(-index for index in row[1])))
    chosen_ids = tuple(roster[index].player_id for index in result[1])
    chosen_set = set(chosen_ids)
    bench_ids = tuple(item.player_id for item in roster if item.player_id not in chosen_set)
    return chosen_ids, bench_ids, result[0]


def evaluate_roster(dataset: Any, roster: Sequence[Any]) -> dict[str, Any]:
    """Score a roster with forecast-selected lineups and hindsight realized lineups."""

    if len(roster) != 10:
        raise ValueError("roster must contain exactly 10 players")
    if not check_roster_shape(roster)[0]:
        raise ValueError("roster does not satisfy the verified starting-slot shape")
    # Draft order is irrelevant to lineup optimization.  Canonicalizing here
    # makes equal-score tie breaks invariant across policies and cache keys.
    roster = tuple(sorted(roster, key=lambda item: str(item.player_id)))
    by_id = {item.player_id: item for item in roster}
    dates = tuple(getattr(dataset, "dates", ()))
    schedule = getattr(dataset, "scheduled", {}) or {}
    outcomes = getattr(dataset, "outcomes", {}) or {}
    weekly: defaultdict[str, float] = defaultdict(float)
    points = 0.0
    hindsight = 0.0
    usable = 0
    scheduled_starts = 0
    benched = 0.0
    allocations: list[dict[str, Any]] = []
    forecast_values = {item.player_id: _player_ppg(item) for item in roster}
    forecast_cache: dict[frozenset[str], tuple[tuple[str, ...], tuple[str, ...], float]] = {}
    for iso_date in dates:
        active_ids = {str(pid) for pid in schedule.get(iso_date, ()) if str(pid) in by_id}
        daily_outcomes = outcomes.get(iso_date, {}) or {}
        active_key = frozenset(active_ids)
        forecast_allocation = forecast_cache.get(active_key)
        if forecast_allocation is None:
            forecast_allocation = _daily_allocation(roster, active_ids, lambda item: forecast_values[item.player_id])
            forecast_cache[active_key] = forecast_allocation
        forecast_ids, bench_ids, _ = forecast_allocation
        actual_values = {pid: _score_actual(daily_outcomes.get(pid, {})) for pid in active_ids}
        actual_ids, _, _ = _daily_allocation(
            roster,
            active_ids,
            lambda item: actual_values.get(item.player_id, 0.0),
        )
        # Never award an outcome to a player outside the known team schedule;
        # absent appearance rows are a zero for a scheduled player.
        forecast_points = sum(actual_values.get(pid, 0.0) for pid in forecast_ids if pid in active_ids)
        hindsight_points = sum(actual_values.get(pid, 0.0) for pid in actual_ids if pid in active_ids)
        benched_points = sum(actual_values.get(pid, 0.0) for pid in bench_ids if pid in active_ids)
        points += forecast_points
        hindsight += hindsight_points
        benched += benched_points
        scheduled_starts += sum(pid in active_ids for pid in forecast_ids)
        usable += sum(pid in active_ids and pid in daily_outcomes for pid in forecast_ids)
        try:
            parsed = date.fromisoformat(str(iso_date)[:10])
            week_key = f"{parsed.isocalendar().year}-W{parsed.isocalendar().week:02d}"
        except ValueError:
            week_key = str(iso_date)[:10]
        weekly[week_key] += forecast_points
        allocations.append({"date": str(iso_date), "forecast": list(forecast_ids), "hindsight": list(actual_ids), "points": forecast_points})
    missing = tuple(dict.fromkeys(str(item) for item in (getattr(dataset, "missing_scoring_fields", ()) or ())))
    return {
        "points": float(points),
        "weekly_points": dict(weekly),
        "usable_starts": int(usable),
        "scheduled_starts": int(scheduled_starts),
        "benched_points": float(benched),
        "hindsight_points": float(hindsight),
        "missing_scoring_fields": missing,
        "allocations": allocations,
    }


def evaluate_rosters(dataset: Any, rosters: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """Evaluate several rosters with a per-call memo for repeated rosters."""

    memo: dict[tuple[str, ...], dict[str, Any]] = {}
    result = []
    for roster in rosters:
        key = tuple(item.player_id for item in roster)
        if key not in memo:
            memo[key] = evaluate_roster(dataset, roster)
        result.append(memo[key])
    return result


def _date_range(base_dataset: Any) -> tuple[str, ...]:
    base_dates = tuple(getattr(base_dataset, "dates", ()) or ())
    try:
        start = date.fromisoformat(str(base_dates[0])[:10]) if base_dates else date(2026, 10, 20)
    except ValueError:
        start = date(2026, 10, 20)
    return tuple((start + timedelta(days=offset)).isoformat() for offset in range(182))


def _world_outcome(
    player: Any,
    seed: int,
    iso_date: str,
    team: str,
    environment: str,
    day_offset: int,
    calibration: Mapping[str, Any] | None = None,
    scheduled_game_index: int | None = None,
) -> dict[str, float]:
    forecast = _stats(player)
    injury_factor = {"nominal": 1.0, "moderate": 1.0, "injury": 1.35, "healthy": 0.75}[environment]
    bias_sd = float((calibration or {}).get("player_bias_sd", 0.10 if environment in {"nominal", "moderate", "healthy"} else 0.25))
    daily_sd = float((calibration or {}).get("daily_sd", 0.20))
    team_shock_sd = float((calibration or {}).get("team_shock_sd", 0.04))
    player_bias = bias_sd * _hash_normal(seed, player.player_id, "persistent-forecast-error")
    team_shock = team_shock_sd * _hash_normal(seed, team, iso_date, "team-shock")

    def sample(key: str, floor: float = 0.0, spread: float = 0.20) -> float:
        mean = max(floor, forecast.get(key, 0.0)) * max(0.0, 1.0 + player_bias + team_shock)
        return max(floor, mean * (1.0 + spread * daily_sd / 0.20 * _hash_normal(seed, player.player_id, iso_date, team, key)))

    def count(key: str, floor: float = 0.0, spread: float = 0.20) -> int:
        """Mean-preserving deterministic stochastic rounding for count stats."""

        raw = max(floor, sample(key, floor=floor, spread=spread))
        whole = math.floor(raw)
        fraction = raw - whole
        up = _hash_unit(seed, player.player_id, iso_date, team, key, "stochastic-round") < fraction
        return int(whole + (1 if up else 0))

    # Box-score primitives are integer counts.  Shooting identity is preserved
    # exactly, and DDTD are derived from those primitives below.
    fgm = count("FGM", spread=0.16)
    three = min(fgm, count("3PM", spread=0.20))
    fga = max(fgm, count("FGA", floor=fgm, spread=0.12))
    ftm = count("FTM", spread=0.20)
    fta = max(ftm, count("FTA", floor=ftm, spread=0.15))
    result: dict[str, float] = {
        "FGM": float(fgm),
        "FGA": float(fga),
        "3PM": float(three),
        "FTM": float(ftm),
        "FTA": float(fta),
        "PTS": float(2 * (fgm - three) + 3 * three + ftm),
        "REB": float(count("REB", spread=0.25)),
        "AST": float(count("AST", spread=0.28)),
        "STL": float(count("STL", spread=0.35)),
        "BLK": float(count("BLK", spread=0.35)),
        "TO": float(count("TO", spread=0.30)),
    }
    if "PF" in forecast:
        result["PF"] = float(count("PF", spread=0.25))
    tens = sum(result[key] >= 10.0 for key in ("PTS", "REB", "AST", "STL", "BLK"))
    result["DD"] = 1.0 if tens >= 2 else 0.0
    result["TD"] = 1.0 if tens >= 3 else 0.0
    result["QD"] = 1.0 if tens >= 4 else 0.0

    # Availability is represented by persistent blocks plus independent game
    # absences.  A team shock can remove several players on the same game day,
    # while the generated schedule remains the known team calendar.
    availability = max(0.0, min(1.0, float(getattr(player, "projected_availability", 1.0))))
    missed_games = int(round(82.0 * (1.0 - availability) * injury_factor))
    block_len = min(35, max(0, missed_games // 2))
    # Availability is measured in scheduled games.  The optional default keeps
    # direct unit calls deterministic while generated worlds pass the actual
    # 0..81 team-game index.  A cyclic block avoids calendar wrap artifacts.
    game_index = (day_offset % 82) if scheduled_game_index is None else int(scheduled_game_index) % 82
    block_start = _hash_int(seed, player.player_id, team, "injury-start") % 82
    block_active = block_len > 0 and ((game_index - block_start) % 82) < block_len
    residual_denominator = max(1, 82 - block_len)
    residual_probability = max(0.0, min(1.0, (missed_games - block_len) / residual_denominator))
    team_absence = environment == "injury" and _hash_normal(seed, team, iso_date, "team-absence") < -2.3
    individual_absence = _hash_unit(seed, player.player_id, iso_date, team, "absence") < residual_probability
    if block_active or individual_absence or team_absence:
        return {}
    return {key: round(float(value), 4) for key, value in result.items()}


def generate_world(base_dataset: Any, seed: int, environment: str, calibration: Mapping[str, Any] | None = None) -> Any:
    """Generate a full 26-week conditional world from forecasts and a shared schedule."""

    if environment == "injury_heavy":
        environment = "injury"
    if environment == "moderate":
        environment = "moderate"
    if environment not in {"nominal", "moderate", "injury", "healthy"}:
        raise ValueError("environment must be moderate or injury (nominal/healthy aliases are also accepted)")
    allowed_calibration = {"player_bias_sd", "daily_sd", "team_shock_sd"}
    if calibration and not set(calibration).issubset(allowed_calibration):
        raise ValueError(f"unsupported calibration field(s): {sorted(set(calibration) - allowed_calibration)}")
    if calibration:
        for key, raw in calibration.items():
            if not isinstance(raw, (int, float)) or not math.isfinite(float(raw)) or float(raw) < 0.0:
                raise ValueError(f"invalid calibration value for {key}")
    players = tuple(getattr(base_dataset, "players", ()) or ())
    dates = _date_range(base_dataset)
    groups: dict[str, list[Any]] = defaultdict(list)
    for index, player in enumerate(players):
        group = str(getattr(player, "schedule_group", "") or getattr(player, "team", "") or f"team-{index % 30:02d}")
        groups[group].append(player)
    team_dates: dict[str, set[str]] = {}
    team_game_index: dict[str, dict[str, int]] = {}
    for team in sorted(groups):
        ranked = sorted(dates, key=lambda item: (_hash_int(seed, team, item, "schedule"), item))
        selected = ranked[:82]
        team_dates[team] = set(selected)
        team_game_index[team] = {iso_date: game_index for game_index, iso_date in enumerate(selected)}
    scheduled: dict[str, tuple[str, ...]] = {}
    outcomes: dict[str, dict[str, dict[str, float]]] = {}
    for day_offset, iso_date in enumerate(dates):
        day_ids: list[str] = []
        day_outcomes: dict[str, dict[str, float]] = {}
        for team in sorted(groups):
            if iso_date not in team_dates[team]:
                continue
            for player in groups[team]:
                day_ids.append(player.player_id)
                outcome = _world_outcome(
                    player,
                    int(seed),
                    iso_date,
                    team,
                    environment,
                    day_offset,
                    calibration,
                    team_game_index[team][iso_date],
                )
                if outcome:
                    day_outcomes[player.player_id] = outcome
        scheduled[iso_date] = tuple(sorted(day_ids))
        outcomes[iso_date] = day_outcomes
    calibration_note = (
        "caller supplied parameters applied; no empirical calibration claim"
        if calibration
        else "assumed synthetic parameters; no empirical calibration was available"
    )
    calendar_note = "synthetic team calendar: each team receives 82 independently chosen dates; not an NBA schedule"
    provenance = dict(getattr(base_dataset, "provenance", {}) or {})
    provenance.update(
        {
            "generator": "campaign_engine.generate_world",
            "seed": int(seed),
            "environment": environment,
            "calibration": calibration_note,
            "calendar": calendar_note,
            "conditional": True,
            "availability_model": {
                "scheduled_games": 82,
                "persistent_block": "floor(missed_games/2) cyclic scheduled-game indices",
                "residual_absence": "remaining missed games over remaining scheduled games",
                "injury_factor": {"moderate": 1.0, "injury": 1.35}.get(environment, 1.0),
                "team_correlated_absence": environment == "injury",
            },
        }
    )
    # DD/TD/QD are generated from primitive lines in this world, so they are
    # modeled fields rather than missing inputs.  PF remains missing when no
    # independent forecast/source provides it.
    base_missing = tuple(
        field for field in (getattr(base_dataset, "missing_scoring_fields", ()) or ())
        if str(field).upper() not in {"DD", "TD", "QD"}
    )
    if not any("PF" in _stats(player) for player in players):
        base_missing = (*base_missing, "PF")
    missing = tuple(dict.fromkeys((*base_missing, "TF", "EJ")))
    payload = {
        "name": f"{getattr(base_dataset, 'name', 'season')}-world-{seed}",
        "players": players,
        "dates": dates,
        "scheduled": scheduled,
        "outcomes": outcomes,
        "provenance": provenance,
        "missing_scoring_fields": missing,
    }
    try:
        from .campaign_data import SeasonDataset

        return SeasonDataset(**payload)
    except ImportError:
        return SimpleNamespace(**payload)


__all__ = [
    "POLICIES",
    "OPPONENT_MODES",
    "rank_policy",
    "simulate_draft",
    "evaluate_roster",
    "evaluate_rosters",
    "generate_world",
]
