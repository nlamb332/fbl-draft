"use strict";

/**
 * Visible-CUA queue fallback runtime.
 *
 * This module is deliberately transport neutral.  The active browser agent
 * supplies visible observations and callbacks that click ESPN's already
 * rendered Queue/remove controls.  No browser discovery, HTTP, ESPN API, or
 * hidden page state is used here.
 *
 * Queue fallback is disabled by default because the current release has only
 * narrow browser evidence.  A live operator must opt in after the canonical
 * run has passed its release gate.  Even when enabled, every write is guarded
 * by a fresh own-turn observation and a single pending mutation lock.
 */

const DEFAULT_MAX_OBSERVATION_AGE_MS = 10_000;
const DEFAULT_MAX_QUEUE_ENTRIES = 2;
const DEFAULT_NEARBY_PICK_GAP = 15;
const EXPECTED_TEAMS = 14;
const EXPECTED_ROUNDS = 10;
const EXPECTED_ROSTER = 10;
const STARTING_SLOTS = ["C", "G", "G", "F", "F", "UTIL"];

const QUEUE_OUTCOMES = Object.freeze({
  DISABLED: "disabled",
  PLANNED: "planned",
  NOOP: "noop",
  QUEUED: "queued",
  OPPONENT_DRAFTED: "opponent-drafted",
  PAUSED: "paused",
  UNCERTAIN: "uncertain",
  BLOCKED: "blocked",
});

class QueueRuntimeError extends Error {
  constructor(message, code = "queue_runtime_error") {
    super(message);
    this.name = "QueueRuntimeError";
    this.code = code;
  }
}

class QueueBlockedError extends QueueRuntimeError {
  constructor(message, code = "queue_blocked") {
    super(message, code);
    this.name = "QueueBlockedError";
  }
}

function own(object, key) {
  return object !== null && typeof object === "object"
    && Object.prototype.hasOwnProperty.call(object, key);
}

function first(object, ...keys) {
  for (const key of keys) {
    if (own(object, key) && object[key] !== undefined && object[key] !== null) return object[key];
  }
  return undefined;
}

