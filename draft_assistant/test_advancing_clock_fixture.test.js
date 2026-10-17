"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { createBrowserObserver } = require("./browser_observer.js");
const {
  OUTCOMES,
  createOperatorRuntime,
  hasAuthoritativeEvidence,
} = require("./operator_runtime.js");
const { AdvancingClockFixture } = require("./fixtures/advancing_clock_fixture.js");

const OWN = "Example Team";
const ORDER = [OWN, ...Array.from({ length: 13 }, (_, index) => `Opponent ${index + 1}`)];
const ROOM = { league_id: "1000000012", season_id: "2027", team_id: "15", practice: true };
const RULES = {
  teams: 14,
  rounds: 10,
  starting_slots: ["C", "G", "G", "F", "F", "UTIL"],
  bench_slots: 4,
  ir_slots: 3,
  scoring: { PTS: 1, REB: 1 },
};

function catalog() {
  return Array.from({ length: 160 }, (_, index) => ({
    player_id: String(1000 + index),
    name: `Player ${index + 1}`,
    eligibility: [index % 2 ? "G" : "F"],
    stats: { PTS: 100 - index, REB: 10, FGA: 1, FGM: 1, FTA: 1, FTM: 1, "3PM": 1, AST: 1, STL: 1, BLK: 1, TO: 1 },
    projected_gp: 70,
    draft_rank: index + 1,
  }));
}

function fixture(options = {}) {
  return new AdvancingClockFixture({
    catalog: catalog(),
    order: ORDER,
    ownTeam: OWN,
    room: ROOM,
    timerSeconds: 30,
    opponentDelayMs: 100,
    ...options,
  });
}

function observerFor(fixtureInstance, { historyHydrationReads = 0 } = {}) {
  const tab = fixtureInstance.tab();
  const simulator = fixtureInstance.simulator;
  simulator.historyHydrationReads = historyHydrationReads;
  const observer = createBrowserObserver({
    tab,
    sessionId: "session-1",
    room: ROOM,
    freshlyVerifiedRules: RULES,
    projectionCatalog: catalog(),
    counter: { n: 0 },
    now: fixtureInstance.nowFn,
  });
  return { observer, tab };
}

function operatorFor(fixtureInstance, observer, tab, { confirmation = null } = {}) {
  let sequence = 0;
  const operations = [];
  const requestOperator = async (payload) => {
    operations.push(payload.operation + (payload.status ? `:${payload.status}` : ""));
    if (payload.operation === "prepare") {
      const observation = payload.observation;
      const player = observation.available_players[0];
      if (!player) return { status: "error", error: "no player available" };
      sequence += 1;
      return {
        status: "command_ready",
        command: {
          type: "select_player",
          action_id: `action-${sequence}`,
          session_id: payload.session_id,
          player_id: player.player_id,
          player_name: player.name,
          expected_pick: observation.current_pick,
          expected_team: observation.on_clock_team,
          revision: observation.revision,
        },
      };
    }
    if (payload.operation === "ack" && payload.status === "submitted") {
      return { status: "submitted", action_id: payload.action_id };
    }
    if (payload.operation === "ack") return { status: payload.status, action_id: payload.action_id };
    if (payload.operation === "reconcile") {
      return { status: "reconciled", actions: [{ action_id: payload.observation ? `action-${sequence}` : "", status: "confirmed" }] };
    }
    throw new Error(`unexpected operator operation ${payload.operation}`);
  };
  const actualConfirmation = confirmation || {
    observer,
    showPickHistory: () => fixtureInstance.simulator.setView("history"),
    showPlayers: () => fixtureInstance.simulator.setView("players"),
    sleepFn: async (milliseconds) => fixtureInstance.advance(milliseconds, { reason: "confirmation_poll" }),
  };
  const runtime = createOperatorRuntime({
    requestOperator,
    observeCurrent: async () => observer.captureFrame(),
    draftTab: tab,
    postClickConfirmation: actualConfirmation,
    now: fixtureInstance.nowFn,
  });
  return { runtime, operations };
}

test("controlled DOM reads advance the visible deadline and independent opponent turns", async () => {
  const room = fixture({ timerSeconds: 4, opponentDelayMs: 500 });
  room.resume();
  assert.equal(room.currentPick, 1);
  room.simulator.draft("1000", OWN);
  room.sync();
  assert.equal(room.currentPick, 2);

  const slowCaptureTab = room.tab({ readLatencyMs: 500 });
  await slowCaptureTab.playwright.locator("body").evaluate((body) => body.textContent);
  assert.equal(room.currentPick, 3, "opponent progressed without a batch-state observation");
  assert.equal(room.clock, 4, "a new opponent turn received a fresh accelerated deadline");
  assert.ok(room.events.some((event) => event.type === "synthetic_opponent_pick"));
  assert.ok(room.events.some((event) => event.source === "dom_read"));
  assert.equal(room.report().real_30_second_endurance_claim, false);
});

test("native timeout and autopick outcomes are explicit synthetic evidence", () => {
  const timedOut = fixture({ timerSeconds: 2, opponentDelayMs: 500, nativeAutopick: false });
  timedOut.resume();
  timedOut.advance(2_000, { reason: "native_timer" });
  assert.equal(timedOut.currentPick, 1);
  assert.equal(timedOut.clock, 0);
  assert.equal(timedOut.expired, true);
  assert.equal(timedOut.report().events.find((event) => event.type === "synthetic_native_timeout").synthetic, true);

  const autopicked = fixture({ timerSeconds: 2, opponentDelayMs: 500, nativeAutopick: true });
  autopicked.resume();
  autopicked.advance(2_000, { reason: "native_timer" });
  assert.equal(autopicked.currentPick, 2);
  const event = autopicked.report().events.find((entry) => entry.type === "synthetic_native_autopick");
  assert.equal(event.source, "simulated_native_autopick");
  assert.equal(autopicked.report().real_30_second_endurance_claim, false);
});

