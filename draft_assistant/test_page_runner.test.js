"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  createPageRunner,
  createPageTab,
  inputCacheKey,
  PageRunnerError,
  readInputCache,
  validateRecommendationFrame,
  writeInputCache,
} = require("./page_runner.js");

const SESSION = "fresh-attachment";
const ROOM = { league_id: "1000000012", season_id: "2027", team_id: "15", practice: true };
const OWN = "Example Team";
const ORDER = [OWN, ...Array.from({ length: 13 }, (_, index) => `Opponent ${index + 1}`)];
const RULES = { teams: 14, rounds: 10, starting_slots: ["C", "G", "G", "F", "F", "UTIL"], bench_slots: 4, ir_slots: 3, scoring: { PTS: 1 } };
const PROJECTION_STATS = { PTS: 1, FGM: 1, FGA: 1, FTM: 1, FTA: 1, "3PM": 1, REB: 1, AST: 1, STL: 1, BLK: 1, TO: 1 };
const CATALOG = [
  { player_id: "p1", name: "Owned Player", eligibility: ["G"], stats: { ...PROJECTION_STATS, PTS: 1 }, projected_gp: 70, draft_rank: 1 },
  { player_id: "p2", name: "Remaining Player", eligibility: ["F"], stats: { ...PROJECTION_STATS, PTS: 2 }, projected_gp: 70, draft_rank: 2 },
];

function frame({ ownPick = true } = {}) {
  return {
    session_id: SESSION,
    room: ROOM,
    observed_at: new Date().toISOString(),
    revision: 1,
    phase: "draft",
    current_pick: ownPick ? 2 : 3,
    on_clock_team: ownPick ? ORDER[1] : ORDER[2],
    pick_order: ORDER,
    seconds_remaining: 30,
    autopick: false,
    complete: true,
    history_complete: true,
    history: ownPick ? [{ pick_number: 1, team: OWN, player_id: "p1", player_name: "Owned Player" }] : [],
    roster: ownPick ? [{ player_id: "p1", name: "Owned Player" }] : [],
    known_players: CATALOG,
    available_players: [],
    warnings: [],
  };
}

function pageDocument() {
  const body = { querySelector: () => null, querySelectorAll: () => [] };
  return { body, querySelector: body.querySelector, querySelectorAll: body.querySelectorAll, visibilityState: "visible" };
}

function memoryStorage(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    raw(key) { return values.get(key); },
  };
}

function cacheInputs(capturedAt = "2026-10-14T13:00:00.000Z", overrides = {}) {
  return {
    rules: RULES,
    catalog: CATALOG,
    catalog_complete: true,
    captured_at: capturedAt,
    catalog_summary: { source: "ESPN Players grid (paused room)", termination_reason: "end_of_grid", eligible_count: CATALOG.length },
    ...overrides,
  };
}

function scanCatalog(size = 24) {
  const stats = { PTS: 1, FGM: 1, FGA: 2, FTM: 1, FTA: 2, "3PM": 1, REB: 1, AST: 1, STL: 1, BLK: 1, TO: 1 };
  return Array.from({ length: size }, (_, index) => ({
    player_id: `scan-${index + 1}`,
    name: `Scan Player ${index + 1}`,
    eligibility: ["G"],
    stats: { ...stats, PTS: index + 1 },
    projected_gp: 70,
    draft_rank: index + 1,
  }));
}

function livePageDocument(simulator, pageWindow) {
  return {
    get body() { return simulator.liveBody(); },
    get defaultView() { return pageWindow; },
    visibilityState: "visible",
    querySelector(selector) { return this.body.querySelector(selector); },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
  };
}

function cachedRecommendationRunner(storage, { leagueId = ROOM.league_id, ownTeam = OWN, teamId = ROOM.team_id } = {}) {
  const io = transport();
  const factory = runnerStub(io);
  return createPageRunner({
    document: pageDocument(),
    window: { getComputedStyle: () => ({ display: "block", visibility: "visible" }), localStorage: storage },
    location: { href: `https://fantasy.espn.com/basketball/draft?leagueId=${leagueId}&seasonId=${ROOM.season_id}&teamId=${teamId}` },
    fetchFn: io.fetchFn,
    csrfToken: "csrf",
    sessionId: SESSION,
    ownerToken: "owner",
    ownTeam,
    expectedSeat: 1,
    rules: null,
    catalog: null,
    runnerFactory: factory,
    mode: "recommendation",
  });
}

