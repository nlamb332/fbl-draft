# Draft dispatch review

The attended draft release keeps browser control in the active CUA agent. The
local coordinator still owns preparation, claims, final guards, acknowledgments,
and reconciliation; the browser helper only uses already-open visible tab
handles supplied by that agent.

## Reusable CUA helper

`draft_assistant/operator_runtime.js` exports `createOperatorRuntime` and
`runVisiblePick`. The runtime accepts:

- `panelTab`, an already-open local panel tab, or an injected
  `requestOperator(payload)` function for tests;
- `draftTab`, an already-open ESPN tab used only by the default exact-row
  clicker;
- `observeCurrent(context)` and `observeAfter(context)`, which are active-agent
  DOM observers that return the fresh observation shape used by the Python
  coordinator; and
- `clickPlayer(command, current)` when the agent wants to supply its own exact
  visible-row click.

The panel adapter writes `#operator-payload`, clicks **Execute operator command**,
and waits for the matching non-pending `request_id`. A prepare request starts a
ten-second freshness window. Before the click the runtime checks the prepared
session, action ID, pick, team, explicit Autopick-off state, positive effective
clock, matching session IDs, parseable fresh timestamps, and the exact returned
`player_id` row. The default clicker targets ESPN's observed
`button[data-player-id="..."]` control directly. It invokes the visible click
callback once, then immediately acknowledges `submitted`. It captures a new
observation and requires both the exact Pick History event and roster player
before sending `reconcile`.

There are two explicit terminal recovery outcomes:

- `not-attempted`: no click callback started. The prepared command is never
  reused; inspect status/journal and obtain a new fresh frame before preparing
  again.
- `uncertain`: the click callback started, or the submitted/reconcile result is
  ambiguous. The helper best-effort acknowledges `uncertain`, then requires
  journal inspection plus fresh history and roster evidence. It never retries a
  click or prepares a second command automatically.

The helper has no browser discovery, standalone Chrome launch, HTTP client,
cookie access, or hidden ESPN state. Inspect the repository file with normal
file tools, then supply its reviewed plain-JS definitions in the active CUA
REPL, omitting the CommonJS `module.exports` wrapper. Use a runtime loader only
when that loader is documented for the active REPL. Pass existing tab handles
from the same session; do not run a separate browser process. After the
definitions are present, a minimal active-session call is:

```javascript
const runtime = createOperatorRuntime({
  panelTab,
  draftTab,
  observeCurrent: ({ stage, draftTab: tab }) => observeVisibleDraft(tab, stage),
  postClickConfirmation: { observer, showPickHistory, showPlayers },
});
const result = await runtime.runTurn({ sessionId });
```

`observeVisibleDraft` is supplied by the active agent and must return the
visible observation object; the helper does not create it or fetch hidden
state. The panel and draft tabs above are existing CUA handles from that same
session.

For a continuous attended rehearsal, `createForegroundBatchRunner` (also
available as `runtime.runForegroundBatch`) keeps the whole observation,
publish, opponent-turn, and own-turn sequence in one foreground call. Supply
the already-created runtime, a visible `observe` callback, an explicit
`ownTeam` identity, an optional `isOwnTurn` callback, and an optional progress
callback. Each state is checked
for a matching session, parseable fresh timestamp, explicit Autopick-off,
verified fourteen-team order, and complete history before it is published.
Opponent turns are published and polled serially; own turns call the same
`runTurn` path, so the ten-second command guard and one-click recovery rules
remain unchanged. `maxDurationMs` bounds the batch and `maxCycles` bounds
hydration loops. Fresh unchanged opponent frames are allowed for a bounded
elapsed no-progress window (120 seconds by default); callers may add an
explicit state-count limit for a test or a stricter room policy. Unknown
`complete` or `history_complete` flags fail closed. The runner stops on stale
or incomplete state, an observer or publish error, a paused session, a
non-confirmed own turn, or a final validation failure. It never calls
Start/Resume and never leaves browser work running after the function returns.
Final success requires phase `complete`, 140 contiguous identified picks across
fourteen teams, ten unique roster players matching the own-team history, and
the configured count of confirmed own turns.

`draft_assistant/browser_observer.js` follows the same loading rule. Inspect
the repository source with normal file tools, provide its reviewed plain-JS
definitions in the active CUA REPL with the CommonJS wrapper omitted, and then
call `createBrowserObserver({ tab, sessionId, room, freshlyVerifiedRules,
projectionCatalog })` with the already-open active tab. The observer only reads
visible DOM through that supplied handle; it does not discover, launch, or
control a browser independently. Its frame fields follow version-one
`DraftObservation`: `known_players` is the supplied catalog, `available_players`
is limited to exact visible enabled IDs with ShowDrafted explicitly off, and
`available_pool_complete` remains false during live frames. Missing forecast
fields remain in each player's `missing_stats`; missing DOM IDs or titles stay
explicitly missing and never become guessed player identities.

