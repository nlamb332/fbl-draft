# Canonical runtime, handoff, and queue

Run exactly one coordinator on localhost port 8765. On Windows, use the
current exclusive-listener server; an occupied port must fail startup.
Before activation, verify the actual panel response includes durable
`claimed` and `dispatched` fields for existing actions. If the CLI and panel
schemas disagree, keep the native room paused and inspect listener ownership
before restarting. Do not synthesize missing provenance from a claim or click.
Coordinate any restart with the browser operator and reconcile outstanding
actions before explicit Resume.

Use the repository's reviewed `browser_observer.js`, `operator_runtime.js`,
and `live_projection_inputs.js`.
Record their source hashes in rehearsal evidence. Two loading paths exist; both
run the same unchanged module bodies:

- **Harness-agnostic in-page runner (any harness that can evaluate JavaScript
  in the visible ESPN tab: Codex/CUA, Claude in Chrome, or DevTools).** Start
  the panel with `.\scripts\local-panel.ps1 -PageRunner`, then print the
  loader with `python -m draft_assistant page-loader` and evaluate it once in
  the room tab. It adds one `<script>` from `http://127.0.0.1:8765/page-runner.js`
  (each module wrapped unchanged in its own function scope, built from disk on
  request), refuses a bundle whose hash differs from this checkout, and hands
  the CSRF token to the page in memory. Claim ownership from the panel or CLI
  first; the page never claims or transfers ownership, and the coordinator
  refuses page-origin requests for an unowned session or without the owner
  token. Then: `FBL.attach({sessionId, ownerToken, ownTeam, expectedSeat})`,
  `await FBL.runner().captureInputs()` and `await FBL.runner().preflight()`
  while paused; after the owner's explicit Start, `await FBL.runner().activate()`
  and `FBL.runner().startBatch()`. The batch runs inside the page, so gaps
  between tool calls do not stall frames; poll `FBL.runner().summary()` and
  coordinator status. A script tag of the repository's own modules is not
  `eval`; still never use hidden ESPN state or network requests to ESPN.
- **Playwright handle in a CUA REPL.** When CUA cannot import local modules,
  read the source with normal file tools and supply its plain JavaScript
  definitions directly in isolated IIFEs in the CUA session. Preserve the
  module body and expose its exports from the IIFE; do not replace the
  decision or guard logic with a shorter prototype. Do not use `eval`, hidden
  ESPN state, network requests to ESPN, or a separate browser automation
  process.

Loading local definitions is preparation; it does not authorize or submit a
pick.

Verify the observer factory is actually bound, for example
`typeof observerExports.createBrowserObserver === "function"`. A recorded
file hash does not establish that its exports exist in the current REPL.
Construct a new observer for each room and session; discard wrappers pointing
at an absent or previous-room instance. Use the actual session ID returned by
Connect, never a guessed ID or the first entry from an unscoped status list.

Handoff marks are turn-scoped. Mark unfinished room and panel tabs immediately
after acquiring or reusing them, and refresh those marks before every setup,
attestation, or orchestrator checkpoint return. Waiting until the end of a long
turn leaves temporary tabs vulnerable to cleanup after an interruption.

Capture projections while the native practice clock is paused. Bind observer
closures to the current room and a mutable revision counter. Forecast catalog
membership never establishes current availability: use fresh, visible,
enabled player controls. Keep the awaited foreground loop observing between
turns. Retain the ten-second freshness limit at every tool boundary.

Use the current runtime's request sequence. Opponent frames publish normally;
our turn's frame enters through `prepare.observation`, avoiding a redundant
panel request. Keep the independent fresh pre-click DOM guard. Submitted
acknowledgements carry that pre-click frame to refresh coordinator freshness;
they never prove the pick. Reconcile only from fresh post-click history and
roster. Do not add redundant panel calls between those stages. Keep filter
diagnostics outside `DraftObservation`; unknown fields such as `_live_filter`
are rejected by the typed coordinator contract.

Finish loading all helpers, catalog data and operator adapters before native
Resume. For a clean latency trial, set ninety seconds and read back the native
timer option while still paused. Perform native Resume, fresh capture, local
Start, and the entire awaited foreground loop in one CUA invocation. Returning
between activation and dispatch can let ESPN consume the turn; two such native
picks invalidated the October 11 room 1000000016 direct-pick gate. Treat a room
with earlier native selections as recovery evidence rather than seed the
runner's direct-confirmation count from its history.

Before activation, inspect actual `stats` dictionaries, not just names, GP and
ranks. Require finite projections for PTS, FGM, FGA, FTM, FTA, 3PM, REB, AST,
STL, BLK and TO, plus positive projected GP. Missing bonus/foul forecasts stay
explicitly unknown. Keep incomplete rows in the retained catalog as unknown
records, but exclude them from the live candidate window and report why.
Apply `filterLiveAvailablePlayers()` to each live frame before publication or
preparation; retain its warnings. Do not reject an authoritative terminal
frame merely because its candidate window is empty. Read repository
`docs/DRAFT_LIVE_PROJECTION_INPUTS.md` for the helper's schema and diagnostics.

If CUA cannot import a forecast JSON file, prepare a sanitized local HTML page
with that JSON in a visible readonly textarea. Serve only its isolated
transfer directory on localhost, then read the textarea value through a normal
read-only browser locator into the CUA binding. This transfers data without
`eval` or a filesystem import. Do not serve the entire runtime directory.
Preserve original per-record source labels and distinguish the retained
snapshot time from an unknown original forecast-capture time. Stop the transfer
server after loading. Never replace valid retained `stats` with an empty
dictionary merely because current columns have not loaded.

