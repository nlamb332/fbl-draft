# Attended draft operation

Run python -m draft_assistant --db runtime/control.sqlite3 serve from the integration worktree, then open http://127.0.0.1:8765.

An active agent session, awake computer, and signed-in browser are required. The server manages decisions and durable action state; the active agent operates ESPN through browser-control tools. Closing the agent stops execution.

## Handoff

Give the agent the ESPN room link and connect it in the panel. Connecting only observes. Read actual room/team identity, Rules, order, Players, Pick History, roster, clock, and explicit Autopick state. Configuration is not evidence of observed rules; unknown fields remain unknown.

To launch a practice, use Practice Draft on the configured team's page, verify fourteen teams in the setup dialog, and click Start Practice Draft exactly once. Wait for the new tab and hydration. If the room cannot be located, stop and ask the user to point to it instead of clicking Start again. ESPN generates a fresh practice league ID. After observing its explicit practice heading, bind it to the connected session with `verify-practice SESSION_ID --heading "ESPN Fantasy Basketball Draft - Practice Draft for Example League" --source-league-id 1000000000`. This verification never starts automation. A practice flag alone cannot satisfy readiness, and the real league cannot be relabeled practice.

Verify fourteen teams, ten rounds, exact custom scoring, C/G/G/F/F/UTIL, four bench, three IR, our identity, continuous snake history, roster consistency, and Autopick off. A visible player window is allowed; missing rows are not inferred drafted. The user explicitly presses Start or gives an explicit Start handoff for this verified session.

## Active loop

Before activation, capture the projection catalog with the practice clock paused. Native horizontal scrolling measured over ten seconds in one trial, so live turns must use the preloaded forecast catalog and fast current-room identity, history, roster, clock, and enabled-player-row reads. Record forecast provenance separately from the fresh observation timestamp. Unknown scoring inputs remain unknown. Exclude candidates missing required primitive projections. Read `DRAFT_DISPATCH_REVIEW.md` for the observer and dispatch contracts.

Observe opponent picks and round transitions. Retain the known player catalog and precompute between turns. Publish fresh observations within ten seconds. Read the randomized order from this room; never reuse a practice seat. Recreate room-specific helper closures when changing tabs; a reassigned REPL variable does not reliably update an earlier helper's binding. Skip transient incomplete frames until hydrated; never prepare from an empty or incomplete candidate window.

Keep the observer and dispatch loop awaited within one foreground browser call, with inline progress. The clean October 11 compressed practice finished in 240.6 seconds this way. Returning between short batches caused a correct stale pause after a 10.569-second gap. The reusable runner's default no-progress limit is 120 seconds, allowing normal ninety-second opponent turns while publishing fresh frames. Its exact canonical implementation and long-duration real-draft operation still require live rehearsal; the clean pass used the root REPL prototype. Never relax the ten-second freshness guard to bridge a tool boundary.

On our turn, locate the exact current player row, capture a fresh frame, then run python -m draft_assistant --db runtime/control.sqlite3 prepare-action SESSION_ID --file runtime/observation.json.

This publishes, recommends, reserves, claims, and consumes one final guard. Immediately dispatch the returned command through visible browser controls. Never dispatch-command twice. The operator confirm flag represents the authorized session handoff, not fresh approval on each pick.

Acknowledge submitted, then read history AND roster, publish the new observation, and reconcile only when both confirm the exact player/team/pick. A click response is insufficient. Record observation, guard, click, and confirmation timestamps for practice latency. Target the first thirty seconds of a verified ninety-second turn; individual passes do not establish a clean full-draft timing pass. Correlate local operator responses by request ID and poll the same request until resolved. Never resubmit or execute an aged command after a response timeout.

## Recovery

Pause stops new agent mutations and preserves dispatched unresolved actions. It does not stop ESPN's native timer, existing queue, or Autopick. Pause the native practice clock separately during diagnostics, and inspect the first sidebar Autopick toggle: League Manager contains a second unrelated toggle. Manual override pauses automation. Staging a manual pick in the panel records intent; it does not click ESPN. Make the manual pick, publish history and roster read-back, reconcile, then explicitly Resume.

For timeouts, disconnects, Autopick changes, missing history, vanished candidates, or empty tables: pause, inspect, hydrate, reconcile, and explicitly Resume. A healthy frame never resumes automation by itself. Never infer failure from a timed-out click or infer a pick from table absence. Mark failure only when visible evidence establishes the outcome. Restart pauses running sessions and preserves the journal.

Only one session may run at once. Pause the old session and reconcile its outstanding actions before starting another room. Keep runtime and credentials outside version control.

## Release gates

Practice must verify exact row targeting, direct submission, history/roster confirmation, queue behavior, takeover, ten selections, final legal roster, and full browser latency. Record the actual practice timer separately from the real ninety-second timer. The October 11 queue pilot verified insertion order, first timeout consumption, native Autopick activation, and removal of an opponent-drafted queued player. Continuous queue maintenance and snake-turn fallback remain unverified; queue fallback stays disabled as a release capability. See the dated rehearsal and recovery matrix for evidence and remaining gates.

For the real room repeat identity/rules checks and obtain a new explicit Start handoff. The accepted `marginal-lineup-v1` artifact is the automatic default; `--foundation-only` opts out. Lookahead remains advisory. See [localhost handoff](LOCALHOST_HANDOFF.md) for the executable panel launcher and the separate attended-practice path.
