# Canonical practice rehearsal: exact operator sequence

Scope: one attended practice room driven by the canonical modules
(`browser_observer.js`, `operator_runtime.js`, `native_activation.js`,
`view_switch.js`, `batch_recovery.js`). The offline dress rehearsal
(`draft_assistant/test_dress_rehearsal.py`) walks this same sequence against
fixtures; it is not live evidence and never closes the #9 gate. Agents never
start live rooms: the user performs Start Practice Draft and hands the room over.

## 0. Before the room

1. Exactly one coordinator on `127.0.0.1:8765`. The practice Start/Resume
   refuses unless the server counts exactly one listener on its port.
2. `connect --practice`, then `verify-practice` with the exact visible heading.
3. Claim ownership once, keep the returned token private, and send it as
   `owner_token` on every operator write:
   `{"operation":"claim-owner","session_id":SID,"label":"agent-a"}`.
4. Load the reviewed modules, the catalog, and construct the observer. Build the
   runtime with `postClickConfirmation` (required once PR #46 lands) and
   `view_switch.createViewSwitcher({tab})` for Pick History/Players.

## 1. Activation (one bounded call, paused room)

In League Manager, read back the pick timer (any value ESPN offers is
accepted; it must be readable) and Autopick off. The first accepted Start
records that value as the session's `pick_timer_seconds`; every later
Start/Resume preflight must read back the same value or it is refused, so a
timer changed mid-draft stops the run. Then a single call:

```javascript
const result = await activateDraft({ tab: draftTab, observe, observePaused,
  requestOperator, sessionId, ownerToken, expectedSeat });
```

It inspects (exactly one visible, enabled Resume in the League Manager control
region, case-insensitive), takes the paused frame, runs coordinator `preflight`
(readable timer matching any recorded one, Autopick off, team + seat, one
listener), clicks Resume once,
publishes the first fresh frame with `start`, all inside 10 s. `refused` means
nothing was clicked: fix the cause (ambiguous/missing Resume, wrong timer) and
call again. `failed`/`budget_exceeded` with `native_resume_clicked: true` means
Resume ran: pause natively, never re-click, recover.

## 2. Foreground loop

Run `runForegroundBatch` in one awaited call. Capture frames with
`captureFastFrame`; when `history_complete` is false, switch to Pick History,
poll `captureHistory({expectedCurrentPick, retain:false})` until complete, call
`setRetainedHistory` **only** with a complete snapshot (an unhydrated empty grid
must not replace good retained history), switch back, recapture. Pass every
frame through `filterLiveAvailablePlayers(...).observation`; diagnostics such as
`_live_filter` never go inside `DraftObservation`.

## 3. Stop and recover (no self-healing)

A batch that stops (stale frame, vanished candidate, crashed observer,
uncertain action) is recovered only by `recoverAfterStop`: pause, resolve the
unresolved action by evidence (a pre-click refusal is acked `failed:not
attempted`; anything dispatched is reconciled from fresh history + roster),
fresh frame, checkpoint, explicit `resume` (preflight enforced again),
fresh running status, then a new batch with `initialCheckpoint`. A run that needs
any recovery is not a clean live run.

## 4. Crashed agent: ownership transfer

The successor reads `status.latest_revision` and sends
`{"operation":"transfer-owner","session_id":SID,"last_confirmed_revision":REV}`.
A stale revision is rejected. On success the old token is revoked, the transfer
is journaled, a running session is paused, and the new token is returned with the
unresolved actions. Resolve those, then `recoverAfterStop` with the new token.

## 5. Export

After `completed` status and 140/10 verification, build the sanitized export
(`session_export.build_sanitized_export`, shape `sanitized-session-v1`) and write
it under `draft_assistant/validation/<run>/`. Record source hashes, timer,
per-pick latency (observation to click, click to confirm), recovery pauses (must
be none for the live gate), and the room/session IDs without credentials.

## Open live checks: run the read-only probe first

The locators in `native_activation.js` and `view_switch.js` and the timer
read-back were built from fixtures. Before any canonical run, confirm them and
the page-to-coordinator path with `live_probe.js`. The probe never clicks and
never sends an observation.

1. Start the single coordinator, then print the snippet with
   `node draft_assistant/live_probe.js`. It reads the CSRF token from the local
   server.
2. Open the practice room, paused and not started, in the attended browser.
   Keep the tab visible.
3. Evaluate the snippet in the room tab three times: on Players, on Pick
   History, and on League Manager. The user switches views between runs. The
   agent may switch them only with permission.
4. On the first run Chrome may show a Local Network Access prompt for
   `127.0.0.1`. **The user** chooses Allow. The agent never accepts browser
   permission prompts.
5. A run passes when:
   - `view_tabs.players.status` and `view_tabs.history.status` are both `unique`;
   - `native_controls.resume.status` is `unique` on the League Manager view of a
     paused room;
   - `native_controls.native_timer_seconds` is a number (the room's configured
     pick timer) and `timer_select_count` is `1`;
   - `coordinator.verdict` is `ok`.
6. On any other status, save the probe JSON. Its `context` section records the
   page structure (tag, role, aria-label, data-testid, classes, ancestry), which
   is enough to correct the selectors. Update the selectors and fixtures, rerun
   the tests, then probe again.

What each verdict means:

| Verdict | Meaning |
|---|---|
| `csp_blocked` | ESPN's CSP `connect-src` forbids the local request. The in-page heartbeat cannot run, and frames keep coming from the agent. |
| `network_blocked` | The Local Network Access permission was denied, or the server is down. |
| `cors_or_pna_blocked` | Server configuration problem. |
| `token_or_origin_rejected` | Server configuration problem. |
