"""Draft advice from forecast Lock-In value with lookahead opportunity cost.

The production adapter is self-contained. It does not import research modules.
There are no position caps, quotas, minimums or fixed positional bonuses:
balance emerges only from (1) an absence-aware lineup objective, where a
backup is worth what it recovers when a starter may miss the week, and (2) a
lookahead that simulates ADP-ordered opponents taking players before our
later picks.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from .depth import DEFAULT_ABSENCE_PROBABILITY, Completion, absence_value
from .lineup import SLOT_ACCEPTS, best_lineup
from .lock_in import ForecastConfig, estimate_player
from .model import DraftState

TIME_BUDGET_S = 2.0
POLICY_ID = "forecast-lock-in-lookahead-v2"
VALUE_CANDIDATES = 25       # top candidates by weekly value
ADP_CANDIDATES = 12         # top candidates by ADP
BASELINE_SHARE = 0.3        # share of the budget the replacement search may use

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


def _absence_probability(forecasts: ForecastConfig | None) -> float:
    if forecasts is None:
        return DEFAULT_ABSENCE_PROBABILITY
    return float(getattr(forecasts.defaults, "weekly_absence_probability",
                         DEFAULT_ABSENCE_PROBABILITY))


def recommend(
    state: DraftState,
    top_n: int = 10,
    *,
    forecasts: ForecastConfig | None = None,
    budget: float = TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> list[Recommendation]:
    """Rank candidates by absence-aware roster value after lookahead.

    Score = V(roster + X + completion) - V(roster + R + completion), where R is
    the best player likely to survive to our next pick. Weekly value is the
    expected score of one sequentially chosen game, not a sum of games.
    """
    deadline = clock() + max(0.0, budget)
    league = state.league
    if forecasts is not None:
        forecasts.validate_for(league.season, league.scoring)
    if state.current_pick is None or not state.available or top_n <= 0:
        return []
    cur = state.current_pick
    room = len(league.starting_slots) + league.bench_slots - len(state.our_roster)
    if room <= 0 or not league.starting_slots:
        return []
    slots = tuple(league.starting_slots)
    if state.our_slot is None:
        picks = [cur + i * league.teams for i in range(room)]
    else:
        picks = [pick for pick in state.our_next_picks if pick >= cur]
        if not picks:
            return []
    picks = picks[:room]
    p0 = picks[0]
    gaps = [b - a - 1 for a, b in zip(picks, picks[1:])]
    pre = max(0, p0 - cur)
    q_abs = _absence_probability(forecasts)

    warnings: list[str] = []
    if state.our_slot is None:
        warnings.append("Our draft slot is unknown; assuming the next pick is ours and "
                        f"our following picks are {league.teams} picks apart")
    for slot in dict.fromkeys(slots):
        if slot != "BN" and slot not in SLOT_ACCEPTS:
            warnings.append(f"Unknown starting slot '{slot}' treated as UTIL")

    base: list[Entry] = [
        (p.player_id, tuple(p.positions), estimate_player(p, league.scoring, forecasts).weekly_points)
        for p in state.our_roster]
    taken = state.drafted_ids | {p.player_id for p in state.our_roster}
    candidates = {p.player_id: p for p in state.available if p.player_id not in taken}
    if not candidates:
        return []
    pool_rank, _ = best_lineup(
        [(p.player_id, tuple(p.positions), 1.0) for p in state.our_roster]
        + [(pid, tuple(p.positions), 1.0) for pid, p in candidates.items()], slots)
    if pool_rank < len(slots):
        warnings.append("Available player pool cannot fill every starting slot; roster completion is uncertain")
    warnings.append("Roster completion uses a capacity bound; future player availability is not guaranteed")
    warnings.append(f"Bench depth assumes a {q_abs:.0%} weekly absence probability per player (unverified assumption)")

    projections = {pid: estimate_player(p, league.scoring, forecasts) for pid, p in candidates.items()}
    entries: list[Entry] = [(pid, tuple(p.positions), projections[pid].weekly_points)
                            for pid, p in candidates.items()]
    entry_of = {e[0]: e for e in entries}
    adp = {pid: p.adp for pid, p in candidates.items()}
    planner = Completion(entries, adp, slots, base, q_abs)
    base_v, base_l = planner.value(list(base))

    # Candidate set: top by weekly value, top by ADP, and the best player of
    # each eligibility tuple (coverage only; no positional weighting).
    cand: dict[str, Entry] = {}
    for e in planner.val_order[:max(VALUE_CANDIDATES, top_n)]:
        cand[e[0]] = e
    for e in planner.adp_order[:ADP_CANDIDATES]:
        cand[e[0]] = e
    for lst in planner.by_tuple.values():
        cand[lst[0][0]] = lst[0]
    cand_list = sorted(cand.values(), key=lambda e: (-e[2], e[0]))

    # Replacement R: best player expected to survive to our next pick.
    later = [pick for pick in picks if pick > p0]
    next_pick = p0 if p0 > cur else (later[0] if later else None)
    timing_pick = next_pick if next_pick is not None else p0
    ref_v, ref_l, ref_entry = base_v, base_l, None
    if next_pick is not None:
        ours_before = sum(1 for pick in picks if pick < next_pick)
        others = max(0, next_pick - cur - ours_before)
        survivors = planner.adp_order[others:]
        seen: set[tuple[str, ...]] = set()
        best_key: tuple[float, float] | None = None
        ref_deadline = clock() + budget * BASELINE_SHARE
        for e in sorted(survivors, key=lambda e: (-e[2], e[0])):
            if e[1] in seen:
                continue
            seen.add(e[1])
            v, l = planner.finish(e, pre, gaps)
            key = (v, e[2])
            if best_key is None or key > best_key:
                best_key, ref_v, ref_l, ref_entry = key, v, l, e
            if clock() > ref_deadline:
                break

    result: list[Recommendation] = []
    truncated = False
    for e in cand_list:
        if result and clock() > deadline:
            truncated = True
            break
        pid = e[0]
        player = candidates[pid]
        projection = projections[pid]
        # Capacity guard with exact matching; not a positional quota.
        rank, _ = best_lineup([(p.player_id, tuple(p.positions), 1.0) for p in state.our_roster]
                              + [(pid, tuple(player.positions), 1.0)], slots)
        if rank + room - 1 < len(slots):
            continue
        v, l = planner.finish(e, pre, gaps)
        score = v - ref_v
        now_total, assignment = best_lineup(base + [e], slots)
        gain = max(0.0, now_total - base_l)
        depth = (v - l) - (ref_v - ref_l)
        slot = assignment.get(pid)
        head = f"Fills {slot} now" if slot and gain > 0 else "Bench depth"
        parts = [f"{head}; {score:+.1f} vs. waiting"]
        if ref_entry is not None and next_pick is not None and ref_entry[0] != pid:
            alt = candidates[ref_entry[0]]
            parts[0] += f" (best likely at pick {next_pick}: {alt.name} {'/'.join(alt.positions)})"
        if player.adp is None:
            parts.append("no ADP")
        item_warnings = list(projection.warnings) + warnings
        if player.injury_status:
            item_warnings.append(f"Injury status: {player.injury_status}; weekly availability is not inferred from this label")
        result.append(Recommendation(
            player_id=pid, name=player.name, positions=tuple(player.positions),
            score=round(score, 4), fp_per_game=round(projection.fp_per_game, 4), adp=player.adp,
            components={"fp_per_game": round(projection.fp_per_game, 4),
                        "lock_in_weekly_points": round(projection.weekly_points, 4),
                        "lineup_gain": round(gain, 4), "lineup_gain_weekly": round(gain, 4),
                        "lookahead_value": round(score, 4),
                        "depth_value": round(depth, 4),
                        "scheduled_games_per_week": float(projection.game_count),
                        "survival": round(_survival(player.adp, timing_pick), 4)},
            explanation="; ".join(parts), warnings=tuple(dict.fromkeys(item_warnings))))
    if truncated:
        result = [Recommendation(r.player_id, r.name, r.positions, r.score, r.fp_per_game, r.adp, r.components,
                                 r.explanation, tuple(dict.fromkeys((*r.warnings, "Time budget reached; not all candidates were evaluated"))))
                  for r in result]
    result.sort(key=lambda r: (-r.score, -r.components["lock_in_weekly_points"],
                               math.inf if r.adp is None else r.adp, r.player_id))
    return result[:top_n]
