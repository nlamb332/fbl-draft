"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DEFAULT_MAX_BATCH_DURATION_MS,
  OUTCOMES,
  clickExactDraftRow,
  createBatchCheckpoint,
  createForegroundBatchRunner,
  createOperatorRuntime,
  createPanelOperator,
  validateBatchFinalObservation,
  validateBatchCheckpoint,
} = require("./operator_runtime.js");

function observation(sessionId, overrides = {}) {
  return {
    session_id: sessionId,
    room: { league_id: `league-${sessionId}`, season_id: "2027", team_id: "15", practice: true },
    observed_at: new Date(Date.now() - 10).toISOString(),
    current_pick: 1,
    on_clock_team: "Example Team",
    seconds_remaining: 30,
    autopick: false,
    complete: true,
    history_complete: true,
    history: [],
    roster: [],
    available_players: [{ player_id: "p1", name: "Player One" }],
    ...overrides,
  };
}

// Dispatching runtimes must confirm through the history-then-Players adapter
// (issue #42). This stub drives that adapter with a canned post-click frame.
function confirmWith(frame) {
  return {
    observer: {
      captureHistory: async () => ({ history: (await frame()).history, history_complete: true }),
      captureFrame: async () => frame(),
    },
    showPickHistory: async () => {},
    showPlayers: async () => {},
  };
}

function command(sessionId = "s1") {
  return {
    type: "select_player",
    action_id: "a1",
    session_id: sessionId,
    player_id: "p1",
    player_name: "Player One",
    expected_pick: 1,
    expected_team: "Example Team",
    revision: 7,
  };
}

function afterObservation(sessionId = "s1", overrides = {}) {
  return observation(sessionId, {
    current_pick: 2,
    on_clock_team: "Team 2",
    history: [{ pick_number: 1, team: "Example Team", player_id: "p1", player_name: "Player One" }],
    roster: [{ player_id: "p1", name: "Player One" }],
    ...overrides,
  });
}

function panelResponses(sessionId = "s1") {
  return async (payload) => {
    if (payload.operation === "prepare") return { operation: "prepare", status: "command_ready", command: command(sessionId) };
    if (payload.operation === "ack" && payload.status === "submitted") return { operation: "ack", status: "submitted", action_id: "a1" };
    if (payload.operation === "reconcile") return { operation: "reconcile", status: "reconciled", actions: [{ action_id: "a1", status: "confirmed", action_type: "draft", session_id: sessionId, claimed: true, dispatched: true }] };
    if (payload.operation === "ack" && payload.status === "uncertain") return { operation: "ack", status: "uncertain", action_id: "a1" };
    throw new Error(`unexpected operation ${payload.operation}`);
  };
}

test("successful turn prepares, checks the current DOM, clicks once, acknowledges, and reconciles", async () => {
  const operations = [];
  let preparePayload;
  let submittedPayload;
  const request = async (payload) => {
    operations.push(payload.operation + (payload.status ? `:${payload.status}` : ""));
    if (payload.operation === "prepare") preparePayload = payload;
    if (payload.operation === "ack" && payload.status === "submitted") submittedPayload = payload;
    return panelResponses()(payload);
  };
  let clicks = 0;
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async () => observation("s1"),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async (picked, current) => {
      clicks += 1;
      assert.equal(picked.player_id, current.available_players[0].player_id);
    },
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1") });
  assert.equal(result.outcome, OUTCOMES.CONFIRMED);
  assert.equal(clicks, 1);
  assert.deepEqual(operations, ["prepare", "ack:submitted", "reconcile"]);
  assert.equal(preparePayload.observation.session_id, "s1");
  assert.equal(submittedPayload.observation.session_id, "s1");
  assert.equal(submittedPayload.status, "submitted");
});

test("an already-published batch frame is not ingested a second time by prepare", async () => {
  const preparePayloads = [];
  const request = async (payload) => {
    if (payload.operation === "prepare") {
      preparePayloads.push(payload);
      return { operation: "prepare", status: "command_ready", command: command("s1") };
    }
    return panelResponses()(payload);
  };
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async () => observation("s1"),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => {},
  });
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observationAlreadyPublished: true,
  });
  assert.equal(result.outcome, OUTCOMES.CONFIRMED);
  assert.equal(preparePayloads.length, 1);
  assert.equal(Object.hasOwn(preparePayloads[0], "observation"), false);
});

