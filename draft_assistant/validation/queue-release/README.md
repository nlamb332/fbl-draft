# Queue release validation

This directory holds queue fallback evidence. `test_queue_runtime.test.js`
is offline coverage for the deterministic planner and visible-CUA procedure;
`test_queue_control.py` covers the coordinator's durable prepare/claim/final
guard/ack/readback lifecycle. Neither suite can establish ESPN behavior.

The live operator must add a sanitized evidence record for each browser pilot
with the exact practice league ID, coordinator session ID, revision, visible
timer, ordered queue IDs, duplicate/no-op result, Autopick before and after,
history and roster read-backs, local pause/resume state, and a `pass`, `fail`,
or `unverified` disposition. Preserve the native timeout case separately
from any local mutation: local Pause does not retract an already-submitted
ESPN queue entry.

Required live gates are Q1 queue insertion/removal and duplicate behavior, Q2
first and nearby second native timeout consumption while local Pause remains
active, Q3 explicit Autopick transition and recovery, Q4 one-pending-mutation
pause race, and a final history/roster reconciliation. Until those records
exist, queue fallback remains disabled for production sessions.

The browser agent should use the coordinator operations documented in
`docs/DRAFT_QUEUE_RELEASE.md`: `queue-prepare`, `queue-claim`,
`queue-final-guard`, `queue-ack`, and `queue-reconcile`. A local Pause cancels
only an unclicked pending intent. A dispatched or uncertain mutation remains
for readback, and native ESPN queue entries may continue to be consumed while
the coordinator is paused.

