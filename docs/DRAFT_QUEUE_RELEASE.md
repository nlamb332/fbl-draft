# Queue fallback release procedure

`draft_assistant/queue_runtime.js` is the browser-agent side of the queue
fallback. It does not discover a browser, call ESPN, use an ESPN API, or read
hidden page state. The active CUA agent supplies the already-open ESPN tab,
the local panel session, a visible observer, and callbacks that click the
rendered Queue/remove controls.

Queue fallback is disabled by default. A coordinator Start handoff authorizes
session maintenance, but does not enable this unverified release feature.
The root agent must set the feature flag only for the disposable live pilot
after the canonical run has passed its own validation. Offline tests prove
guards and procedure shape; they do not prove ESPN queue order or timeout
consumption.

## API contract

The runtime is created in the active CUA REPL after the reviewed source has
been loaded:

```javascript
const queueRuntime = createQueueRuntime({
  enabled: QUEUE_FALLBACK_LIVE_VALIDATED,
  onPause: ({ reason, sessionId, observation }) => {
    // Ask the single coordinator on 127.0.0.1:8765 to pause this session.
    // The callback does not click ESPN or turn Autopick off.
  },
});
```

`planQueue({ sessionId, observation, recommendation, room, enabled: true })`
is pure. It validates the exact session and room, a fresh complete frame, the
fourteen-team snake order, contiguous history, explicit Autopick off, a
positive visible clock, and the expected current pick/team. The plan uses the
foundation recommendation order only. A promoted lookahead recommendation is
rejected. A candidate must be present in the current visible Draft/QUEUE
rows with a stable `data-player-id`; `known_players` and a retained catalog
cannot substitute for a missing row.

The plan contains at most two candidates. The second is included only when
the next own snake pick is within the configured nearby gap (15 picks by
default), fewer than ten own roster players remain, and the second candidate
is legal after the first candidate has already been added to the roster
feasibility check. The observed current pick/team remains in the plan even
when an opponent is on the clock, so a later write cannot silently use a
different turn.
Each candidate's `expected_pick` and `expected_team` are this live guard frame;
its separate target fields describe the future own snake turn that justified
keeping the candidate in the nearby queue plan.

`execute({ sessionId, plan, observe, readState, queuePlayer,
removeQueuedPlayer, queueLifecycle, onPause })` is the only mutating
procedure. `queueLifecycle` is required for every write and must implement
`prepare`, `claim`, `finalGuard`, and `ack` callbacks backed by the coordinator
operations below. The runtime checks that every returned `mutation_id`,
session, operation, and player ID matches the intended row; a standalone
`queuePlayer` callback cannot bypass the durable journal. The final guard
returns a command containing the session, verified room, stable player
ID/name, expected pick/team, and plan revision; `queuePlayer` must use that
command to click the exact visible row once. Every mutation is preceded by a
fresh observation and an explicit visible queue/history/roster read-back. The
runtime permits one pending queue mutation at a time.

An existing queue prefix is preserved. An already matching plan is a no-op;
duplicate rows and an order mismatch are reported without removing and
re-adding rows. A target found in authoritative history under another team
is reported as `opponent-drafted` and is removed from the desired plan. It is
never re-added from the catalog. A target that disappears without history is
blocked pending a fresh read-back.

`reconcile({ sessionId, plan, observe, readState })` is required after an
ambiguous callback, a native timeout, or an Autopick transition. Queue
presence confirms queue maintenance; a queue entry consumed by our team is
reported only when both history and roster contain the same player. A
history-only match remains unresolved. No queue status is converted into a
confirmed Draft action.

## Coordinator lifecycle API

The browser agent uses the existing single coordinator on
`http://127.0.0.1:8765/api/operator`. These operations are separate from the
direct `prepare`/`ack` DraftAction flow and therefore do not add rows to the
ten-pick action count:

1. `queue-prepare` reserves one exact visible row. Send `session_id`,
   `queue_operation` (`add` or `remove`), `player_id`, the latest observation
   (or first publish it through the normal observation endpoint), and the
   expected `revision`, `current_pick`, and `on_clock_team`. An add requires
   that stable ID in the fresh visible player rows plus the current queue
   read-back. A remove requires that exact ID in the visible queue read-back.
2. `queue-claim` consumes the one-time coordinator claim. Send
   `mutation_id` from the prepare response.
3. `queue-final-guard` rereads running state, room, revision, current pick,
   team, clock, Autopick, and the current queue immediately before the visible
   CUA click. Send `mutation_id` and the current queue read-back for both add
   and remove; an add that appeared in the read-back becomes a no-op, and a
   missing remove row is rejected.
4. The browser agent clicks the rendered Queue or remove control exactly once.
   Then send `queue-ack` with `ack_status: "submitted"` and a concise UI
   detail. Follow with a fresh explicit `autopick: false` observation plus
   queue read-back and `ack_status: "confirmed"`. Confirmation proves queue
   presence/absence (or independently verified history plus roster
   consumption) only; draft confirmation still requires authoritative history
   and roster.
