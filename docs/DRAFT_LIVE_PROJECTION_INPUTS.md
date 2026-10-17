# Live projection input guard

`draft_assistant/live_projection_inputs.js` is the browser-side preactivation
check for visible candidates. It requires a finite, positive projected GP no
greater than 82 and all core primitive projections consumed by the current
custom scorer: `PTS`, `FGM`, `FGA`, `FTM`, `FTA`, `3PM`, `REB`, `AST`, `STL`,
`BLK`, and `TO`. Boolean, null, nonfinite, zero, negative, and out-of-range
values are reported and excluded.

The helper never substitutes `FPTS`, ADP, rank, row order, availability, or
bonus/foul fields for a missing core projection. `PF`, `TF`, `EJ`, `DD`, `TD`,
and `QD` remain optional missing metadata. `filterLiveAvailablePlayers`
returns a deep-cloned observation with only eligible visible rows in
`available_players`; it retains `known_players`, history, roster, and visible
pool metadata. Incomplete rows are never added to history or treated as
drafted. Duplicate stable IDs make every occurrence ineligible, and the
filter operates on validated row indexes so an invalid duplicate cannot pass
through beside a valid row.

When filtering excludes rows from an observation that claimed
`available_pool_complete`, the returned observation sets that flag to `false`
and adds a diagnostic warning. The returned `filter_metadata` retains the
original completeness claim and the retained/excluded row counts for the
caller to publish separately.

This guard is intentionally separate from valuation and coordinator readiness.
The browser activation path should call `validateLiveProjectionInputs` (or the
strict filter) before any live recommendation or dispatch, and pause when no
eligible visible row exists.
