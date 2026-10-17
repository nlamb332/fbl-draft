"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  QUEUE_OUTCOMES,
  QueueBlockedError,
  createQueueRuntime,
  expectedTeamForPick,
  planQueue,
} = require("./queue_runtime.js");

const ROOM = { league_id: "1000000007", season_id: "2027", team_id: "15", practice: true };
const SESSION = "queue-session";
const OWN = "Example Team";

function isoNow() {
  return new Date().toISOString();
}

function orderWithOwnAtSeat(seat = 7) {
  const order = Array.from({ length: 14 }, (_, index) => `Team ${index + 1}`);
  order[seat - 1] = OWN;
  return order;
}

function historyThrough(currentPick, order, overrides = {}) {
  const history = [];
  for (let pick = 1; pick < currentPick; pick += 1) {
    const team = expectedTeamForPick(pick, order);
    const id = overrides[pick] || `draft-${pick}`;
    history.push({ pick_number: pick, team, player_id: id, player_name: id });
  }
  return history;
}

function player(id, eligibility = ["G"]) {
  return { player_id: id, name: id.toUpperCase(), eligibility, stats: { PTS: 20 }, projected_gp: 80 };
}

function observation(overrides = {}) {
  const currentPick = overrides.current_pick || 7;
  const order = overrides.pick_order || orderWithOwnAtSeat(7);
  return {
    session_id: SESSION,
    room: ROOM,
    observed_at: isoNow(),
    revision: 12,
    phase: "draft",
    complete: true,
    history_complete: true,
    current_pick: currentPick,
    on_clock_team: OWN,
    seconds_remaining: 30,
    autopick: false,
    pick_order: order,
    history: overrides.history || historyThrough(currentPick, order),
    roster: overrides.roster || [],
    available_players: overrides.available_players || [player("p1", ["C"]), player("p2", ["F"]), player("p3", ["G"])],
    known_players: overrides.known_players || [],
    available_pool_complete: false,
    ...overrides,
  };
}

function recommendation(ids = ["p1", "p2", "p3"], status = "advisory") {
  return { lookahead_status: status, candidates: ids.map((id, index) => ({ player_id: id, name: id.toUpperCase(), score: 100 - index })) };
}

function readback(queue, extra = {}) {
  return { session_id: SESSION, room: ROOM, queue, history: [], roster: [], ...extra };
}

function lifecycle() {
  let sequence = 0;
  return {
    prepare: async (request) => ({ ...request, mutation_id: `mutation-${++sequence}`, operation: request.operation }),
    claim: async (request) => ({ ...request, status: "pending" }),
    finalGuard: async (request) => ({ ...request, type: request.operation === "remove" ? "remove_queued_player" : "queue_player", player_id: request.player_id, mutation_id: request.mutation_id, session_id: SESSION }),
    ack: async (request) => ({ ...request, status: request.ack_status || request.status, mutation_id: request.mutation_id }),
  };
}

function makePlan(overrides = {}) {
  const obs = observation(overrides.observation || {});
  const plan = planQueue({
    sessionId: SESSION,
    observation: obs,
    recommendation: overrides.recommendation || recommendation(),
    room: ROOM,
    ownTeam: OWN,
    enabled: true,
    ...overrides.options,
  });
  return { obs, plan };
}

test("queue fallback is disabled until explicitly enabled and rejects promoted lookahead", () => {
  const obs = observation();
  const disabled = planQueue({ sessionId: SESSION, observation: obs, recommendation: recommendation() });
  assert.equal(disabled.outcome, QUEUE_OUTCOMES.DISABLED);
  assert.throws(() => planQueue({
    sessionId: SESSION,
    observation: obs,
    recommendation: recommendation(["p1", "p2"], "promoted"),
    room: ROOM,
    enabled: true,
  }), (error) => error instanceof QueueBlockedError && error.code === "lookahead_not_allowed");
});

test("planning uses foundation visible IDs, accounts for nearby snake turns, and is side-effect free", () => {
  const { plan } = makePlan();
  assert.equal(plan.expected_pick, 7);
  assert.equal(plan.next_own_pick, 22);
  assert.equal(plan.nearby_pick_gap, 15);
  assert.deepEqual(plan.desired_ids, ["p1", "p2"]);
  assert.equal(plan.candidates[0].expected_pick, 7);
  assert.equal(plan.candidates[1].expected_pick, 7);
  assert.equal(plan.candidates[1].target_pick, 22);
  // A catalog-only candidate cannot be substituted for a missing visible row.
  assert.throws(() => makePlan({ recommendation: recommendation(["catalog-only"]) }), /visible pool/);
});