test("a deferred own-turn frame is ingested by prepare and submitted ack never confirms it", async () => {
  const initial = observation("s1", { revision: 11 });
  const current = observation("s1", { revision: 12 });
  const payloads = [];
  const request = async (payload) => {
    payloads.push(payload);
    if (payload.operation === "prepare") return { operation: "prepare", status: "command_ready", command: command("s1") };
    if (payload.operation === "ack" && payload.status === "submitted") return { operation: "ack", status: "submitted", action_id: "a1" };
    if (payload.operation === "reconcile") return { operation: "reconcile", status: "reconciled", actions: [] };
    if (payload.operation === "ack" && payload.status === "uncertain") return { operation: "ack", status: "uncertain", action_id: "a1" };
    throw new Error(`unexpected operation ${payload.operation}`);
  };
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async ({ stage }) => {
      assert.equal(stage, "before-click");
      return current;
    },
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => {},
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: initial, expectedPick: 1, expectedTeam: "Example Team" });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  const prepare = payloads.find((payload) => payload.operation === "prepare");
  const submitted = payloads.find((payload) => payload.operation === "ack" && payload.status === "submitted");
  assert.equal(prepare.observation, initial);
  assert.equal(submitted.observation, current);
  assert.ok(!payloads.some((payload) => payload.status === "confirmed"));
});

test("a status-poll pause race preserves submitted provenance and does not confirm from the ack frame", async () => {
  const current = observation("s1", { revision: 12 });
  const payloads = [];
  let statusPolls = 0;
  const request = async (payload) => {
    payloads.push(payload);
    if (payload.operation === "prepare") {
      // The panel may observe a stale pause after the final guard and before
      // the submitted acknowledgement; the frame remains unresolved.
      statusPolls += 1;
      return { operation: "prepare", status: "command_ready", command: command("s1") };
    }
    if (payload.operation === "ack" && payload.status === "submitted") return { operation: "ack", status: "submitted", action_id: "a1", state: "paused" };
    if (payload.operation === "reconcile") return { operation: "reconcile", status: "reconciled", actions: [] };
    if (payload.operation === "ack" && payload.status === "uncertain") return { operation: "ack", status: "uncertain", action_id: "a1" };
    throw new Error(`unexpected operation ${payload.operation}`);
  };
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async () => current,
    postClickConfirmation: confirmWith(async () => afterObservation("s1", { roster: [] })),
    clickPlayer: async () => {},
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1", { revision: 11 }) });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  assert.equal(statusPolls, 1);
  const submitted = payloads.find((payload) => payload.operation === "ack" && payload.status === "submitted");
  assert.equal(submitted.observation, current);
  assert.ok(!payloads.some((payload) => payload.status === "confirmed"));
});

test("a command whose prepare request waited past freshness is not attempted", async () => {
  let clock = 1_000_000;
  let clicks = 0;
  const request = async (payload) => {
    if (payload.operation === "prepare") {
      clock += 10_001;
      return { operation: "prepare", status: "command_ready", command: command("s1") };
    }
    throw new Error("no post-prepare request is allowed");
  };
  const runtime = createOperatorRuntime({
    now: () => clock,
    requestOperator: request,
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  const initial = observation("s1", { observed_at: new Date(clock - 10).toISOString() });
  const result = await runtime.runTurn({ sessionId: "s1", observation: initial });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /aged/);
  assert.equal(result.recovery.kind, OUTCOMES.NOT_ATTEMPTED);
  assert.equal(clicks, 0);
});

test("a changed turn, expired clock, or missing exact row is not attempted", async () => {
  let clicks = 0;
  const runtime = createOperatorRuntime({
    requestOperator: panelResponses(),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observe: async () => observation("s1", { current_pick: 2 }),
  });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /current pick/);
  assert.equal(clicks, 0);
});

test("the current DOM session must match the prepared command", async () => {
  let clicks = 0;
  const runtime = createOperatorRuntime({
    requestOperator: panelResponses(),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observe: async () => observation("other-session"),
  });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /session/);
  assert.equal(clicks, 0);
});

test("prepare and current DOM observations require parseable timestamps", async () => {
  const prepareRuntime = createOperatorRuntime({ requestOperator: panelResponses() });
  const missingPrepareTimestamp = await prepareRuntime.runTurn({
    sessionId: "s1",
    observation: observation("s1", { observed_at: undefined }),
  });
  assert.equal(missingPrepareTimestamp.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(missingPrepareTimestamp.reason, /timestamp/);

  const currentRuntime = createOperatorRuntime({
    requestOperator: panelResponses(),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { throw new Error("must not click"); },
  });
  const missingCurrentTimestamp = await currentRuntime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observe: async () => observation("s1", { observed_at: undefined }),
  });
  assert.equal(missingCurrentTimestamp.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(missingCurrentTimestamp.reason, /timestamp/);
});

test("a missing active draft tab is not attempted before reading or clicking a row", async () => {
  const runtime = createOperatorRuntime({ requestOperator: panelResponses() });
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observe: async () => observation("s1"),
  });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /draft tab/);
});

