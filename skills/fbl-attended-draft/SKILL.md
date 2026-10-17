---
name: fbl-attended-draft
description: Run an attended ESPN Fantasy Basketball draft through the FBL local control plane, including one-time practice setup and fully automatic picks after a verified handoff. Use for FBL practice rehearsals or the final real draft; requires visible browser control and an explicit Start handoff for each room.
metadata:
  short-description: Attend and safely automate an ESPN fantasy draft
  version: 1.3.3
---

# FBL attended draft

Use this skill when operating the FBL repository's `draft_assistant` against a
visible, signed-in ESPN draft room. It covers both a practice rehearsal and a
final real draft. The local server is a durable coordinator; the active agent
must observe and operate ESPN through visible browser controls. Do not use an
ESPN API, hidden page state, network calls, or a browser automation shortcut
that bypasses the visible room.

The localhost panel is `http://127.0.0.1:8765`; it does not launch a room or
start drafting. Attended practice requires a separate owner-provided ESPN
practice-room link and Start handoff. An offline simulator rehearsal is
fixture evidence only. See [localhost handoff](../../docs/LOCALHOST_HANDOFF.md)
for the executable launcher/check and end-to-end setup.

The user's authorization is session-scoped. If the user explicitly authorizes
full automatic picking for the verified current practice or real run, that
single handoff authorizes each pick in that session; do not ask for repeated
per-pick approval. A practice authorization never transfers to the real room:
the real room requires a new explicit Start handoff after fresh identity and
rules checks. The coordinator's operator-confirm flag represents this session
handoff, not a new approval for every pick.

## Before operating

Read the repository runbook and control/browser notes when present:

- `docs/LOCALHOST_HANDOFF.md`
- `docs/DRAFT_RUNBOOK.md`
- `docs/DRAFT_BROWSER.md`
- `docs/DRAFT_CONTROL.md`
- `docs/DRAFT_OPERATOR_BRIDGE.md`
- `docs/PRACTICE_REHEARSAL_2026-10-10.md`
- `docs/PRACTICE_LAUNCH_2026-10-10.md`
- `docs/DRAFT_READINESS.md`
- `docs/DRAFT_DISPATCH_REVIEW.md`
- `docs/DRAFT_RECOVERY_TEST_MATRIX.md`

Start the local panel from the integration checkout:

```powershell
python -m draft_assistant --db runtime/control.sqlite3 serve
```

The service must remain on `127.0.0.1:8765`; keep `runtime/` and credentials
out of version control. Connect a room with the CLI or panel. Connecting is
observation-only and never starts automation:

```powershell
python -m draft_assistant connect "<room-url>" --practice
python -m draft_assistant verify-practice <SESSION_ID> --heading "<exact visible practice heading>" --source-league-id <LIVE_LEAGUE_ID>
```

For a real room, omit `--practice` and never attest it as practice. Keep the
room URL, session ID, observed league/team identity, and visible room identity
together. Only one session may run; pause and reconcile an old session before
using another room.

## Launching a practice room

Use a signed-in Chrome tab and the configured team's page. Select **Practice
Draft**, inspect the setup dialog, verify it says fourteen teams and the
expected format, then click **Start Practice Draft exactly once**. Wait for
the new tab and for the room to hydrate. ESPN creates a fresh practice league
ID on every launch; never reuse an earlier ID or a hard-coded allowlist.

If the new room cannot be located after the single click, stop and ask the
user to point to the room. Do not click Start again. Copy the exact one-line
practice heading rendered in the room and attest it with `verify-practice`,
using the configured live league ID as `--source-league-id`. Attestation binds
that visible practice room to the session and does not start automation. A
practice flag or a heading alone is insufficient, and a live room cannot be
relabeled as practice.

The known October 10, 2026 launch observed a new practice ID, a heading of
`ESPN Fantasy Basketball Draft - Practice Draft for Example League`,
and a hydrated fourteen-team room. Those values are evidence, not permanent
constants: read the current room every time.

## Readiness gate

Before Start or Resume, publish a fresh complete observation and read the
visible ESPN room. Read actual values from the room; configured defaults are
not evidence. Require all of the following:

- fourteen teams, thirteen opponents, ten rounds, and a continuous randomized
  snake order that includes our team;
- our league/team identity and the current phase, current pick, on-clock team,
  and nonnegative visible clock;
- exact custom scoring, including the rendered meanings behind aliases such as
  `TPM` (three-pointers made) and `E` (ejections);
- roster slots `C/G/G/F/F/UTIL`, four bench slots, and three IR slots;
- complete visible pick history, roster consistency, and Autopick explicitly
  off for our team;
- at least one visible candidate row in the Draft/QUEUE player table.

Unknown fields remain unknown. A generic `Autopick` label is not evidence of
its state; require an explicit checked/unchecked or on/off value. A
virtualized player window is valid, but a player absent from that window is not
inferred to be drafted. `No Players Available` is a transient empty table.
Retain the known player catalog while replacing the current visible window.

