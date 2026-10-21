"""Injury-status availability haircut (flag-gated, scoring-based).

``LivePlayer.injury_status`` is captured from the draft room but, until this
policy is enabled, no valuation path reads it.  When
``LeagueConfig.injury_policy.enabled`` is true a recognised status multiplies the
player's GP / availability factor, so the haircut flows through the foundation
``custom_fp x gp_factor`` score and the marginal ``activity_vector`` alike.  It
is a pure scoring adjustment: no position cap, quota or exclusion is involved.

Status mapping (case-insensitive, whitespace/underscore/hyphen tolerant):

* healthy -- ``None``/blank, ``ACTIVE``, ``HEALTHY``, ``A``: factor 1.0.
* out -- ``O``, ``OUT``, ``IR``, ``INJURED RESERVE``, ``SUSPENDED``, ``SSPD``:
  ``out_factor``.  The draft-time status does not reveal the length of the
  absence, so the default is a conservative half season.
* day-to-day -- ``DTD``, ``DAY TO DAY``, ``Q``, ``QUESTIONABLE``, ``GTD``:
  ``dtd_factor``, a small haircut because most such players miss few games.
* anything else is left unchanged (factor 1.0) with a warning.

The default factors are placeholders until the policy is re-validated; they are
tunable per league via ``InjuryPolicy``.
"""

from __future__ import annotations

import re

from .contracts import InjuryPolicy, LivePlayer

_HEALTHY = frozenset({"", "ACTIVE", "HEALTHY", "A", "NONE"})
_OUT = frozenset({"O", "OUT", "IR", "INJURED RESERVE", "SUSPENDED", "SSPD"})
_DAY_TO_DAY = frozenset({"DTD", "DAY TO DAY", "Q", "QUESTIONABLE", "GTD", "GAME TIME DECISION"})


def normalize_status(status: str | None) -> str:
    return re.sub(r"[\s_\-]+", " ", str(status or "")).strip().upper()


def injury_factor(player: LivePlayer, policy: InjuryPolicy | None) -> tuple[float, tuple[str, ...]]:
    """Return the multiplicative availability factor and any warnings."""

    if policy is None or not policy.enabled:
        return 1.0, ()
    key = normalize_status(player.injury_status)
    if key in _HEALTHY:
        return 1.0, ()
    if key in _OUT:
        return float(policy.out_factor), ()
    if key in _DAY_TO_DAY:
        return float(policy.dtd_factor), ()
    return 1.0, (f"unknown injury status {player.injury_status!r}; availability unchanged",)
