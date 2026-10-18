"""Draft advice from forecast Lock-In value and exact native-position fit.

The production adapter is self-contained. It does not import research modules
or add the unproven replacement, market-timing or fixed bench-weight bonuses.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from .lineup import SLOT_ACCEPTS, best_lineup
from .lock_in import ForecastConfig, estimate_player
from .model import DraftState

TIME_BUDGET_S = 2.0
POLICY_ID = "forecast-lock-in-lineup-v1"

Entry = tuple[str, tuple[str, ...], float]


@dataclass(frozen=True)
class Recommendation:
    player_id: str
    name: str
    positions: tuple[str, ...]
    score: float
    fp_per_game: float
    adp: float | None
    components: dict[str, float]
    explanation: str
    warnings: tuple[str, ...]


def _survival(adp: float | None, next_pick: int) -> float:
    """Uncalibrated ADP timing indicator; never a draft-score bonus."""
    if adp is None:
        return 1.0
    scale = max(3.0, 0.15 * next_pick)
    z = (adp - next_pick) / scale
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def recommend(
    state: DraftState,
    top_n: int = 10,
    *,
    forecasts: ForecastConfig | None = None,
    budget: float = TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> list[Recommendation]:
    """Rank marginal weekly Lock-In contribution; depth ties use player value.

    Weekly value is the expected score of one sequentially chosen game, not a
    sum of games. The lineup-fit guard is an operational adaptation of the
    research metric; the synthetic study is not production-policy validation.
    """
    deadline = clock() + max(0.0, budget)
    league = state.league
    if forecasts is not None:
        forecasts.validate_for(league.season, league.scoring)
    if state.current_pick is None or not state.available or top_n <= 0:
        return []
    room = len(league.starting_slots) + league.bench_slots - len(state.our_roster)
    if room <= 0 or not league.starting_slots:
        return []
    upcoming = [pick for pick in state.our_next_picks if pick >= state.current_pick]
    if state.our_slot is not None and not upcoming:
        return []
    warnings: list[str] = []
    if state.our_slot is None:
        warnings.append("Our draft slot is unknown; assuming the next pick is ours")
    for slot in dict.fromkeys(league.starting_slots):
        if slot not in SLOT_ACCEPTS:
            warnings.append(f"Unknown starting slot '{slot}' treated as UTIL")

    base = [(p.player_id, tuple(p.positions), estimate_player(p, league.scoring, forecasts).weekly_points)
            for p in state.our_roster]
    base_total, _ = best_lineup(base, league.starting_slots)
    taken = state.drafted_ids | {p.player_id for p in state.our_roster}
    candidates = {p.player_id:p for p in state.available if p.player_id not in taken}
    pool_rank, _ = best_lineup(
        [(p.player_id, tuple(p.positions), 1.0) for p in state.our_roster]
        + [(pid, tuple(p.positions), 1.0) for pid, p in candidates.items()], league.starting_slots)
    if pool_rank < len(league.starting_slots):
        warnings.append("Available player pool cannot fill every starting slot; roster completion is uncertain")
    warnings.append("Roster completion uses a capacity bound; future player availability is not guaranteed")
    projections = {pid:estimate_player(p, league.scoring, forecasts) for pid,p in candidates.items()}
    next_pick = next((pick for pick in upcoming if pick > (upcoming[0] if upcoming else state.current_pick)), None)
    timing_pick = next_pick or (upcoming[0] if upcoming else state.current_pick)
    ordered = sorted(candidates, key=lambda pid: (-projections[pid].weekly_points, pid))
    result: list[Recommendation] = []
    truncated = False
    for pid in ordered:
        if result and clock() > deadline:
            truncated = True
            break
        player = candidates[pid]
        projection = projections[pid]
        entry = (pid, tuple(player.positions), projection.weekly_points)
        # Check remaining roster capacity with exact matching, without imposing
        # position quotas or pretending to reserve future opponent selections.
        rank, _ = best_lineup([(p.player_id, tuple(p.positions), 1.0) for p in state.our_roster]
                              + [(pid, tuple(player.positions), 1.0)], league.starting_slots)
        if rank + room - 1 < len(league.starting_slots):
            continue
        total, assignment = best_lineup(base + [entry], league.starting_slots)
        gain = max(0.0, total - base_total)
        slot = assignment.get(pid)
        parts = [f"Adds {gain:.1f} estimated weekly Lock-In points",
                 f"{projection.weekly_points:.1f} estimated Lock-In FP/week",
                 f"Fills {slot}" if slot and gain > 0 else "Depth or rotation option"]
        if player.adp is None:
            parts.append("no ADP")
        else:
            parts.append(f"ADP {player.adp:.1f}")
        item_warnings = list(projection.warnings) + warnings
        if player.injury_status:
            item_warnings.append(f"Injury status: {player.injury_status}; weekly availability is not inferred from this label")
        result.append(Recommendation(
            player_id=pid, name=player.name, positions=tuple(player.positions),
            score=round(gain, 4), fp_per_game=round(projection.fp_per_game, 4), adp=player.adp,
            components={"fp_per_game":round(projection.fp_per_game,4),
                        "lock_in_weekly_points":round(projection.weekly_points,4),
                        "lineup_gain":round(gain,4), "lineup_gain_weekly":round(gain,4),
                        "scheduled_games_per_week":float(projection.game_count),
                        "survival":round(_survival(player.adp,timing_pick),4)},
            explanation="; ".join(parts), warnings=tuple(dict.fromkeys(item_warnings))))
    if truncated:
        result = [Recommendation(r.player_id,r.name,r.positions,r.score,r.fp_per_game,r.adp,r.components,
                                 r.explanation,tuple(dict.fromkeys((*r.warnings,"Time budget reached; not all candidates were evaluated"))))
                  for r in result]
    result.sort(key=lambda r:(-r.score,-r.components["lock_in_weekly_points"],
                              math.inf if r.adp is None else r.adp,r.player_id))
    return result[:top_n]