5. `queue-reconcile` accepts the fresh queue, history, and roster read-backs
   after a timeout, pause race, or uncertain callback. It resolves an add when
   the row is still queued, when history and roster show our consumption, or
   when authoritative history shows an opponent pick. It resolves a remove
   only when the exact row is absent.

Example request shapes (the `request_id` is optional but should be recorded):

```json
{"operation":"queue-prepare","request_id":"q-1","session_id":"S","queue_operation":"add","player_id":"p1","expected_revision":42,"expected_pick":23,"expected_team":"Example Team","queue":[],"observation":{ "session_id":"S", "revision":42, "...":"fresh visible observation" }}
{"operation":"queue-claim","request_id":"q-1-claim","mutation_id":"M"}
{"operation":"queue-final-guard","request_id":"q-1-guard","mutation_id":"M","queue":[]}
{"operation":"queue-ack","request_id":"q-1-submitted","mutation_id":"M","ack_status":"submitted","detail":"Queue click returned"}
{"operation":"queue-ack","request_id":"q-1-readback","mutation_id":"M","ack_status":"confirmed","queue":[{"player_id":"p1"}],"observation":{"session_id":"S","revision":43,"...":"fresh explicit Autopick-off observation accompanying the post-observation queue read-back"}}
{"operation":"queue-reconcile","request_id":"q-reconcile","session_id":"S","queue":[],"observation":{ "session_id":"S", "revision":43, "...":"fresh history and roster observation" }}
```

The server also exposes the same lifecycle as `/api/queue-prepare`,
`/api/queue-claim`, `/api/queue-final-guard`, `/api/queue-ack`, and
`/api/queue-reconcile` for local integrations. All browser writes still go
through the visible CUA callback; these endpoints never call ESPN.

## Browser pilot

The browser agent owns the visible ESPN writes; the root agent owns the one
coordinator and its panel on `127.0.0.1:8765`.

1. Bind a fresh practice room to the coordinator session and complete the
   normal fourteen-team, ten-round, rules, roster, order, and Autopick-off
   readiness gate. Obtain the explicit Start handoff. Keep the room/session
   IDs together in every evidence record.
2. With Autopick explicitly off, capture one fresh observation and call
   `planQueue` with the foundation recommendation returned for that exact
   revision. Read the visible queue, history, and roster before writing.
3. Call `execute` with an active CUA `queuePlayer` callback. Queue the first
   candidate, wait for the visible queue row to hydrate, and read queue,
   history, and roster again. If the next own snake pick is nearby, queue the
   second candidate through the same one-mutation procedure. Do not remove
   and re-add rows to force an order; preserve an already matching prefix.
4. Verify Q1 evidence: stable IDs in intended and observed insertion order,
   the duplicate behavior, the explicit queue empty state after a separately
   authorized cleanup, the exact visible timer, and Autopick still off.
5. For Q2, leave the coordinator locally paused after the queue has been
   deliberately set. Let the visible ESPN timer consume the first queue entry
   and read Pick History, roster, queue, Autopick, and the next turn. If the
   second entry is still queued and the next own snake turn is nearby, native
   ESPN may consume it while the local coordinator remains paused. The runtime
   must perform no further writes in this state. Record the journal as having
   no FBL direct dispatch or queue mutation during the pause.
6. If fresh read-back shows Autopick enabled, keep the coordinator paused.
   Read the exact first sidebar Autopick control, turn it off through visible
   UI only, and read back an explicit unchecked/off value. Do not turn it off
   automatically from this runtime. Publish a fresh frame and explicitly
   Resume only after history, roster, queue, turn, and clock checks pass.
7. Reconcile each queue entry with queue/history/roster read-backs. A local
   Pause blocks new FBL writes but cannot retract an ESPN queue entry that was
   already submitted. Never claim queue cancellation stopped ESPN unless the
   visible queue is empty and the empty state was read back.

The browser record must include the test ID, UTC timestamps, room league ID,
coordinator session ID, plan and read-back revisions, visible timer, current
pick/team, Autopick values, ordered queue IDs before and after each mutation,
duplicate/no-op result, history pick/team/player IDs, roster IDs, local
state/reason, action or mutation outcomes, and final `pass`, `fail`, or
`unverified` disposition. A successful offline run does not promote queue
fallback or close the live release gate.

## Recovery rules

Call `pause` before investigating stale data, a changed turn, an unknown
Autopick value, a missing row, a queue read-back mismatch, or an uncertain
callback. Pause stops subsequent local mutations immediately. A callback that
has started may have reached ESPN; its pending mutation remains until
`reconcile` finds queue presence, authoritative history plus roster, or an
authoritative opponent draft. Never retry an unresolved mutation.