function text(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

function sameText(left, right) {
  return text(left).toLowerCase() === text(right).toLowerCase();
}

function integer(value) {
  const number = Number(value);
  return Number.isInteger(number) ? number : null;
}

function finite(value) {
  if (value === undefined || value === null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function playerId(value) {
  return text(first(value, "player_id", "playerId", "data_player_id", "dataPlayerId", "data-player-id", "id"));
}

function playerName(value) {
  return text(first(value, "player_name", "playerName", "name", "title", "text"));
}

function observedAgeMs(observation, nowMs) {
  const raw = first(observation, "observed_at", "observedAt", "captured_at", "capturedAt");
  if (!raw) return Number.NaN;
  const parsed = typeof raw === "number" ? raw : Date.parse(String(raw));
  return Number.isFinite(parsed) ? nowMs - parsed : Number.NaN;
}

function roomOf(value) {
  const room = first(value, "room", "room_identity", "roomIdentity");
  return room && typeof room === "object" ? room : null;
}

function sameRoom(left, right) {
  if (!left || !right) return false;
  return text(left.league_id || left.leagueId) === text(right.league_id || right.leagueId)
    && text(left.season_id || left.seasonId) === text(right.season_id || right.seasonId)
    && text(left.team_id || left.teamId) === text(right.team_id || right.teamId)
    && Boolean(first(left, "practice")) === Boolean(first(right, "practice"));
}

function historyEvents(observationOrState) {
  const value = first(observationOrState, "history", "pick_history", "pickHistory");
  return Array.isArray(value) ? value : [];
}

function rosterPlayers(observationOrState) {
  const value = first(observationOrState, "roster", "roster_players", "rosterPlayers");
  return Array.isArray(value) ? value : [];
}

function queueEntries(state) {
  const value = first(state, "queue", "queued_players", "queuedPlayers", "entries", "items");
  if (!Array.isArray(value)) throw new QueueRuntimeError("visible queue read-back must contain an array", "queue_readback_missing");
  return value.map((entry, index) => {
    const id = playerId(entry);
    if (!id) throw new QueueRuntimeError(`queue row ${index + 1} has no stable player id`, "queue_identity_missing");
    return { ...entry, player_id: id, player_name: playerName(entry) || null };
  });
}

function availablePlayers(observation) {
  const value = first(observation, "available_players", "availablePlayers");
  return Array.isArray(value) ? value : [];
}

function pickOrder(observation) {
  const value = first(observation, "pick_order", "pickOrder");
  return Array.isArray(value) ? value.map(text) : [];
}

function expectedTeamForPick(pick, order) {
  if (!Number.isInteger(pick) || pick < 1 || pick > EXPECTED_TEAMS * EXPECTED_ROUNDS || order.length !== EXPECTED_TEAMS) return null;
  const round = Math.floor((pick - 1) / EXPECTED_TEAMS);
  const offset = (pick - 1) % EXPECTED_TEAMS;
  const seat = round % 2 === 0 ? offset : EXPECTED_TEAMS - 1 - offset;
  return order[seat] || null;
}

function uniqueIds(values) {
  return new Set(values.filter(Boolean)).size === values.filter(Boolean).length;
}

function rosterCanFillStarters(roster) {
  return matchedStarterSlots(roster) === STARTING_SLOTS.length;
}

function matchedStarterSlots(roster) {
  const eligibilities = roster.map((player) => new Set((first(player, "eligibility", "positions") || []).map((value) => text(value).toUpperCase())));
  let best = 0;
  // Compute a maximum assignment count over observed roster players.  The
  // matcher may leave a slot empty; unknown future players are never added.
  function match(slotIndex, used, matched) {
    if (slotIndex === STARTING_SLOTS.length) {
      best = Math.max(best, matched);
      return;
    }
    match(slotIndex + 1, used, matched);
    const slot = STARTING_SLOTS[slotIndex];
    for (let index = 0; index < eligibilities.length; index += 1) {
      if (used.has(index)) continue;
      if (slot !== "UTIL" && !eligibilities[index].has(slot)) continue;
      const next = new Set(used);
      next.add(index);
      match(slotIndex + 1, next, matched + 1);
    }
  }
  match(0, new Set(), 0);
  return best;
}

function candidateCanBeLegal(player, roster, remainingOwnPicks) {
  const eligibility = (first(player, "eligibility", "positions") || []).map((value) => text(value).toUpperCase());
  if (!eligibility.some((value) => ["C", "G", "F"].includes(value))) return false;
  if (roster.length >= EXPECTED_ROSTER) return false;
  const after = [...roster, player];
  // A roster that can already fill its starters is always safe.  Before that
  // point, retain candidates while enough own turns remain to fill any still
  // missing slot.  This is a legality bound, not an invented player pool.
  const requiredAdditionalPlayers = STARTING_SLOTS.length - matchedStarterSlots(after);
  return requiredAdditionalPlayers <= remainingOwnPicks;
}

function validateHistory(observation, order, currentPick, ownTeam) {
  const events = historyEvents(observation);
  const numbers = events.map((event) => integer(first(event, "pick_number", "pickNumber", "pick")));
  if (!numbers.every((value, index) => value === index + 1 && value < currentPick)) {
    throw new QueueBlockedError("pick history is not contiguous through the current turn", "history_incomplete");
  }
  const ids = events.map(playerId);
  if (!ids.every(Boolean) || !uniqueIds(ids)) throw new QueueBlockedError("pick history contains missing or duplicate player ids", "history_duplicate_player");
  for (let index = 0; index < events.length; index += 1) {
    const expected = expectedTeamForPick(numbers[index], order);
    if (!expected || !sameText(first(events[index], "team"), expected)) {
      throw new QueueBlockedError(`pick history team does not match snake order at pick ${numbers[index]}`, "history_snake_mismatch");
    }
  }
  if (!ownTeam || order.filter((team) => sameText(team, ownTeam)).length !== 1) {
    throw new QueueBlockedError("our team must occur exactly once in the verified pick order", "team_identity_invalid");
  }
  return events;
}

function validateObservation(observation, {
  sessionId,
  room,
  expectedPick = null,
  expectedTeam = null,
  ownTeam,
  requireOwnTurn = true,
  now = Date.now,
  maxObservationAgeMs = DEFAULT_MAX_OBSERVATION_AGE_MS,
  requireRunning = true,
} = {}) {
  if (!observation || typeof observation !== "object") throw new QueueBlockedError("fresh visible observation is required", "observation_missing");
  const observedSession = text(first(observation, "session_id", "sessionId"));
  if (!sessionId || observedSession !== sessionId) throw new QueueBlockedError("observation session does not match the queue session", "session_mismatch");
  if (!sameRoom(roomOf(observation), room)) throw new QueueBlockedError("observation room does not match the verified room", "room_mismatch");
  const age = observedAgeMs(observation, typeof now === "function" ? now() : now);
  if (!Number.isFinite(age) || age < 0 || age > maxObservationAgeMs) throw new QueueBlockedError("observation is missing, future-dated, or stale", "stale_observation");
  if (observation.complete !== true || observation.history_complete !== true) throw new QueueBlockedError("observation completeness is unknown or false", "observation_incomplete");
  const state = text(first(observation, "state", "session_state", "sessionState", "status")).toLowerCase();
  if (requireRunning && state && state !== "running") throw new QueueBlockedError("queue mutation requires an explicitly running session", "session_not_running");
  if (first(observation, "manual_override", "manualOverride") === true) throw new QueueBlockedError("manual override blocks queue mutation", "manual_override");
  if (first(observation, "autopick", "autoPick", "auto_pick") !== false) throw new QueueBlockedError("Autopick must be explicitly off before queue maintenance", "autopick_enabled");
  const currentPick = integer(first(observation, "current_pick", "currentPick", "pick"));
  const onClock = text(first(observation, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"));
  const seconds = finite(first(observation, "seconds_remaining", "secondsRemaining", "clockSeconds", "clock_seconds"));
  if (!currentPick || !onClock || seconds === null || seconds <= 0) throw new QueueBlockedError("fresh own turn and positive visible clock are required", "turn_or_clock_invalid");
  if (expectedPick !== null && currentPick !== expectedPick) throw new QueueBlockedError("current pick changed since queue plan", "pick_changed");
  if (expectedTeam !== null && !sameText(onClock, expectedTeam)) throw new QueueBlockedError("on-clock team changed since queue plan", "team_changed");
  if (requireOwnTurn && !sameText(onClock, ownTeam)) throw new QueueBlockedError("queue maintenance is allowed only on our turn", "not_our_turn");
  const order = pickOrder(observation);
  if (order.length !== EXPECTED_TEAMS || !order.every(Boolean) || !uniqueIds(order)) throw new QueueBlockedError("verified fourteen-team order is required", "pick_order_invalid");
  const history = validateHistory(observation, order, currentPick, ownTeam);
  const ownPicks = history.filter((event) => sameText(first(event, "team"), ownTeam));
  if (ownPicks.length >= EXPECTED_ROSTER) throw new QueueBlockedError("our ten-pick roster is already complete", "roster_complete");
  const roster = rosterPlayers(observation);
  const rosterIds = roster.map(playerId);
  if (!uniqueIds(rosterIds) || !rosterIds.every(Boolean)) throw new QueueBlockedError("roster identity is incomplete or duplicated", "roster_invalid");
  const available = availablePlayers(observation);
  if (!available.length) throw new QueueBlockedError("current visible player rows are required; catalog rows cannot substitute", "available_pool_missing");
  return { ageMs: age, currentPick, onClockTeam: onClock, secondsRemaining: seconds, order, history, ownPicks, roster, available };
}

function nextOwnPicks(currentPick, order, ownTeam) {
  const result = [];
  for (let pick = currentPick; pick <= EXPECTED_TEAMS * EXPECTED_ROUNDS; pick += 1) {
    if (sameText(expectedTeamForPick(pick, order), ownTeam)) result.push(pick);
  }
  return result;
}

function foundationCandidates(recommendation) {
  if (!recommendation || typeof recommendation !== "object") throw new QueueBlockedError("foundation recommendation is required", "recommendation_missing");
  const lookahead = text(first(recommendation, "lookahead_status", "lookaheadStatus")).toLowerCase();
  if (lookahead === "promoted") throw new QueueBlockedError("promoted lookahead cannot drive queue fallback", "lookahead_not_allowed");
  const candidates = first(recommendation, "foundation_candidates", "foundationCandidates", "candidates");
  if (!Array.isArray(candidates)) throw new QueueBlockedError("foundation recommendation candidates are missing", "recommendation_invalid");
  return candidates;
}

function planQueue({
  sessionId,
  observation,
  recommendation,
  ownTeam = "Example Team",
  room = roomOf(observation),
  enabled = false,
  maxEntries = DEFAULT_MAX_QUEUE_ENTRIES,
  nearbyPickGap = DEFAULT_NEARBY_PICK_GAP,
  now = Date.now,
  maxObservationAgeMs = DEFAULT_MAX_OBSERVATION_AGE_MS,
} = {}) {
  if (!enabled) return Object.freeze({ outcome: QUEUE_OUTCOMES.DISABLED, enabled: false, session_id: sessionId || null, candidates: [], reason: "queue fallback is disabled until root live validation" });
  if (!Number.isInteger(maxEntries) || maxEntries < 1 || maxEntries > DEFAULT_MAX_QUEUE_ENTRIES) throw new QueueBlockedError("queue fallback supports at most two entries", "queue_size_invalid");
  if (!Number.isInteger(nearbyPickGap) || nearbyPickGap < 0) throw new QueueBlockedError("nearbyPickGap must be a non-negative integer", "nearby_gap_invalid");
  const observedTeam = text(first(observation, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"));
  const checked = validateObservation(observation, { sessionId, room, ownTeam, requireOwnTurn: false, now, maxObservationAgeMs });
  const source = foundationCandidates(recommendation);
  const current = checked.currentPick;
  const ownPicks = checked.ownPicks.length;
  const remainingOwnPicks = EXPECTED_ROSTER - ownPicks;
  const ownTurnNumbers = nextOwnPicks(current, checked.order, ownTeam);
  const nextOwnPick = ownTurnNumbers.length > 1 ? ownTurnNumbers[1] : null;
  const targetOwnPick = sameText(observedTeam, ownTeam) ? current : ownTurnNumbers[0];
  const allowedEntries = Math.min(maxEntries, remainingOwnPicks, nextOwnPick !== null && nextOwnPick - targetOwnPick <= nearbyPickGap ? 2 : 1);
  const visibleById = new Map(checked.available.map((player) => [playerId(player), player]));
  const drafted = new Set(checked.history.map(playerId));
  const rosterIds = new Set(checked.roster.map(playerId));
  const candidates = [];
  const seen = new Set();
  for (const recommendationCandidate of source) {
    const id = playerId(recommendationCandidate);
    if (!id || id.startsWith("visible:") || seen.has(id) || drafted.has(id) || rosterIds.has(id)) continue;
    seen.add(id);
    const visible = visibleById.get(id);
    // Validate the second queue entry against the first as an already-owned
    // player.  A foundation ranking can otherwise choose two guards (or two
    // forwards) that leave the ten-player roster unable to fill C/G/G/F/F.
    const prospectiveRoster = [
      ...checked.roster,
      ...candidates.map((candidate) => visibleById.get(candidate.player_id)).filter(Boolean),
    ];
    if (!visible || !candidateCanBeLegal(visible, prospectiveRoster, remainingOwnPicks - candidates.length - 1)) continue;
    candidates.push({
      player_id: id,
      player_name: playerName(visible) || playerName(recommendationCandidate),
      // Queue maintenance is guarded against the frame that authorized the
      // write, including when that frame is an opponent turn.  Keep the
      // strategic own-turn target separately so the coordinator never tries
      // to reserve a queue row against a future pick/team that is not live.
      expected_pick: current,
      expected_team: observedTeam,
      target_pick: candidates.length === 0 ? targetOwnPick : nextOwnPick,
      target_team: ownTeam,
      queue_position: candidates.length + 1,
      source: "foundation",
      score: finite(first(recommendationCandidate, "score")),
    });
    if (candidates.length >= allowedEntries) break;
  }
  if (!candidates.length) throw new QueueBlockedError("no legal foundation candidate is present in the current visible pool", "no_visible_foundation_candidate");
  return Object.freeze({
    outcome: QUEUE_OUTCOMES.PLANNED,
    enabled: true,
    session_id: sessionId,
    room,
    revision: integer(first(observation, "revision")),
    expected_pick: current,
    expected_team: observedTeam,
    target_own_pick: targetOwnPick,
    own_team: ownTeam,
    requires_own_turn: sameText(observedTeam, ownTeam),
    remaining_own_picks: remainingOwnPicks,
    next_own_pick: nextOwnPick,
    nearby_pick_gap: nextOwnPick === null ? null : nextOwnPick - current,
    candidates: Object.freeze(candidates.map((candidate) => Object.freeze(candidate))),
    desired_ids: Object.freeze(candidates.map((candidate) => candidate.player_id)),
    reason: "foundation recommendation; exact visible rows only",
  });
}

function normalizeReadback(state) {
  if (!state || typeof state !== "object") throw new QueueRuntimeError("visible queue/history/roster read-back is required", "readback_missing");
  const queue = queueEntries(state);
  const history = historyEvents(state);
  const roster = rosterPlayers(state);
  if (!Array.isArray(history) || !Array.isArray(roster)) throw new QueueRuntimeError("read-back must include history and roster arrays", "authoritative_readback_missing");
  return { ...state, queue, history, roster };
}

function eventForPlayer(history, id) {
  return history.find((event) => playerId(event) === id) || null;
}

function queueIds(state) {
  return state.queue.map((entry) => entry.player_id);
}

function duplicateIds(ids) {
  const seen = new Set();
  const duplicates = [];
  for (const id of ids) {
    if (seen.has(id) && !duplicates.includes(id)) duplicates.push(id);
    seen.add(id);
  }
  return duplicates;
}

function assertReadbackIdentity(state, sessionId, room) {
  const stateSession = text(first(state, "session_id", "sessionId"));
  if (stateSession && stateSession !== sessionId) throw new QueueRuntimeError("queue read-back session does not match", "readback_session_mismatch");
  const stateRoom = roomOf(state);
  if (stateRoom && !sameRoom(stateRoom, room)) throw new QueueRuntimeError("queue read-back room does not match", "readback_room_mismatch");
}

function requireQueueLifecycle(queueLifecycle) {
  if (!queueLifecycle || typeof queueLifecycle !== "object"
    || typeof queueLifecycle.prepare !== "function"
    || typeof queueLifecycle.claim !== "function"
    || typeof queueLifecycle.finalGuard !== "function"
    || typeof queueLifecycle.ack !== "function") {
    throw new QueueRuntimeError("durable queue prepare/claim/finalGuard/ack callbacks are required for every visible queue mutation", "queue_lifecycle_missing");
  }
  return queueLifecycle;
}

function assertMutationIdentity(mutation, { sessionId, operation, playerId: expectedPlayerId }) {
  if (!mutation || typeof mutation !== "object") throw new QueueRuntimeError("queue lifecycle did not return a mutation", "queue_mutation_missing");
  const mutationId = text(first(mutation, "mutation_id", "mutationId"));
  const mutationSession = text(first(mutation, "session_id", "sessionId"));
  const mutationOperation = text(first(mutation, "operation", "queue_operation", "queueOperation")).toLowerCase();
  const mutationPlayer = playerId(mutation);
  if (!mutationId || mutationSession !== sessionId || mutationOperation !== operation || mutationPlayer !== expectedPlayerId) {
    throw new QueueRuntimeError("queue lifecycle mutation identity does not match the visible command", "queue_mutation_identity_mismatch");
  }
  return mutationId;
}

function assertLifecycleResult(result, mutationId, stage) {
  if (!result || typeof result !== "object") throw new QueueRuntimeError(`queue lifecycle ${stage} returned no mutation`, "queue_lifecycle_result_missing");
  const returnedId = text(first(result, "mutation_id", "mutationId"));
  if (returnedId && returnedId !== mutationId) throw new QueueRuntimeError(`queue lifecycle ${stage} returned a different mutation`, "queue_mutation_identity_mismatch");
  return result;
}

function createQueueRuntime({
  enabled = false,
  now = Date.now,
  maxObservationAgeMs = DEFAULT_MAX_OBSERVATION_AGE_MS,
  onPause,
} = {}) {
  let paused = false;
  let pauseReason = null;
  let pendingMutation = null;

  function pause(reason = "local pause") {
    paused = true;
    pauseReason = reason;
    return { paused: true, reason };
  }

  function resume() {
    if (pendingMutation) throw new QueueRuntimeError("an unresolved queue mutation must be reconciled before resume", "pending_mutation");
    paused = false;
    pauseReason = null;
    return { paused: false };
  }

  function state() {
    return { enabled, paused, pause_reason: pauseReason, pending_mutation: pendingMutation ? { ...pendingMutation } : null };
  }

  function requireWritable() {
    if (!enabled) throw new QueueBlockedError("queue fallback is disabled until root live validation", "feature_disabled");
    if (paused) throw new QueueBlockedError(`local pause blocks queue mutations${pauseReason ? `: ${pauseReason}` : ""}`, "local_pause");
    if (pendingMutation) throw new QueueBlockedError("one queue mutation is already pending", "pending_mutation");
  }

  async function observeAndValidate({ sessionId, plan, observe }) {
    if (typeof observe !== "function") throw new QueueRuntimeError("a fresh visible observation callback is required", "observe_missing");
    const observation = await observe({ stage: "queue-guard", sessionId, plan });
    validateObservation(observation, {
      sessionId,
      room: plan.room,
      expectedPick: plan.expected_pick,
      expectedTeam: plan.expected_team,
      ownTeam: plan.own_team,
      requireOwnTurn: plan.requires_own_turn,
      now,
      maxObservationAgeMs,
    });
    return observation;
  }

  async function observeForCleanup({ sessionId, plan, observe }) {
    if (typeof observe !== "function") throw new QueueRuntimeError("a fresh visible observation callback is required", "observe_missing");
    const observation = await observe({ stage: "queue-cleanup-guard", sessionId, plan });
    // Removing a stale row that ESPN has already drafted is maintenance, so
    // it may run after the turn advances.  It still requires the exact room,
    // session, fresh frame, explicit Autopick-off state, and a valid snake
    // history; only the old pick/team is intentionally relaxed.
    validateObservation(observation, {
      sessionId,
      room: plan.room,
      ownTeam: plan.own_team,
      requireOwnTurn: false,
      now,
      maxObservationAgeMs,
    });
    return observation;
  }

  async function readVisibleState({ sessionId, plan, readState }) {
    if (typeof readState !== "function") throw new QueueRuntimeError("a visible queue/history/roster readback callback is required", "readback_callback_missing");
    const raw = await readState({ stage: "queue-readback", sessionId, plan });
    const normalized = normalizeReadback(raw);
    assertReadbackIdentity(normalized, sessionId, plan.room);
    return normalized;
  }

  function classifyDesired(state, plan) {
    const ids = queueIds(state);
    const duplicates = duplicateIds(ids);
    const opponentDrafted = [];
    const consumedByUs = [];
    for (const candidate of plan.candidates) {
      const event = eventForPlayer(state.history, candidate.player_id);
      if (!event) continue;
      if (sameText(first(event, "team"), plan.own_team)) consumedByUs.push(candidate.player_id);
      else opponentDrafted.push({ player_id: candidate.player_id, team: text(first(event, "team")), pick_number: integer(first(event, "pick_number", "pickNumber", "pick")) });
    }
    const effective = plan.candidates.filter((candidate) => !opponentDrafted.some((item) => item.player_id === candidate.player_id) && !consumedByUs.includes(candidate.player_id));
    const desiredIds = effective.map((candidate) => candidate.player_id);
    const prefixLength = ids.filter((id, index) => desiredIds[index] === id).length;
    const orderMatches = prefixLength === Math.min(ids.length, desiredIds.length)
      && desiredIds.slice(0, prefixLength).every((id, index) => ids[index] === id);
    return {
      ids,
      duplicates,
      opponentDrafted,
      consumedByUs,
      effective,
      desiredIds,
      prefixLength,
      orderMatches,
      allDesiredQueued: desiredIds.every((id, index) => ids[index] === id),
    };
  }

  async function execute({
    sessionId,
    plan,
    observe,
    readState,
    queuePlayer,
    removeQueuedPlayer = null,
    queueLifecycle = null,
    onPause: executeOnPause = null,
  } = {}) {
    if (!plan || plan.outcome !== QUEUE_OUTCOMES.PLANNED || plan.session_id !== sessionId) return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "queue plan is missing or belongs to another session" };
    const notifyPause = typeof executeOnPause === "function" ? executeOnPause : onPause;
    try {
      requireWritable();
      await observeAndValidate({ sessionId, plan, observe });
      let stateNow = await readVisibleState({ sessionId, plan, readState });
      let classification = classifyDesired(stateNow, plan);
      if (classification.duplicates.length) return { outcome: QUEUE_OUTCOMES.NOOP, reason: "duplicate queue rows observed; no reorder/remove mutation attempted", duplicate_ids: classification.duplicates, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
      if (classification.opponentDrafted.length) {
        // Opponent picks are authoritative removal from the desired queue. If
        // ESPN has not removed the stale visible row yet, remove that exact
        // row once. Never remove/re-add a healthy queue prefix.
        const opponentDrafted = classification.opponentDrafted;
        const staleRows = opponentDrafted.filter((item) => classification.ids.includes(item.player_id));
        if (staleRows.length) {
          if (typeof removeQueuedPlayer !== "function") {
            return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "an opponent-drafted queue row requires a visible remove callback", queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, opponent_drafted: opponentDrafted };
          }
          const lifecycle = requireQueueLifecycle(queueLifecycle);
          for (const stale of staleRows) {
            if (paused) return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason || "local pause", queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, opponent_drafted: opponentDrafted };
            const current = await observeForCleanup({ sessionId, plan, observe });
            stateNow = await readVisibleState({ sessionId, plan, readState });
            if (!queueIds(stateNow).includes(stale.player_id)) continue;
            const expectedPick = integer(first(current, "current_pick", "currentPick", "pick"));
            let mutationId = null;
            try {
              const proposed = await lifecycle.prepare({
                operation: "remove",
                queue_operation: "remove",
                session_id: sessionId,
                room: plan.room,
                player_id: stale.player_id,
                player_name: playerName(stateNow.queue.find((entry) => entry.player_id === stale.player_id)),
                expected_pick: expectedPick,
                expected_team: text(first(current, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team")),
                revision: integer(first(current, "revision")) || plan.revision,
                observation: current,
                queue: stateNow.queue,
              });
              mutationId = assertMutationIdentity(proposed, { sessionId, operation: "remove", playerId: stale.player_id });
              const claimed = assertLifecycleResult(await lifecycle.claim({ mutation_id: mutationId, session_id: sessionId }), mutationId, "claim");
              pendingMutation = { type: "remove", session_id: sessionId, player_id: stale.player_id, mutation_id: mutationId, expected_pick: expectedPick, started: false };
              assertLifecycleResult(claimed, mutationId, "claim");
              const command = assertLifecycleResult(await lifecycle.finalGuard({ operation: "remove", mutation_id: mutationId, session_id: sessionId, player_id: stale.player_id, queue: stateNow.queue }), mutationId, "final guard");
              if (text(first(command, "type")) !== "remove_queued_player" || playerId(command) !== stale.player_id) throw new QueueRuntimeError("queue final guard command identity does not match the stale row", "queue_mutation_identity_mismatch");
              pendingMutation.started = true;
              const result = await removeQueuedPlayer(command);
              assertLifecycleResult(await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "submitted", status: "submitted", detail: "visible remove click returned" }), mutationId, "submitted ack");
              stateNow = await readVisibleState({ sessionId, plan, readState });
              const immediateRemovalReceipt = !queueIds(stateNow).includes(stale.player_id);
              const fresh = await observeForCleanup({ sessionId, plan, observe });
              // The queue read above belonged to the pre-confirmation frame.
              // Take a second authoritative queue receipt after the fresh
              // observation and acknowledge only that matching generation.
              stateNow = await readVisibleState({ sessionId, plan, readState });
              if (queueIds(stateNow).includes(stale.player_id)) {
                await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "uncertain", status: "uncertain", detail: immediateRemovalReceipt ? "stale queue row reappeared in the post-observation read-back" : "remove callback returned but the post-observation queue row remains" });
                pause("queue removal read-back changed after the fresh observation; explicit reconciliation and Resume are required");
                if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, mutation_id: mutationId });
                return { outcome: QUEUE_OUTCOMES.UNCERTAIN, reason: "stale queue row was not absent in the post-observation read-back; do not retry", opponent_drafted: opponentDrafted, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, callback_result: result, pending_mutation: { ...pendingMutation } };
              }
              assertLifecycleResult(await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "confirmed", status: "confirmed", queue: stateNow.queue, observation: fresh, detail: "exact opponent-drafted queue row absent after visible removal" }), mutationId, "confirmed ack");
              pendingMutation = null;
            } catch (error) {
              if (error.code === "autopick_enabled") {
                pause("Autopick became enabled or unknown after queue removal; explicit Autopick-off readback and Resume are required");
                if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, mutation_id: mutationId, error });
                return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason, opponent_drafted: opponentDrafted, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, pending_mutation: pendingMutation ? { ...pendingMutation } : null };
              }
              if (mutationId) {
                try { await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "uncertain", status: "uncertain", detail: "queue removal outcome is unknown" }); } catch (_ackError) { /* keep the durable mutation unresolved */ }
              }
              pause("queue removal outcome is uncertain; explicit reconciliation and Resume are required");
              if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, mutation_id: mutationId, error });
              return { outcome: QUEUE_OUTCOMES.UNCERTAIN, reason: `Queue removal outcome is unknown: ${error.message || error}; inspect queue, history, and roster before retry`, opponent_drafted: opponentDrafted, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, error: String(error && error.message ? error.message : error), pending_mutation: pendingMutation ? { ...pendingMutation } : null };
            }
          }
        }
        // Do not try to re-add a player absent from the visible available pool.
        plan = { ...plan, candidates: plan.candidates.filter((candidate) => !classification.opponentDrafted.some((item) => item.player_id === candidate.player_id)) };
        classification = classifyDesired(stateNow, plan);
        classification = { ...classification, opponentDrafted };
      }
      if (!classification.orderMatches && classification.ids.length) {
        return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "existing queue order differs; preserving queue to avoid order churn", queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, opponent_drafted: classification.opponentDrafted };
      }
      if (classification.allDesiredQueued || !classification.effective.length) {
        return { outcome: classification.opponentDrafted.length ? QUEUE_OUTCOMES.OPPONENT_DRAFTED : QUEUE_OUTCOMES.NOOP, reason: classification.opponentDrafted.length ? "opponent-drafted queue entries removed by authoritative history" : "desired queue already matches visible order", queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, opponent_drafted: classification.opponentDrafted };
      }
      if (typeof queuePlayer !== "function") throw new QueueRuntimeError("a visible Queue callback is required", "queue_callback_missing");
      const lifecycle = requireQueueLifecycle(queueLifecycle);
      const queued = [];
      for (let index = classification.prefixLength; index < classification.effective.length; index += 1) {
        if (paused) return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason || "local pause", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        // Re-read the turn before every visible mutation.  This also catches
        // native Autopick enabling between two nearby queue entries.
        const current = await observeAndValidate({ sessionId, plan, observe });
        // Re-read and reclassify the queue immediately before selecting the
        // next row.  A native timeout or another operator may have consumed
        // or changed the prefix since the prior callback.
        stateNow = await readVisibleState({ sessionId, plan, readState });
        classification = classifyDesired(stateNow, plan);
        if (classification.duplicates.length) return { outcome: QUEUE_OUTCOMES.NOOP, reason: "duplicate queue rows observed; no reorder/remove mutation attempted", duplicate_ids: classification.duplicates, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        if (!classification.orderMatches && classification.ids.length) return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "existing queue order changed; preserving queue to avoid order churn", queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        const candidate = classification.effective[index];
        if (!candidate) return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "queue target was consumed or reclassified before the next mutation", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        const available = availablePlayers(current).find((item) => playerId(item) === candidate.player_id);
        if (!available) {
          const event = eventForPlayer(historyEvents(current), candidate.player_id);
          if (event && !sameText(first(event, "team"), plan.own_team)) {
            return { outcome: QUEUE_OUTCOMES.OPPONENT_DRAFTED, reason: "candidate was drafted by an opponent before queue mutation", queued, opponent_drafted: [{ player_id: candidate.player_id, team: text(first(event, "team")), pick_number: integer(first(event, "pick_number", "pickNumber", "pick")) }], queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
          }
          return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "exact candidate row disappeared; no catalog substitution", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        }
        if (paused) return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason || "local pause", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        let mutationId = null;
        try {
          const proposed = await lifecycle.prepare({
            operation: "add",
            queue_operation: "add",
            session_id: sessionId,
            room: plan.room,
            player_id: candidate.player_id,
            player_name: candidate.player_name,
            expected_pick: candidate.expected_pick,
            expected_team: candidate.expected_team,
            revision: integer(first(current, "revision")) || plan.revision,
            observation: current,
            queue: stateNow.queue,
          });
          mutationId = assertMutationIdentity(proposed, { sessionId, operation: "add", playerId: candidate.player_id });
          const claimed = assertLifecycleResult(await lifecycle.claim({ mutation_id: mutationId, session_id: sessionId }), mutationId, "claim");
          pendingMutation = { type: "queue", session_id: sessionId, player_id: candidate.player_id, mutation_id: mutationId, expected_pick: candidate.expected_pick, started: false };
          assertLifecycleResult(claimed, mutationId, "claim");
          const command = assertLifecycleResult(await lifecycle.finalGuard({ operation: "add", mutation_id: mutationId, session_id: sessionId, player_id: candidate.player_id, queue: stateNow.queue }), mutationId, "final guard");
          if (text(first(command, "type")) !== "queue_player" || playerId(command) !== candidate.player_id) throw new QueueRuntimeError("queue final guard command identity does not match the candidate", "queue_mutation_identity_mismatch");
          pendingMutation.started = true;
          const result = await queuePlayer(command);
          assertLifecycleResult(await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "submitted", status: "submitted", detail: "visible Queue click returned" }), mutationId, "submitted ack");
          // Read once immediately after the callback to detect a missing
          // receipt, then obtain a fresh explicit Autopick-off frame and read
          // the queue again.  Confirmation must use the latter receipt, not
          // the queue captured before that frame.
          stateNow = await readVisibleState({ sessionId, plan, readState });
          // Confirmation uses a fresh full frame with explicit Autopick off.
          // It may show a changed turn if the native timeout consumed the row;
          // that is safe for acknowledgement but blocks any second write.
          const fresh = await observeForCleanup({ sessionId, plan, observe });
          stateNow = await readVisibleState({ sessionId, plan, readState });
          const afterIds = queueIds(stateNow);
          const event = eventForPlayer(stateNow.history, candidate.player_id);
          if (!afterIds.includes(candidate.player_id) && !event) {
            await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "uncertain", status: "uncertain", detail: "Queue callback returned but post-observation queue/history readback lacks the player" });
            pause("queue insertion read-back is uncertain; explicit reconciliation and Resume are required");
            if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, mutation_id: mutationId });
            return { outcome: QUEUE_OUTCOMES.UNCERTAIN, reason: "Queue callback returned but the post-observation queue/history readback lacks the player; do not retry", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster, callback_result: result, pending_mutation: { ...pendingMutation } };
          }
          const confirmed = assertLifecycleResult(await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "confirmed", status: "confirmed", queue: stateNow.queue, observation: fresh, detail: afterIds.includes(candidate.player_id) ? "queue presence confirmed by fresh visible read-back" : "queue entry consumed; authoritative read-back supplied" }), mutationId, "confirmed ack");
          if (text(first(confirmed, "status")).toLowerCase() === "uncertain") throw new QueueRuntimeError("coordinator did not confirm the queue mutation from authoritative read-back", "queue_confirmation_uncertain");
          queued.push(candidate.player_id);
          pendingMutation = null;
          classification = classifyDesired(stateNow, plan);
          if (event && !sameText(first(event, "team"), plan.own_team)) {
            return { outcome: QUEUE_OUTCOMES.OPPONENT_DRAFTED, reason: "opponent drafted the queued player; queue entry was consumed/removed", queued, opponent_drafted: [{ player_id: candidate.player_id, team: text(first(event, "team")), pick_number: integer(first(event, "pick_number", "pickNumber", "pick")) }], queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
          }
        } catch (error) {
          if (error.code === "autopick_enabled") {
            pause("Autopick became enabled or unknown after queue mutation; explicit Autopick-off readback and Resume are required");
            if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, mutation_id: mutationId, error });
            return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason, queued, pending_mutation: pendingMutation ? { ...pendingMutation } : null };
          }
          if (mutationId) {
            try { await lifecycle.ack({ mutation_id: mutationId, session_id: sessionId, ack_status: "uncertain", status: "uncertain", detail: "queue mutation outcome is unknown" }); } catch (_ackError) { /* keep the durable mutation unresolved */ }
          }
          pause("queue mutation outcome is uncertain; explicit reconciliation and Resume are required");
          if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, mutation_id: mutationId, error });
          return { outcome: QUEUE_OUTCOMES.UNCERTAIN, reason: `Queue mutation outcome is unknown: ${error.message || error}; inspect queue, history, and roster before retry`, queued, pending_mutation: pendingMutation ? { ...pendingMutation } : null, error: String(error && error.message ? error.message : error) };
        }
        if (paused) return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason || "local pause", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        const fresh = await observeForCleanup({ sessionId, plan, observe });
        if (integer(first(fresh, "current_pick", "currentPick", "pick")) !== plan.expected_pick
          || !sameText(first(fresh, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"), plan.expected_team)) {
          return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "turn changed after queue mutation; stop queue maintenance", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
        }
      }
      return { outcome: QUEUE_OUTCOMES.QUEUED, reason: "visible queue readback matches requested insertion order", queued, queue: stateNow.queue, history: stateNow.history, roster: stateNow.roster };
    } catch (error) {
      if (error instanceof QueueBlockedError || error instanceof QueueRuntimeError) {
        if (error.code === "autopick_enabled") {
          pause("Autopick is enabled; explicit Autopick-off readback and Resume are required");
          if (typeof notifyPause === "function") await notifyPause({ reason: pauseReason, sessionId, error });
          return { outcome: QUEUE_OUTCOMES.PAUSED, reason: pauseReason, code: error.code, pending_mutation: pendingMutation ? { ...pendingMutation } : null };
        }
        return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: error.message, code: error.code, pending_mutation: pendingMutation ? { ...pendingMutation } : null };
      }
      throw error;
    }
  }

  async function reconcile({ sessionId, plan, observe, readState } = {}) {
    if (!plan || plan.session_id !== sessionId) return { outcome: QUEUE_OUTCOMES.BLOCKED, reason: "queue plan is missing or belongs to another session" };
    if (typeof observe !== "function") {
      pause("fresh explicit Autopick-off observation is required before queue reconciliation");
      if (typeof onPause === "function") await onPause({ reason: pauseReason, sessionId });
      return { outcome: QUEUE_OUTCOMES.UNCERTAIN, reason: pauseReason, pending_mutation: pendingMutation ? { ...pendingMutation } : null, paused: true, pause_reason: pauseReason };
    }
    let current;
    try {
      current = await observeForCleanup({ sessionId, plan, observe });
    } catch (error) {
      pause(error.code === "autopick_enabled" ? "Autopick is enabled or unknown; explicit Autopick-off readback and Resume are required" : "fresh post-reconcile observation failed");
      if (typeof onPause === "function") await onPause({ reason: pauseReason, sessionId, error });
      return { outcome: QUEUE_OUTCOMES.UNCERTAIN, reason: pauseReason, pending_mutation: pendingMutation ? { ...pendingMutation } : null, paused: true, pause_reason: pauseReason };
    }
    const stateNow = await readVisibleState({ sessionId, plan, readState });
    const events = historyEvents(stateNow);
    const consumed = [];
    const opponentDrafted = [];
    for (const candidate of plan.candidates) {
      const event = eventForPlayer(events, candidate.player_id);
      if (!event) continue;
      const row = { player_id: candidate.player_id, team: text(first(event, "team")), pick_number: integer(first(event, "pick_number", "pickNumber", "pick")) };
      if (sameText(row.team, plan.own_team) && rosterPlayers(stateNow).some((player) => playerId(player) === candidate.player_id)) consumed.push(row);
      else if (!sameText(row.team, plan.own_team)) opponentDrafted.push(row);
    }
    const queue = queueIds(stateNow);
    const queuedDesired = plan.candidates.filter((candidate) => queue.includes(candidate.player_id)).map((candidate) => candidate.player_id);
    const pending = pendingMutation ? { ...pendingMutation } : null;
    if (pending) {
      const resolved = queue.includes(pending.player_id) || consumed.some((item) => item.player_id === pending.player_id) || opponentDrafted.some((item) => item.player_id === pending.player_id);
      if (resolved) pendingMutation = null;
    }
    return {
      outcome: consumed.length || queuedDesired.length ? QUEUE_OUTCOMES.QUEUED : opponentDrafted.length ? QUEUE_OUTCOMES.OPPONENT_DRAFTED : pendingMutation ? QUEUE_OUTCOMES.UNCERTAIN : QUEUE_OUTCOMES.NOOP,
      queue: stateNow.queue,
      history: stateNow.history,
      roster: stateNow.roster,
      consumed,
      opponent_drafted: opponentDrafted,
      pending_mutation: pendingMutation ? { ...pendingMutation } : null,
      paused,
      pause_reason: pauseReason,
    };
  }

  return Object.freeze({
    planQueue: (options = {}) => planQueue({ ...options, enabled: options.enabled === undefined ? enabled : options.enabled }),
    execute,
    reconcile,
    pause,
    resume,
    state,
  });
}

module.exports = {
  DEFAULT_MAX_OBSERVATION_AGE_MS,
  DEFAULT_MAX_QUEUE_ENTRIES,
  DEFAULT_NEARBY_PICK_GAP,
  QUEUE_OUTCOMES,
  QueueBlockedError,
  QueueRuntimeError,
  createQueueRuntime,
  expectedTeamForPick,
  planQueue,
  validateObservation,
};
