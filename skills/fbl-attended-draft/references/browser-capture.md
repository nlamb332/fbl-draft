# ESPN browser capture learned in practice

Use only documented CUA browser controls and read-only mounted DOM. These
selectors were observed on October 10, 2026; inspect the new room before reuse.

- Pick History renders **one grid per round**. Read all its grids with
  `getByRole('grid').evaluateAll(...)`, combine rows, and retain a Map keyed by
  absolute pick number. Each row's first gridcell is that number, third is team,
  `.playerinfo__playername` gives full name, and the mounted NBA headshot URL
  contains `/full/<player-id>.png`. Never discard earlier rounds on navigation.
- Players has one grid. Its mounted row order may be recycled and unsorted;
  map headers to cells in each row rather than infer ranking from DOM order.
  Stable IDs are on `button[data-player-id]`. Scope the Draft click to the
  Players grid: the page may contain another button for the same player.
- The scoring table virtualizes columns. Click Players to focus ESPN **before
  native scrolling**, especially after a panel command. Native scroll may
  otherwise reach the foreground panel. Scroll left, read RK/GP/FGM/FGA/FTM,
  scroll right and read through PTS/ADP. Read back headers after scrolling.
  An edge caret button can also page columns; it is not always mounted.
- Read the selected `2027 Projected` and Averages controls. These determine
  whether stat values are per game. RK is draft-room rank; ADP is separate.
  Preserve missing EJ/PF/TF/DD/TD/QD forecasts explicitly; displayed blanket
  zeroes were not treated as credible forecasts. Capture injury badges and
  preserve unknown availability rather than silently assuming full health.
- Retain a known-player catalog; replace current available IDs with the current
  mounted window. `available_pool_complete=false`. Never infer a pick merely
  because a row leaves the window. Require full primitive inputs for the
  candidate, or clearly reject/flag the incomplete forecast.
- Our roster is the table whose first `th` is `POS`. Its player identity is the
  full `title` on `.player-column`, not the abbreviated link text. Match that
  exact full name uniquely to the captured catalog and independently require
  the same stable ID in our pick history. When the title is missing, resolve by
  headshot ID, then a unique abbreviated name. Ambiguity blocks confirmation.
- The first select contains the roster team order; trim whitespace in option
  names. `.on-the-clock` contains the absolute pick; its parent's `.team-name`
  identifies the on-clock team. `.clock__digits` is `MM:SS` while running.
  `.autoPick-toggle input` exposes checked state. Unknown stays unknown.
- Fast AUTO opponents can advance while switching tabs. A non-contiguous
  history/frame is a recovery condition, not a reason to invent missed picks.
  On our stable turn, recapture complete history before explicitly resuming.
- After our tenth pick, Players stops rendering a grid. Inspect the completion
  heading/dialog, switch to Pick History, collect all ten rounds and the roster,
  then publish `phase=complete`, no on-clock pick/team/clock, 140 unique picks,
  ten legal roster players, and `history_complete=true`.

Keep revisions in a mutable object shared by helpers, e.g. `counter.n += 1`.
When reconnecting, begin above the coordinator's persisted latest revision.
New tab handles require new capture helpers; old closures can keep old handles.
Recreate the observer factory with the new tab, session, room, history Map, and
revision object. Reassigning a REPL variable did not update earlier helper
closures in the October 11 trial. Check the actual tab URL each new binding.

Preload projections before activation. With Show Drafted enabled while paused,
drafted rows omit the action button ID; their mounted headshot URL still
provides the stable player ID. Turn Show Drafted off for the live loop, and
require a currently visible enabled exact Draft button. A preloaded catalog is
forecast data, never current availability evidence. Capture both column ends
and merge by ID; virtualized row order changes while scrolling.

Native scrolling measured 10,401 ms for one horizontal page. Avoid it during
active observation. Preserve forecast provenance and unknown fields; the fresh
room timestamp must not be presented as the forecast capture timestamp.

A visible-state locator wait timed out even though the operator command
succeeded. Read `#operator-result`
text, parse JSON, verify the unique request ID and non-pending status, and
inspect the outcome without resubmitting. A bounded pending-result read loop
may stop with an explicit pending outcome. Never act on the previous result.

Prepare, check deadline and current visible turn, and click in one tool batch.
Any interruption between prepare and click invalidates the immediate dispatch
assumption. Record the click timestamp, acknowledge once, then read history
and roster immediately; do not spend time collecting projections first.

## Fast live feed

The October 11 room kept its pick feed mounted while Players was selected.
`li.pick-message__container` contains `.playerinfo__playername`, a headshot
with the stable ID, and `.pick-info` text such as `R5, P12 - Example Opponent 27`.
Its child span names the selecting team. In a verified fourteen-team room,
absolute pick is `(round - 1) * 14 + pickInRound`. Ignore non-pick system
messages; incomplete pick identities never establish complete history.

Use the repository's `captureFastFrame()` to read that feed, controls, roster,
and exact enabled Players buttons together. Switching tabs for every opponent
pick caused continuous history races and stale pauses in a compressed room.
When a table disappears briefly, wait for the actual player row to reappear
before publishing or preparing. After our tenth pick, an empty Players table
is allowed only by the coordinator's narrow verified-final-roster guard.

Keep observation batches continuous. A commentary or model/tool gap can exceed
ten seconds; the resulting pause is correct and disqualifies a clean rehearsal.
Do not change freshness thresholds to hide this limitation. In an orchestrated
trial, one browser operator owns the room while the root agent reports progress.

Room `1000000007` completed all ten direct selections in a single awaited
240.6-second foreground invocation, with inline progress. This validates the
foreground procedure used by the REPL prototype, not an imported execution of
the repository's canonical modules. File-URL import was unavailable and dynamic
code evaluation was rejected. Read the reviewed source through normal file
tools and supply plain JavaScript definitions to the browser REPL when needed;
do not assume an import or background execution capability. A normal opponent
turn may leave the same pick visible for ninety seconds; use elapsed no-progress
limits, not a few identical-frame counts, while continuing fresh observations.
