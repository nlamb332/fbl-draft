"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

// The launch worktree owns canonical_runner.js until the parent integrates it.
// The fallback lets this test run there and resolves locally after integration.
function load(name) {
  try {
    const local = require(`./${name}.js`);
    if (name !== "view_switch" || typeof local.createViewSwitcher({ tab: { playwright: { locator() {} } } }).showLeagueManager === "function") return local;
    return require(path.join(__dirname, "../../luna-launch/draft_assistant", `${name}.js`));
  }
  catch (error) {
    if (error.code !== "MODULE_NOT_FOUND") throw error;
    return require(path.join(__dirname, "../../luna-launch/draft_assistant", `${name}.js`));
  }
}

const { createCanonicalPracticeRunner } = load("canonical_runner");
const { activateDraft, inspectNativeControls } = load("native_activation");
const { createViewSwitcher, inspectGridKinds } = load("view_switch");
const { EspnRoomSimulator } = load("room_simulator");

const OWN = "Example Team";
const ORDER = [OWN, ...Array.from({ length: 13 }, (_, i) => `Opponent ${i + 1}`)];
const CATALOG = Array.from({ length: 160 }, (_, i) => ({
  player_id: String(1000 + i), name: `Player ${i}`, eligibility: ["G"], stats: { PTS: 100 - i }, projected_gp: 70, draft_rank: i + 1,
}));
const ROOM = { league_id: "1000000012", season_id: "2027", team_id: "15", practice: true };

function frame(revision = 1, pick = 1) {
  return {
    session_id: "session-1", revision, observed_at: new Date().toISOString(), phase: "draft", current_pick: pick,
    on_clock_team: ORDER[(pick - 1) % ORDER.length], seconds_remaining: 80, autopick: false,
    // A live draft frame always shows candidates; an empty grid is a transient render the runner waits out.
    history_complete: true, history: [], roster: [], available_players: [{ player_id: "1000", name: "Player 0" }], warnings: [],
  };
}

function fixture({ resumeMode = "present", activationResponse = null, preflightResponse = null } = {}) {
  const simulator = new EspnRoomSimulator({ catalog: CATALOG, order: ORDER, ownTeam: OWN, room: ROOM, resumeMode });
  const draftTab = simulator.tab();
  const panelTab = { playwright: { locator: () => ({ evaluate: async () => ({}) }) } };
  const calls = { observerFrames: 0, historyReads: 0, retained: null, switches: [], operators: [], controls: [], clicks: [], batches: 0, recoveries: 0 };
  const modules = {
    createBrowserObserver: () => ({
      async captureFrame({ historySnapshot } = {}) {
        calls.observerFrames += 1;
        const result = frame(calls.observerFrames, calls.observerFrames === 1 ? 1 : 2);
        result.history_complete = historySnapshot ? historySnapshot.history_complete : calls.observerFrames !== 1;
        return result;
      },
      async captureFastFrame() {
        return this.captureFrame();
      },
      async captureHistory() {
        calls.historyReads += 1;
        return calls.historyReads === 1 ? { history_complete: false } : { history_complete: true, events: [] };
      },
      setRetainedHistory(value) { calls.retained = value; },
    }),
    createViewSwitcher: ({ tab }) => {
      const actual = createViewSwitcher({ tab });
      return {
        async showPlayers() { calls.switches.push("players"); return actual.showPlayers(); },
        async showPickHistory() { calls.switches.push("history"); return actual.showPickHistory(); },
        async showLeagueManager() { calls.switches.push("league_manager"); return actual.showLeagueManager(); },
      };
    },
    inspectGridKinds,
    inspectNativeControls,
    activateDraft: activationResponse || activateDraft,
    createPanelOperator: () => async (payload) => ({ status: "ok", ...payload }),
    clickExactDraftRow: async (_tab, command) => { calls.clicks.push(command.action_id); return { clicked: true }; },
    createOperatorRuntime: (options) => options,
    async runForegroundBatch(options) {
      calls.batches += 1;
      const actions = new Set();
      for (let pick = 1; pick <= 140; pick += 1) {
        if ((pick - 1) % 14 !== 0) continue;
        const observation = frame(pick, pick);
        const actionId = `action-${pick}`;
        await options.onProgress({ event: "own-turn", observation });
        await options.runtime.requestOperator({ operation: "prepare", session_id: "session-1", pick });
        await options.runtime.clickPlayer({ action_id: actionId, expected_pick: pick, player_name: `Player ${pick}` });
        assert.equal(actions.has(actionId), false, `duplicate click for ${actionId}`);
        actions.add(actionId);
        await options.onProgress({ event: "own-result", result: { outcome: "confirmed", command: { action_id: actionId, expected_pick: pick, player_name: `Player ${pick}` } } });
      }
      await options.readFinalCoordinatorStatus();
      return { status: "completed", cycles: 140, own_picks_confirmed: 10 };
    },
    async recoverAfterStop(options) {
      calls.recoveries += 1;
      await options.requestControl({ command: "pause" });
      await options.requestOperator({ operation: "status", session_id: "session-1" });
      return { recovered: true };
    },
    filterLiveAvailablePlayers: (observation) => ({ observation }),
  };
  const now = () => Date.now();
  const runner = createCanonicalPracticeRunner({
    modules, draftTab, panelTab, room: ROOM, freshlyVerifiedRules: { teams: 14 }, projectionCatalog: CATALOG,
    sessionId: "session-1", ownerToken: "owner-secret", ownTeam: OWN, expectedSeat: 1,
    requestOperator: async (payload) => {
      calls.operators.push(payload);
      if (payload.operation === "preflight" && preflightResponse) return preflightResponse;
      return payload.operation === "preflight" ? { status: "preflight_ok", ok: true }
        : payload.operation === "start" ? { status: "running" }
          : { status: "ok", latest_revision: 0 };
    },
    now,
  });
  return { runner, simulator, calls };
}