When both reviewed CommonJS modules are supplied to one CUA REPL, wrap each
module body in its own plain-JS IIFE and return only the reviewed public
definitions. For example, use
`const observerExports = (() => { /* reviewed browser_observer body */ return { createBrowserObserver, extractFastSnapshot }; })();`
and then
`const runtimeExports = (() => { /* reviewed operator_runtime body */ return { createOperatorRuntime, createForegroundBatchRunner }; })();`.
Keep the `text`, `first`, and `own` helpers inside their respective IIFEs so
the modules cannot collide. This is a manual, reviewed definition transfer:
do not use `eval`, `new Function`, dynamic file imports, `file:` URLs, or a
standalone browser process.

ESPN can mount Pick History and Players as exclusive panels. Call
`captureHistory({ expectedCurrentPick })` while the history panel is visible,
retain its returned `history`, `observed_at`, and `history_complete`, then pass
that snapshot to `captureFrame({ historySnapshot })` while the Players panel is
visible. The factory also accepts the same object as `retainedHistory`, and
`setRetainedHistory` can replace it after an explicit refresh. The frame checks
the retained snapshot age against the configured history freshness window and
keeps `complete=false` when freshness or completeness is missing; it never
assumes both grids are mounted together.

When Players is absent after a fresh contiguous history read-back and the
visible roster contains the expected ten legal players, `captureFrame` can
produce a complete `phase="draft"` frame for the coordinator's post-own-final
empty-pool guard. `captureFastFrame` and `completeFrame` require explicit
visible native completion evidence before normalizing a stale on-clock label:
the completion heading, 140 unique identified picks, and the complete ten
player roster must all be present. Only then are `current_pick`,
`on_clock_team`, and `seconds_remaining` published as null for the terminal
frame. A missing heading keeps terminal completion unclaimed. These helpers do
not clear the live clean-room release gate; root validation must still
establish the fast path in the attended room.

For a running room, `captureFastFrame()` performs one read-only `body.evaluate`
against the already-open tab. It reads the visible
`li.pick-message__container` feed, current pick and clock, on-clock team,
Autopick, order, roster, and Players rows in one DOM snapshot. Feed labels such
as `R5, P12` become absolute pick 68 using the verified fourteen-team room
shape. A fresh retained history snapshot may fill a virtualized feed, but the
merged history is always revalidated against the current pick and retains the
oldest real observation timestamp. This avoids tab switches during the guarded
live loop and never fabricates freshness.

## Live-trial freshness recommendation

The live trial exposed a timing boundary that the ten-second coordinator guard
is correctly catching. Native horizontal projection scrolling took 10,401 ms
for the left page, followed by a 114 ms read, then 2,207 ms for the right page
and a 171 ms read. Projection collection performed during a running turn can
therefore trigger stale pause before the next guarded action is prepared.

Capture and merge the complete projection catalog while ESPN is paused and
before the explicit Start handoff. During the running draft, limit fresh reads
to the fast control state, current pick and on-clock team, clock, explicit
Autopick state, Pick History, roster, and the exact visible candidate row.
Reuse the pre-captured catalog as data; preserve any missing projection fields
as unknown. Do not assign a new projection timestamp to delayed data, infer
freshness from a completed scroll, or relax the coordinator's stale-data guard
to accommodate native scrolling.

The first clean-room picks (13 and 16) were guarded and confirmed, but the
room paused between turns. Later diagnostic work led to an ESPN timeout at
picks 41 and 44 with Autopick enabled; the native control was paused at pick
58 and Autopick was then read back as off. Those earlier observations did not
clear the clean freshness gate. Room `1000000007` later cleared that gate with
a pre-captured catalog and fast live observation in one uninterrupted
foreground REPL invocation. It used the root prototype; exact execution of
these reusable modules remains a separate live validation item.

## Existing recovery coverage

The Python suite already covers manual takeover and reconnect persistence:

- `test_wrong_turn_and_manual_takeover` verifies paused manual override,
  staged manual picks, blocked automation, and explicit resume release.
- `test_restart_pauses_and_preserves_unresolved_action` verifies a process
  restart pauses the session and preserves unresolved actions.

The new Node tests add the browser-side edges that were absent from those
tests: prepare failure during takeover/reconnect is `not-attempted`, a delayed
prepare result is rejected before any click, a click exception is `uncertain`,
and history-only read-back cannot confirm a pick without the roster.

## Validation

```powershell
node --test draft_assistant/test_browser_observer.test.js draft_assistant/test_operator_runtime.test.js
python -m unittest discover -s . -p 'test_*.py'
```