test("a command without an action id is not executable", async () => {
  const runtime = createOperatorRuntime({
    requestOperator: async (payload) => {
      if (payload.operation === "prepare") return { status: "command_ready", command: { ...command("s1"), action_id: "" } };
      throw new Error("no acknowledgement is allowed");
    },
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { throw new Error("must not click"); },
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1") });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /incomplete command/);
});

test("a slow current DOM check cannot let a command cross the click freshness window", async () => {
  let clock = 2_000_000;
  let clicks = 0;
  const runtime = createOperatorRuntime({
    now: () => clock,
    requestOperator: panelResponses(),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  const initial = observation("s1", { observed_at: new Date(clock - 10).toISOString() });
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: initial,
    observe: async () => {
      clock += 10_001;
      return observation("s1", { observed_at: new Date(clock - 10).toISOString() });
    },
  });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /before the click/);
  assert.equal(clicks, 0);
});

test("a click exception becomes uncertain and is marked uncertain for recovery", async () => {
  const operations = [];
  const request = async (payload) => {
    operations.push(payload.operation + (payload.status ? `:${payload.status}` : ""));
    return panelResponses()(payload);
  };
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async () => observation("s1"),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { throw new Error("UI timeout after click began"); },
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1") });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  assert.equal(result.clicked, true);
  assert.match(result.recovery.action, /Do not retry/);
  assert.deepEqual(operations, ["prepare", "ack:uncertain"]);
});

test("submitted click without both history and roster evidence stays uncertain", async () => {
  const operations = [];
  const request = async (payload) => {
    operations.push(payload.operation + (payload.status ? `:${payload.status}` : ""));
    return panelResponses()(payload);
  };
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async () => observation("s1"),
    postClickConfirmation: confirmWith(async () => afterObservation("s1", { roster: [] })),
    clickPlayer: async () => {},
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1") });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  assert.match(result.reason, /roster=false/);
  assert.deepEqual(operations, ["prepare", "ack:submitted", "ack:uncertain"]);
});

test("post-click confirmation also requires a fresh timestamp", async () => {
  const operations = [];
  const request = async (payload) => {
    operations.push(payload.operation + (payload.status ? `:${payload.status}` : ""));
    return panelResponses()(payload);
  };
  const runtime = createOperatorRuntime({
    requestOperator: request,
    observeCurrent: async () => observation("s1"),
    postClickConfirmation: confirmWith(async () => afterObservation("s1", { observed_at: undefined })),
    clickPlayer: async () => {},
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1") });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  assert.match(result.reason, /timestamp/);
  assert.deepEqual(operations, ["prepare", "ack:submitted", "ack:uncertain"]);
});

test("prepare errors during takeover or reconnect are explicitly not attempted", async () => {
  let clicks = 0;
  const runtime = createOperatorRuntime({
    requestOperator: async () => ({ operation: "prepare", status: "error", error: "automation is not running" }),
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  const result = await runtime.runTurn({ sessionId: "s1", observation: observation("s1") });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /automation is not running/);
  assert.equal(clicks, 0);
});

test("default clicker targets ESPN's actionable button[data-player-id] directly", async () => {
  const selectors = [];
  let clicks = 0;
  const draftTab = {
    playwright: {
      locator(selector) {
        selectors.push(selector);
        return { click: async () => { clicks += 1; } };
      },
    },
  };
  await clickExactDraftRow(draftTab, command("s1"));
  assert.deepEqual(selectors, ['button[data-player-id="p1"]']);
  assert.equal(clicks, 1);
});

test("panel adapter waits for the matching non-pending request id", async () => {
  let filled;
  let clicks = 0;
  let reads = 0;
  const output = {
    async innerText() {
      reads += 1;
      if (reads === 1) return JSON.stringify({ status: "pending", request_id: "other" });
      return JSON.stringify({ status: "ok", request_id: JSON.parse(filled).request_id, value: 7 });
    },
  };
  const panel = {
    playwright: {
      locator(selector) {
        if (selector === "#operator-payload") return { fill: async (value) => { filled = value; } };
        if (selector === "#operator-result") return output;
        throw new Error(`unexpected locator ${selector}`);
      },
      getByRole(role, options) {
        assert.equal(role, "button");
        assert.equal(options.name, "Execute operator command");
        return { click: async () => { clicks += 1; } };
      },
    },
  };
  const request = createPanelOperator(panel, { pollMs: 0, maxPolls: 3, now: () => 123 });
  const result = await request({ operation: "status", session_id: "s1" });
  assert.equal(result.value, 7);
  assert.equal(result.status_received_at, new Date(123).toISOString());
  assert.equal(clicks, 1);
  assert.equal(JSON.parse(filled).operation, "status");
});

function batchFrame(sessionId, pick, teams, ownTeam, clock, { complete = true, phase = "draft" } = {}) {
  const history = [];
  const roster = [];
  for (let number = 1; number < pick; number += 1) {
    const round = Math.floor((number - 1) / teams.length);
    const offset = (number - 1) % teams.length;
    const team = teams[round % 2 === 0 ? offset : teams.length - 1 - offset];
    const playerId = `p${number}`;
    history.push({ pick_number: number, team, player_id: playerId, player_name: `Player ${number}` });
    if (team === ownTeam) roster.push({ player_id: playerId, name: `Player ${number}` });
  }
  if (phase === "complete") {
    for (let number = pick; number <= 140; number += 1) {
      const round = Math.floor((number - 1) / teams.length);
      const offset = (number - 1) % teams.length;
      const team = teams[round % 2 === 0 ? offset : teams.length - 1 - offset];
      const playerId = `p${number}`;
      history.push({ pick_number: number, team, player_id: playerId, player_name: `Player ${number}` });
      if (team === ownTeam) roster.push({ player_id: playerId, name: `Player ${number}` });
    }
  }
  return {
    session_id: sessionId,
    room: { league_id: `league-${sessionId}`, season_id: "2027", team_id: "15", practice: true },
    observed_at: new Date(clock).toISOString(),
    revision: pick,
    phase,
    current_pick: phase === "complete" ? null : pick,
    on_clock_team: phase === "complete" ? null : teams[(Math.floor((pick - 1) / teams.length) % 2 === 0) ? (pick - 1) % teams.length : teams.length - 1 - ((pick - 1) % teams.length)],
    seconds_remaining: phase === "complete" ? null : 90,
    pick_order: teams,
    history,
    roster,
    available_players: phase === "complete" ? [] : [{ player_id: `available-${pick}`, name: "Available" }],
    autopick: false,
    complete,
    history_complete: true,
  };
}

function confirmedActionFor(frame, actionId = `action-${frame.current_pick}`) {
  const event = Array.isArray(frame.history)
    ? frame.history.find((item) => item.pick_number === frame.current_pick)
    : null;
  return {
    action_id: actionId,
    status: "confirmed",
    session_id: frame.session_id,
    action_type: "draft",
    claimed: true,
    dispatched: true,
    expected_pick: frame.current_pick,
    expected_team: frame.on_clock_team,
    player_id: event ? event.player_id : `p${frame.current_pick}`,
    player_name: event ? event.player_name : `Player ${frame.current_pick}`,
  };
}

function coordinatorStatusFor(frame, ownTeam, state = "paused") {
  const actions = (Array.isArray(frame.history) ? frame.history : [])
    .filter((event) => event.team === ownTeam)
    .map((event) => ({
      action_id: `action-${event.pick_number}`,
      status: "confirmed",
      session_id: frame.session_id,
      action_type: "draft",
      claimed: true,
      dispatched: true,
      expected_pick: event.pick_number,
      expected_team: event.team,
      player_id: event.player_id,
      player_name: event.player_name,
    }));
  return {
    session_id: frame.session_id,
    state,
    room: frame.room,
    latest_revision: frame.revision,
    observation: frame,
    status_received_at: frame.observed_at,
    actions,
    queue_mutations: [],
    unresolved_actions: actions.filter((action) => ["pending", "submitted", "uncertain"].includes(action.status)),
    unresolved_queue_mutations: [],
  };
}

test("foreground batch stops before publish on stale, incomplete, or observer-error state", async () => {
  const sessionId = "batch-stale";
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  let clock = Date.parse("2026-10-11T12:00:00.000Z");
  let publishes = 0;
  const runtime = { runTurn: async () => { throw new Error("pick must not run"); } };
  const runner = createForegroundBatchRunner({ runtime });
  const stale = batchFrame(sessionId, 1, teams, "Our Team", clock - 10_001);
  const staleResult = await runner.run({
    sessionId,
    ownTeam: "Our Team",
    observe: async () => stale,
    publishObservation: async () => { publishes += 1; },
    now: () => clock,
  });
  assert.equal(staleResult.status, "stopped");
  assert.match(staleResult.reason, /old/);
  assert.equal(publishes, 0);

  const incompleteResult = await runner.run({
    sessionId,
    ownTeam: "Our Team",
    observe: async () => batchFrame(sessionId, 1, teams, "Our Team", clock, { complete: false }),
    publishObservation: async () => { publishes += 1; },
    now: () => clock,
  });
  assert.match(incompleteResult.reason, /incomplete/);
  assert.equal(publishes, 0);

  const unknownCompleteness = batchFrame(sessionId, 1, teams, "Our Team", clock);
  delete unknownCompleteness.complete;
  const unknownCompleteResult = await runner.run({
    sessionId,
    ownTeam: "Our Team",
    observe: async () => unknownCompleteness,
    publishObservation: async () => { publishes += 1; },
    now: () => clock,
  });
  assert.match(unknownCompleteResult.reason, /completeness is unknown/);
  assert.equal(publishes, 0);

  const unknownHistory = batchFrame(sessionId, 1, teams, "Our Team", clock);
  delete unknownHistory.history_complete;
  const unknownHistoryResult = await runner.run({
    sessionId,
    ownTeam: "Our Team",
    observe: async () => unknownHistory,
    publishObservation: async () => { publishes += 1; },
    now: () => clock,
  });
  assert.match(unknownHistoryResult.reason, /history is incomplete/);
  assert.equal(publishes, 0);

  const errorResult = await runner.run({
    sessionId,
    ownTeam: "Our Team",
    observe: async () => { throw new Error("tab observer failed"); },
    publishObservation: async () => { publishes += 1; },
    now: () => clock,
  });
  assert.match(errorResult.reason, /observation failed/);
  assert.equal(publishes, 0);
  assert.equal(DEFAULT_MAX_BATCH_DURATION_MS, 240_000);
});

test("foreground batch tolerates many unchanged fresh opponent frames until the elapsed no-progress limit", async () => {
  const teams = Array.from({ length: 14 }, (_, index) => `Team ${index + 1}`);
  const sessionId = "batch-no-progress";
  let clock = Date.parse("2026-10-11T12:00:00.000Z");
  let reads = 0;
  let published = 0;
  const runner = createForegroundBatchRunner({
    runtime: { runTurn: async () => { throw new Error("no own turn expected"); } },
  });
  const result = await runner.run({
    sessionId,
    isOwnTurn: () => false,
    ownTeam: "Never Our Team",
    expectedRoster: 0,
    expectedOwnPicks: 0,
    maxNoProgressMs: 1_000,
    pollIntervalMs: 0,
    observe: async () => {
      clock += 100;
      reads += 1;
      if (reads <= 8) return batchFrame(sessionId, 1, teams, "Never Our Team", clock);
      if (reads === 9) return batchFrame(sessionId, 2, teams, "Never Our Team", clock);
      return batchFrame(sessionId, 2, teams, "Never Our Team", clock, { phase: "complete" });
    },
    publishObservation: async () => {
      published += 1;
      return { status: "running" };
    },
    readFinalCoordinatorStatus: async ({ observation }) => coordinatorStatusFor(observation, "Never Our Team", "completed"),
    now: () => clock,
  });
  assert.equal(result.status, "completed");
  assert.equal(result.own_picks_confirmed, 0);
  assert.equal(reads, 10);
  assert.equal(published, 9);
});

test("foreground batch requires explicit ownTeam even with a custom own-turn predicate", async () => {
  const runner = createForegroundBatchRunner({ runtime: { runTurn: async () => ({ outcome: OUTCOMES.CONFIRMED }) } });
  const result = await runner.run({
    sessionId: "batch-no-identity",
    isOwnTurn: () => true,
    observe: async () => ({}),
    publishObservation: async () => ({ status: "running" }),
  });
  assert.equal(result.stop_reason, "invalid_configuration");
  assert.match(result.reason, /ownTeam is required/);
});

test("final batch validation checks observed snake order and ten picks per team", () => {
  const teams = Array.from({ length: 14 }, (_, index) => `Team ${index + 1}`);
  const final = batchFrame("batch-order", 140, teams, "Never Our Team", Date.parse("2026-10-11T12:00:00.000Z"), { phase: "complete" });
  const valid = validateBatchFinalObservation(final, { expectedRoster: 0, expectedOwnPicks: 0, ownTeam: "Never Our Team" });
  assert.equal(valid.ok, true);
  final.history[0] = { ...final.history[0], team: teams[1] };
  const invalid = validateBatchFinalObservation(final, { expectedRoster: 0, expectedOwnPicks: 0, ownTeam: "Never Our Team" });
  assert.equal(invalid.ok, false);
  assert.ok(invalid.reasons.some((reason) => /team\/order mismatch|picks for each team/.test(reason)));
});

test("foreground batch halts on uncertain own turn without retry or resume", async () => {
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  let clock = Date.parse("2026-10-11T12:00:00.000Z");
  let observed = 0;
  let published = 0;
  let turns = 0;
  const runner = createForegroundBatchRunner({
    runtime: {
      runTurn: async () => {
        turns += 1;
        return { outcome: OUTCOMES.UNCERTAIN, reason: "post-click evidence is ambiguous" };
      },
    },
  });
  const result = await runner.run({
    sessionId: "batch-uncertain",
    ownTeam: "Our Team",
    observe: async () => {
      observed += 1;
      return batchFrame("batch-uncertain", 1, teams, "Our Team", clock);
    },
    publishObservation: async () => {
      published += 1;
      return { status: "running" };
    },
    readFinalCoordinatorStatus: async ({ observation }) => coordinatorStatusFor(observation, "Our Team", "completed"),
    now: () => clock,
  });
  assert.equal(result.status, "stopped");
  assert.equal(result.outcome, "stopped");
  assert.match(result.reason, /ambiguous/);
  assert.equal(observed, 1);
  // The own-turn frame is ingested by prepare; it does not need a
  // redundant publish panel request before the guarded click.
  assert.equal(published, 0);
  assert.equal(turns, 1);
});

test("foreground batch completes only after ten confirmed own picks and a valid 140-pick final frame", async () => {
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  const sessionId = "batch-complete";
  let clock = Date.parse("2026-10-11T12:00:00.000Z");
  let index = 0;
  let published = 0;
  let turns = 0;
  const runner = createForegroundBatchRunner({
    runtime: {
      runTurn: async ({ observation }) => {
        turns += 1;
        const evidenceFrame = batchFrame(sessionId, Math.min(140, observation.current_pick + 1), teams, "Our Team", clock);
        return {
          outcome: OUTCOMES.CONFIRMED,
          after: null,
          current_pick: observation.current_pick,
          confirmed_action: confirmedActionFor({ ...evidenceFrame, current_pick: observation.current_pick, on_clock_team: observation.on_clock_team }),
        };
      },
    },
  });
  const result = await runner.run({
    sessionId,
    ownTeam: "Our Team",
    maxDurationMs: 600_000,
    pollIntervalMs: 0,
    observe: async () => {
      clock += 1;
      index += 1;
      if (index > 140) return batchFrame(sessionId, 140, teams, "Our Team", clock, { phase: "complete" });
      return batchFrame(sessionId, index, teams, "Our Team", clock, { phase: "draft" });
    },
    publishObservation: async () => {
      published += 1;
      return { status: "running" };
    },
    readFinalCoordinatorStatus: async ({ observation }) => coordinatorStatusFor(observation, "Our Team", "completed"),
    now: () => clock,
  });
  assert.equal(result.status, "completed");
  assert.equal(result.outcome, "completed");
  assert.equal(result.own_picks_confirmed, 10);
  assert.equal(turns, 10);
  // Ten own-turn frames are deferred to prepare; the 130 opponent frames
  // and the terminal frame continue publishing.
  assert.equal(published, 131);
  assert.equal(result.final_validation.counts.history, 140);
  assert.equal(result.final_validation.counts.roster, 10);
  assert.equal(result.final_validation.counts.teams, 14);
  assert.equal(result.final_validation.counts.own_picks, 10);
});

test("terminal frame is published before the final coordinator status read", async () => {
  const teams = Array.from({ length: 14 }, (_, index) => `Team ${index + 1}`);
  const sessionId = "batch-terminal-publish";
  const ownTeam = "Never Our Team";
  const clock = Date.parse("2026-10-11T12:00:00.000Z");
  const terminal = batchFrame(sessionId, 140, teams, ownTeam, clock, { phase: "complete" });
  const operations = [];
  const runner = createForegroundBatchRunner({ runtime: { runTurn: async () => { throw new Error("no own turn expected"); } } });
  const result = await runner.run({
    sessionId,
    ownTeam,
    expectedRoster: 0,
    expectedOwnPicks: 0,
    observe: async () => terminal,
    publishObservation: async (observation) => {
      operations.push(["publish", observation.revision]);
      return { status: "completed" };
    },
    readFinalCoordinatorStatus: async ({ observation }) => {
      operations.push(["status", observation.revision]);
      return coordinatorStatusFor(observation, ownTeam, "completed");
    },
    now: () => clock,
  });
  assert.equal(result.status, "completed");
  assert.deepEqual(operations, [["publish", 140], ["status", 140]]);
  assert.equal(result.final_validation.terminal_publish.status, "completed");
});

test("durable foreground handoff carries confirmed history across a simulated 90-minute boundary", async () => {
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  const sessionId = "batch-endurance";
  const ownTeam = "Our Team";
  const ninetyMinutes = 90 * 60 * 1000;
  let clock = Date.parse("2026-10-11T12:00:00.000Z");
  const initialClock = clock;
  let nextPick = 1;
  let checkpoint = null;
  const checkpoints = [];
  let totalConfirmed = 0;
  let finalResult = null;
  let batches = 0;

  while (!finalResult || finalResult.status !== "completed") {
    batches += 1;
    let cursor = nextPick;
    const runner = createForegroundBatchRunner({
      runtime: {
        runTurn: async ({ observation }) => {
          const pick = observation.current_pick;
          const after = pick === 140
            ? batchFrame(sessionId, 140, teams, ownTeam, clock, { phase: "complete" })
            : batchFrame(sessionId, pick + 1, teams, ownTeam, clock, { phase: "draft" });
          return {
            outcome: OUTCOMES.CONFIRMED,
            after,
            current_pick: pick,
            confirmed_action: confirmedActionFor({ ...after, current_pick: pick, on_clock_team: observation.on_clock_team }),
          };
        },
      },
    });
    const resumeFrame = batchFrame(sessionId, nextPick, teams, ownTeam, clock);
    finalResult = await runner.run({
      sessionId,
      ownTeam,
      initialCheckpoint: checkpoint,
      resumeCoordinatorStatus: checkpoint ? coordinatorStatusFor(resumeFrame, ownTeam, "running") : null,
      maxDurationMs: ninetyMinutes,
      maxCycles: 2_000,
      pollIntervalMs: 0,
      observe: async () => {
        clock += 90_000;
        const frame = cursor <= 140
          ? batchFrame(sessionId, cursor, teams, ownTeam, clock, { phase: "draft" })
          : batchFrame(sessionId, 140, teams, ownTeam, clock, { phase: "complete" });
        cursor += 1;
        return frame;
      },
      now: () => clock,
      publishObservation: async () => ({ status: "running" }),
      readFinalCoordinatorStatus: async ({ observation }) => coordinatorStatusFor(observation, ownTeam, "completed"),
    });
    if (finalResult.status === "completed") break;
    assert.equal(finalResult.stop_reason, "max_duration_exceeded", JSON.stringify({ stop_reason: finalResult.stop_reason, own: finalResult.own_picks_confirmed, reason: finalResult.reason, final: finalResult.final_validation, observation: finalResult.last_observation && { pick: finalResult.last_observation.current_pick, history: finalResult.last_observation.history && finalResult.last_observation.history.length } }));
    assert.ok(finalResult.last_observation, "a safe boundary must retain the last fresh frame");
    checkpoint = createBatchCheckpoint(finalResult.last_observation, {
      sessionId,
      ownTeam,
      ownPicksConfirmed: finalResult.own_picks_confirmed,
      coordinatorStatus: coordinatorStatusFor(finalResult.last_observation, ownTeam),
      checkpointId: `endurance-${batches}`,
      now: () => clock,
    });
    checkpoints.push(checkpoint);
    const checkpointValidation = validateBatchCheckpoint(checkpoint, {
      sessionId,
      ownTeam,
      expectedOwnPicks: 10,
      expectedTeams: 14,
      now: () => clock,
    });
    assert.equal(checkpointValidation.ok, true);
    assert.equal(checkpoint.pending_action, null);
    assert.equal(checkpoint.state, "paused");
    const last = finalResult.last_observation;
    const lastHistory = Array.isArray(last.history) ? last.history.length : 0;
    nextPick = last.current_pick + (lastHistory >= last.current_pick ? 1 : 0);
    assert.ok(Number.isInteger(nextPick) && nextPick > 1 && nextPick < 140);
    totalConfirmed = finalResult.own_picks_confirmed;
    assert.equal(checkpoint.own_picks_confirmed, totalConfirmed);
    assert.ok(batches < 5, "the handoff must be bounded rather than an unbounded promise");
  }

  assert.equal(finalResult.status, "completed");
  assert.equal(finalResult.own_picks_confirmed, 10);
  assert.equal(finalResult.final_validation.counts.history, 140);
  assert.equal(finalResult.final_validation.counts.roster, 10);
  assert.equal(finalResult.final_validation.counts.own_picks, 10);
  assert.ok(clock - initialClock >= ninetyMinutes, "the fixture covers at least 90 simulated minutes");
  assert.ok(checkpoints.length >= 2, "continuation crossed explicit durable boundaries");
  assert.ok(checkpoints.every((item) => item.pending_action === null));
  assert.ok(checkpoints.every((item) => item.confirmed_actions.length === item.own_picks_confirmed));
});

test("batch checkpoint refuses pending queue state or a claimed count beyond history", () => {
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  const frame = batchFrame("checkpoint-guards", 30, teams, "Our Team", Date.parse("2026-10-11T12:00:00.000Z"));
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: "checkpoint-guards",
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: { ...coordinatorStatusFor(frame, "Our Team"), pending_queue: [{ player_id: "pending" }] },
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: "checkpoint-guards",
    ownTeam: "Our Team",
    ownPicksConfirmed: 4,
    coordinatorStatus: coordinatorStatusFor(frame, "Our Team"),
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_batch_checkpoint");
});

test("foreground batch rejects a bare confirmed-pick seed without a durable checkpoint", async () => {
  const runner = createForegroundBatchRunner({ runtime: { runTurn: async () => ({ outcome: OUTCOMES.CONFIRMED }) } });
  const result = await runner.run({
    sessionId: "bare-seed",
    ownTeam: "Our Team",
    initialOwnPicksConfirmed: 1,
    observe: async () => ({}),
    publishObservation: async () => ({ status: "running" }),
  });
  assert.equal(result.stop_reason, "invalid_configuration");
  assert.match(result.reason, /durable checkpoint/);
});

test("batch checkpoint never promotes native history into confirmed coordinator actions", () => {
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  const frame = batchFrame("evidence-guards", 30, teams, "Our Team", Date.parse("2026-10-11T12:00:00.000Z"));
  const nativeOnlyStatus = { session_id: frame.session_id, state: "paused", observed_at: frame.observed_at, actions: [] };
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: nativeOnlyStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");

  const wrongActionStatus = coordinatorStatusFor(frame, "Our Team");
  wrongActionStatus.actions[0] = { ...wrongActionStatus.actions[0], action_id: "wrong-room-action", player_id: "native-player" };
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: wrongActionStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");

  const crossRoomStatus = coordinatorStatusFor(frame, "Our Team");
  crossRoomStatus.session_id = "other-session";
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: crossRoomStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");

  const unresolvedStatus = coordinatorStatusFor(frame, "Our Team");
  unresolvedStatus.unresolved_actions = [{ action_id: "pending-direct", status: "submitted", action_type: "draft" }];
  unresolvedStatus.unresolved_queue_mutations = [{ action_id: "queued", status: "confirmed", action_type: "queue" }];
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: unresolvedStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");

  const unclaimedStatus = coordinatorStatusFor(frame, "Our Team");
  unclaimedStatus.actions[0] = { ...unclaimedStatus.actions[0], claimed: false };
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: unclaimedStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");

  const queueStatus = coordinatorStatusFor(frame, "Our Team");
  queueStatus.actions[0] = { ...queueStatus.actions[0], action_type: "queue" };
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: queueStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");

  const historicalStatus = coordinatorStatusFor(frame, "Our Team");
  historicalStatus.actions.push({ action_id: "old-queue", status: "confirmed", action_type: "queue", session_id: frame.session_id, player_id: "queue-player" });
  historicalStatus.actions.push({ action_id: "manual-old", status: "confirmed", action_type: "draft", session_id: frame.session_id, detail: "manual_stage: user-requested pick", player_id: "manual-player" });
  historicalStatus.queue_mutations = [{ mutation_id: "old-queue", status: "confirmed", dispatched: true }];
  const historicalCheckpoint = createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: historicalStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  });
  assert.equal(historicalCheckpoint.confirmed_actions.length, 3);

  const crossSessionStatus = coordinatorStatusFor(frame, "Our Team");
  crossSessionStatus.actions[0] = { ...crossSessionStatus.actions[0], session_id: "other-session" };
  assert.throws(() => createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: crossSessionStatus,
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  }), (error) => error.code === "invalid_coordinator_checkpoint");
});