test("the second nearby queue candidate is evaluated after the first for roster legality", () => {
  const order = orderWithOwnAtSeat(7);
  const history = historyThrough(106, order);
  const roster = [player("roster-c", ["C"]), ...Array.from({ length: 6 }, (_, index) => player(`roster-f-${index}`, ["F"]))];
  const obs = observation({ current_pick: 106, pick_order: order, history, roster, available_players: [player("p1", ["F"]), player("p2", ["F"])] });
  const plan = planQueue({
    sessionId: SESSION,
    observation: obs,
    recommendation: recommendation(["p1", "p2"]),
    room: ROOM,
    enabled: true,
  });
  assert.deepEqual(plan.desired_ids, ["p1"]);
});

test("planning rejects stale identity, Autopick, and unsafe turns", () => {
  const stale = observation({ observed_at: new Date(Date.now() - 10_001).toISOString() });
  assert.throws(() => planQueue({ sessionId: SESSION, observation: stale, recommendation: recommendation(), room: ROOM, enabled: true }), /stale/);
  assert.throws(() => planQueue({ sessionId: "wrong", observation: observation(), recommendation: recommendation(), room: ROOM, enabled: true }), /session/);
  assert.throws(() => planQueue({ sessionId: SESSION, observation: observation({ autopick: true }), recommendation: recommendation(), room: ROOM, enabled: true }), /Autopick/);
  const opponentPlan = planQueue({ sessionId: SESSION, observation: observation({ on_clock_team: "Team 1" }), recommendation: recommendation(), room: ROOM, enabled: true });
  assert.equal(opponentPlan.expected_team, "Team 1");
  assert.equal(opponentPlan.requires_own_turn, false);
  assert.equal(opponentPlan.candidates[0].expected_pick, 7);
  assert.equal(opponentPlan.candidates[0].expected_team, "Team 1");
  assert.equal(opponentPlan.candidates[0].target_team, OWN);
});

test("an opponent-drafted stale queue row is removed exactly once", async () => {
  const { obs, plan } = makePlan({ recommendation: recommendation(["p1"]) });
  const opponentHistory = historyThrough(7, obs.pick_order, { 6: "p1" });
  let queue = [{ player_id: "p1", player_name: "P1" }];
  const removed = [];
  const runtime = createQueueRuntime({ enabled: true });
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => ({ ...obs, history: opponentHistory }),
    readState: async () => readback(queue, { history: opponentHistory }),
    queuePlayer: async () => { throw new Error("must not re-add opponent pick"); },
    queueLifecycle: lifecycle(),
    removeQueuedPlayer: async (command) => {
      removed.push(command.player_id);
      queue = [];
    },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.OPPONENT_DRAFTED);
  assert.deepEqual(removed, ["p1"]);
  assert.deepEqual(queue, []);
});

test("Autopick at the initial execute guard pauses explicitly", async () => {
  const { plan } = makePlan();
  const runtime = createQueueRuntime({ enabled: true });
  let notice = null;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => observation({ autopick: true }),
    readState: async () => readback([]),
    queuePlayer: async () => { throw new Error("must not write with Autopick"); },
    onPause: async (detail) => { notice = detail; },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.PAUSED);
  assert.equal(runtime.state().paused, true);
  assert.match(notice.reason, /Autopick/);
});

test("existing queue prefix is a no-op and does not remove/re-add rows", async () => {
  const { obs, plan } = makePlan();
  const runtime = createQueueRuntime({ enabled: true });
  let writes = 0;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback([{ player_id: "p1" }, { player_id: "p2" }]),
    queuePlayer: async () => { writes += 1; },
    removeQueuedPlayer: async () => { writes += 1; },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.NOOP);
  assert.equal(writes, 0);
});

test("visible queue callback cannot bypass the durable lifecycle", async () => {
  const { obs, plan } = makePlan({ recommendation: recommendation(["p1"]) });
  const runtime = createQueueRuntime({ enabled: true });
  let writes = 0;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback([]),
    queuePlayer: async () => { writes += 1; },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.BLOCKED);
  assert.equal(result.code, "queue_lifecycle_missing");
  assert.equal(writes, 0);
});

test("maintenance queues two nearby candidates serially with one pending mutation", async () => {
  const { obs, plan } = makePlan();
  const runtime = createQueueRuntime({ enabled: true });
  let queue = [];
  let active = 0;
  let maxActive = 0;
  const writes = [];
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback(queue),
    queuePlayer: async (command) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      writes.push(command.player_id);
      queue = [...queue, { player_id: command.player_id, player_name: command.player_name }];
      active -= 1;
    },
    queueLifecycle: lifecycle(),
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.QUEUED);
  assert.deepEqual(writes, ["p1", "p2"]);
  assert.equal(maxActive, 1);
  assert.deepEqual(result.queue.map((entry) => entry.player_id), ["p1", "p2"]);
  assert.equal(runtime.state().pending_mutation, null);
});

