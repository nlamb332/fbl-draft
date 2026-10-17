# Draft strategy

The draft policy is deterministic and consumes a typed `DraftObservation`. It
filters history and the current roster by player id, computes the custom
per-game points from the configured scoring map, and multiplies by projected GP
divided by 82. When direct GP is unavailable it uses the supplied availability
fraction. A missing input remains unknown and is reported on the candidate; it
is not borrowed from row order or silently fabricated.

Coverage is a small tie adjustment for currently scarce C/G/F starting
positions. It cannot overcome a meaningful production gap. An authoritative
available pool is checked for exact starter matching and future roster
completion; when no candidate can preserve a legal roster the recommendation
is empty so the coordinator can pause for reconciliation. Incomplete pools
remain advisory and carry an explicit warning.

`lookahead.py` performs bounded local rollouts through every pick before the
next own snake turn. Opponents use direct draft rank when present and a small
rank-plus-unmet-position-needs model. A retained player catalog is used for
players already drafted so opponent needs do not treat all prior picks as
positionless. The rollout has a wall-clock budget, evaluates a bounded top
candidate set, and returns the full foundation ordering when any horizon is
unfinished. It has no browser or network calls.

Lookahead promotion is gated by a static validation artifact. The artifact must
contain at least 100 independent seed clusters, exact 14-team/13-opponent,
140-pick and all-seat counts, a foundation-versus-lookahead comparison, finite
paired bootstrap metrics with a positive 95% lower bound, no seat mean loss
greater than five percent, and matching algorithm/config/rollout fingerprints.
The live coordinator then binds a promoted recommendation to its current
observation revision before any automatic action.

The replay harness in `draft_assistant/replay.py` runs the same policy over JSON
observations, accepts sparse patches, and records malformed frames as explicit
faults. It never submits a browser action.

## Validation

`draft_assistant/validation_runner.py` compares foundation and promoted
lookahead policies in complete 14-team, ten-round drafts with all 14 starting
seats. Each run has 140 unique picks and 13 modeled opponents. Draft decisions
use forecast records; exact dynamic-programming lineup allocation is scored
against a separate seeded ±10% realized-stat copy, so forecast values do not
leak into evaluation. Bootstrap resampling is clustered by independent seed,
not by correlated seat rows. Nominal and market/custom opponent
misspecification scenarios are supported.

The checked-in validation directory contains the actual matrix outputs when a
run completes. A partial or failed run is not treated as a promotion artifact.
Synthetic records and schedules test mechanics and robustness only; they do
not validate real-player projections, ADP, injury forecasts, or live ESPN
execution.
