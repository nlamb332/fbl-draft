"""Sleeper fantasy-point scoring for per-game projected stat lines."""
from __future__ import annotations

from typing import Any, Mapping

# Counting stats Sleeper projects; projections omit them when the value is
# zero, so absence means "zero this game" rather than "not projected".
_ZERO_WHEN_MISSING = frozenset(
    {"pts", "reb", "ast", "stl", "blk", "to", "tpm", "dd", "td"}
)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    return float(value)


def fantasy_points(
    stats: Mapping[str, Any], scoring: Mapping[str, Any]
) -> tuple[float, dict[str, float], tuple[str, ...]]:
    """Return ``(per_game_points, contributions_by_key, warnings)``.

    Every scoring key with a non-zero weight is multiplied by the matching
    projected stat. Projected counting stats (``pts``, ``blk``, ``dd`` ...)
    missing from ``stats`` count as 0 silently.
    Any other scoring key with no projected stat (bonuses, penalties such as
    ``bonus_pt_40p`` or ``tf``) contributes 0 and adds one warning.
    """
    total = 0.0
    contributions: dict[str, float] = {}
    warnings: list[str] = []
    for key in sorted(scoring):
        weight = _num(scoring[key])
        if not weight:
            continue
        stat = _num(stats.get(key)) if key in stats else None
        if stat is None:
            if key not in _ZERO_WHEN_MISSING:
                warnings.append(
                    f"Scoring key '{key}' is not projected; counted as 0"
                )
            continue
        value = weight * stat
        contributions[key] = value
        total += value
    return total, contributions, tuple(warnings)