function transport({ actions = [], ownPick = true, room = ROOM } = {}) {
  const calls = [];
  const state = {
    state: "observing",
    latest_revision: 0,
    observation: null,
    actions,
  };
  const status = () => ({
    session_id: SESSION,
    status: state.state,
    state: state.state,
    latest_revision: state.latest_revision,
    room,
    observation: state.observation,
    actions: state.actions,
    unresolved_actions: [],
    unresolved_queue_mutations: [],
    practice_attestation: { room: ROOM },
  });
  const fetchFn = async (_url, init) => {
    const payload = JSON.parse(init.body);
    calls.push(payload);
    if (payload.operation === "status") return { json: async () => status() };
    if (payload.operation === "pause") {
      state.state = "paused";
      return { json: async () => status() };
    }
    if (payload.operation === "publish") {
      state.observation = payload.observation;
      state.latest_revision = payload.observation.revision;
      return { json: async () => ({ ...status(), status: "ingested" }) };
    }
    if (payload.operation === "recommend-preview") {
      return { json: async () => ({ ...status(), status: "preview", recommendation: {
        session_id: SESSION,
        revision: payload.observation.revision,
        generated_at: new Date().toISOString(),
        candidates: [{ player_id: "p2", name: "Remaining Player", score: 2, components: {}, explanation: "foundation" }],
        lookahead_status: "advisory",
        explanation: "deterministic custom-points ranking",
      } }) };
    }
    return { json: async () => status() };
  };
  return { calls, state, fetchFn, status, initialFrame: frame({ ownPick }) };
}

function runnerStub(transportState, captureFrames = null, extras = {}) {
  let batchOptions = null;
  const prepared = transportState.initialFrame;
  let captureIndex = 0;
  const factory = () => ({
    ...extras,
    async prepareActivation() { return prepared; },
    async captureFrame() {
      if (!Array.isArray(captureFrames) || !captureFrames.length) return prepared;
      const frame = captureFrames[Math.min(captureIndex, captureFrames.length - 1)];
      captureIndex += 1;
      return frame;
    },
    async activate() {
      transportState.state.state = "running";
      return { status: "activated", native_resume_clicked: true };
    },
    async runBatch(options) {
      batchOptions = options;
      return { status: "completed", own_picks_confirmed: options.initialOwnPicksConfirmed || 0 };
    },
    evidenceSnapshot() { return {}; },
    getBatchOptions() { return batchOptions; },
  });
  factory.getBatchOptions = () => batchOptions;
  return factory;
}

function makeRunner(transportState, factory, options = {}) {
  return createPageRunner({
    document: pageDocument(),
    window: { getComputedStyle: () => ({ display: "block", visibility: "visible" }) },
    location: { href: "https://fantasy.espn.com/basketball/draft?leagueId=1000000012&seasonId=2027&teamId=15" },
    fetchFn: transportState.fetchFn,
    csrfToken: "csrf",
    sessionId: SESSION,
    ownerToken: "owner",
    ownTeam: OWN,
    expectedSeat: 1,
    rules: RULES,
    catalog: CATALOG,
    catalogComplete: true,
    runnerFactory: factory,
    mode: options.mode || "attended",
    now: options.now || Date.now,
  });
}