test("resume validation preserves exact confirmed action IDs and player evidence", () => {
  const teams = ["Our Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
  const clock = Date.parse("2026-10-11T12:00:00.000Z");
  const frame = batchFrame("resume-evidence", 30, teams, "Our Team", clock);
  const status = coordinatorStatusFor(frame, "Our Team");
  const checkpoint = createBatchCheckpoint(frame, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    ownPicksConfirmed: 3,
    coordinatorStatus: status,
    now: () => clock,
  });
  const changed = { ...status, state: "running", actions: status.actions.map((action, index) => index === 0 ? { ...action, player_id: "changed-player" } : action) };
  const validation = validateBatchCheckpoint(checkpoint, {
    sessionId: frame.session_id,
    ownTeam: "Our Team",
    resumeCoordinatorStatus: changed,
    observation: frame,
    now: () => clock,
  });
  assert.equal(validation.ok, false);
  assert.ok(validation.reasons.some((reason) => /does not preserve confirmed action/.test(reason)));
});

test("a dispatch-capable runtime fails closed without a valid postClickConfirmation", () => {
  const base = { requestOperator: panelResponses() };
  assert.throws(() => createOperatorRuntime({ ...base, clickPlayer: async () => {} }), /postClickConfirmation/);
  assert.throws(() => createOperatorRuntime({ ...base, draftTab: {}, observeAfter: async () => afterObservation() }), /postClickConfirmation/);
  const valid = confirmWith(async () => afterObservation());
  assert.throws(() => createOperatorRuntime({ ...base, clickPlayer: async () => {}, postClickConfirmation: { ...valid, observer: {} } }), /captureHistory and captureFrame/);
  assert.throws(() => createOperatorRuntime({ ...base, clickPlayer: async () => {}, postClickConfirmation: { ...valid, showPlayers: undefined } }), /view switchers/);
  assert.doesNotThrow(() => createOperatorRuntime({ ...base, clickPlayer: async () => {}, postClickConfirmation: valid }));
});

