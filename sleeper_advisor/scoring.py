"""Sleeper fantasy-point scoring for per-game projected stat lines."""
from __future__ import annotations

from typing import Any, Mapping

# Counting stats Sleeper projects; projections omit them when the value is
# zero, so absence means "zero this game" rather than "not projected".
_ZERO_WHEN_MISSING = frozenset(
    {"pts", "reb", "ast", "stl", "blk", "to", "tpm", "dd", "td",
     "fgm", "fga", "fgmi", "ftm", "fta", "ftmi", "tpa", "tpmi", "dreb"}
)

# Exact box-score identities: ``total = part_a + part_b``.  Sleeper projects
# ``reb`` and ``dreb`` but not ``oreb``; shot misses are usually present but
# are filled from attempts and makes when absent.
_IDENTITIES = (
    ("reb", "oreb", "dreb"),
    ("fga", "fgm", "fgmi"),
    ("fta", "ftm", "ftmi"),
    ("tpa", "tpm", "tpmi"),
)

# Personal fouls are not projected.  When scored, estimate them from projected
# seconds played (``sp``) at a league-average rate of about 0.08 fouls per
# minute (roughly 19.5 team fouls per 240 minutes).  This is a labelled
# assumption, not a player-specific projection.
PF_PER_MINUTE = 0.08


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
    Box-score identities fill a missing component (``oreb = reb - dreb``,
    ``fgmi = fga - fgm`` ...) without a warning. An unprojected ``pf`` is
    estimated from projected minutes at ``PF_PER_MINUTE`` with one warning.
    """
    stats = _complete(stats, scoring)
    total = 0.0
    contributions: dict[str, float] = {}
    warnings: list[str] = []
    for key in sorted(scoring):
        weight = _num(scoring[key])
        if not weight:
            continue
        stat = _num(stats.get(key)) if key in stats else None
        if stat is None and key == "pf" and _num(stats.get("sp")) is not None:
            stat = _num(stats["sp"]) / 60.0 * PF_PER_MINUTE
            warnings.append(
                f"Scoring key 'pf' is not projected; estimated from minutes "
                f"at {PF_PER_MINUTE:g} per minute"
            )
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


def _complete(stats: Mapping[str, Any], scoring: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return ``stats`` with scored identity components derived when missing."""
    derived: dict[str, float] = {}
    for whole, part_a, part_b in _IDENTITIES:
        if not any(_num(scoring.get(k)) for k in (whole, part_a, part_b)):
            continue
        values = {k: _num(stats.get(k)) for k in (whole, part_a, part_b)}
        if values[whole] is not None and values[part_a] is None and values[part_b] is not None:
            derived[part_a] = max(0.0, values[whole] - values[part_b])
        elif values[whole] is not None and values[part_b] is None and values[part_a] is not None:
            derived[part_b] = max(0.0, values[whole] - values[part_a])
        elif values[whole] is None and values[part_a] is not None and values[part_b] is not None:
            derived[whole] = values[part_a] + values[part_b]
    if not derived:
        return stats
    return {**stats, **derived}