test("fresh page attachment builds a durable checkpoint from coordinator actions", async () => {
  const action = {
    action_id: "action-7",
    status: "confirmed",
    action_type: "draft",
    claimed: true,
    dispatched: true,
    session_id: SESSION,
    expected_pick: 1,
    expected_team: OWN,
    player_id: "p1",
    player_name: "Owned Player",
    revision: 1,
  };
  const io = transport({ actions: [action] });
  const factory = runnerStub(io);
  const runner = makeRunner(io, factory);
  const activation = await runner.activate();
  assert.equal(activation.status, "activated");
  const report = runner.summary();
  assert.equal(report.attachment.own_picks_confirmed, 1);
  const start = runner.startBatch();
  assert.ok(start.started_at);
  const result = await runner.awaitBatch();
  assert.equal(result.status, "completed");
  const options = factory.getBatchOptions();
  assert.equal(options.initialOwnPicksConfirmed, 1);
  assert.deepEqual(options.initialCheckpoint.confirmed_actions.map((item) => item.action_id), ["action-7"]);
  assert.equal(options.resumeCoordinatorStatus.state, "running");
  assert.equal(report.attachment.checkpoint_id, "batch-fresh-attachment-1");
  const ops = io.calls.map((call) => call.operation);
  assert.deepEqual(ops, ["status", "status", "pause", "status", "publish", "status", "status"]);
  assert.equal(io.state.state, "running");
});

