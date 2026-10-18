"""Exact best starting lineup for Sleeper native-position slots."""
from __future__ import annotations

from typing import Sequence

SLOT_ACCEPTS: dict[str, set[str]] = {
    "PG": {"PG"},
    "SG": {"SG"},
    "SF": {"SF"},
    "PF": {"PF"},
    "C": {"C"},
    "G": {"PG", "SG"},
    "F": {"SF", "PF"},
    "UTIL": {"PG", "SG", "SF", "PF", "C"},
}


def best_lineup(
    players: Sequence[tuple[str, tuple[str, ...], float]],
    slots: Sequence[str],
) -> tuple[float, dict[str, str]]:
    """Max-weight assignment of players to starting slots.

    Players are ``(player_id, positions, value)``. Slots are Sleeper slot
    names; ``BN`` is ignored and an unknown slot name accepts any player (it is
    treated like UTIL). Non-positive values are never started. Greedy by
    descending value, keeping a player only if the chosen set stays matchable
    (Kuhn augmenting paths); exact because the matchable sets form a
    transversal matroid. Returns ``(total, {player_id: slot})``.
    """
    starting = [s for s in slots if s != "BN"]
    accepts = [SLOT_ACCEPTS.get(s) for s in starting]  # None = accepts any
    n_slots = len(starting)
    order = sorted(range(len(players)), key=lambda i: (-players[i][2], i))
    slot_player = [-1] * n_slots

    def can_fill(slot: int, idx: int) -> bool:
        acc = accepts[slot]
        return acc is None or any(p in acc for p in players[idx][1])

    def augment(idx: int, seen: list[bool]) -> bool:
        for s in range(n_slots):
            if seen[s] or not can_fill(s, idx):
                continue
            seen[s] = True
            if slot_player[s] == -1 or augment(slot_player[s], seen):
                slot_player[s] = idx
                return True
        return False

    filled = 0
    for idx in order:
        if filled >= n_slots or players[idx][2] <= 0:
            break
        if augment(idx, [False] * n_slots):
            filled += 1

    total = 0.0
    assignment: dict[str, str] = {}
    for s, idx in enumerate(slot_player):
        if idx != -1:
            total += players[idx][2]
            assignment[players[idx][0]] = starting[s]
    return total, assignment
