"""Draft recommendations from marginal starting-lineup value.

No position caps, quotas, or fixed positional bonuses: position value emerges
only from the best-lineup assignment (marginal gain) plus a small bench term.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

from .lineup import SLOT_ACCEPTS, best_lineup
from .model import DraftState
from .scoring import fantasy_points

BENCH_WEIGHT = 0.15
TIME_BUDGET_S = 2.0
BASELINE_SHARE = 0.4        # share of the budget the replacement search may use
FP_CANDIDATES = 12          # top candidates by FP
ADP_CANDIDATES = 12         # top candidates by ADP
_NO_ADP = float("inf")

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
    """Logistic probability a player with this ADP is still there at next_pick."""
    if adp is None:
        return 1.0
    scale = max(3.0, 0.15 * next_pick)
    z = (adp - next_pick) / scale
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _bench_value(roster: list[Entry], assignment: dict[str, str]) -> float:
    return max((e[2] for e in roster if e[0] not in assignment), default=0.0)


class _Planner:
    def __init__(self, entries: list[Entry], adp: dict[str, float | None],
                 slots: tuple[str, ...], base: list[Entry]):
        self.slots = slots
        self.base = base
        self.val_order = sorted(entries, key=lambda e: (-e[2], e[0]))
        self.adp_order = sorted(
            entries,
            key=lambda e: (
                _NO_ADP if adp.get(e[0]) is None else adp[e[0]], -e[2], e[0]),
        )
        self.n_tuples = len({e[1] for e in entries})

    def finish(self, chosen: Entry, pre: int, gaps: list[int]) -> tuple[float, float]:
        """Roster completion after taking ``chosen``; returns (total, bench)."""
        taken = {chosen[0]}
        ptr = 0
        adp_order = self.adp_order
        slots = self.slots

        def remove_others(n: int) -> None:
            nonlocal ptr
            while n > 0 and ptr < len(adp_order):
                e = adp_order[ptr]
                ptr += 1
                if e[0] not in taken:
                    taken.add(e[0])
                    n -= 1

        remove_others(pre)
        roster = self.base + [chosen]
        for gap in gaps:
            remove_others(gap)
            seen: set[tuple[str, ...]] = set()
            best: Entry | None = None
            best_key: tuple[float, float] | None = None
            for e in self.val_order:
                if e[0] in taken or e[1] in seen:
                    continue
                seen.add(e[1])
                key = (best_lineup(roster + [e], slots)[0], e[2])
                if best_key is None or key > best_key:
                    best, best_key = e, key
                if len(seen) >= self.n_tuples:
                    break
            if best is None:
                break
            taken.add(best[0])
            roster.append(best)
        total, assignment = best_lineup(roster, slots)
        return total, _bench_value(roster, assignment)


def recommend(
    state: DraftState,
    top_n: int = 10,
    *,
    budget: float = TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> list[Recommendation]:
    """Rank available players by expected lineup value (deterministic)."""
    deadline = clock() + budget
    league = state.league
    cur = state.current_pick
    if cur is None or not state.available or top_n <= 0:
        return []

    roster_size = len(league.starting_slots) + league.bench_slots
    room = roster_size - len(state.our_roster)
    if room <= 0:
        return []
    if state.our_slot is None:
        picks = [cur + i * league.teams for i in range(room)]
    else:
        picks = list(state.our_next_picks)
    if not picks:
        return []
    p0 = picks[0]
    picks = picks[:room]
    gaps = [b - a - 1 for a, b in zip(picks, picks[1:])]
    pre = max(0, p0 - cur)

    slots = tuple(league.starting_slots)
    common_warnings: list[str] = []
    for s in dict.fromkeys(slots):
        if s != "BN" and s not in SLOT_ACCEPTS:
            common_warnings.append(f"Unknown starting slot '{s}' treated as UTIL")
    if state.our_slot is None:
        common_warnings.append(
            "Our draft slot is unknown; assuming the next pick is ours and our "
            f"following picks are {league.teams} picks apart")

    # Step 1: per-game values.
    values: dict[str, float] = {}
    pwarn: dict[str, list[str]] = {}
    scoring_warnings: dict[str, None] = {}
    adp: dict[str, float | None] = {}
    by_id = {}
    for p in state.available:
        by_id[p.player_id] = p
        adp[p.player_id] = p.adp
        w: list[str] = []
        if p.has_projection:
            v, _, sw = fantasy_points(p.stats, league.scoring)
            for x in sw:
                scoring_warnings[x] = None
        else:
            v = 0.0
            w.append("No stat projection; value counted as 0 (ADP only)")
        if p.injury_status:
            w.append(f"Injury status: {p.injury_status}")
        values[p.player_id] = v
        pwarn[p.player_id] = w
    common_warnings.extend(scoring_warnings)

    entries: list[Entry] = [
        (p.player_id, tuple(p.positions), values[p.player_id])
        for p in state.available
    ]
    base: list[Entry] = []
    for p in state.our_roster:
        v = fantasy_points(p.stats, league.scoring)[0] if p.has_projection else 0.0
        base.append((p.player_id, tuple(p.positions), v))

    planner = _Planner(entries, adp, slots, base)
    base_total, base_assign = best_lineup(base, slots)

    # Candidates: top by FP, top by ADP, and the best player of each
    # eligibility set (coverage only; no positional weighting).
    cand: dict[str, Entry] = {}
    for e in planner.val_order[:FP_CANDIDATES]:
        cand[e[0]] = e
    for e in planner.adp_order[:ADP_CANDIDATES]:
        cand[e[0]] = e
    seen_tuples: set[tuple[str, ...]] = set()
    for e in planner.val_order:
        if e[1] not in seen_tuples:
            seen_tuples.add(e[1])
            cand[e[0]] = e
    cand_list = sorted(cand.values(), key=lambda e: (-e[2], e[0]))

    # Our next pick for survival / replacement purposes.
    q = p0 if p0 > cur else (picks[1] if len(picks) > 1 else None)
    survival_pick = q if q is not None else p0

    # Replacement R: best player expected to survive to our next pick.
    ref_total = base_total
    ref_bench = _bench_value(base, base_assign)
    if q is not None:
        ours_before_q = sum(1 for p in picks if p < q)
        others = max(0, q - cur - ours_before_q)
        survivors = planner.adp_order[others:]
        seen_tuples = set()
        best_key: tuple[float, float] | None = None
        baseline_deadline = clock() + budget * BASELINE_SHARE
        for e in sorted(survivors, key=lambda e: (-e[2], e[0])):
            if e[1] in seen_tuples:
                continue
            seen_tuples.add(e[1])
            tot, bench = planner.finish(e, pre, gaps)
            key = (tot + BENCH_WEIGHT * bench, e[2])
            if best_key is None or key > best_key:
                best_key = key
                ref_total, ref_bench = tot, bench
            if clock() > baseline_deadline:
                break

    results: list[Recommendation] = []
    for i, e in enumerate(cand_list):
        if i > 0 and clock() > deadline:
            common_warnings.append(
                "Time budget reached; only the top candidates were evaluated")
            break
        tot, bench = planner.finish(e, pre, gaps)
        bench_term = BENCH_WEIGHT * (bench - ref_bench)
        score = (tot - ref_total) + bench_term
        now_total, assign = best_lineup(base + [e], slots)
        lineup_gain = now_total - base_total
        p = by_id[e[0]]
        surv = _survival(p.adp, survival_pick)
        slot = assign.get(e[0])
        parts = [f"Fills {slot}" if slot else "Bench depth",
                 f"{e[2]:.1f} FP/g"]
        if p.adp is None:
            parts.append("no ADP")
        elif surv < 0.35:
            parts.append(f"likely gone before pick {survival_pick} (ADP {p.adp:.1f})")
        elif surv > 0.65:
            parts.append(f"likely available at pick {survival_pick} (ADP {p.adp:.1f})")
        else:
            parts.append(f"may be gone by pick {survival_pick} (ADP {p.adp:.1f})")
        warnings = tuple(dict.fromkeys(pwarn[e[0]] + common_warnings))
        results.append(Recommendation(
            player_id=e[0], name=p.name, positions=tuple(p.positions),
            score=round(score, 4), fp_per_game=round(e[2], 4), adp=p.adp,
            components={
                "fp_per_game": round(e[2], 4),
                "lineup_gain": round(lineup_gain, 4),
                "survival": round(surv, 4),
                "bench_term": round(bench_term, 4),
            },
            explanation="; ".join(parts),
            warnings=warnings,
        ))
    results.sort(key=lambda r: (-r.score, -r.fp_per_game, r.player_id))
    return results[:top_n]