test("attachment checkpoint is one-shot across direct batch starts", async () => {
  const action = {
    action_id: "action-7",
    status: "confirmed",
    action_type: "draft",
    claimed: true,
    dispatched: true,
    session_id: SESSION,
    expected_pick: 1,
    expected_team: OWN,
    player_id: "p1",
    player_name: "Owned Player",
    revision: 1,
  };
  const io = transport({ actions: [action] });
  const factory = runnerStub(io);
  const runner = makeRunner(io, factory);
  await runner.activate();

  runner.startBatch();
  await runner.awaitBatch();
  assert.equal(factory.getBatchOptions().initialOwnPicksConfirmed, 1);

  runner.startBatch();
  await runner.awaitBatch();
  const secondOptions = factory.getBatchOptions();
  assert.equal(Object.prototype.hasOwnProperty.call(secondOptions, "initialCheckpoint"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(secondOptions, "initialOwnPicksConfirmed"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(secondOptions, "resumeCoordinatorStatus"), false);
});

test("native history without coordinator action evidence carries zero guarded picks", async () => {
  const io = transport({ actions: [] });
  io.state.state = "paused";
  const factory = runnerStub(io);
  const runner = makeRunner(io, factory);
  await runner.activate();
  assert.equal(runner.summary().attachment.own_picks_confirmed, 0);
});

test("page tab refuses a Draft click after the document becomes hidden", async () => {
  const body = { querySelectorAll: () => [{ disabled: false, getAttribute: () => null, getClientRects: () => [{}], click() {} }] };
  const document = { body, querySelector: body.querySelector, querySelectorAll: body.querySelectorAll, visibilityState: "hidden" };
  const tab = createPageTab({ document, window: { getComputedStyle: () => ({ display: "block", visibility: "visible" }) }, location: { href: "https://example.test" } });
  await assert.rejects(() => tab.playwright.locator('button[data-player-id="p1"]').click(), (error) => {
    assert.equal(error instanceof PageRunnerError, true);
    assert.equal(error.code, "page_hidden");
    return true;
  });
});

test("scanInputs waits only for practice lifecycle and pauses it before a complete capture", async () => {
  const { EspnRoomSimulator } = require("./room_simulator.js");
  const scanRoom = { ...ROOM };
  const simulator = new EspnRoomSimulator({
    catalog: scanCatalog(),
    order: ORDER,
    ownTeam: OWN,
    room: scanRoom,
    paused: false,
    playersWindow: 14,
  });
  const io = transport();
  const pageWindow = {
    getComputedStyle: () => ({ display: "block", visibility: "visible" }),
    localStorage: memoryStorage(),
  };
  const document = livePageDocument(simulator, pageWindow);
  const runner = createPageRunner({
    document,
    window: pageWindow,
    location: { href: simulator.url() },
    fetchFn: io.fetchFn,
    csrfToken: "csrf",
    sessionId: SESSION,
    ownerToken: "owner",
    ownTeam: OWN,
    expectedSeat: 1,
    wheelScroller: async (deltaX, deltaY) => simulator.scrollPlayers(deltaX, deltaY),
    sleep: async () => {},
    mode: "recommendation",
  });
  const previousWindow = global.window;
  const previousDocument = global.document;
  global.window = pageWindow;
  global.document = document;
  try {
    const result = await runner.scanInputs({ maxPlayers: 50 });
    assert.equal(result.scan_mode, "practice_wait_pause_scan");
    assert.equal(simulator.paused, true);
    assert.equal(result.catalog_complete, true);
    assert.ok(result.catalog_players >= 24);
  } finally {
    if (previousWindow === undefined) delete global.window;
    else global.window = previousWindow;
    if (previousDocument === undefined) delete global.document;
    else global.document = previousDocument;
  }
});

test("scanInputs drives a non-practice room immediately without pausing it", async () => {
  const { EspnRoomSimulator } = require("./room_simulator.js");
  const realRoom = { ...ROOM, practice: false };
  const simulator = new EspnRoomSimulator({
    catalog: scanCatalog(),
    order: ORDER,
    ownTeam: OWN,
    room: realRoom,
    heading: "ESPN Fantasy Basketball Draft for Example League",
    paused: false,
    playersWindow: 14,
  });
  const io = transport({ room: realRoom });
  const pageWindow = {
    getComputedStyle: () => ({ display: "block", visibility: "visible" }),
    localStorage: memoryStorage(),
  };
  const document = livePageDocument(simulator, pageWindow);
  const runner = createPageRunner({
    document,
    window: pageWindow,
    location: { href: simulator.url() },
    fetchFn: io.fetchFn,
    csrfToken: "csrf",
    sessionId: SESSION,
    ownerToken: "owner",
    ownTeam: OWN,
    expectedSeat: 1,
    wheelScroller: async (deltaX, deltaY) => simulator.scrollPlayers(deltaX, deltaY),
    sleep: async () => {},
    mode: "recommendation",
  });
  const previousWindow = global.window;
  const previousDocument = global.document;
  global.window = pageWindow;
  global.document = document;
  try {
    const result = await runner.scanInputs({ maxPlayers: 50 });
    assert.equal(result.scan_mode, "immediate_scan");
    assert.equal(simulator.paused, false);
    assert.equal(result.catalog_complete, true);
    assert.equal(io.calls.some(call => call.operation === "pause"), false);
  } finally {
    if (previousWindow === undefined) delete global.window;
    else global.window = previousWindow;
    if (previousDocument === undefined) delete global.document;
    else global.document = previousDocument;
  }
});

test("recommendation mode has no actor path and polls a full-catalog preview", async () => {
  const io = transport();
  const factory = runnerStub(io);
  const runner = makeRunner(io, factory, { mode: "recommendation" });
  assert.equal(runner.summary().mode, "recommendation");
  await assert.rejects(() => runner.preflight(), /recommendation-only mode/);
  await assert.rejects(() => runner.activate(), /recommendation-only mode/);
  assert.throws(() => runner.startBatch(), /recommendation-only mode/);
  await assert.rejects(() => runner.recover(), /recommendation-only mode/);
  await assert.rejects(() => runner.recoverAndContinue(), /recommendation-only mode/);

  runner.startRecommendations({ intervalMs: 100 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const recommendation = runner.recommendationSummary();
  assert.equal(recommendation.running, true);
  assert.equal(recommendation.last_good.candidate_count, 1);
  assert.equal(recommendation.remaining_universe, 1);
  assert.equal(recommendation.projection_coverage, "2/2 eligible");
  assert.equal(recommendation.own_roster_count, 1);
  assert.equal(recommendation.current_pick, 2);
  assert.equal(recommendation.clock_seconds, 30);
  assert.equal(recommendation.last.candidates[0].player_id, "p2");
  assert.deepEqual(recommendation.last.candidates[0].eligibility, ["F"]);
  runner.stopRecommendations();
  assert.equal(runner.recommendationSummary().running, false);
  assert.deepEqual(io.calls.map((call) => call.operation), ["status", "recommend-preview"]);
});

test("recommendation summary marks an old last-good frame stale at read time", async () => {
  let clock = Date.now();
  const io = transport();
  const factory = runnerStub(io);
  const runner = makeRunner(io, factory, { mode: "recommendation", now: () => clock });
  runner.startRecommendations({ intervalMs: 100 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(runner.recommendationSummary().stale, false);
  clock += 10_001;
  assert.equal(runner.recommendationSummary().stale, true);
  runner.stopRecommendations();
});

test("recommendation feed validation freezes on a gap instead of dropping history", () => {
  const invalid = frame();
  invalid.history = [
    { pick_number: 1, team: OWN, player_id: "p1", player_name: "Owned Player" },
    { pick_number: 3, team: ORDER[2], player_id: "p2", player_name: "Remaining Player" },
  ];
  invalid.current_pick = 4;
  assert.equal(validateRecommendationFrame(invalid, OWN).ok, false);
  assert.match(validateRecommendationFrame(invalid, OWN).reason, /gap|out-of-order/);
});

test("recommendation validation waits for roster hydration and freezes a regressed feed", async () => {
  const rosterLag = frame();
  rosterLag.roster = [];
  assert.match(validateRecommendationFrame(rosterLag, OWN).reason, /reconciling/);

  const io = transport();
  const initial = frame();
  const regressed = { ...initial, history: [], roster: [], current_pick: 1, revision: 2 };
  const factory = runnerStub(io, [initial, regressed]);
  const runner = makeRunner(io, factory, { mode: "recommendation" });
  runner.startRecommendations({ intervalMs: 100 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  await new Promise((resolve) => setTimeout(resolve, 120));
  const summary = runner.recommendationSummary();
  assert.equal(summary.successes, 1);
  assert.match(summary.blocked_reason, /regressed|conflicted/);
  assert.equal(summary.last_good.candidate_count, 1);
  runner.stopRecommendations();
});

test("recommendation mode merges an expired fast snapshot without switching views", async () => {
  const io = transport();
  const initial = frame();
  const advanced = {
    ...initial,
    revision: 2,
    current_pick: 3,
    on_clock_team: ORDER[2],
    history: [
      ...initial.history,
      { pick_number: 2, team: ORDER[1], player_id: "p2", player_name: "Remaining Player" },
    ],
    complete: false,
    history_complete: false,
    warnings: ["retained_history_stale", "fast_capture_not_confirmable"],
  };
  let reads = 0;
  let viewSwitches = 0;
  const observer = {
    async captureFastFrame() {
      reads += 1;
      return reads === 1 ? initial : advanced;
    },
  };
  const switcher = {
    async showPickHistory() { viewSwitches += 1; },
    async showPlayers() { viewSwitches += 1; },
  };
  const runner = makeRunner(io, runnerStub(io, null, { observer, switcher }), { mode: "recommendation" });
  runner.startRecommendations({ intervalMs: 100 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  await new Promise((resolve) => setTimeout(resolve, 120));
  const summary = runner.recommendationSummary();
  assert.ok(summary.successes >= 2, summary);
  assert.equal(viewSwitches, 0);
  assert.equal(summary.last_good.current_pick, 3);
  runner.stopRecommendations();
});

test("wrong-team advisory evidence stays staged and cannot change drafted universe", async () => {
  const io = transport();
  const initial = frame();
  const wrongTeam = {
    ...initial,
    revision: 2,
    current_pick: 3,
    on_clock_team: ORDER[2],
    history: [
      ...initial.history,
      { pick_number: 2, team: ORDER[2], player_id: "p2", player_name: "Remaining Player" },
    ],
  };
  const runner = makeRunner(io, runnerStub(io, [initial, wrongTeam]), { mode: "recommendation" });
  runner.startRecommendations({ intervalMs: 100 });
  await new Promise((resolve) => setTimeout(resolve, 20));
  await new Promise((resolve) => setTimeout(resolve, 120));
  const summary = runner.recommendationSummary();
  assert.equal(summary.successes, 1);
  assert.match(summary.blocked_reason, /team order/);
  assert.equal(summary.last_good.global_drafted_count, 1);
  runner.stopRecommendations();
});

test("recommendation attachment reloads a valid cache without starting a catalog scan", () => {
  const storage = memoryStorage();
  const saved = writeInputCache(storage, ROOM, OWN, cacheInputs());
  assert.equal(saved.status, "saved");

  // A fresh practice league may have a different league ID while retaining
  // the same season/team scope. Constructing the recommendation runner must
  // consume the durable catalog synchronously and never touch a wheel.
  const runner = cachedRecommendationRunner(storage, { leagueId: "1000000018" });
  const summary = runner.summary();
  assert.equal(summary.recommendation.capture.source, "cache");
  assert.equal(summary.recommendation.capture.cache_status, "loaded");
  assert.equal(summary.recommendation.capture.captured_at, "2026-10-14T13:00:00.000Z");
  assert.equal(summary.recommendation.capture.catalog_players, CATALOG.length);
  assert.equal(runner.cacheSnapshot().catalog.length, CATALOG.length);
  assert.equal(Object.prototype.hasOwnProperty.call(runner.cacheSnapshot(), "feed"), false);
});

test("corrupt, partial, and different-scope cache records are ignored safely", () => {
  const room = ROOM;
  const key = inputCacheKey(room, OWN);
  const corrupt = memoryStorage({ [key]: "{not-json" });
  const corruptRunner = cachedRecommendationRunner(corrupt);
  assert.equal(corruptRunner.summary().recommendation.capture.source, null);
  assert.equal(corruptRunner.summary().recommendation.capture.cache_status, "ignored");

  const partialStorage = memoryStorage();
  const partial = writeInputCache(partialStorage, room, OWN, cacheInputs("2026-10-14T13:00:00.000Z", { catalog_complete: false }));
  assert.equal(partial.status, "rejected");
  assert.equal(readInputCache(partialStorage, room, OWN).status, "missing");

  const wrongScopeRecord = {
    schema: "fbl-advisor-input-cache",
    version: 1,
    scope: { season_id: ROOM.season_id, team_id: "999", own_team: OWN },
    source: "captured",
    captured_at: "2026-10-14T13:00:00.000Z",
    rules: RULES,
    catalog: CATALOG,
    catalog_complete: true,
  };
  const wrongScope = memoryStorage({ [key]: JSON.stringify(wrongScopeRecord) });
  const wrongScopeRunner = cachedRecommendationRunner(wrongScope);
  assert.equal(wrongScopeRunner.summary().recommendation.capture.cache_status, "ignored");
  assert.match(wrongScopeRunner.summary().recommendation.capture.cache_status, /ignored/);
});

test("only a successful complete rescan replaces cache and live feed fields never persist", () => {
  const storage = memoryStorage();
  const initial = writeInputCache(storage, ROOM, OWN, cacheInputs("2026-10-14T13:00:00.000Z"));
  assert.equal(initial.status, "saved");

  const rejected = writeInputCache(storage, ROOM, OWN, cacheInputs("2026-10-14T14:00:00.000Z", {
    catalog_complete: false,
    feed: [{ player_id: "live-pick" }],
    roster: [{ player_id: "live-roster" }],
    actions: [{ action_id: "secret-action" }],
    owner_token: "secret",
  }));
  assert.equal(rejected.status, "rejected");
  assert.equal(readInputCache(storage, ROOM, OWN).snapshot.captured_at, "2026-10-14T13:00:00.000Z");

  const replacement = writeInputCache(storage, ROOM, OWN, cacheInputs("2026-10-14T15:00:00.000Z", {
    feed: [{ player_id: "live-pick" }],
    roster: [{ player_id: "live-roster" }],
    actions: [{ action_id: "secret-action" }],
    owner_token: "secret",
  }));
  assert.equal(replacement.status, "saved");
  const raw = JSON.parse(storage.raw(inputCacheKey(ROOM, OWN)));
  assert.equal(raw.captured_at, "2026-10-14T15:00:00.000Z");
  assert.equal(Object.prototype.hasOwnProperty.call(raw, "feed"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(raw, "roster"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(raw, "actions"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(raw, "owner_token"), false);
});

test("localStorage failures leave recommendation attachment and manual capture usable", () => {
  const brokenStorage = {
    getItem() { throw new Error("storage denied"); },
    setItem() { throw new Error("storage denied"); },
  };
  const runner = cachedRecommendationRunner(brokenStorage);
  assert.equal(runner.summary().recommendation.capture.cache_status, "unavailable");
  assert.equal(writeInputCache(brokenStorage, ROOM, OWN, cacheInputs()).status, "unavailable");
});

