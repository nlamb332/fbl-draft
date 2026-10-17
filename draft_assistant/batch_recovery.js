"use strict";

/**
 * Explicit recovery after a stopped foreground batch.
 *
 * `runForegroundBatch` stops (it never self-heals) on a stale frame, a
 * refused pre-click guard, a vanished candidate, a crashed observer, and so on.
 * This helper is the reviewed agent-side sequence that turns such a stop into a
 * validated Resume: pause, resolve any unresolved action by evidence, capture a
 * fresh authoritative frame, build a durable checkpoint, resume explicitly, and
 * read back fresh running status.  It never clicks ESPN and never retries a
 * dispatched click.  It only edits the coordinator through the operator
 * transport it is given (every write carries the current owner token).
 */

const { createBatchCheckpoint } = require("./operator_runtime.js");

function stamp(status, now) {
  const value = typeof now === "function" ? now() : now;
  const ms = value instanceof Date ? value.getTime() : Number(value);
  return { ...status, status_received_at: new Date(ms).toISOString() };
}

function isError(response) {
  return !response || response.status === "error" || Boolean(response.error);
}

function ownConfirmedCount(status, ownTeam) {
  return (status.actions || []).filter((action) => (
    action.status === "confirmed" && action.action_type === "draft" && action.claimed === true
    && action.dispatched === true && String(action.expected_team).toLowerCase() === String(ownTeam).toLowerCase()
  )).length;
}

async function recoverAfterStop({
  stop,
  sessionId,
  ownTeam,
  ownerToken,
  requestOperator,
  requestControl,
  capture,
  readPreflight,
  now = Date.now,
  log = [],
} = {}) {
  const note = (step, detail = {}) => log.push({ step, at: new Date().toISOString(), ...detail });
  const write = (payload) => requestOperator({ ...payload, session_id: sessionId, owner_token: ownerToken });
  const statusNow = async () => stamp(await requestOperator({ operation: "status", session_id: sessionId }), now);
  const failed = (reason, detail = {}) => { note("failed", { reason }); return { recovered: false, reason, ...detail }; };

  note("pause", { stop_reason: stop && stop.reason });
  const paused = await requestControl({ session_id: sessionId, command: "pause", reason: `recovery: ${(stop && stop.reason) || "batch stopped"}`.slice(0, 120) });
  if (isError(paused) && paused.state !== "paused") return failed("coordinator pause failed", { paused });

  let status = await statusNow();
  if ((status.unresolved_queue_mutations || []).length) return failed("an unresolved queue mutation needs manual reconciliation", { status });
  const notAttempted = stop && stop.result && stop.result.outcome === "not-attempted" ? stop.result.command : null;
  for (const action of status.unresolved_actions || []) {
    if (notAttempted && action.action_id === notAttempted.action_id && action.status === "pending") {
      // The pre-click guard refused and no click ran: the only truthful
      // resolution is an explicit not-attempted failure, never a retry.
      const resolved = await write({
        operation: "ack",
        action_id: action.action_id,
        status: "failed",
        detail: `not attempted: ${stop.reason}`.slice(0, 200),
      });
      note("resolved_not_attempted", { action_id: action.action_id, ok: !isError(resolved) });
      if (isError(resolved)) return failed("could not resolve the not-attempted action", { resolved });
    } else {
      // Anything dispatched must be settled from fresh history + roster.
      const evidence = await capture();
      const reconciled = await write({ operation: "reconcile", observation: evidence });
      note("reconcile_unresolved", { action_id: action.action_id, status: reconciled && reconciled.status });
    }
  }
  status = await statusNow();
  if ((status.unresolved_actions || []).length) return failed("an unresolved draft action remains; manual inspection required", { status });

  const frame = await capture();
  const published = await write({ operation: "publish", observation: frame });
  if (isError(published)) return failed("fresh frame publish failed", { published });
  status = await statusNow();
  const ownPicksConfirmed = ownConfirmedCount(status, ownTeam);
  let checkpoint;
  try {
    checkpoint = createBatchCheckpoint(frame, { sessionId, ownTeam, ownPicksConfirmed, coordinatorStatus: status, now });
  } catch (error) {
    return failed(`checkpoint refused: ${error.message}`, { status });
  }
  note("checkpoint", { revision: checkpoint.revision, own_picks_confirmed: ownPicksConfirmed });

  const resumeFrame = await capture();
  const resumePayload = { operation: "resume", observation: resumeFrame };
  if (typeof readPreflight === "function") resumePayload.preflight = await readPreflight(resumeFrame);
  const resumed = await write(resumePayload);
  if (isError(resumed)) return failed(`explicit resume refused: ${resumed && resumed.error}`, { resumed });
  const resumeStatus = await statusNow();
  note("resumed", { state: resumeStatus.state });
  return { recovered: true, checkpoint, resumeStatus, ownPicksConfirmed, log };
}

module.exports = { ownConfirmedCount, recoverAfterStop };
