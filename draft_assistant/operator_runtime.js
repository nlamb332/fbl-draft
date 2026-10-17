"use strict";

/**
 * Visible CUA-side dispatch runtime.
 *
 * This file deliberately has no browser, HTTP, cookie, or ESPN dependency.
 * The active CUA agent supplies already-open panel/draft tab handles and a
 * DOM observer.  The runtime only uses those handles to fill the local
 * operator form, read visible state, and click one exact player row.
 *
 * A command is usable only for the short interval beginning when the prepare
 * request was sent.  A command returned after that interval is not clicked.
 * Once the click callback starts, every result is uncertain until the fresh
 * Pick History and roster read-back both contain the exact player.
 */

const DEFAULT_MAX_COMMAND_AGE_MS = 10_000;
const DEFAULT_MIN_SECONDS_REMAINING = 1;
const DEFAULT_POLL_MS = 50;
const DEFAULT_POST_CLICK_POLL_DELAYS_MS = Object.freeze([100, 200, 400, 800]);
// Frames carrying these warnings came from a fast/virtualized read or reused
// retained history; they can never confirm a click.
const POST_CLICK_FORBIDDEN_WARNING = /^(?:retained_history_|fast_capture_)/;
const DEFAULT_MAX_POLLS = 40;
const DEFAULT_MAX_BATCH_DURATION_MS = 240_000;
const DEFAULT_BATCH_POLL_MS = 25;
const DEFAULT_MAX_BATCH_CYCLES = 2_000;
const DEFAULT_MAX_BATCH_NO_PROGRESS_MS = 120_000;

const OUTCOMES = Object.freeze({
  CONFIRMED: "confirmed",
  NOT_ATTEMPTED: "not-attempted",
  UNCERTAIN: "uncertain",
});

