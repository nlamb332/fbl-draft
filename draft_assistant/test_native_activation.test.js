"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { activateDraft, clickPauseInPage, inspectDraftLifecycle, inspectNativeControls, waitForDraftStart } = require("./native_activation.js");
const { EspnRoomSimulator } = require("./room_simulator.js");
const { filterLiveAvailablePlayers } = require("./live_projection_inputs.js");

const ORDER = ["Example Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
const CATALOG = Array.from({ length: 160 }, (_, index) => ({
  player_id: String(1000 + index),
  name: `Player ${index}`,
  eligibility: ["G"],
  stats: { PTS: 100 - index, FGM: 1, FGA: 2, FTM: 1, FTA: 1, "3PM": 1, REB: 1, AST: 1, STL: 1, BLK: 1, TO: 1 },
  projected_gp: 70,
  draft_rank: index + 1,
}));

function room(options = {}) {
  return new EspnRoomSimulator({ catalog: CATALOG, order: ORDER, ownTeam: "Example Team", ...options });
}

async function inspect(simulator) {
  return simulator.tab().playwright.locator("body").evaluate(inspectNativeControls);
}

async function lifecycle(simulator) {
  return simulator.tab().playwright.locator("body").evaluate(inspectDraftLifecycle);
}

const VIEWS = ["players", "history", "league_manager"];
const LABELS = ["Resume", "RESUME", "resume draft", "Resume Draft"];

test("locator resolves exactly one Resume on every view, whatever the label casing", async () => {
  for (const view of VIEWS) {
    for (const resumeLabel of LABELS) {
      const result = await inspect(room({ view, resumeLabel }));
      assert.equal(result.resume.status, "unique", `${view} / ${resumeLabel}: ${result.resume.reason}`);
      assert.equal(result.native_paused, true);
    }
  }
});

test("failure mode 2: a stray visible Resume outside League Manager does not break the scoped locator", async () => {
  const simulator = room({ view: "players", strayResume: true });
  const body = simulator.body();
  // The unscoped query that failed live is ambiguous on this fixture ...
  const unscoped = body.querySelectorAll("button").filter((node) => /^resume/i.test(node.textContent.trim()));
  assert.equal(unscoped.length, 2);
  // ... the region-scoped locator is not.
  const result = await inspect(simulator);
  assert.equal(result.resume.status, "unique");
  assert.equal(result.resume.match_count, 1);
});

test("failure mode 3: exact-case locator misses after switching to League Manager; the case-insensitive one resolves", async () => {
  const simulator = room({ view: "league_manager", resumeLabel: "RESUME DRAFT" });
  const exactCase = simulator.body().querySelectorAll("button").filter((node) => node.textContent.trim() === "Resume");
  assert.equal(exactCase.length, 0);
  assert.equal((await inspect(simulator)).resume.status, "unique");
});

test("ambiguous, missing, hidden, disabled and region-less fixtures refuse", async () => {
  assert.equal((await inspect(room({ resumeMode: "duplicate" }))).resume.status, "ambiguous");
  assert.equal((await inspect(room({ resumeMode: "absent" }))).resume.status, "missing");
  assert.equal((await inspect(room({ resumeMode: "disabled" }))).resume.status, "disabled");
  assert.equal((await inspect(room({ noRegion: true }))).resume.status, "no_region");
  // A hidden twin is not a second match: exactly one VISIBLE enabled control remains.
  const hiddenTwin = await inspect(room({ resumeMode: "hidden_duplicate" }));
  assert.equal(hiddenTwin.resume.status, "unique");
  assert.equal(hiddenTwin.resume.match_count, 2);
  assert.equal(hiddenTwin.resume.usable_count, 1);
  // A running room has no Resume to click.
  assert.equal((await inspect(room({ paused: false }))).resume.status, "missing");
});

test("a Resume that exists only on the League Manager view is reported missing elsewhere", async () => {
  const simulator = room({ resumeMode: "league_manager_only", view: "players" });
  assert.equal((await inspect(simulator)).resume.status, "missing");
  simulator.setView("league_manager");
  assert.equal((await inspect(simulator)).resume.status, "unique");
});

test("native Resume refuses a hidden document and pauses the coordinator", async () => {
  const simulator = room({ view: "league_manager" });
  const operations = [];
  const previousDocument = global.document;
  global.document = { visibilityState: "hidden" };
  try {
    const result = await activateDraft({
      tab: simulator.tab(),
      observe: async () => ({ session_id: "s1", observed_at: new Date().toISOString(), revision: operations.length + 1 }),
      requestOperator: async (payload) => {
        operations.push(payload);
        return payload.operation === "preflight"
          ? { status: "preflight_ok", ok: true }
          : { status: "paused", state: "paused" };
      },
      sessionId: "s1",
      expectedSeat: 1,
      ownerToken: "owner-token",
    });
    assert.equal(result.status, "refused");
    assert.equal(result.native_resume_clicked, false);
    assert.equal(result.stage, "visibility");
    assert.equal(simulator.resumeClicks, 0);
    assert.deepEqual(operations.map((entry) => entry.operation), ["preflight", "pause"]);
  } finally {
    if (previousDocument === undefined) delete global.document;
    else global.document = previousDocument;
  }
});

test("configured timer is read back from the select, not the running clock", async () => {
  assert.equal((await inspect(room({ timerSeconds: 90 }))).native_timer_seconds, 90);
  assert.equal((await inspect(room({ timerSeconds: 30 }))).native_timer_seconds, 30);
});

test("draft lifecycle identifies the practice banner and native running/paused state", async () => {
  const paused = await lifecycle(room({ paused: true }));
  assert.equal(paused.practice, true);
  assert.equal(paused.started, true);
  assert.equal(paused.paused, true);
  assert.equal(paused.running, false);

  const running = await lifecycle(room({ paused: false }));
  assert.equal(running.practice, true);
  assert.equal(running.started, true);
  assert.equal(running.paused, false);
  assert.equal(running.running, true);
});

test("Scan/Rescan pause mutation clicks one scoped native Pause and reads it back", async () => {
  const simulator = room({ paused: false });
  const result = await simulator.tab().playwright.locator("body").evaluate(clickPauseInPage);
  assert.deepEqual(result, { clicked: true, usable_count: 1 });
  assert.equal(simulator.paused, true);
  assert.equal((await lifecycle(simulator)).paused, true);
});

test("practice scan wait reports progress and resolves when ESPN starts", async () => {
  let clock = 0;
  let reads = 0;
  const progress = [];
  const result = await waitForDraftStart({
    read: async () => {
      reads += 1;
      return { started: reads > 1 };
    },
    now: () => clock,
    sleep: async () => { clock += 1; },
    timeoutMs: 10,
    pollMs: 1,
    onProgress: event => progress.push(event.phase),
  });
  assert.equal(result.started, true);
  assert.equal(reads, 2);
  assert.deepEqual(progress, ["waiting_for_start"]);
});

function harness({ simulator = room(), preflight = async () => ({ status: "preflight_ok", ok: true }), start = async () => ({ status: "running" }) } = {}) {
  const operations = [];
  return {
    simulator,
    operations,
    options: {
      tab: simulator.tab(),
      observe: async () => ({ session_id: "s1", observed_at: new Date().toISOString(), revision: operations.length + 1 }),
      requestOperator: async (payload) => {
        operations.push(payload);
        return payload.operation === "preflight" ? preflight(payload) : start(payload);
      },
      sessionId: "s1",
      expectedSeat: 1,
      ownerToken: "owner-token",
    },
  };
}

test("activation is one bounded call: preflight, a single Resume click, first fresh frame, Start", async () => {
  for (const view of VIEWS) {
    const { simulator, operations, options } = harness({ simulator: room({ view, resumeLabel: "RESUME" }) });
    const result = await activateDraft(options);
    assert.equal(result.status, "activated", result.reason);
    assert.equal(result.native_resume_clicked, true);
    assert.equal(simulator.resumeClicks, 1);
    assert.equal(simulator.paused, false);
    assert.deepEqual(operations.map((entry) => entry.operation), ["preflight", "start"]);
    assert.equal(operations[0].preflight.native_timer_seconds, 90);
    assert.equal(operations[0].preflight.expected_seat, 1);
    assert.ok(operations.every((entry) => entry.owner_token === "owner-token"));
    assert.ok(result.elapsed_ms < result.budget_ms);
    assert.deepEqual(result.steps.map((step) => step.step), ["inspect", "paused-frame", "preflight", "click", "first-frame", "start"]);
  }
});

test("activation never clicks when the locator refuses or the preflight refuses", async () => {
  for (const resumeMode of ["duplicate", "absent", "disabled"]) {
    const { simulator, operations, options } = harness({ simulator: room({ resumeMode }) });
    const result = await activateDraft(options);
    assert.equal(result.status, "refused");
    assert.equal(result.native_resume_clicked, false);
    assert.equal(simulator.resumeClicks, 0);
    assert.equal(operations.length, 0, "locator refusal happens before any coordinator write");
  }
  const { simulator, operations, options } = harness({ preflight: async () => ({ status: "error", error: "native pick timer read-back is 30 s" }) });
  const result = await activateDraft(options);
  assert.equal(result.status, "refused");
  assert.equal(result.stage, "preflight");
  assert.equal(simulator.resumeClicks, 0);
  assert.deepEqual(operations.map((entry) => entry.operation), ["preflight"]);
});

test("a missing Resume refuses, then succeeds after the operator reveals it on League Manager", async () => {
  const simulator = room({ resumeMode: "league_manager_only", view: "players" });
  const first = harness({ simulator });
  assert.equal((await activateDraft(first.options)).status, "refused");
  assert.equal(simulator.resumeClicks, 0);
  simulator.setView("league_manager");
  const second = harness({ simulator });
  assert.equal((await activateDraft(second.options)).status, "activated");
  assert.equal(simulator.resumeClicks, 1);
});

test("the freshness budget bounds the whole operation and never retries a click", async () => {
  // Slow preflight: budget exhausted before any click.
  const slowPre = harness({ preflight: () => new Promise((resolve) => setTimeout(() => resolve({ status: "preflight_ok" }), 300)) });
  const refused = await activateDraft({ ...slowPre.options, budgetMs: 100 });
  assert.equal(refused.status, "budget_exceeded");
  assert.equal(refused.native_resume_clicked, false);
  assert.equal(slowPre.simulator.resumeClicks, 0);
  // Slow Start after the click: reported as clicked, never re-clicked.
  const slowStart = harness({ start: () => new Promise((resolve) => setTimeout(() => resolve({ status: "running" }), 300)) });
  const late = await activateDraft({ ...slowStart.options, budgetMs: 100 });
  assert.equal(late.status, "budget_exceeded");
  assert.equal(late.native_resume_clicked, true);
  assert.equal(slowStart.simulator.resumeClicks, 1);
  assert.ok(late.elapsed_ms < 250, "returns at the deadline instead of waiting for the slow request");
});

test("a local Start failure after the click is reported as clicked", async () => {
  const { options, simulator } = harness({ start: async () => ({ status: "error", error: "readiness failed" }) });
  const result = await activateDraft(options);
  assert.equal(result.status, "failed");
  assert.equal(result.native_resume_clicked, true);
  assert.equal(simulator.resumeClicks, 1);
});

test("_live_filter regression: filter diagnostics stay outside the observation", () => {
  const observation = {
    session_id: "s1",
    revision: 1,
    available_players: [
      { player_id: "1", name: "Complete", eligibility: ["G"], stats: CATALOG[0].stats, projected_gp: 70 },
      { player_id: "2", name: "Incomplete", eligibility: ["G"], stats: {}, projected_gp: 70 },
    ],
    available_pool_complete: true,
    warnings: [],
  };
  const result = filterLiveAvailablePlayers(observation);
  assert.ok(result.filter_metadata);
  assert.equal(Object.prototype.hasOwnProperty.call(result.observation, "_live_filter"), false);
  assert.equal(result.observation.available_players.length, 1);
  assert.equal(result.observation.available_pool_complete, false);
  const allowed = new Set(["session_id", "revision", "available_players", "available_pool_complete", "warnings"]);
  assert.deepEqual(Object.keys(result.observation).filter((key) => !allowed.has(key)), []);
});