test("post-observation queue receipt supersedes an earlier stale receipt", async () => {
  const { obs, plan } = makePlan({ recommendation: recommendation(["p1"]) });
  const runtime = createQueueRuntime({ enabled: true });
  const queues = [[], [{ player_id: "p1" }], []];
  let readCount = 0;
  const acknowledgements = [];
  const queueLifecycle = lifecycle();
  const originalAck = queueLifecycle.ack;
  queueLifecycle.ack = async (request) => {
    acknowledgements.push({ ...request, queue: request.queue ? [...request.queue] : request.queue });
    return originalAck(request);
  };
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback(queues[Math.min(readCount++, queues.length - 1)]),
    queuePlayer: async () => {},
    queueLifecycle,
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.UNCERTAIN);
  assert.equal(acknowledgements.find((request) => request.ack_status === "confirmed"), undefined);
  assert.equal(acknowledgements.at(-1).ack_status, "uncertain");
});

test("opponent-drafted queue entries are removed from the desired plan without re-adding", async () => {
  const { obs, plan } = makePlan({ recommendation: recommendation(["p1"]) });
  const opponentHistory = historyThrough(7, obs.pick_order, { 6: "p1" });
  const state = readback([], { history: opponentHistory });
  const runtime = createQueueRuntime({ enabled: true });
  let writes = 0;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => state,
    queuePlayer: async () => { writes += 1; },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.OPPONENT_DRAFTED);
  assert.equal(writes, 0);
  assert.equal(result.opponent_drafted[0].player_id, "p1");
});

test("own-team consumption is not misclassified when the plan was prepared on an opponent turn", async () => {
  const { obs, plan } = makePlan({
    recommendation: recommendation(["p1"]),
    observation: { current_pick: 8, on_clock_team: "Team 8" },
  });
  const ownHistory = historyThrough(8, obs.pick_order, { 7: "p1" });
  const state = readback([], { history: ownHistory, roster: [player("p1", ["G"])] });
  const runtime = createQueueRuntime({ enabled: true });
  let writes = 0;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => state,
    queuePlayer: async () => { writes += 1; },
    queueLifecycle: lifecycle(),
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.NOOP);
  assert.equal(writes, 0);
});

test("Autopick read-back pauses queue maintenance and blocks the second nearby write", async () => {
  const { obs, plan } = makePlan();
  const runtime = createQueueRuntime({ enabled: true });
  let queue = [];
  let observations = 0;
  let pauseNotice = null;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => {
      observations += 1;
      return observations > 2 ? { ...obs, autopick: true } : obs;
    },
    readState: async () => readback(queue),
    queuePlayer: async (command) => { queue = [...queue, { player_id: command.player_id }]; },
    queueLifecycle: lifecycle(),
    onPause: async (detail) => { pauseNotice = detail; },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.PAUSED);
  assert.deepEqual(queue.map((entry) => entry.player_id), ["p1"]);
  assert.equal(pauseNotice.reason.includes("Autopick"), true);
  assert.equal(runtime.state().paused, true);
});

test("local pause prevents new writes and does not claim native queue cancellation", async () => {
  const { obs, plan } = makePlan();
  const runtime = createQueueRuntime({ enabled: true });
  runtime.pause("operator pause");
  let writes = 0;
  const result = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback([]),
    queuePlayer: async () => { writes += 1; },
  });
  assert.equal(result.outcome, QUEUE_OUTCOMES.BLOCKED);
  assert.equal(writes, 0);
  assert.equal(runtime.state().pause_reason, "operator pause");
});

test("ambiguous queue mutation stays unresolved until authoritative queue/history/roster reconcile", async () => {
  const { obs, plan } = makePlan({ recommendation: recommendation(["p1"]) });
  const runtime = createQueueRuntime({ enabled: true });
  const uncertain = await runtime.execute({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback([]),
    queuePlayer: async () => { throw new Error("UI result lost"); },
    queueLifecycle: lifecycle(),
  });
  assert.equal(uncertain.outcome, QUEUE_OUTCOMES.UNCERTAIN);
  assert.ok(runtime.state().pending_mutation);
  const blocked = await runtime.execute({ sessionId: SESSION, plan, observe: async () => obs, readState: async () => readback([]), queuePlayer: async () => {}, queueLifecycle: lifecycle() });
  assert.equal(blocked.outcome, QUEUE_OUTCOMES.BLOCKED);
  const reconciled = await runtime.reconcile({
    sessionId: SESSION,
    plan,
    observe: async () => obs,
    readState: async () => readback([{ player_id: "p1" }]),
  });
  assert.equal(reconciled.pending_mutation, null);
  assert.equal(reconciled.outcome, QUEUE_OUTCOMES.QUEUED);
  assert.equal(runtime.state().pending_mutation, null);
});