The observation must be no more than ten seconds old and must not be dated in
the future. Fresh data never resumes a paused session by itself. An explicit
Start or Resume control action is required. Under existing authorization for
full automatic practice picking, the agent may perform validated recovery and
Resume without asking again. A user-requested stop or takeover overrides this.
The real room always needs its own user Start handoff.

The loop can run from any harness that evaluates JavaScript in the visible
room tab: start the panel with `-PageRunner` and load the in-page runner with
`python -m draft_assistant page-loader` (see the runtime handoff reference).
Read [browser capture details](references/browser-capture.md) before assembling
the CUA loop. Recreate helpers when a tab changes; retain a mutable revision
counter. Mark unfinished room tabs for handoff before yielding to the user.

For a canonical practice run, follow the exact sequence in
[canonical rehearsal runbook](references/canonical-rehearsal-runbook.md).

Read [runtime handoff and queue procedures](references/runtime-handoff.md)
before loading canonical modules, splitting a draft across foreground calls,
or exercising queue fallback. Use the current readiness report to distinguish
implemented behavior from a passed live gate.

## Automatic pick loop

Capture the projection catalog while the practice clock is paused, before
activation. Native horizontal scrolling exceeded ten seconds in the October
11 trial; do not collect projection columns during a running draft. Keep
forecast capture time/source separate from fresh room observations. Exclude
candidates missing primitive scoring inputs or GP; preserve missing bonus/foul
forecasts explicitly. During the running draft use fast state, history, roster,
and exact current-player controls against the captured catalog. An incomplete
catalog is a readiness limitation, not a reason to invent values or relax the
ten-second guard.

On each opponent turn, use the reviewed `captureFastFrame()` observer to read
the visible pick feed, controls, roster, and Players in one DOM snapshot.
Switching between History and Players can lag behind rapid opponent picks and
prevent any complete frame from being published before the ten-second guard.
Validate feed round/pick labels, stable headshot IDs, and continuous unique
history. Retain the catalog while precomputing between turns; catalog presence
never establishes availability. Never reuse a practice seat or assume a fixed
draft order. The required room math is 14 teams x 10 rounds = 140 unique picks,
with 13 opponent teams.

Wait for actual row hydration before publishing or preparing. Use state-based
locator waits when the Players grid briefly disappears; rapid empty reads do
not establish a complete frame. Completion messages are not pick events. Keep
the active observer running between turns: a model/tool gap over ten seconds
correctly pauses the coordinator and invalidates a clean rehearsal claim.

Use an awaited foreground loop for the whole compressed practice run. Report
progress from inside that call; returning between short batches can leave the
coordinator without a fresh frame for over ten seconds. Unawaited browser work
does not survive the tool call reliably. The October 11 room `1000000007`
completed in one 240.6-second foreground invocation without recovery pauses.
That run used the root agent's REPL prototype; the repository's reusable
`runForegroundBatch` still needs its own live rehearsal. Do not describe unit
validation of that module as direct browser execution. For a long real draft,
plan bounded foreground sessions and explicit recovery at tool boundaries;
continuous operation for the full real-draft duration remains unverified.

When our team is on the clock:

1. Locate the exact current player row in the visible Draft/QUEUE table. Use
   the stable DOM `data-player-id`; do not target by rank, abbreviation, or
   rendered position alone. Capture a fresh frame immediately before planning.
2. Run one short-lived prepare command. It publishes the observation,
   recommends, reserves, claims, and emits a command with one final guard:

   ```powershell
   python -m draft_assistant --db runtime/control.sqlite3 prepare-action <SESSION_ID> --file runtime/observation.json
   ```

3. Dispatch the returned visible-browser command once, immediately, in the same
   tool batch as preparation. Check elapsed time again before the click: abort
   if the observation is ten seconds old, the clock has expired, or the visible
   room/turn/Autopick/player button no longer agrees. Do not execute a command
   recovered after a long response timeout. Inspect the journal and room and
   resolve it as explicitly not attempted or uncertain before recovery. Do not
   invoke `dispatch-command` twice for one action and do not click a second
   player while the action is unresolved. There is only one pending action.
4. Acknowledge the click as submitted, reread ESPN Pick History **and** the
   roster, publish the post-pick observation, and reconcile only when both show
   the exact player, team, and pick number:

   ```powershell
   python -m draft_assistant --db runtime/control.sqlite3 ack-action <ACTION_ID> submitted
   python -m draft_assistant --db runtime/control.sqlite3 reconcile <SESSION_ID>
   ```

   Confirmation must come from the runtime's `postClickConfirmation` adapter
   (Pick History poll, then Players `captureFrame` with that snapshot); see
   `references/runtime-handoff.md` for the exact `createOperatorRuntime` call and
   view-switch helpers. Never confirm from a fast frame or retained history; if
   the budget expires the action stays `uncertain` and is never re-clicked.
   A click response or history-only match is insufficient. Record observation,
   final-guard, click, and confirmation timestamps for practice latency.

