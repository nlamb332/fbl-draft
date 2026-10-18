"""Absence-aware lineup value and roster-completion lookahead.

No position caps, quotas, minimums or fixed positional bonuses live here.
Positional balance arises only because (a) the objective ``V`` gives a
backup a value when a starter may be absent, and (b) the lookahead lets
opponents remove players before our later picks.
"""
from __future__ import annotations

from typing import Sequence

from .lineup import SLOT_ACCEPTS, best_lineup

DEFAULT_ABSENCE_PROBABILITY = 0.10

Entry = tuple[str, tuple[str, ...], float]
_NO_ADP = float("inf")


def _slot_list(slots: Sequence[str]) -> list[set[str] | None]:
    return [SLOT_ACCEPTS.get(s) for s in slots if s != "BN"]


def _mask(positions: tuple[str, ...], accepts: list[set[str] | None]) -> int:
    mask = 0
    for i, acc in enumerate(accepts):
        if acc is None or any(p in acc for p in positions):
            mask |= 1 << i
    return mask


def _lineup(values: list[float], masks: list[int], n_slots: int,
            skip: int = -1) -> tuple[float, list[int]]:
    """Same greedy-augmenting optimum as ``best_lineup`` on bitmasks.

    Returns the total and the indexes of started players; ``skip`` omits one
    player (used for the leave-one-out terms).
    """
    order = sorted((i for i in range(len(values)) if i != skip),
                   key=lambda i: (-values[i], i))
    slot_player = [-1] * n_slots

    def augment(idx: int, seen: int) -> tuple[bool, int]:
        m = masks[idx]
        for s in range(n_slots):
            bit = 1 << s
            if seen & bit or not m & bit:
                continue
            seen |= bit
            occupant = slot_player[s]
            if occupant == -1:
                slot_player[s] = idx
                return True, seen
            ok, seen = augment(occupant, seen)
            if ok:
                slot_player[s] = idx
                return True, seen
        return False, seen

    filled = 0
    for idx in order:
        if filled >= n_slots or values[idx] <= 0:
            break
        if augment(idx, 0)[0]:
            filled += 1
    started = [i for i in slot_player if i != -1]
    return sum(values[i] for i in started), started


def absence_value(
    roster: Sequence[Entry], slots: Sequence[str], q: float,
) -> tuple[float, float]:
    """Return ``(V, L)`` for a roster.

    ``L`` is the optimal weekly Lock-In lineup total. With each player
    independently absent with probability ``q`` (first-order approximation),
    ``V = L - q * sum_s [L - L(roster - s)]`` over the starters ``s`` of the
    optimal lineup: depth is worth what it recovers when a starter is out.
    """
    accepts = _slot_list(slots)
    values = [e[2] for e in roster]
    masks = [_mask(e[1], accepts) for e in roster]
    total, started = _lineup(values, masks, len(accepts))
    if q <= 0.0 or not started:
        return total, total
    loss = 0.0
    for idx in started:
        loss += total - _lineup(values, masks, len(accepts), skip=idx)[0]
    return total - q * loss, total


class Completion:
    """Simulate our remaining picks against ADP-ordered opponents."""

    def __init__(self, entries: Sequence[Entry], adp: dict[str, float | None],
                 slots: Sequence[str], base: Sequence[Entry], q: float):
        self.slots = tuple(slots)
        self.base = list(base)
        self.q = q
        self.val_order = sorted(entries, key=lambda e: (-e[2], e[0]))
        self.adp_order = sorted(
            entries,
            key=lambda e: (_NO_ADP if adp.get(e[0]) is None else adp[e[0]], -e[2], e[0]))
        by_tuple: dict[tuple[str, ...], list[Entry]] = {}
        for e in self.val_order:  # already value-descending
            by_tuple.setdefault(e[1], []).append(e)
        self.by_tuple = by_tuple
        self._memo: dict[tuple[str, ...], tuple[float, float]] = {}

    def value(self, roster: list[Entry]) -> tuple[float, float]:
        key = tuple(sorted(e[0] for e in roster))
        hit = self._memo.get(key)
        if hit is None:
            hit = absence_value(roster, self.slots, self.q)
            self._memo[key] = hit
        return hit

    def finish(self, chosen: Entry | None, pre: int, gaps: Sequence[int]) -> tuple[float, float]:
        """``(V, L)`` after taking ``chosen`` and playing out the gaps."""
        taken: set[str] = set() if chosen is None else {chosen[0]}
        adp_order = self.adp_order
        ptr = 0

        def remove_others(n: int) -> None:
            nonlocal ptr
            while n > 0 and ptr < len(adp_order):
                e = adp_order[ptr]
                ptr += 1
                if e[0] not in taken:
                    taken.add(e[0])
                    n -= 1

        remove_others(pre)
        roster = self.base + ([] if chosen is None else [chosen])
        heads = {t: 0 for t in self.by_tuple}
        for gap in gaps:
            remove_others(gap)
            best: Entry | None = None
            best_key: tuple[float, float] | None = None
            for t, lst in self.by_tuple.items():
                i = heads[t]
                while i < len(lst) and lst[i][0] in taken:
                    i += 1
                heads[t] = i
                if i >= len(lst):
                    continue
                e = lst[i]
                key = (self.value(roster + [e])[0], e[2])
                if best_key is None or key > best_key:
                    best, best_key = e, key
            if best is None:
                break
            taken.add(best[0])
            roster.append(best)
        return self.value(roster)