## Post-click confirmation adapter (required to dispatch)

`createOperatorRuntime` fails closed: a runtime that can click (a
`clickPlayer` callback or a `draftTab`) throws `invalid_configuration` unless
it receives `postClickConfirmation: { observer, showPickHistory, showPlayers }`.
`observeAfter` remains only for observe-only, non-dispatching use. The adapter
switches to Pick History, polls `captureHistory({ expectedCurrentPick })`
(100/200/400/800 ms backoff) until `history_complete` and our pick is present,
switches to Players, and calls `captureFrame({ historySnapshot })` with that
exact snapshot. It never reads `captureFastFrame` or retained history, never
clicks, and when the ten-second observation budget is spent the action stays
`uncertain` (no re-click; reconcile through the journal). Frames carrying
`retained_history_*` or `fast_capture_*` warnings are rejected as evidence.

```javascript
const observer = observerExports.createBrowserObserver({
  tab: draftTab, sessionId, room, freshlyVerifiedRules, projectionCatalog, counter,
});
// View switchers: click the visible control, then verify the panel mounted by
// checking its grid kind. Throw if it did not; a throw leaves the action uncertain.
async function showPanel(name, kind) {
  await draftTab.playwright.getByRole("tab", { name }).click();   // adapt to the room's visible control
  const grids = await draftTab.playwright.getByRole("grid").evaluateAll((g) => g.map((x) => x.textContent.slice(0, 80)));
  if (!grids.length) throw new Error(`${name} panel did not mount`);
}
const showPickHistory = () => showPanel(/^pick history$/i, "history");
const showPlayers = () => showPanel(/^players$/i, "players");
const runtime = createOperatorRuntime({
  panelTab,
  draftTab,
  observeCurrent: ({ stage, draftTab: tab }) => observeVisibleDraft(tab, stage),
  postClickConfirmation: { observer, showPickHistory, showPlayers },
});
const result = await runtime.runTurn({ sessionId });
```

Inspect the room's actual Pick History/Players control once before use (role
and name can differ) and keep the helper's verification. Roster cells may be
abbreviated ("K. Towns") with no title: the observer resolves them by headshot
ID, then a unique abbreviation; an ambiguous abbreviation stays unresolved.

## Bounded foreground handoff

At a planned boundary, pause the coordinator, reconcile all direct and queue
actions, capture a fresh room frame, and read fresh coordinator status. For a
practice diagnosis, pause the native timer separately. Local Pause does not
stop ESPN's timer or already queued native actions.

Create a checkpoint from actual status and observations, not a claimed pick
count. Confirmed direct actions must have matching session, player, team and
pick identities, `action_type=draft`, `claimed=true`, and `dispatched=true`.
Match each against both history and roster. Native Autopick selections and
staged manual picks do not count as confirmed automated submissions. Require
the actual `unresolved_actions` and `unresolved_queue_mutations` arrays to be
empty. Stamp status receipt time locally after its response; the nested
observation timestamp is a separate freshness check.

Before the next batch, verify the current room, fresh history, roster, rules,
order and explicit Autopick-off state. Perform an explicit Resume under the
existing session authorization, then verify fresh `running` status and restore
the exact checkpoint action records. Fresh observations never resume by
themselves. At completion publish the terminal frame, reconcile the final pick,
and verify actual `completed` coordinator status, matching revision, 140 unique
selections, ten owned selections, exact direct-action evidence and legal roster.

An accelerated multi-batch test is logical-time evidence. Report live elapsed
time and observed browser latency separately. Do not describe an accelerated
210-minute test as 210 minutes of actual browser continuity.

## Queue pilot and execution

Read repository `docs/DRAFT_QUEUE_RELEASE.md` for current endpoints and the
Q1–Q4 live pilot checklist. Queue fallback stays disabled unless that dated
pilot has passed and the readiness report explicitly enables the capability.
The accepted `marginal-lineup-v1` artifact is the automatic policy by default;
`--foundation-only` opts out. Lookahead remains advisory while its separate
promotion gate is failed.

Queue writes use `queue_runtime.js` with a durable lifecycle adapter:
`queue-prepare → queue-claim → queue-final-guard → visible click → queue-ack`.
Pass the returned mutation identity and exact player identity to the visible
callback. Direct Draft and queue mutations share exclusion: neither can run
while the other has an unresolved outcome. Do not call a bare Queue/Remove
callback to bypass the journal. Each candidate in a nearby-turn pair must keep
the final roster feasible after earlier candidates are provisionally selected.

Read fresh queue order, history, roster, turn, clock and explicit Autopick state
before every mutation and after the click. Confirm only from fresh queue
presence/absence or independently verified history and roster consumption.
An exception or ambiguous result remains unresolved; pause and reconcile
before retrying. A dispatched queue mutation cannot be cancelled to clear that
gate. Preserve an already correct queue prefix rather than remove/re-add it.

During the timeout pilot, local Pause must produce no further FBL writes even
if ESPN consumes two queued entries across nearby snake turns. If native
Autopick becomes enabled, keep the coordinator paused, visibly turn our sidebar
toggle off, read back OFF, reconcile queue/history/roster, and explicitly Resume.
Never infer that local Pause removed an existing ESPN queue.

Record room/session identity, timer, UTC events, mutation/action IDs, before and
after queue order, exact history/roster evidence, Autopick changes, journal state,
and a pass/fail/unverified disposition. Keep member IDs and credentials out of
version control. A practice pass never transfers the user's Start to the real
room; obtain a fresh real-room handoff after all identity and readiness checks.