The command guard rejects stale room/session identity, revision, turn, player,
clock, or missing-row conditions. If the visible submitter raises or its result
is ambiguous, mark the action `uncertain`, pause automation, and inspect a fresh
authoritative history plus roster before any retry. Never infer a failed click
from a timeout, and never infer a pick from the disappearance of a table row.
Only mark failure when visible evidence establishes it. A retry is allowed
only after the unknown outcome has been inspected and reconciled.

## Strategy and queue boundaries

The accepted `marginal-lineup-v1` artifact is the automatic selection policy
loaded by default; `--foundation-only` opts out to the foundation policy.
Lookahead may be displayed as advisory, but must remain fallback-to-foundation
whenever the candidate pool, rank, order, retained catalog, or budget is
incomplete. Do not change policy selection from the browser request. Queue
fallback remains disabled as a release capability.

The October 11 pilot verified insertion-order queue consumption on timeout:
Shai queued before higher-ranked Jokic was selected first. ESPN then enabled
our Autopick toggle. Jokic disappeared after an opponent selected him. These
are narrow observed behaviors; sustained queue maintenance and snake-turn
fallback remain unverified. Keep queue fallback disabled as a release
capability until those remaining cases pass.

Local Pause stops agent mutations but cannot stop ESPN's native timer or an
existing queue/Autopick setting. In a practice recovery trial, pause the native
clock separately when needed, inspect our first sidebar Autopick toggle, and
read back OFF before resuming. Do not claim local Pause alone prevents native
timeout selections. On the League Manager tab, `.autoPick-toggle` also matches
an unrelated disconnected-manager setting; scope to our sidebar control.

## Recovery

The real draft cannot be natively paused. Native Pause/Resume controls are
practice setup and diagnostic tools only. Local Pause stops agent mutations
while ESPN's clock and opponent picks continue. A release rehearsal must
complete the canonical workflow without native pauses after activation.
Recovery must keep observing the advancing room, reconcile an uncertain
outcome without retrying it, and either explicitly resume on fresh state or
hand over immediately while the clock continues. Do not count a practice run
requiring native pauses as proof of real-draft readiness.

With the in-page heartbeat (`draft_assistant/heartbeat_publisher.js`, see
`docs/DRAFT_READINESS.md`) running, frames no longer depend on tool-call
cadence. Keep the draft tab visible and foreground. A stale board during an
opponent turn with nothing unresolved reports `degraded` and returns to
`running` by itself; any other stale board pauses and still needs an explicit
resume. In-page dispatch stays off unless deliberately enabled.

Pause on stale or future observations, disconnects, timeouts, Autopick changes,
missing history, vanished candidates, empty tables, a revision change, or an
uncertain outcome. Inspect the room, wait for hydration, reread identity,
rules, clock, history, and roster, then reconcile outstanding actions. A manual
override pauses automation; `manual-pick` stages intent only and does not click
ESPN. After a human makes a manual pick, publish both history and roster and
explicitly Resume once the room is consistent.

On process restart, expect running sessions to be paused while unresolved
actions remain journaled. Do not resume from a healthy frame alone. Never
retry a click while an action is pending, submitted, or uncertain.

## Practice acceptance and final run

Practice is complete only when the actual room demonstrates exact row
targeting, direct Draft submission, both read-backs for every pick, queue and
takeover behavior as applicable, ten selections for our team, a legal final
roster, and measured observation-to-confirmation latency. Read the room's pick
timer from League Manager (`select[data-testid="pickTimeDropdown"]`, values in
milliseconds) at the start of every draft. Any timer is accepted; the first
accepted Start records it as the session's `pick_timer_seconds` and later
Resumes must read back the same value. Record the practice timer separately
from the real timer, since it does not carry to another room. Short timers
leave less margin: the October 11 run needed 3.1–4.5 s from observation to
confirmation, and room 1000000016 lost turns on an unread 30-second timer.

For the final ESPN draft, repeat the full identity/rules/order/roster/Autopick
readiness gate in the real room, read the randomized order anew, and obtain a
new explicit Start handoff. Then use the same single-guard, single-pending,
history-and-roster confirmation loop through all ten legal selections. Finish
only after the final room observation confirms 140 unique picks and our legal
roster. Report what was actually validated and any remaining gate; do not
claim completion from offline tests, a practice launch, or a recommendation
alone.

The October 11 room `1000000007` passed the clean ten-pick timing gate:
fourteen teams, 140 unique selections, ten direct picks confirmed in history
and roster, and a legal final roster. All submissions occurred within nine
seconds of a verified ninety-second turn starting; observation-to-confirmation
took 3.137–4.474 seconds. Earlier rehearsals with pauses and delayed read-backs
remain recovery evidence. Queue fallback remains disabled, and the legal
center-heavy roster needs strategy review before any policy change. Consult
the dated readiness report for outstanding release gates; this practice pass
does not authorize starting the real room or promoting lookahead.