test("factory is observation-only and batch/recovery require successful explicit activation", async () => {
  const { runner, simulator, calls } = fixture();
  assert.equal(simulator.resumeClicks, 0);
  assert.deepEqual(calls.operators, []);
  await assert.rejects(runner.runBatch(), /until activate/);
  await assert.rejects(runner.recover({ reason: "stop" }), /until practice activation/);
  assert.equal(calls.batches, 0);
  assert.equal(calls.recoveries, 0);

  const pausedObservation = await runner.prepareActivation();
  const result = await runner.activate();
  assert.equal(result.status, "activated", result.reason);
  assert.equal(pausedObservation.history_complete, true);
  assert.equal(simulator.resumeClicks, 1);
  assert.deepEqual(calls.operators.map((call) => call.operation), ["status", "preflight", "start"]);
  assert.ok(calls.operators.every((call) => call.owner_token === "owner-secret"));
});

test("refused activation does not enable work, and activation cannot retry after Resume was clicked", async () => {
  const refused = fixture({ resumeMode: "absent" });
  await refused.runner.prepareActivation();
  const result = await refused.runner.activate();
  assert.equal(result.status, "refused");
  assert.equal(result.native_resume_clicked, false);
  await assert.rejects(refused.runner.runBatch(), /until activate/);
  assert.equal(refused.calls.batches, 0);

  const stale = fixture({ preflightResponse: { status: "error", error: "observation is stale" } });
  await stale.runner.prepareActivation();
  const staleResult = await stale.runner.activate();
  assert.equal(staleResult.status, "refused");
  assert.equal(staleResult.native_resume_clicked, false);
  assert.equal(stale.simulator.resumeClicks, 0);
  assert.deepEqual(stale.calls.operators.map((call) => call.operation), ["status", "preflight"]);

  const afterClickFailure = fixture({ activationResponse: async () => ({ status: "failed", native_resume_clicked: true, reason: "local Start failed" }) });
  await afterClickFailure.runner.prepareActivation();
  const failed = await afterClickFailure.runner.activate();
  assert.equal(failed.native_resume_clicked, true);
  await assert.rejects(afterClickFailure.runner.activate(), /one-shot/);
  assert.equal(afterClickFailure.simulator.resumeClicks, 0);
});

test("captureFrame refreshes incomplete history and a 140-pick batch records ten unique confirmations with owner transport", async () => {
  const { runner, simulator, calls } = fixture();
  const captured = await runner.captureFrame();
  assert.equal(captured.history_complete, true);
  assert.equal(calls.historyReads, 2);
  assert.equal(calls.retained.history_complete, true);
  assert.deepEqual(calls.switches, ["history", "players"]);

  await runner.prepareActivation();
  await runner.activate();
  const result = await runner.runBatch();
  assert.equal(result.cycles, 140);
  assert.equal(result.own_picks_confirmed, 10);
  assert.equal(calls.clicks.length, 10);
  assert.equal(new Set(calls.clicks).size, 10);
  assert.ok(calls.operators.every((call) => call.owner_token === "owner-secret"));
  assert.equal(runner.evidenceSnapshot().foreground.invocation_count, 1);

  const recovered = await runner.recover({ reason: "test stop" });
  assert.equal(recovered.recovered, true);
  assert.equal(calls.controls.length, 0);
  assert.ok(calls.operators.some((call) => call.operation === "pause" && call.owner_token === "owner-secret"));
});