test("observe-only runtimes may still use observeAfter and never click", async () => {
  const runtime = createOperatorRuntime({ requestOperator: panelResponses(), observeCurrent: async () => observation("s1"), observeAfter: async () => afterObservation() });
  assert.equal(typeof runtime.runTurn, "function");
});

test("a prepared command refused before the click is released as failed and never clicked", async () => {
  let clicks = 0;
  const acks = [];
  const base = panelResponses();
  const runtime = createOperatorRuntime({
    requestOperator: async (payload) => {
      if (payload.operation === "ack" && payload.status === "failed") {
        acks.push(payload);
        return { operation: "ack", status: "failed", action_id: payload.action_id };
      }
      return base(payload);
    },
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  // The recommended player disappears between prepare and the click.
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observe: async () => observation("s1", { current_pick: 2 }),
  });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.equal(result.released, true);
  assert.equal(clicks, 0);
  assert.equal(acks.length, 1);
  assert.equal(acks[0].action_id, "a1");
  assert.match(acks[0].detail, /^not clicked: /);
});

test("a failed release acknowledgement still never clicks and reports the action as unreleased", async () => {
  let clicks = 0;
  const runtime = createOperatorRuntime({
    requestOperator: async (payload) => {
      if (payload.operation === "ack" && payload.status === "failed") throw new Error("panel unavailable");
      return panelResponses()(payload);
    },
    postClickConfirmation: confirmWith(async () => afterObservation()),
    clickPlayer: async () => { clicks += 1; },
  });
  const result = await runtime.runTurn({
    sessionId: "s1",
    observation: observation("s1"),
    observe: async () => observation("s1", { current_pick: 2 }),
  });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.equal(result.released, false);
  assert.match(result.releaseAck.error, /panel unavailable/);
  assert.equal(clicks, 0);
});