class OperatorRuntimeError extends Error {
  constructor(message, code = "operator_runtime_error") {
    super(message);
    this.name = "OperatorRuntimeError";
    this.code = code;
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function own(object, key) {
  return object !== null && typeof object === "object" && Object.prototype.hasOwnProperty.call(object, key);
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

function finiteNumber(value) {
  if (value === undefined || value === null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function observedAgeMs(observation, nowMs) {
  const observedAt = first(observation, "observed_at", "observedAt", "captured_at", "capturedAt");
  if (!observedAt) return Number.NaN;
  const parsed = typeof observedAt === "number" ? observedAt : Date.parse(String(observedAt));
  if (!Number.isFinite(parsed)) return Number.NaN;
  return nowMs - parsed;
}

function commandPlayerId(command) {
  return text(first(command, "player_id", "playerId", "data_player_id", "dataPlayerId"));
}

function commandSessionId(command) {
  return text(first(command, "session_id", "sessionId"));
}

function commandActionId(command) {
  return text(first(command, "action_id", "actionId"));
}

function commandExpectedPick(command) {
  return finiteNumber(first(command, "expected_pick", "expectedPick", "pick", "current_pick", "currentPick"));
}

function commandExpectedTeam(command) {
  return text(first(command, "expected_team", "expectedTeam", "team", "on_clock_team", "onClockTeam"));
}

function rowPlayerId(row) {
  return text(first(row, "player_id", "playerId", "data_player_id", "dataPlayerId", "data-player-id"));
}

function availableRows(dom) {
  const rows = first(dom, "available_players", "availablePlayers", "players", "rows", "player_rows", "playerRows");
  if (Array.isArray(rows)) return rows;
  const single = first(dom, "current_player", "currentPlayer", "player_row", "playerRow");
  return single ? [single] : [];
}

function validateCurrentDom(command, dom, {
  now = Date.now,
  maxObservationAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
  minSecondsRemaining = DEFAULT_MIN_SECONDS_REMAINING,
} = {}) {
  if (!dom || typeof dom !== "object") return { ok: false, reason: "current visible DOM observation is missing" };

  const expectedSession = commandSessionId(command);
  const observedSession = text(first(dom, "session_id", "sessionId"));
  if (!expectedSession || !observedSession || observedSession !== expectedSession) {
    return { ok: false, reason: "current DOM observation session does not match prepared command" };
  }

  const expectedPick = commandExpectedPick(command);
  const currentPick = finiteNumber(first(dom, "current_pick", "currentPick", "pick"));
  if (expectedPick === null || currentPick === null || currentPick !== expectedPick) {
    return { ok: false, reason: `current pick does not match prepared command (${currentPick} !== ${expectedPick})` };
  }

  const expectedTeam = commandExpectedTeam(command);
  const onClockTeam = text(first(dom, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"));
  if (!expectedTeam || !sameText(onClockTeam, expectedTeam)) {
    return { ok: false, reason: "current on-clock team does not match prepared command" };
  }

  const autopick = first(dom, "autopick", "autoPick", "auto_pick");
  if (autopick !== false) return { ok: false, reason: "Autopick is not explicitly off for the current team" };

  const secondsRemaining = finiteNumber(first(dom, "seconds_remaining", "secondsRemaining", "clockSeconds", "clock_seconds"));
  if (secondsRemaining === null || secondsRemaining <= 0) return { ok: false, reason: "visible draft clock is expired or unknown" };

  const age = observedAgeMs(dom, now());
  if (!Number.isFinite(age)) {
    return { ok: false, reason: "current DOM observation timestamp is required and must be parseable" };
  }
  if (age < 0 || age > maxObservationAgeMs) {
    return { ok: false, reason: `current DOM observation is ${Math.round(age)}ms old` };
  }
  const effectiveSeconds = secondsRemaining - Math.max(0, (age || 0) / 1000);
  if (effectiveSeconds < minSecondsRemaining) {
    return { ok: false, reason: `draft deadline has only ${Math.max(0, effectiveSeconds).toFixed(2)} seconds remaining` };
  }

  const playerId = commandPlayerId(command);
  const rows = availableRows(dom);
  const row = rows.find((candidate) => rowPlayerId(candidate) === playerId);
  if (!playerId || !row) {
    return { ok: false, reason: "exact returned data-player-id row is not visible" };
  }

  return {
    ok: true,
    currentPick,
    onClockTeam,
    secondsRemaining,
    effectiveSeconds,
    ageMs: age,
    row,
  };
}

function historyEvents(observation) {
  const events = first(observation, "history", "pick_history", "pickHistory");
  return Array.isArray(events) ? events : [];
}

function rosterPlayers(observation) {
  const players = first(observation, "roster", "roster_players", "rosterPlayers");
  return Array.isArray(players) ? players : [];
}

function hasAuthoritativeEvidence(command, observation, {
  now = Date.now,
  maxObservationAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
} = {}) {
  if (!observation || typeof observation !== "object") return { ok: false, reason: "post-click observation is missing" };
  const expectedSession = commandSessionId(command);
  const observedSession = text(first(observation, "session_id", "sessionId"));
  if (!expectedSession || !observedSession || observedSession !== expectedSession) {
    return { ok: false, reason: "post-click observation session does not match command" };
  }
  const age = observedAgeMs(observation, now());
  if (!Number.isFinite(age)) {
    return { ok: false, reason: "post-click observation timestamp is required and must be parseable" };
  }
  if (age < 0 || age > maxObservationAgeMs) {
    return { ok: false, reason: `post-click observation is ${Math.round(age)}ms old` };
  }
  if (observation.complete !== true || observation.history_complete !== true) {
    return { ok: false, reason: "post-click observation is not a complete authoritative frame" };
  }
  // Hard rule: confirmation never rests on a virtualized fast frame or on
  // retained history, whatever the frame's own completeness flags claim.
  const forbidden = (Array.isArray(observation.warnings) ? observation.warnings : []).find((warning) => POST_CLICK_FORBIDDEN_WARNING.test(String(warning)));
  if (forbidden) {
    return { ok: false, reason: `post-click observation is not confirmable (${forbidden})` };
  }
  if (observation.autopick !== false) {
    return { ok: false, reason: "post-click observation does not prove Autopick is off" };
  }
  if (!Array.isArray(first(observation, "history", "pick_history", "pickHistory")) || !Array.isArray(first(observation, "roster", "roster_players", "rosterPlayers"))) {
    return { ok: false, reason: "post-click observation lacks complete history and roster arrays" };
  }
  const expectedPick = commandExpectedPick(command);
  const expectedTeam = commandExpectedTeam(command);
  const playerId = commandPlayerId(command);
  const historyMatch = historyEvents(observation).some((event) => (
    finiteNumber(first(event, "pick_number", "pickNumber", "pick")) === expectedPick
      && sameText(first(event, "team"), expectedTeam)
      && rowPlayerId(event) === playerId
  ));
  const rosterMatch = rosterPlayers(observation).some((player) => rowPlayerId(player) === playerId);
  if (!historyMatch || !rosterMatch) {
    return {
      ok: false,
      reason: `authoritative read-back incomplete (history=${historyMatch}, roster=${rosterMatch})`,
    };
  }
  return { ok: true, historyMatch: true, rosterMatch: true };
}

function validateFinalCoordinatorEvidence(status, observation, confirmedActions, {
  sessionId,
  ownTeam,
  now = Date.now,
  maxStatusAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
} = {}) {
  const reasons = [];
  const fields = coordinatorStatusFields(status || {});
  if (!status || typeof status !== "object") reasons.push("fresh final coordinator status is required");
  if (fields.sessionId !== text(sessionId)) reasons.push("final coordinator status session does not match requested session");
  if (fields.state !== "completed") reasons.push("final coordinator status must be explicitly completed");
  if (fields.unresolved.length) reasons.push("final coordinator status contains unresolved draft or queue actions");
  if (!roomsMatch(fields.room, first(observation, "room", "room_identity", "roomIdentity"))) reasons.push("final coordinator status room does not match final observation");
  const finalRevision = finiteNumber(first(observation, "revision"));
  if (fields.latestRevision === null || finalRevision === null || fields.latestRevision !== finalRevision) reasons.push("final coordinator revision does not match final observation");
  const observedTime = typeof fields.observedAt === "number" ? fields.observedAt : Date.parse(String(fields.observedAt || ""));
  const receivedTime = typeof fields.receivedAt === "number" ? fields.receivedAt : Date.parse(String(fields.receivedAt || ""));
  const nowValue = typeof now === "function" ? now() : now;
  const nowMs = nowValue instanceof Date ? nowValue.getTime() : Number(nowValue);
  if (!Number.isFinite(observedTime) || !Number.isFinite(receivedTime) || !Number.isFinite(nowMs)) reasons.push("final coordinator observation and receipt timestamps are required and must be parseable");
  else if (observedTime > nowMs || receivedTime > nowMs || nowMs - observedTime > maxStatusAgeMs || nowMs - receivedTime > maxStatusAgeMs) reasons.push("final coordinator observation or receipt is stale or from the future");
  const finalHistory = historyEvents(observation);
  const finalRoster = rosterPlayers(observation);
  const expectedActions = Array.isArray(confirmedActions) ? confirmedActions : [];
  for (const action of expectedActions) {
    if (!actionMatchesEvidence(action, finalHistory, finalRoster, ownTeam, sessionId)) reasons.push(`final history and roster do not match confirmed action ${action && action.action_id}`);
    if (!fields.actions.some((candidate) => candidate && candidate.action_id === action.action_id && candidate.status === "confirmed" && candidate.action_type === "draft" && candidate.session_id === text(sessionId) && candidate.claimed === true && candidate.dispatched === true)) reasons.push(`final coordinator status does not preserve confirmed action ${action && action.action_id}`);
  }
  return { ok: reasons.length === 0, reasons, fields };
}

function batchState(value) {
  return text(first(value, "state", "session_state", "sessionState", "status")).toLowerCase();
}

function batchClock(now) {
  const value = typeof now === "function" ? now() : now;
  const milliseconds = value instanceof Date ? value.getTime() : Number(value);
  if (!Number.isFinite(milliseconds)) throw new OperatorRuntimeError("batch clock is invalid", "invalid_batch_clock");
  return milliseconds;
}

function validateBatchObservation(observation, {
  sessionId,
  now = Date.now,
  maxObservationAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
  expectedTeams = 14,
} = {}) {
  if (!observation || typeof observation !== "object") return { ok: false, reason: "batch observation is missing" };
  const observedSession = text(first(observation, "session_id", "sessionId"));
  if (!sessionId || observedSession !== sessionId) return { ok: false, reason: "batch observation session does not match requested session" };
  const age = observedAgeMs(observation, batchClock(now));
  if (!Number.isFinite(age)) return { ok: false, reason: "batch observation timestamp is required and must be parseable" };
  if (age < 0 || age > maxObservationAgeMs) return { ok: false, reason: `batch observation is ${Math.round(age)}ms old` };
  if (observation.complete !== true) return { ok: false, reason: "batch observation is incomplete: completeness is unknown or false" };
  const state = batchState(observation);
  if (state === "paused") return { ok: false, reason: "operator session is paused" };
  if (state === "error") return { ok: false, reason: "operator session reported an error" };
  if (observation.autopick !== false) return { ok: false, reason: "Autopick is not explicitly off" };
  if (observation.history_complete !== true) return { ok: false, reason: "batch history is incomplete: completeness is unknown or false" };
  const order = first(observation, "pick_order", "pickOrder");
  if (!Array.isArray(order) || order.length !== expectedTeams || new Set(order.map(text)).size !== expectedTeams) {
    return { ok: false, reason: `batch pick order must contain ${expectedTeams} unique teams` };
  }
  const phase = text(first(observation, "phase")).toLowerCase();
  const currentPick = finiteNumber(first(observation, "current_pick", "currentPick", "pick"));
  if (phase === "complete" || currentPick === null) return { ok: true, terminal: true, age, state };
  if (!Number.isInteger(currentPick) || currentPick < 1 || currentPick > 140) return { ok: false, reason: "batch current pick is missing or invalid" };
  if (!text(first(observation, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"))) return { ok: false, reason: "batch on-clock team is missing" };
  if (!Array.isArray(first(observation, "history", "pick_history", "pickHistory"))) return { ok: false, reason: "batch history rows are missing" };
  if (!Array.isArray(first(observation, "roster", "roster_players", "rosterPlayers"))) return { ok: false, reason: "batch roster rows are missing" };
  return { ok: true, terminal: false, age, state, currentPick };
}

function validateBatchFinalObservation(observation, {
  expectedPicks = 140,
  expectedRoster = 10,
  expectedTeams = 14,
  expectedOwnPicks = 10,
  ownTeam = null,
} = {}) {
  const reasons = [];
  if (!observation || typeof observation !== "object") return { ok: false, reasons: ["final observation is missing"] };
  if (text(first(observation, "phase")).toLowerCase() !== "complete") reasons.push("final phase is not complete");
  if (observation.complete !== true) reasons.push("final observation is not complete");
  if (observation.history_complete !== true) reasons.push("final history is not complete");
  const history = first(observation, "history", "pick_history", "pickHistory");
  const roster = first(observation, "roster", "roster_players", "rosterPlayers");
  const order = first(observation, "pick_order", "pickOrder");
  if (!Array.isArray(history) || history.length !== expectedPicks) reasons.push(`final history must contain ${expectedPicks} picks`);
  if (!Array.isArray(roster) || roster.length !== expectedRoster) reasons.push(`final roster must contain ${expectedRoster} players`);
  if (!Array.isArray(order) || order.length !== expectedTeams || new Set((order || []).map(text)).size !== expectedTeams) reasons.push(`final order must contain ${expectedTeams} unique teams`);
  const events = Array.isArray(history) ? history : [];
  const numbers = events.map((event) => finiteNumber(first(event, "pick_number", "pickNumber", "pick")));
  if (numbers.length === expectedPicks && numbers.some((pick, index) => pick !== index + 1)) reasons.push(`final history picks are not contiguous 1..${expectedPicks}`);
  const playerIds = events.map(rowPlayerId);
  if (playerIds.length === expectedPicks && (playerIds.some((id) => !id) || new Set(playerIds).size !== expectedPicks)) reasons.push("final history player IDs are missing or duplicated");
  const teams = new Set(events.map((event) => text(first(event, "team"))).filter(Boolean));
  if (teams.size !== expectedTeams) reasons.push(`final history must contain ${expectedTeams} teams`);
  const orderedTeams = Array.isArray(order) ? order.map(text) : [];
  if (orderedTeams.length === expectedTeams && numbers.length === expectedPicks && events.length === expectedPicks) {
    const counts = new Map(orderedTeams.map((team) => [team, 0]));
    for (let index = 0; index < events.length; index += 1) {
      const pick = index + 1;
      const round = Math.floor((pick - 1) / expectedTeams);
      const offset = (pick - 1) % expectedTeams;
      const seat = round % 2 === 0 ? offset : expectedTeams - 1 - offset;
      const expectedTeam = orderedTeams[seat];
      const actualTeam = text(first(events[index], "team"));
      if (actualTeam !== expectedTeam) {
        reasons.push(`final history team/order mismatch at pick ${pick}`);
        break;
      }
      counts.set(actualTeam, (counts.get(actualTeam) || 0) + 1);
    }
    if (Number.isInteger(expectedPicks / expectedTeams)) {
      const expectedPerTeam = expectedPicks / expectedTeams;
      if (orderedTeams.some((team) => counts.get(team) !== expectedPerTeam)) reasons.push(`final history must contain ${expectedPerTeam} picks for each team`);
    }
  }
  const rosterIds = (Array.isArray(roster) ? roster : []).map(rowPlayerId);
  if (rosterIds.length === expectedRoster && (rosterIds.some((id) => !id) || new Set(rosterIds).size !== expectedRoster)) reasons.push("final roster IDs are missing or duplicated");
  let ownHistoryIds = [];
  if (ownTeam) {
    ownHistoryIds = events.filter((event) => sameText(first(event, "team"), ownTeam)).map(rowPlayerId);
    if (ownHistoryIds.length !== expectedOwnPicks) reasons.push(`final history must contain ${expectedOwnPicks} picks for the own team`);
    if (rosterIds.length === expectedRoster && new Set(ownHistoryIds).size === expectedOwnPicks && new Set(rosterIds).size === expectedRoster) {
      const ownSet = new Set(ownHistoryIds);
      if (rosterIds.some((id) => !ownSet.has(id))) reasons.push("final roster does not match own-team history IDs");
    }
  }
  return {
    ok: reasons.length === 0,
    reasons,
    counts: {
      history: events.length,
      roster: Array.isArray(roster) ? roster.length : 0,
      teams: teams.size,
      own_picks: ownHistoryIds.length,
    },
  };
}

function normalizeConfirmedAction(action) {
  if (!action || typeof action !== "object") return null;
  const actionId = text(first(action, "action_id", "actionId"));
  const status = text(first(action, "status")).toLowerCase();
  const sessionId = text(first(action, "session_id", "sessionId"));
  const actionType = text(first(action, "action_type", "actionType"));
  const claimed = first(action, "claimed");
  const dispatched = first(action, "dispatched");
  const expectedPick = finiteNumber(first(action, "expected_pick", "expectedPick", "pick", "current_pick", "currentPick"));
  const expectedTeam = text(first(action, "expected_team", "expectedTeam", "team", "on_clock_team", "onClockTeam"));
  const playerId = rowPlayerId(action);
  if (!actionId || status !== "confirmed" || actionType !== "draft" || sessionId === "" || claimed !== true || dispatched !== true || expectedPick === null || !expectedTeam || !playerId) return null;
  return {
    action_id: actionId,
    status: "confirmed",
    claimed: true,
    dispatched: true,
    session_id: sessionId,
    action_type: actionType,
    expected_pick: expectedPick,
    expected_team: expectedTeam,
    player_id: playerId,
    player_name: text(first(action, "player_name", "playerName", "name")) || null,
    revision: finiteNumber(first(action, "revision")),
  };
}

function coordinatorStatusFields(status) {
  const session = first(status, "session", "session_status", "sessionStatus") || {};
  const observation = first(status, "observation", "latest_observation", "latestObservation") || {};
  const room = first(status, "room", "room_json", "roomJson") || first(observation, "room", "room_identity", "roomIdentity") || {};
  const rawActions = first(status, "actions", "draft_actions", "draftActions");
  const actions = Array.isArray(rawActions) ? rawActions : [];
  const unresolved = [];
  for (const key of ["pending_action", "pendingAction", "pending_draft", "pendingDraft", "pending_queue", "pendingQueue", "unresolved_actions", "unresolvedActions", "unresolved_queue_mutations", "unresolvedQueueMutations"]) {
    const value = first(status, key);
    if (Array.isArray(value) ? value.length : value) unresolved.push(value);
  }
  for (const action of actions) {
    const actionStatus = text(first(action, "status")).toLowerCase();
    if (["pending", "submitted", "uncertain"].includes(actionStatus)) unresolved.push(action);
  }
  const state = text(first(status, "state", "status")) || text(first(session, "state", "status"));
  const sessionId = text(first(status, "session_id", "sessionId")) || text(first(session, "session_id", "sessionId"));
  const observedAt = first(observation, "observed_at", "observedAt", "captured_at", "capturedAt");
  const receivedAt = first(status, "status_received_at", "statusReceivedAt", "received_at", "receivedAt", "_received_at");
  const latestRevision = finiteNumber(first(status, "latest_revision", "latestRevision"));
  return { sessionId, state, room, actions, unresolved, observedAt, receivedAt, latestRevision, observation };
}

function roomIdentity(room) {
  if (!room || typeof room !== "object") return null;
  const leagueId = text(first(room, "league_id", "leagueId"));
  const seasonId = text(first(room, "season_id", "seasonId"));
  const teamId = text(first(room, "team_id", "teamId"));
  const practice = first(room, "practice");
  if (!leagueId || !seasonId || !teamId || typeof practice !== "boolean") return null;
  return { league_id: leagueId, season_id: seasonId, team_id: teamId, practice };
}

function roomsMatch(left, right) {
  const a = roomIdentity(left);
  const b = roomIdentity(right);
  return !!a && !!b && a.league_id === b.league_id && a.season_id === b.season_id && a.team_id === b.team_id && a.practice === b.practice;
}

function actionMatchesEvidence(action, history, roster, ownTeam, sessionId) {
  const normalized = normalizeConfirmedAction(action);
  if (!normalized || normalized.session_id !== text(sessionId) || !sameText(normalized.expected_team, ownTeam)) return false;
  const historyMatch = (Array.isArray(history) ? history : []).some((event) => (
    finiteNumber(first(event, "pick_number", "pickNumber", "pick")) === normalized.expected_pick
    && sameText(first(event, "team"), normalized.expected_team)
    && rowPlayerId(event) === normalized.player_id
  ));
  const rosterMatch = (Array.isArray(roster) ? roster : []).some((row) => rowPlayerId(row) === normalized.player_id);
  return historyMatch && rosterMatch;
}

function validateCoordinatorCheckpointEvidence(coordinatorStatus, observation, {
  sessionId,
  ownTeam,
  ownPicksConfirmed,
  now = Date.now,
  maxStatusAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
} = {}) {
  const reasons = [];
  if (!coordinatorStatus || typeof coordinatorStatus !== "object") reasons.push("fresh coordinator status is required");
  const fields = coordinatorStatusFields(coordinatorStatus || {});
  if (!fields.sessionId || fields.sessionId !== text(sessionId)) reasons.push("coordinator status session does not match requested session");
  if (fields.state !== "paused") reasons.push("coordinator status must be explicitly paused at a batch boundary");
  if (fields.unresolved.length) reasons.push("coordinator status contains a pending draft or queue action");
  const observationRoom = first(observation, "room", "room_identity", "roomIdentity");
  if (!roomsMatch(fields.room, observationRoom)) reasons.push("coordinator status room does not match the observed draft room");
  const observationRevision = finiteNumber(first(observation, "revision"));
  if (fields.latestRevision === null || observationRevision === null || fields.latestRevision !== observationRevision) reasons.push("coordinator latest revision does not match the checkpoint observation");
  const statusTime = typeof fields.observedAt === "number" ? fields.observedAt : Date.parse(String(fields.observedAt || ""));
  const receivedTime = typeof fields.receivedAt === "number" ? fields.receivedAt : Date.parse(String(fields.receivedAt || ""));
  const nowValue = typeof now === "function" ? now() : now;
  const nowMs = nowValue instanceof Date ? nowValue.getTime() : Number(nowValue);
  if (!Number.isFinite(statusTime) || !Number.isFinite(receivedTime) || !Number.isFinite(nowMs)) reasons.push("coordinator status observation and receipt timestamps are required and must be parseable");
  else if (statusTime > nowMs || receivedTime > nowMs || nowMs - statusTime > maxStatusAgeMs || nowMs - receivedTime > maxStatusAgeMs) reasons.push("coordinator status observation or receipt is stale or from the future");
  const actions = fields.actions.map(normalizeConfirmedAction);
  if (actions.some((action, index) => {
    if (action) return false;
    const raw = fields.actions[index] || {};
    const status = text(first(raw, "status")).toLowerCase();
    const type = text(first(raw, "action_type", "actionType"));
    const detail = text(first(raw, "detail"));
    return status === "confirmed" && type !== "queue" && !detail.toLowerCase().startsWith("manual_stage:");
  })) reasons.push("coordinator status contains malformed confirmed direct-action evidence");
  const validActions = actions.filter(Boolean);
  if (validActions.some((action) => action.session_id !== text(sessionId))) reasons.push("coordinator status contains a confirmed action from another session");
  const ownActions = validActions.filter((action) => action.session_id === text(sessionId) && sameText(action.expected_team, ownTeam));
  const actionIds = new Set();
  const playerIds = new Set();
  const history = first(observation, "history", "pick_history", "pickHistory");
  const roster = first(observation, "roster", "roster_players", "rosterPlayers");
  for (const action of ownActions) {
    if (actionIds.has(action.action_id)) reasons.push(`duplicate confirmed coordinator action ${action.action_id}`);
    actionIds.add(action.action_id);
    if (playerIds.has(action.player_id)) reasons.push(`duplicate confirmed coordinator player ${action.player_id}`);
    playerIds.add(action.player_id);
    if (!actionMatchesEvidence(action, history, roster, ownTeam, sessionId)) reasons.push(`confirmed coordinator action ${action.action_id} does not match history and roster`);
  }
  if (ownActions.length !== ownPicksConfirmed) reasons.push(`coordinator has ${ownActions.length} confirmed own actions, expected ${ownPicksConfirmed}`);
  return { ok: reasons.length === 0, reasons, fields, confirmed_actions: ownActions };
}

function createBatchCheckpoint(observation, {
  sessionId,
  ownTeam,
  ownPicksConfirmed = 0,
  coordinatorStatus,
  checkpointId = null,
  now = Date.now,
  expectedTeams = 14,
  expectedPicks = 140,
  expectedRoster = 10,
  expectedOwnPicks = 10,
  maxObservationAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
  maxStatusAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
} = {}) {
  const validation = validateBatchObservation(observation, {
    sessionId,
    now,
    maxObservationAgeMs,
    expectedTeams,
  });
  if (!validation.ok) {
    throw new OperatorRuntimeError(`cannot checkpoint invalid observation: ${validation.reason}`, "invalid_batch_checkpoint");
  }
  if (!Number.isInteger(ownPicksConfirmed) || ownPicksConfirmed < 0 || ownPicksConfirmed > expectedOwnPicks) {
    throw new OperatorRuntimeError(`checkpoint own pick count must be an integer from 0 to ${expectedOwnPicks}`, "invalid_batch_checkpoint");
  }
  const history = first(observation, "history", "pick_history", "pickHistory");
  const roster = first(observation, "roster", "roster_players", "rosterPlayers");
  const observedOwnPicks = Array.isArray(history) && ownTeam
    ? history.filter((event) => sameText(first(event, "team"), ownTeam)).length
    : 0;
  if (observedOwnPicks < ownPicksConfirmed) {
    throw new OperatorRuntimeError("checkpoint own pick count exceeds authoritative history", "invalid_batch_checkpoint");
  }
  const coordinatorEvidence = validateCoordinatorCheckpointEvidence(coordinatorStatus, observation, {
    sessionId,
    ownTeam,
    ownPicksConfirmed,
    now,
    maxStatusAgeMs,
  });
  if (!coordinatorEvidence.ok) {
    throw new OperatorRuntimeError(`cannot checkpoint without coordinator evidence: ${coordinatorEvidence.reasons.join("; ")}`, "invalid_coordinator_checkpoint");
  }
  const observedAt = first(observation, "observed_at", "observedAt", "captured_at", "capturedAt");
  const checkpointedAt = typeof now === "function" ? now() : now;
  const checkpointMs = checkpointedAt instanceof Date ? checkpointedAt.getTime() : Number(checkpointedAt);
  if (!Number.isFinite(checkpointMs)) throw new OperatorRuntimeError("checkpoint clock is invalid", "invalid_batch_checkpoint");
  return Object.freeze({
    schema_version: 1,
    checkpoint_id: text(checkpointId) || `batch-${sessionId}-${first(observation, "revision") || "unknown"}`,
    session_id: text(sessionId),
    state: "paused",
    reason: "explicit foreground batch boundary",
    checkpointed_at: new Date(checkpointMs).toISOString(),
    observed_at: text(observedAt),
    revision: finiteNumber(first(observation, "revision")),
    current_pick: finiteNumber(first(observation, "current_pick", "currentPick", "pick")),
    on_clock_team: text(first(observation, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team")) || null,
    room: first(observation, "room", "room_identity", "roomIdentity") || null,
    own_team: text(ownTeam),
    own_picks_confirmed: ownPicksConfirmed,
    authoritative_history_own_picks: observedOwnPicks,
    confirmed_actions: coordinatorEvidence.confirmed_actions,
    history_count: Array.isArray(history) ? history.length : 0,
    roster_count: Array.isArray(roster) ? roster.length : 0,
    pending_action: null,
    terminal: validation.terminal === true || text(first(observation, "phase")).toLowerCase() === "complete",
    expected: { picks: expectedPicks, roster: expectedRoster, teams: expectedTeams, own_picks: expectedOwnPicks },
  });
}

function validateBatchCheckpoint(checkpoint, {
  sessionId,
  ownTeam,
  expectedOwnPicks = 10,
  expectedTeams = 14,
  maxAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
  now = Date.now,
  resumeCoordinatorStatus = null,
  observation = null,
} = {}) {
  const reasons = [];
  if (!checkpoint || typeof checkpoint !== "object") reasons.push("checkpoint is missing");
  if (!text(first(checkpoint, "session_id", "sessionId")) || text(first(checkpoint, "session_id", "sessionId")) !== text(sessionId)) reasons.push("checkpoint session does not match requested session");
  if (text(first(checkpoint, "own_team", "ownTeam")) !== text(ownTeam)) reasons.push("checkpoint own team does not match requested team");
  if (first(checkpoint, "state") !== "paused") reasons.push("checkpoint must be explicitly paused");
  if (first(checkpoint, "pending_action", "pendingAction")) reasons.push("checkpoint contains a pending action");
  const ownPicks = finiteNumber(first(checkpoint, "own_picks_confirmed", "ownPicksConfirmed"));
  if (!Number.isInteger(ownPicks) || ownPicks < 0 || ownPicks > expectedOwnPicks) reasons.push("checkpoint own pick count is invalid");
  const revision = finiteNumber(first(checkpoint, "revision"));
  if (revision === null || revision < 0) reasons.push("checkpoint revision is invalid");
  const order = first(checkpoint, "expected", "expectations");
  if (order && finiteNumber(first(order, "teams")) !== expectedTeams) reasons.push("checkpoint team expectation does not match requested room");
  const storedRaw = first(checkpoint, "confirmed_actions", "confirmedActions") || [];
  const storedActions = Array.isArray(storedRaw) ? storedRaw.map(normalizeConfirmedAction) : [];
  if (storedActions.some((action) => !action)) reasons.push("checkpoint contains malformed confirmed action evidence");
  if (storedActions.some((action) => action && (action.session_id !== text(sessionId) || action.action_type !== "draft"))) reasons.push("checkpoint confirmed action identity is invalid");
  if (storedActions.length !== ownPicks) reasons.push("checkpoint confirmed action evidence count does not match own pick count");
  if (new Set(storedActions.filter(Boolean).map((action) => action.action_id)).size !== storedActions.length) reasons.push("checkpoint confirmed action IDs are not unique");
  if (new Set(storedActions.filter(Boolean).map((action) => action.player_id)).size !== storedActions.length) reasons.push("checkpoint confirmed player IDs are not unique");
  const observedAt = first(checkpoint, "checkpointed_at", "checkpointedAt");
  const parsed = typeof observedAt === "number" ? observedAt : Date.parse(String(observedAt || ""));
  const nowValue = typeof now === "function" ? now() : now;
  const nowMs = nowValue instanceof Date ? nowValue.getTime() : Number(nowValue);
  if (resumeCoordinatorStatus) {
    const resumeFields = coordinatorStatusFields(resumeCoordinatorStatus);
    if (!resumeFields.sessionId || resumeFields.sessionId !== text(sessionId)) reasons.push("resume coordinator status session does not match requested session");
    if (resumeFields.state !== "running") reasons.push("resume coordinator status must be explicitly running after Resume");
    if (resumeFields.unresolved.length) reasons.push("resume coordinator status contains a pending draft or queue action");
    if (!roomsMatch(resumeFields.room, first(observation, "room", "room_identity", "roomIdentity") || first(checkpoint, "room", "room_identity", "roomIdentity"))) reasons.push("resume coordinator status room does not match the draft room");
    const checkpointRevision = finiteNumber(first(checkpoint, "revision"));
    if (resumeFields.latestRevision === null || checkpointRevision === null || resumeFields.latestRevision < checkpointRevision) reasons.push("resume coordinator revision regressed below the checkpoint");
    const resumeTime = typeof resumeFields.observedAt === "number" ? resumeFields.observedAt : Date.parse(String(resumeFields.observedAt || ""));
    const resumeReceived = typeof resumeFields.receivedAt === "number" ? resumeFields.receivedAt : Date.parse(String(resumeFields.receivedAt || ""));
    if (!Number.isFinite(resumeTime) || !Number.isFinite(resumeReceived) || !Number.isFinite(nowMs)) reasons.push("resume coordinator status observation and receipt timestamps are required and must be parseable");
    else if (resumeTime > nowMs || resumeReceived > nowMs || (Number.isFinite(maxAgeMs) && (nowMs - resumeTime > maxAgeMs || nowMs - resumeReceived > maxAgeMs))) reasons.push("resume coordinator status observation or receipt is stale or from the future");
    const resumeActions = resumeFields.actions.map(normalizeConfirmedAction).filter(Boolean);
    for (const stored of storedActions.filter(Boolean)) {
      const current = resumeActions.find((action) => action.action_id === stored.action_id);
      if (!current || current.status !== "confirmed" || current.expected_pick !== stored.expected_pick || !sameText(current.expected_team, stored.expected_team) || current.player_id !== stored.player_id) {
        reasons.push(`resume coordinator status does not preserve confirmed action ${stored.action_id}`);
      }
    }
    if (observation) {
      const history = first(observation, "history", "pick_history", "pickHistory");
      const roster = first(observation, "roster", "roster_players", "rosterPlayers");
      for (const stored of storedActions.filter(Boolean)) {
        if (!actionMatchesEvidence(stored, history, roster, ownTeam, sessionId)) reasons.push(`resumed observation no longer matches confirmed action ${stored.action_id}`);
      }
    }
  }
  if (!Number.isFinite(parsed) || !Number.isFinite(nowMs)) reasons.push("checkpoint timestamp is invalid");
  else if (parsed > nowMs || (Number.isFinite(maxAgeMs) && nowMs - parsed > maxAgeMs)) reasons.push("checkpoint is stale or from the future");
  return { ok: reasons.length === 0, reasons, own_picks_confirmed: ownPicks, revision };
}

function recovery(outcome) {
  if (outcome === OUTCOMES.NOT_ATTEMPTED) {
    return {
      kind: OUTCOMES.NOT_ATTEMPTED,
      action: "Do not click or reuse this command. Inspect the journal/status and capture a new fresh frame before preparing again.",
    };
  }
  return {
    kind: OUTCOMES.UNCERTAIN,
    action: "Do not retry or prepare another command. Inspect the journal, capture fresh Pick History and roster evidence, reconcile, then explicitly resume if safe.",
  };
}

function notAttempted(reason, details = {}) {
  return {
    status: OUTCOMES.NOT_ATTEMPTED,
    outcome: OUTCOMES.NOT_ATTEMPTED,
    clicked: false,
    acked: false,
    reconciled: false,
    reason,
    recovery: recovery(OUTCOMES.NOT_ATTEMPTED),
    ...details,
  };
}

function uncertain(reason, details = {}) {
  return {
    status: OUTCOMES.UNCERTAIN,
    outcome: OUTCOMES.UNCERTAIN,
    clicked: true,
    acked: false,
    reconciled: false,
    reason,
    recovery: recovery(OUTCOMES.UNCERTAIN),
    ...details,
  };
}

function confirmed(details = {}) {
  return {
    status: OUTCOMES.CONFIRMED,
    outcome: OUTCOMES.CONFIRMED,
    clicked: true,
    acked: true,
    reconciled: true,
    recovery: null,
    ...details,
  };
}

function isErrorResponse(response) {
  return !response || response.status === "error" || response.error;
}

function escapeAttribute(value) {
  return text(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

async function clickExactDraftRow(draftTab, command) {
  if (!draftTab || !draftTab.playwright || typeof draftTab.playwright.locator !== "function") {
    throw new OperatorRuntimeError("an active draft tab handle is required for the visible click", "missing_draft_tab");
  }
  const playerId = commandPlayerId(command);
  if (!playerId) {
    throw new OperatorRuntimeError("the command has no explicit player id", "missing_player_id");
  }
  // ESPN renders the actionable control itself as button[data-player-id].
  // Selecting that exact element avoids assuming the attribute belongs to an
  // ancestor row or clicking a similarly labelled nested control.
  const button = draftTab.playwright.locator(`button[data-player-id="${escapeAttribute(playerId)}"]`);
  if (!button || typeof button.click !== "function") {
    throw new OperatorRuntimeError("the exact player row has no Draft button", "missing_draft_button");
  }
  await button.click();
}

function createPanelOperator(panelTab, {
  now = Date.now,
  requestPrefix = "fbl",
  pollMs = DEFAULT_POLL_MS,
  maxPolls = DEFAULT_MAX_POLLS,
  random = Math.random,
} = {}) {
  if (!panelTab || !panelTab.playwright) throw new OperatorRuntimeError("an active operator panel tab is required", "missing_panel_tab");
  let requestSequence = 0;
  return async function requestOperator(payload) {
    requestSequence += 1;
    const requestId = `${requestPrefix}-${now()}-${requestSequence}-${random().toString(16).slice(2)}`;
    const body = { ...payload, request_id: requestId };
    const playwright = panelTab.playwright;
    const payloadInput = playwright.locator("#operator-payload");
    const resultOutput = playwright.locator("#operator-result");
    const executeButton = playwright.getByRole("button", { name: "Execute operator command", exact: true });
    await payloadInput.fill(JSON.stringify(body));
    await executeButton.click();
    for (let attempt = 0; attempt < maxPolls; attempt += 1) {
      let result;
      try {
        result = JSON.parse(await resultOutput.innerText());
      } catch (_error) {
        result = null;
      }
      if (result && result.request_id === requestId && result.status !== "pending") {
        const receiptValue = now();
        const receiptMs = receiptValue instanceof Date ? receiptValue.getTime() : Number(receiptValue);
        if (Number.isFinite(receiptMs) && typeof result === "object") {
          return { ...result, status_received_at: new Date(receiptMs).toISOString() };
        }
        return result;
      }
      if (attempt + 1 < maxPolls) await sleep(pollMs);
    }
    throw new OperatorRuntimeError("operator result remained pending; inspect the journal before retrying", "operator_result_timeout");
  };
}

/**
 * Post-click confirmation adapter. After every dispatched click: show Pick
 * History, poll captureHistory until it is complete and contains the expected
 * pick, show Players, then capture one frame from that exact history snapshot.
 * It never reads the fast frame or retained history, never clicks, and throws
 * (leaving the action uncertain) once the observation budget is spent.
 */
function createPostClickConfirmation({
  observer,
  showPickHistory,
  showPlayers,
  now = Date.now,
  sleepFn = sleep,
  budgetMs = DEFAULT_MAX_COMMAND_AGE_MS,
  pollDelaysMs = DEFAULT_POST_CLICK_POLL_DELAYS_MS,
} = {}) {
  if (!observer || typeof observer.captureHistory !== "function" || typeof observer.captureFrame !== "function") {
    throw new OperatorRuntimeError("post-click confirmation requires an observer with captureHistory and captureFrame", "invalid_configuration");
  }
  if (typeof showPickHistory !== "function" || typeof showPlayers !== "function") {
    throw new OperatorRuntimeError("post-click confirmation requires Pick History and Players view switchers", "invalid_configuration");
  }
  const delays = Array.isArray(pollDelaysMs) && pollDelaysMs.length ? pollDelaysMs : DEFAULT_POST_CLICK_POLL_DELAYS_MS;

  return async function confirmAfterClick({ command } = {}) {
    const expectedPick = commandExpectedPick(command);
    if (expectedPick === null) throw new OperatorRuntimeError("post-click confirmation needs the command's expected pick", "post_click_missing_pick");
    const deadline = now() + budgetMs;
    const exhausted = (polls) => new OperatorRuntimeError(`post-click confirmation budget of ${budgetMs}ms exhausted after ${polls} history polls`, "post_click_budget_exhausted");
    let polls = 0;
    let expectedCurrentPick = expectedPick + 1;

    for (;;) {
      await showPickHistory();
      let snapshot = null;
      while (!snapshot) {
        if (now() >= deadline) throw exhausted(polls);
        polls += 1;
        const candidate = await observer.captureHistory({ expectedCurrentPick });
        const events = historyEvents(candidate);
        const picks = events.map((event) => finiteNumber(first(event, "pick_number", "pickNumber", "pick"))).filter((pick) => pick !== null);
        if (candidate && candidate.history_complete === true && picks.includes(expectedPick)) {
          snapshot = candidate;
          break;
        }
        // Another team may already have picked; completeness is judged against
        // the furthest pick visible, never below the one after ours.
        expectedCurrentPick = Math.max(expectedPick + 1, ...picks.map((pick) => pick + 1));
        const delay = delays[Math.min(polls - 1, delays.length - 1)];
        if (deadline - now() <= delay) throw exhausted(polls);
        await sleepFn(delay);
      }
      await showPlayers();
      const frame = await observer.captureFrame({ historySnapshot: snapshot });
      if (now() > deadline) throw exhausted(polls);
      const rejected = frame && Array.isArray(frame.warnings) && frame.warnings.find((warning) => POST_CLICK_FORBIDDEN_WARNING.test(String(warning)));
      if (frame && frame.complete === true && frame.history_complete === true && !rejected) return frame;
      // The history moved on between snapshot and Players read; poll again
      // while budget remains. Still no click is ever issued here.
      expectedCurrentPick = Math.max(expectedCurrentPick, ...historyEvents(frame).map((event) => (finiteNumber(first(event, "pick_number", "pickNumber", "pick")) || 0) + 1));
      if (deadline - now() <= delays[0]) throw exhausted(polls);
    }
  };
}

function createOperatorRuntime({
  panelTab,
  draftTab,
  observeCurrent,
  observeAfter,
  postClickConfirmation,
  requestOperator,
  clickPlayer,
  now = Date.now,
  maxCommandAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
  maxObservationAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
  minSecondsRemaining = DEFAULT_MIN_SECONDS_REMAINING,
} = {}) {
  const request = requestOperator || createPanelOperator(panelTab, { now });
  const currentObserver = observeCurrent;
  // Fail closed: a runtime that can dispatch a click (a clickPlayer callback or
  // a draftTab for the default click) must confirm through the history-then-
  // Players adapter. observeAfter remains only for non-dispatching use.
  if ((typeof clickPlayer === "function" || draftTab) && !postClickConfirmation) {
    throw new OperatorRuntimeError("a dispatch-capable runtime requires postClickConfirmation {observer, showPickHistory, showPlayers}", "invalid_configuration");
  }
  const afterObserver = postClickConfirmation
    ? createPostClickConfirmation({ now, budgetMs: maxObservationAgeMs, ...postClickConfirmation })
    : observeAfter || observeCurrent;
  const usesDefaultClick = typeof clickPlayer !== "function";
  const click = clickPlayer || ((command) => clickExactDraftRow(draftTab, command));

  function freshAckObservation(observation, sessionId) {
    if (!observation || typeof observation !== "object") return null;
    const observedSession = text(first(observation, "session_id", "sessionId"));
    if (!observedSession || observedSession !== sessionId) return null;
    const age = observedAgeMs(observation, now());
    if (!Number.isFinite(age) || age < 0 || age > maxObservationAgeMs) return null;
    return observation;
  }

  async function bestEffortUncertain(sessionId, actionId, detail, observation = null) {
    if (!actionId) return null;
    try {
      const payload = { operation: "ack", session_id: sessionId, action_id: actionId, status: "uncertain", detail };
      const fresh = freshAckObservation(observation, sessionId);
      if (fresh) payload.observation = fresh;
      return await request(payload);
    } catch (_error) {
      return null;
    }
  }

  // A prepared command has consumed its one-time final guard, so the
  // coordinator records it as dispatched.  When the runtime refuses before the
  // visible click, report that explicitly as a failed, unclicked attempt;
  // otherwise the action stays pending and blocks the next preparation.
  async function releaseUnclicked(sessionId, command, reason, details = {}) {
    const actionId = commandActionId(command);
    let releaseAck = null;
    if (actionId) {
      try {
        releaseAck = await request({ operation: "ack", session_id: sessionId, action_id: actionId, status: "failed", detail: `not clicked: ${reason}` });
      } catch (error) {
        releaseAck = { error: String((error && error.message) || error) };
      }
    }
    return notAttempted(reason, { ...details, releaseAck, released: Boolean(releaseAck) && !isErrorResponse(releaseAck) });
  }

  async function runTurn({
    sessionId,
    observation,
    expectedPick = null,
    expectedTeam = null,
    preparePayload = {},
    observationAlreadyPublished = false,
    observe = currentObserver,
    observeConfirmation = afterObserver,
    clickVisible = click,
  } = {}) {
    if (!sessionId) return notAttempted("session_id is required");
    let initial = observation;
    if (!initial) {
      if (typeof observe !== "function") return notAttempted("a fresh prepare observation callback is required");
      try {
        // With no batch frame supplied, this is the pre-prepare dispatch
        // frame. The existing prepare request ingests it before any claim or
        // final guard; no separate publish round trip is needed.
        initial = await observe({ stage: "before-prepare", sessionId, draftTab });
      } catch (error) {
        return notAttempted(`fresh pre-prepare observation failed: ${error.message || error}`);
      }
    }
    const initialSession = text(first(initial, "session_id", "sessionId"));
    if (!initialSession || initialSession !== sessionId) {
      return notAttempted("prepare observation session does not match requested session");
    }
    const initialAge = observedAgeMs(initial, now());
    if (!Number.isFinite(initialAge)) {
      return notAttempted("prepare observation timestamp is required and must be parseable");
    }
    if (initialAge < 0 || initialAge > maxObservationAgeMs) {
      return notAttempted(`prepare observation is ${Math.round(initialAge)}ms old`);
    }
    const dispatchPick = finiteNumber(first(initial, "current_pick", "currentPick", "pick"));
    if (dispatchPick === null) return notAttempted("pre-prepare observation current pick is missing or invalid");
    if (expectedPick !== null && dispatchPick !== finiteNumber(expectedPick)) return notAttempted("pre-prepare observation current pick changed before prepare");
    const dispatchTeam = text(first(initial, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"));
    if (!dispatchTeam) return notAttempted("pre-prepare observation on-clock team is missing");
    if (expectedTeam !== null && !sameText(dispatchTeam, expectedTeam)) return notAttempted("pre-prepare observation on-clock team changed before prepare");
    if (first(initial, "autopick", "autoPick", "auto_pick") !== false) return notAttempted("Autopick is not explicitly off for the pre-prepare own turn");

    const prepareStartedAt = now();
    let prepared;
    try {
      const prepareRequest = {
        ...preparePayload,
        operation: "prepare",
        session_id: sessionId,
      };
      // A foreground batch publishes the same fresh frame immediately before
      // entering our turn. Re-sending that identical revision through the
      // HTTP prepare endpoint is rejected by the coordinator as a duplicate.
      // Standalone turns still publish their observation here; the batch marks
      // its already-published frame explicitly below.
      if (observationAlreadyPublished) delete prepareRequest.observation;
      else prepareRequest.observation = initial;
      prepared = await request(prepareRequest);
    } catch (error) {
      return notAttempted(`prepare request failed: ${error.message || error}`);
    }
    const prepareElapsed = now() - prepareStartedAt;
    if (prepareElapsed > maxCommandAgeMs) {
      return releaseUnclicked(sessionId, prepared && prepared.command, `prepared command aged ${Math.round(prepareElapsed)}ms before it could be checked`, { prepare: prepared });
    }
    if (isErrorResponse(prepared) || prepared.status !== "command_ready" || !prepared.command) {
      return notAttempted(prepared && prepared.error ? String(prepared.error) : "prepare returned no executable command", { prepare: prepared });
    }
    const command = prepared.command;
    if (commandSessionId(command) !== sessionId || commandActionId(command) === "" || text(first(command, "type", "command_type", "commandType")) !== "select_player" || !commandPlayerId(command) || commandExpectedPick(command) === null || !commandExpectedTeam(command)) {
      return releaseUnclicked(sessionId, command, "prepare returned an incomplete command", { command, prepare: prepared });
    }
    if (usesDefaultClick && (!draftTab || !draftTab.playwright || typeof draftTab.playwright.locator !== "function")) {
      return releaseUnclicked(sessionId, command, "an active draft tab handle is required before the click", { command, prepare: prepared });
    }

    let current;
    if (typeof observe !== "function") return releaseUnclicked(sessionId, command, "a current DOM observer is required before the click", { command, prepare: prepared });
    try {
      current = await observe({ stage: "before-click", sessionId, command, draftTab });
    } catch (error) {
      return releaseUnclicked(sessionId, command, `current DOM check failed: ${error.message || error}`, { command, prepare: prepared });
    }
    const domCheck = validateCurrentDom(command, current, { now, maxObservationAgeMs, minSecondsRemaining });
    if (!domCheck.ok) return releaseUnclicked(sessionId, command, domCheck.reason, { command, prepare: prepared, current });
    const clickAge = now() - prepareStartedAt;
    if (clickAge > maxCommandAgeMs) {
      return releaseUnclicked(sessionId, command, `prepared command aged ${Math.round(clickAge)}ms before the click`, { command, prepare: prepared, current });
    }

    let clickResult;
    try {
      // This is the only call site for the visible Draft button.  The helper
      // never retries a click, even if a later acknowledgement is ambiguous.
      clickResult = await clickVisible(command, current, { draftTab });
    } catch (error) {
      if (error instanceof OperatorRuntimeError && ["missing_draft_tab", "missing_player_id", "missing_draft_button"].includes(error.code)) {
        return releaseUnclicked(sessionId, command, `visible click was not attempted: ${error.message}`, { command, prepare: prepared, current, clickError: String(error) });
      }
      const detail = `visible click outcome is unknown: ${error.message || error}`;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, current);
      return uncertain(detail, { command, prepare: prepared, current, uncertainAck, clickError: String(error) });
    }

    let submittedAck;
    try {
      submittedAck = await request({
        operation: "ack",
        session_id: sessionId,
        action_id: command.action_id,
        status: "submitted",
        detail: "visible Draft button click dispatched",
        observation: current,
      });
    } catch (error) {
      const detail = `click completed but submitted acknowledgement failed: ${error.message || error}`;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, current);
      return uncertain(detail, { command, prepare: prepared, current, uncertainAck, clickResult });
    }
    if (isErrorResponse(submittedAck)) {
      const detail = `click completed but submitted acknowledgement returned an error: ${submittedAck.error || "unknown error"}`;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, current);
      return uncertain(detail, { command, prepare: prepared, current, submittedAck, uncertainAck, clickResult });
    }

    if (typeof observeConfirmation !== "function") {
      const detail = "submitted click has no post-click history and roster observer";
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, current);
      return uncertain(detail, { command, prepare: prepared, current, submittedAck, uncertainAck, clickResult });
    }
    let after;
    try {
      after = await observeConfirmation({ stage: "after-click", sessionId, command, draftTab });
    } catch (error) {
      const detail = `post-click history and roster capture failed: ${error.message || error}`;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, freshAckObservation(after, sessionId) || current);
      return uncertain(detail, { command, prepare: prepared, current, submittedAck, uncertainAck, clickResult });
    }
    const evidence = hasAuthoritativeEvidence(command, after, { now, maxObservationAgeMs });
    if (!evidence.ok) {
      const detail = evidence.reason;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, freshAckObservation(after, sessionId) || current);
      return uncertain(detail, { command, prepare: prepared, current, submittedAck, after, uncertainAck, clickResult });
    }

    let reconciled;
    try {
      reconciled = await request({
        operation: "reconcile",
        session_id: sessionId,
        observation: after,
      });
    } catch (error) {
      const detail = `browser evidence is present but reconcile failed: ${error.message || error}`;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, freshAckObservation(after, sessionId) || current);
      return uncertain(detail, { command, prepare: prepared, current, submittedAck, after, uncertainAck, clickResult });
    }
    const reconciledAction = Array.isArray(reconciled && reconciled.actions)
      ? reconciled.actions.find((action) => action.action_id === command.action_id && action.status === "confirmed")
      : null;
    if (isErrorResponse(reconciled) || reconciled.status !== "reconciled" || !reconciledAction) {
      const detail = `history and roster matched, but durable reconcile did not confirm action ${command.action_id}`;
      const uncertainAck = await bestEffortUncertain(sessionId, command.action_id, detail, freshAckObservation(after, sessionId) || current);
      return uncertain(detail, { command, prepare: prepared, current, submittedAck, after, reconciled, uncertainAck, clickResult });
    }
    return confirmed({
      command,
      prepare: prepared,
      current,
      submittedAck,
      after,
      reconciled,
      clickResult,
      confirmed_action: {
        ...command,
        ...(reconciledAction || {}),
        action_type: command.action_type || "draft",
        status: "confirmed",
      },
    });
  }

  const runtime = {
    requestOperator: request,
    runTurn,
    // Descriptive aliases keep the CUA REPL call site readable without
    // introducing a second implementation or a second click path.
    executeTurn: runTurn,
    dispatch: runTurn,
  };
  const foregroundBatch = createForegroundBatchRunner({ runtime });
  runtime.runForegroundBatch = foregroundBatch.run;
  runtime.executeForegroundBatch = foregroundBatch.run;
  return Object.freeze(runtime);
}

function createForegroundBatchRunner({ runtime } = {}) {
  if (!runtime || typeof runtime.runTurn !== "function") {
    throw new OperatorRuntimeError("a runtime with runTurn is required for the foreground batch", "missing_runtime");
  }
  const requestOperator = typeof runtime.requestOperator === "function" ? runtime.requestOperator : null;

  async function run(options = {}) {
    const {
      sessionId,
      observe,
      observeFinal = observe,
      publishObservation,
      isOwnTurn,
      ownTeam,
      preparePayload = {},
      turnOptions,
      onProgress,
      now = Date.now,
      maxDurationMs = DEFAULT_MAX_BATCH_DURATION_MS,
      maxObservationAgeMs = DEFAULT_MAX_COMMAND_AGE_MS,
      pollIntervalMs = DEFAULT_BATCH_POLL_MS,
      maxCycles = DEFAULT_MAX_BATCH_CYCLES,
      maxNoProgressMs = DEFAULT_MAX_BATCH_NO_PROGRESS_MS,
      maxUnchangedStates = null,
      expectedPicks = 140,
      expectedRoster = 10,
      expectedTeams = 14,
      expectedOwnPicks = 10,
      initialCheckpoint = null,
      initialOwnPicksConfirmed = 0,
      resumeCoordinatorStatus = null,
      checkpointMaxAgeMs = null,
      finalCoordinatorStatus = null,
      readFinalCoordinatorStatus = null,
    } = options;
    if (!sessionId) return { status: "stopped", outcome: "stopped", reason: "session_id is required", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    if (typeof observe !== "function") return { status: "stopped", outcome: "stopped", reason: "a foreground observation callback is required", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    if (typeof maxDurationMs !== "number" || !Number.isFinite(maxDurationMs) || maxDurationMs <= 0) return { status: "stopped", outcome: "stopped", reason: "maxDurationMs must be a positive finite number", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    if (typeof maxCycles !== "number" || !Number.isInteger(maxCycles) || maxCycles <= 0) return { status: "stopped", outcome: "stopped", reason: "maxCycles must be a positive integer", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    if (typeof maxNoProgressMs !== "number" || !Number.isFinite(maxNoProgressMs) || maxNoProgressMs <= 0) return { status: "stopped", outcome: "stopped", reason: "maxNoProgressMs must be a positive finite number", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    if (maxUnchangedStates !== null && (!Number.isInteger(maxUnchangedStates) || maxUnchangedStates < 0)) return { status: "stopped", outcome: "stopped", reason: "maxUnchangedStates must be null or a non-negative integer", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    if (!text(ownTeam)) return { status: "stopped", outcome: "stopped", reason: "ownTeam is required for full-batch identity validation", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    let checkpointOwnPicks = initialOwnPicksConfirmed;
    if (!Number.isInteger(checkpointOwnPicks) || checkpointOwnPicks < 0 || checkpointOwnPicks > expectedOwnPicks) {
      return { status: "stopped", outcome: "stopped", reason: "initialOwnPicksConfirmed must be an integer within the expected own-pick count", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    }
    if (!initialCheckpoint && checkpointOwnPicks !== 0) {
      return { status: "stopped", outcome: "stopped", reason: "a positive confirmed-pick seed requires a durable checkpoint", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    }
    if (initialCheckpoint && !resumeCoordinatorStatus) {
      return { status: "stopped", outcome: "stopped", reason: "a resumed batch requires fresh coordinator status evidence", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };
    }
    if (initialCheckpoint) {
      const checkpointValidation = validateBatchCheckpoint(initialCheckpoint, {
        sessionId,
        ownTeam,
        expectedOwnPicks,
        expectedTeams,
        maxAgeMs: checkpointMaxAgeMs,
        now,
        resumeCoordinatorStatus,
      });
      if (!checkpointValidation.ok) {
        return { status: "stopped", outcome: "stopped", reason: `initial batch checkpoint is invalid: ${checkpointValidation.reasons.join("; ")}`, stop_reason: "invalid_configuration", own_picks_confirmed: 0, checkpoint_validation: checkpointValidation };
      }
      if (checkpointOwnPicks !== 0 && checkpointOwnPicks !== checkpointValidation.own_picks_confirmed) {
        return { status: "stopped", outcome: "stopped", reason: "initial own-pick count does not match the durable checkpoint", stop_reason: "invalid_configuration", own_picks_confirmed: 0, checkpoint_validation: checkpointValidation };
      }
      checkpointOwnPicks = checkpointValidation.own_picks_confirmed;
    }
    const publish = publishObservation || (requestOperator
      ? (observation) => requestOperator({ operation: "publish", session_id: sessionId, observation })
      : null);
    if (typeof publish !== "function") return { status: "stopped", outcome: "stopped", reason: "a publishObservation callback or runtime requestOperator is required", stop_reason: "invalid_configuration", own_picks_confirmed: 0 };

    const startedAt = batchClock(now);
    const deadline = startedAt + maxDurationMs;
    let cycles = 0;
    let ownPicksConfirmed = checkpointOwnPicks;
    let lastPick = null;
    let lastTeam = null;
    let unchangedStates = 0;
    let noProgressStartedAt = startedAt;
    let lastConfirmedPick = null;
    let lastObservation = null;
    let lastPublishedRevision = null;
    let confirmedActions = initialCheckpoint
      ? (Array.isArray(first(initialCheckpoint, "confirmed_actions", "confirmedActions")) ? first(initialCheckpoint, "confirmed_actions", "confirmedActions") : []).map((action) => normalizeConfirmedAction(action)).filter(Boolean)
      : [];

    async function progress(event, detail = {}) {
      if (typeof onProgress === "function") await onProgress({ event, session_id: sessionId, own_picks_confirmed: ownPicksConfirmed, cycles, ...detail });
    }

    function stopped(reason, detail = {}) {
      return {
        status: "stopped",
        outcome: "stopped",
        completed: false,
        reason,
        stop_reason: reason,
        own_picks_confirmed: ownPicksConfirmed,
        confirmed_actions: confirmedActions,
        cycles,
        elapsed_ms: Math.max(0, batchClock(now) - startedAt),
        last_observation: lastObservation,
        ...detail,
      };
    }

    async function finishIfValid(observation, source) {
      const validation = validateBatchFinalObservation(observation, {
        expectedPicks,
        expectedRoster,
        expectedTeams,
        expectedOwnPicks,
        ownTeam,
      });
      validation.counts.confirmed_own_turns = ownPicksConfirmed;
      if (ownPicksConfirmed !== expectedOwnPicks) {
        validation.reasons.push(`foreground batch confirmed ${ownPicksConfirmed} own turns; expected ${expectedOwnPicks}`);
        validation.ok = false;
      }
      let terminalPublished = null;
      const finalRevision = finiteNumber(first(observation, "revision"));
      // A final frame produced immediately after the last visible pick has a
      // new revision and must reach the coordinator before the final status
      // read. A terminal frame observed on the next poll may repeat the
      // revision already published by the normal loop, so publish it only
      // once. This keeps the final status bound to the terminal evidence
      // without triggering the coordinator's duplicate-revision guard.
      if (validation.ok && (finalRevision === null || lastPublishedRevision !== finalRevision)) {
        try {
          terminalPublished = await publish(observation, {
            sessionId,
            cycle: cycles,
            draftTab: options.draftTab,
            terminal: true,
          });
        } catch (error) {
          validation.reasons.push(`terminal observation publish failed: ${error.message || error}`);
          validation.ok = false;
        }
        const terminalState = batchState(terminalPublished);
        if (terminalPublished && (terminalPublished.error || terminalState === "error" || terminalState === "paused")) {
          validation.reasons.push("terminal observation publish reported an error or paused state");
          validation.ok = false;
        } else if (validation.ok && finalRevision !== null) {
          lastPublishedRevision = finalRevision;
        }
      }
      const finalStatus = typeof readFinalCoordinatorStatus === "function"
        ? await readFinalCoordinatorStatus({ sessionId, observation, ownPicksConfirmed, confirmedActions, cycles })
        : finalCoordinatorStatus;
      const coordinatorValidation = validateFinalCoordinatorEvidence(finalStatus, observation, confirmedActions, {
        sessionId,
        ownTeam,
        now,
        maxStatusAgeMs: maxObservationAgeMs,
      });
      validation.coordinator = coordinatorValidation;
      validation.terminal_publish = terminalPublished;
      if (!coordinatorValidation.ok) {
        validation.reasons.push(...coordinatorValidation.reasons);
        validation.ok = false;
      }
      await progress("final-check", { source, observation, validation });
      if (!validation.ok) return null;
      return {
        status: "completed",
        outcome: "completed",
        completed: true,
        own_picks_confirmed: ownPicksConfirmed,
        confirmed_actions: confirmedActions,
        cycles,
        elapsed_ms: Math.max(0, batchClock(now) - startedAt),
        final_observation: observation,
        final_validation: validation,
        final_coordinator_status: finalStatus,
      };
    }

    try {
      await progress("started", { deadline_ms: deadline, max_duration_ms: maxDurationMs });
      while (cycles < maxCycles) {
        if (batchClock(now) > deadline) return stopped("max_duration_exceeded");
        cycles += 1;
        let observation;
        try {
          observation = await observe({ stage: "batch-state", sessionId, draftTab: options.draftTab, cycle: cycles });
        } catch (error) {
          return stopped("foreground observation failed", { error: String(error && error.message ? error.message : error) });
        }
        lastObservation = observation;
        const checked = validateBatchObservation(observation, { sessionId, now, maxObservationAgeMs, expectedTeams });
        if (!checked.ok) return stopped(checked.reason, { observation, validation: checked });
        if (initialCheckpoint && cycles === 1) {
          const resumedValidation = validateBatchCheckpoint(initialCheckpoint, {
            sessionId,
            ownTeam,
            expectedOwnPicks,
            expectedTeams,
            maxAgeMs: checkpointMaxAgeMs,
            now,
            resumeCoordinatorStatus,
            observation,
          });
          if (!resumedValidation.ok) return stopped(`resumed checkpoint validation failed: ${resumedValidation.reasons.join("; ")}`, { observation, checkpoint_validation: resumedValidation });
          const history = first(observation, "history", "pick_history", "pickHistory");
          const observedOwnPicks = Array.isArray(history)
            ? history.filter((event) => sameText(first(event, "team"), ownTeam)).length
            : 0;
          if (observedOwnPicks < ownPicksConfirmed) return stopped("fresh observation regressed below durable own-pick checkpoint", { observation, observed_own_picks: observedOwnPicks });
          const revision = finiteNumber(first(observation, "revision"));
          const checkpointRevision = finiteNumber(first(initialCheckpoint, "revision"));
          if (checkpointRevision !== null && revision !== null && revision < checkpointRevision) return stopped("fresh observation regressed below durable checkpoint", { observation, checkpoint_revision: checkpointRevision });
        }
        await progress("observed", { observation, validation: checked });
        if (checked.terminal) {
          let finalObservation = observation;
          if (observeFinal !== observe) {
            try {
              finalObservation = await observeFinal({ stage: "batch-final", sessionId, draftTab: options.draftTab, cycle: cycles, observation });
            } catch (error) {
              return stopped("foreground final observation failed", { observation, error: String(error && error.message ? error.message : error) });
            }
          }
          const completed = await finishIfValid(finalObservation, "observation");
          if (completed) return completed;
          return stopped("terminal observation failed final validation", { observation: finalObservation, validation: validateBatchFinalObservation(finalObservation, { expectedPicks, expectedRoster, expectedTeams, expectedOwnPicks, ownTeam }) });
        }

        const currentPick = checked.currentPick;
        const currentTeam = text(first(observation, "on_clock_team", "onClockTeam", "on_clock", "onClock", "team"));
        const unchanged = currentPick === lastPick && sameText(currentTeam, lastTeam);
        if (unchanged) {
          unchangedStates += 1;
          if (maxUnchangedStates !== null && unchangedStates > maxUnchangedStates) return stopped("draft pick did not advance within the configured state-count limit", { observation });
          if (batchClock(now) - noProgressStartedAt > maxNoProgressMs) return stopped("draft pick did not advance within the no-progress time limit", { observation });
        } else {
          unchangedStates = 0;
          noProgressStartedAt = batchClock(now);
        }
        lastPick = currentPick;
        lastTeam = currentTeam;

        let own;
        try {
          own = typeof isOwnTurn === "function" ? await isOwnTurn(observation, { sessionId, cycle: cycles }) : sameText(currentTeam, ownTeam);
        } catch (error) {
          return stopped("own-turn check failed", { observation, error: String(error && error.message ? error.message : error) });
        }
        if (own !== true) {
          let published;
          try {
            // Opponent frames remain continuously published while waiting for
            // our turn. A verified own-turn frame is deferred to the existing
            // prepare ingress to avoid a redundant panel round trip.
            published = await publish(observation, { sessionId, cycle: cycles, draftTab: options.draftTab });
          } catch (error) {
            return stopped("foreground publish failed", { observation, error: String(error && error.message ? error.message : error) });
          }
          const publishedState = batchState(published);
          if (published && (published.error || publishedState === "error")) return stopped("publish reported an error", { observation, published });
          if (publishedState === "paused") return stopped("operator session is paused", { observation, published });
          const publishedRevision = finiteNumber(first(observation, "revision"));
          if (publishedRevision !== null) lastPublishedRevision = publishedRevision;
          if (publishedState === "completed") {
            let finalObservation = observation;
            if (observeFinal !== observe) {
              try {
                finalObservation = await observeFinal({ stage: "batch-final", sessionId, draftTab: options.draftTab, cycle: cycles, observation });
              } catch (error) {
                return stopped("foreground final observation failed", { observation, published, error: String(error && error.message ? error.message : error) });
              }
            }
            const completed = await finishIfValid(finalObservation, "publish");
            if (completed) return completed;
            return stopped("publish reported completion without a valid final observation", { observation: finalObservation, published });
          }
          await progress("published", { observation, published });
          await progress("opponent-turn", { observation, published });
          if (pollIntervalMs > 0) await sleep(pollIntervalMs);
          continue;
        }
        if (lastConfirmedPick === currentPick) return stopped("own pick did not advance after confirmation", { observation });
        await progress("own-turn", { observation, published: null, publication: "deferred-to-prepare" });
        let optionsForTurn = {};
        try {
          optionsForTurn = typeof turnOptions === "function" ? (await turnOptions({ observation, sessionId, cycle: cycles })) || {} : (turnOptions || {});
        } catch (error) {
          return stopped("turn-options callback failed", { observation, error: String(error && error.message ? error.message : error) });
        }
        const payload = typeof preparePayload === "function" ? await preparePayload({ observation, sessionId, cycle: cycles }) : preparePayload;
        let turnResult;
        try {
          turnResult = await runtime.runTurn({
            ...optionsForTurn,
            sessionId,
            observation,
            preparePayload: payload,
            observationAlreadyPublished: false,
            expectedPick: currentPick,
            expectedTeam: ownTeam,
          });
        } catch (error) {
          return stopped("foreground turn failed", { observation, error: String(error && error.message ? error.message : error) });
        }
        await progress("own-result", { observation, result: turnResult });
        if (!turnResult || turnResult.outcome !== OUTCOMES.CONFIRMED) {
          return stopped(turnResult && turnResult.reason ? turnResult.reason : "own turn was not confirmed", { observation, result: turnResult });
        }
        const confirmedAction = normalizeConfirmedAction(first(turnResult, "confirmed_action", "confirmedAction", "action"));
        if (!confirmedAction || confirmedAction.expected_pick !== currentPick || !sameText(confirmedAction.expected_team, ownTeam)) {
          return stopped("confirmed own turn omitted matching durable coordinator action evidence", { observation, result: turnResult });
        }
        if (confirmedActions.some((action) => action.action_id === confirmedAction.action_id || action.player_id === confirmedAction.player_id)) {
          return stopped("confirmed own turn duplicated durable coordinator action evidence", { observation, result: turnResult });
        }
        confirmedActions.push(confirmedAction);
        if (turnResult.after && typeof turnResult.after === "object") lastObservation = turnResult.after;
        ownPicksConfirmed += 1;
        lastConfirmedPick = currentPick;
        if (turnResult.after && text(first(turnResult.after, "phase")).toLowerCase() === "complete") {
          const completed = await finishIfValid(turnResult.after, "turn-confirmation");
          if (completed) return completed;
        }
        if (batchClock(now) > deadline) return stopped("max_duration_exceeded", { result: turnResult });
      }
      return stopped("max_cycles_exceeded");
    } catch (error) {
      return stopped("foreground batch failed", { error: String(error && error.message ? error.message : error) });
    }
  }

  return Object.freeze({ run, execute: run, runForegroundBatch: run });
}

async function runVisiblePick(options) {
  return createOperatorRuntime(options).runTurn(options);
}

async function runForegroundBatch(options) {
  const runner = createForegroundBatchRunner({ runtime: options && options.runtime });
  return runner.run(options);
}

module.exports = {
  DEFAULT_MAX_COMMAND_AGE_MS,
  DEFAULT_POST_CLICK_POLL_DELAYS_MS,
  DEFAULT_MIN_SECONDS_REMAINING,
  DEFAULT_MAX_BATCH_DURATION_MS,
  DEFAULT_BATCH_POLL_MS,
  DEFAULT_MAX_BATCH_CYCLES,
  DEFAULT_MAX_BATCH_NO_PROGRESS_MS,
  OUTCOMES,
  OperatorRuntimeError,
  createBatchCheckpoint,
  clickExactDraftRow,
  createPanelOperator,
  createPostClickConfirmation,
  createOperatorRuntime,
  createForegroundBatchRunner,
  executeVisiblePick: runVisiblePick,
  hasAuthoritativeEvidence,
  runForegroundBatch,
  runVisiblePick,
  validateBatchFinalObservation,
  validateFinalCoordinatorEvidence,
  validateBatchCheckpoint,
  validateBatchObservation,
  validateCurrentDom,
};
