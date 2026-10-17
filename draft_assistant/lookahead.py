"""Bounded two-pick snake-turn lookahead.

The module contains a small local rollout evaluator.  It intentionally has no
browser calls and observes a wall-clock budget; when the budget is exhausted it
returns the already computed direct ranking.  A caller may ask for advisory
lookahead at any time, while promotion of its ordering is gated by the current
revision artifact in :func:`draft_assistant.valuation.recommend`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .contracts import CandidateScore, DraftObservation, LeagueConfig, LivePlayer, snake_pick_numbers
from .valuation import _coverage, rank_players, value_player


@dataclass(frozen=True)
class LookaheadResult:
    candidates: tuple[CandidateScore, ...]
    status: str = "advisory"
    explanation: str = ""
    rollouts: int = 0
    elapsed_seconds: float = 0.0


def _team_at_pick(pick: int, order: Sequence[str]) -> str | None:
    if pick <= 0 or not order:
        return None
    index = (pick - 1) % len(order)
    round_number = (pick - 1) // len(order)
    if round_number % 2:
        index = len(order) - 1 - index
    return order[index]


def _next_own_pick(observation: DraftObservation, config: LeagueConfig) -> int:
    current = observation.current_pick or 1
    own = observation.room.team_id
    # Prefer the explicitly named team when available.  Some adapters use team
    # names, while the room identity uses numeric ids, so accept either.
    own_names = {own, observation.room.team_id, config.our_team}
    # During an opponent pick ``on_clock_team`` describes the opponent, so it is
    # never used as an inferred own-team identity.
    order_has_our_name = bool(set(observation.pick_order) & own_names)
    for pick in range(current + 1, config.teams * config.rounds + 1):
        team = _team_at_pick(pick, observation.pick_order)
        if team in own_names or team == "our_team":
            return pick
    # A missing order is common in early replay frames.  One complete snake
    # turn is a conservative bound and is still deterministic.
    return min(config.teams * config.rounds + 1, current + config.teams)


def _opponent_need(
    history,
    team: str,
    available: Sequence[LivePlayer],
    catalog: Mapping[str, LivePlayer] | None = None,
) -> dict[str, int]:
    picked = [event for event in history if event.team == team]
    counts = {position: 0 for position in ("C", "G", "F")}
    for event in picked:
        player = (catalog or {}).get(event.player_id)
        if player is None:
            player = next((p for p in available if p.player_id == event.player_id), None)
        if player:
            for position in counts:
                counts[position] += position in player.eligibility
    return {position: max(0, need - counts[position]) for position, need in {"C": 1, "G": 2, "F": 2}.items()}


def _opponent_pick(
    players: Sequence[LivePlayer],
    *,
    history,
    team: str,
    config: LeagueConfig,
    catalog: Mapping[str, LivePlayer] | None = None,
    base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> LivePlayer | None:
    if not players:
        return None
    need = _opponent_need(history, team, players, catalog)
    ranked: list[tuple[tuple[float, float, str], LivePlayer]] = []
    for player in players:
        if base_cache is not None and player.player_id in base_cache:
            custom, _, factor, _ = base_cache[player.player_id]
            season_value = custom * factor
        else:
            value = value_player(player, config, _base_cache=base_cache)
            season_value = value.season_value
        rank = player.draft_rank if player.draft_rank is not None else 10_000.0
        positional = sum(need[position] for position in player.eligibility if position in need)
        # Opponents are a deliberately simple rank-plus-needs model.  This
        # models uncertainty without pretending to know a specific opponent's
        # private preferences.
        key = (float(rank) - min(4.0, positional * 2.0), -season_value, player.player_id)
        ranked.append((key, player))
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _rollout_score(
    observation: DraftObservation,
    config: LeagueConfig,
    first: LivePlayer,
    *,
    deadline: float,
    catalog: Mapping[str, LivePlayer] | None = None,
    base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> tuple[float, bool]:
    available = [p for p in observation.available_players if p.player_id != first.player_id]
    history = list(observation.history)
    current = observation.current_pick or 1
    next_own = _next_own_pick(observation, config)
    # Simulate every intervening pick through the next own turn.  The wall-clock
    # deadline remains the hard bound; shortening the horizon by seat would bias
    # early snake seats toward the wrong comparison.
    modeled = 0
    complete = True
    for pick in range(current + 1, next_own):
        if time.perf_counter() >= deadline:
            complete = False
            break
        team = _team_at_pick(pick, observation.pick_order) or f"opponent-{pick}"
        chosen = _opponent_pick(available, history=history, team=team, config=config, catalog=catalog, base_cache=base_cache)
        if chosen is None:
            complete = False
            break
        available = [p for p in available if p.player_id != chosen.player_id]
        history.append(type(observation.history[0])(pick, team, chosen.player_id, chosen.name) if observation.history else _event(pick, team, chosen))
        modeled += 1
    value = value_player(first, config, roster=observation.roster, _base_cache=base_cache)
    # Return value of the best remaining option at our next pick.  This is a
    # bounded, two-stage value estimate rather than a full-season simulator.
    if available and time.perf_counter() < deadline:
        future_roster = tuple((*observation.roster, first))
        future_observation = replace(observation, roster=future_roster, history=tuple(history), available_players=tuple(available))
        future_values = rank_players(future_observation, config, limit=1, _base_cache=base_cache)
        next_value = future_values[0].score if future_values else 0.0
        if not future_values or time.perf_counter() >= deadline:
            complete = False
    else:
        next_value = 0.0
        complete = False
    # This is a two-stage marginal value: current pick plus the best legal
    # value surviving to the next own turn.  Coverage enters through each
    # value_player call rather than through a fixed positional coefficient.
    return value.season_value + value.coverage_bonus + next_value, complete


def _event(pick: int, team: str, player: LivePlayer):
    # Local tiny event object avoids importing a heavier coordinator module.
    from .contracts import PickEvent

    return PickEvent(pick, team, player.player_id, player.name)


def apply_lookahead(
    observation: DraftObservation,
    config: LeagueConfig,
    candidates: Sequence[CandidateScore] | None = None,
    *,
    promote: bool = False,
    budget_seconds: float | None = None,
    max_candidates: int = 8,
    player_catalog: Mapping[str, LivePlayer] | Sequence[LivePlayer] | None = None,
    _base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> LookaheadResult:
    """Evaluate top candidates through the next snake turn.

    ``promote`` is an explicit gate from the coordinator/recommendation layer;
    false means results remain advisory and the direct ranking stays first.
    """

    started = time.perf_counter()
    budget = max(0.001, float(budget_seconds if budget_seconds is not None else config.recommendation_budget_seconds))
    deadline = started + budget
    direct = tuple(candidates or rank_players(observation, config))
    if not direct:
        return LookaheadResult((), "fallback", "no available candidates", 0, time.perf_counter() - started)
    if (not observation.available_pool_complete or len(observation.pick_order) != config.teams
            or len(set(observation.pick_order)) != config.teams or config.our_team not in observation.pick_order
            or observation.on_clock_team != config.our_team or observation.current_pick is None
            or any(player.draft_rank is None for player in observation.available_players)):
        return LookaheadResult(direct, "fallback", "insufficient pool, rank, order, or turn inputs; foundation used")
    own_seat = observation.pick_order.index(config.our_team) + 1
    if not any(pick > observation.current_pick for pick in snake_pick_numbers(own_seat)):
        return LookaheadResult(direct, "fallback", "final own turn; foundation used")
    by_id = {player.player_id: player for player in observation.available_players}
    by_id.update({player.player_id: player for player in observation.roster})
    if isinstance(player_catalog, Mapping):
        by_id.update(player_catalog)
    elif player_catalog is not None:
        by_id.update({player.player_id: player for player in player_catalog})
    # Rules are the only extensible field in the frozen v1 contract; accept a
    # known-player catalog there for adapters that retain drafted records.
    known = observation.rules.get("known_players") if observation.rules else None
    if known is None:
        retained = getattr(observation, "known_players", ())
        known = retained
    if isinstance(known, Mapping):
        by_id.update({str(key): value for key, value in known.items() if isinstance(value, LivePlayer)})
    elif isinstance(known, Sequence) and not isinstance(known, (str, bytes)):
        by_id.update({player.player_id: player for player in known if isinstance(player, LivePlayer)})
    if any(event.player_id not in by_id for event in observation.history):
        return LookaheadResult(direct, "fallback", "opponent eligibility history is incomplete; foundation used")
    evaluated: list[tuple[float, CandidateScore]] = []
    rollouts = 0
    for candidate in direct[: max(1, int(max_candidates))]:
        if time.perf_counter() >= deadline and evaluated:
            break
        player = by_id.get(candidate.player_id)
        if player is None:
            continue
        score, complete = _rollout_score(observation, config, player, deadline=deadline, catalog=by_id, base_cache=_base_cache)
        if not complete:
            return LookaheadResult(direct, "fallback", "lookahead budget exhausted; foundation ranking used", rollouts, time.perf_counter() - started)
        evaluated.append((score, candidate))
        rollouts += 1
    if len(evaluated) != min(len(direct), max(1, int(max_candidates))) or time.perf_counter() >= deadline:
        return LookaheadResult(direct, "fallback", "lookahead budget exhausted; direct ranking used", rollouts, time.perf_counter() - started)
    evaluated.sort(key=lambda item: (-item[0], item[1].player_id))
    if not promote:
        ordered = direct
        status = "advisory"
        explanation = f"Advisory lookahead favors {evaluated[0][1].name} (modeled pair score {evaluated[0][0]:.2f}; {rollouts} rollouts); foundation ordering retained"
    else:
        chosen = {candidate.player_id: candidate for _, candidate in evaluated}
        ordered = tuple(chosen[candidate.player_id] for _, candidate in evaluated) + tuple(
            candidate for candidate in direct if candidate.player_id not in chosen
        )
        status = "promoted"
        explanation = f"promoted two-pick lookahead ({rollouts} bounded rollouts)"
    return LookaheadResult(ordered, status, explanation, rollouts, time.perf_counter() - started)


# Public aliases used by replay and validation tools.
run_lookahead = apply_lookahead

__all__ = ["LookaheadResult", "apply_lookahead", "run_lookahead"]