test("slow post-click confirmation sees later turns and never issues a second click", async () => {
  const room = fixture({ timerSeconds: 4, opponentDelayMs: 500 });
  room.resume();
  const { observer, tab } = observerFor(room);
  let slowRead = false;
  const confirmation = {
    observer,
    showPickHistory: () => {
      room.simulator.setView("history");
      if (!slowRead) {
        slowRead = true;
        room.advance(2_500, { reason: "slow_confirmation" });
      }
    },
    showPlayers: () => room.simulator.setView("players"),
    sleepFn: async (milliseconds) => room.advance(milliseconds, { reason: "confirmation_poll" }),
  };
  const { runtime, operations } = operatorFor(room, observer, tab, { confirmation });
  const before = await observer.captureFrame();
  const result = await runtime.runTurn({ sessionId: "session-1", observation: before });
  assert.equal(result.outcome, OUTCOMES.CONFIRMED, JSON.stringify(result.reason || result.detail));
  assert.equal(room.clicks.length, 1);
  assert.ok(room.events.some((event) => event.type === "synthetic_opponent_pick"));
  assert.equal(operations.filter((operation) => operation === "prepare").length, 1);
  assert.equal(operations.filter((operation) => operation === "reconcile").length, 1);
  assert.deepEqual(operations.slice(0, 3), ["prepare", "ack:submitted", "reconcile"]);
});

test("expired and stale frames fail closed before a click, while fast evidence stays unconfirmable", async () => {
  const expired = fixture({ timerSeconds: 2, opponentDelayMs: 500 });
  expired.resume();
  expired.advance(2_000, { reason: "expired_guard" });
  const expiredParts = observerFor(expired);
  const expiredRuntime = operatorFor(expired, expiredParts.observer, expiredParts.tab);
  const expiredFrame = await expiredParts.observer.captureFrame();
  const expiredResult = await expiredRuntime.runtime.runTurn({ sessionId: "session-1", observation: expiredFrame });
  assert.equal(expiredResult.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(expiredResult.reason, /expired/);
  assert.equal(expired.clicks.length, 0);

  const stale = fixture({ paused: true });
  const staleParts = observerFor(stale);
  const staleRuntime = operatorFor(stale, staleParts.observer, staleParts.tab);
  const staleFrame = await staleParts.observer.captureFrame();
  stale.advance(10_001, { reason: "stale_guard" });
  const staleResult = await staleRuntime.runtime.runTurn({ sessionId: "session-1", observation: staleFrame });
  assert.equal(staleResult.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(staleResult.reason, /old/);
  assert.equal(stale.clicks.length, 0);

  const fast = await staleParts.observer.captureFastFrame();
  assert.equal(hasAuthoritativeEvidence({
    action_id: "unused",
    session_id: "session-1",
    player_id: "1000",
    expected_pick: 1,
    expected_team: OWN,
  }, fast, { now: stale.nowFn }).ok, false);
});

test("confirmation budget exhaustion records uncertainty and does not double-click", async () => {
  const room = fixture({ timerSeconds: 30, opponentDelayMs: 250, simulatorOptions: { historyHydrationReads: 100 } });
  room.resume();
  const { observer, tab } = observerFor(room, { historyHydrationReads: 100 });
  const { runtime, operations } = operatorFor(room, observer, tab);
  const before = await observer.captureFrame();
  const result = await runtime.runTurn({ sessionId: "session-1", observation: before });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  assert.match(result.reason, /budget|authoritative|history/i);
  assert.equal(room.clicks.length, 1);
  assert.equal(operations.filter((operation) => operation === "prepare").length, 1);
  assert.equal(operations.filter((operation) => operation === "reconcile").length, 0);
  assert.ok(result.recovery && /Do not retry/.test(result.recovery.action));
});

test("clean accelerated run reaches 140 unique picks with ten confirmed own picks", async () => {
  const room = fixture({ timerSeconds: 30, opponentDelayMs: 25 });
  room.resume();
  const { observer, tab } = observerFor(room);
  const { runtime } = operatorFor(room, observer, tab);
  let ownConfirmed = 0;
  let safety = 0;
  while (!room.complete && safety++ < 300) {
    if (room.ownTurn) {
      const before = await observer.captureFrame();
      const result = await runtime.runTurn({ sessionId: "session-1", observation: before });
      assert.equal(result.outcome, OUTCOMES.CONFIRMED, JSON.stringify(result.reason || result.detail));
      ownConfirmed += 1;
    } else {
      room.advance(25, { reason: "accelerated_opponent_schedule" });
    }
  }
  assert.equal(room.complete, true, "synthetic accelerated schedule completed");
  assert.equal(room.picks.length, 140);
  assert.equal(new Set(room.picks.map((pick) => pick.player_id)).size, 140);
  assert.equal(room.ownPickNumbers.length, 10);
  assert.equal(ownConfirmed, 10);
  assert.equal(room.report().real_30_second_endurance_claim, false);
});
