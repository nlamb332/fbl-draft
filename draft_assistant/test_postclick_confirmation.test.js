"use strict";

// Issue #42: post-click confirmation must be history-then-Players, never a
// fast frame or retained history, and bounded by the observation budget.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const { createBrowserObserver } = require("./browser_observer.js");
const {
  OUTCOMES,
  createOperatorRuntime,
  createPostClickConfirmation,
  hasAuthoritativeEvidence,
} = require("./operator_runtime.js");
const { createDraftSim } = require("./fixtures/virtualized_draft_sim.js");

const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "postclick_virtualized_feed.json"), "utf8"));
const DIAGNOSTIC = JSON.parse(fs.readFileSync(path.join(__dirname, "validation", "canonical-release-2026-10-11", "postclick-dom-diagnostic-2026-10-12.json"), "utf8"));
const ROOM = { league_id: "1000000012", season_id: "2027", team_id: "15", practice: true };
const RULES = { teams: 14, rounds: 10, starting_slots: ["C", "G", "G", "F", "F", "UTIL"], bench_slots: 4, ir_slots: 3, scoring: { PTS: 1, REB: 1 } };
const OWN = FIXTURE.own_team;
const ALLEN = "4066328";

function catalogFor(history) {
  const players = history.map((event) => ({ player_id: event.player_id, name: event.player_name, eligibility: ["F"], stats: { FPTS: 50 }, projected_gp: 70 }));
  const fresh = [
    FIXTURE.history.find((event) => event.player_id === ALLEN),
    ...FIXTURE.history.filter((event) => event.pick_number >= 42),
  ].map((event) => ({ player_id: event.player_id, name: event.player_name, eligibility: ["F"], stats: { FPTS: 90 }, projected_gp: 78 }));
  const seen = new Set();
  return [...fresh, ...players, { player_id: "7001", name: "Avail Guard", eligibility: ["G"], stats: { FPTS: 40 }, projected_gp: 60 }]
    .filter((player) => !seen.has(player.player_id) && seen.add(player.player_id));
}

// Clock the observer, the adapter and the runtime all share; sleeping advances it.
function fakeClock(startIso = "2026-10-12T03:00:00.000Z") {
  let current = Date.parse(startIso);
  return { now: () => current, sleep: async (ms) => { current += ms; }, advance: (ms) => { current += ms; } };
}

function makeSim({ clock, hydrate, upTo = 40, afterOwnPick = () => 2 } = {}) {
  const history = FIXTURE.history.filter((event) => event.pick_number <= upTo);
  // Towns and Duren carry titles in the diagnostic; Allen's cell has none.
  const titled = new Set(["3136195", "4683021"]);
  return createDraftSim({
    room: ROOM,
    rules: RULES,
    teams: FIXTURE.team_order,
    ownTeam: OWN,
    catalog: catalogFor(FIXTURE.history.filter((event) => event.pick_number <= upTo + 3)),
    history,
    hydrate,
    afterOwnPick,
    rosterTitles: (id, name) => (titled.has(id) ? name : ""),
    now: clock.now,
  });
}

function command() {
  return {
    type: "select_player",
    action_id: "a1",
    session_id: "session-1",
    player_id: ALLEN,
    player_name: "Jarrett Allen",
    expected_pick: 41,
    expected_team: OWN,
    revision: 7,
  };
}

function operatorResponses(operations) {
  return async (payload) => {
    operations.push(payload.operation + (payload.status ? `:${payload.status}` : ""));
    if (payload.operation === "prepare") return { operation: "prepare", status: "command_ready", command: command() };
    if (payload.operation === "ack" && payload.status === "submitted") return { operation: "ack", status: "submitted", action_id: "a1" };
    if (payload.operation === "reconcile") return { operation: "reconcile", status: "reconciled", actions: [{ action_id: "a1", status: "confirmed" }] };
    if (payload.operation === "ack") return { operation: "ack", status: payload.status, action_id: "a1" };
    throw new Error(`unexpected operation ${payload.operation}`);
  };
}

function runtimeFor(sim, clock, operations) {
  return createOperatorRuntime({
    requestOperator: operatorResponses(operations),
    observeCurrent: async () => sim.beforeClickFrame(),
    clickPlayer: (picked) => sim.click(picked),
    postClickConfirmation: { observer: sim.observer, showPickHistory: () => sim.showPickHistory(), showPlayers: () => sim.showPlayers(), sleepFn: clock.sleep },
    now: clock.now,
  });
}

test("diagnostic fixture: 31-row virtualized Feed against a 43-pick history rejects the fast frame and confirms history-then-Players", async () => {
  const clock = fakeClock();
  const sim = makeSim({ clock });
  const operations = [];
  const runtime = runtimeFor(sim, clock, operations);
  const before = await sim.beforeClickFrame();
  assert.equal(before.current_pick, 41);

  const result = await runtime.runTurn({ sessionId: "session-1", observation: before });
  assert.equal(sim.events.length, 43);
  assert.equal(FIXTURE.feed.length, DIAGNOSTIC.raw_dom.players_view.feed_total);
  assert.equal(result.outcome, OUTCOMES.CONFIRMED, JSON.stringify(result.detail || result.reason));
  assert.equal(sim.stats.clicks, 1);
  assert.equal(sim.stats.bodyEvaluates, 0, "confirmation never reads the fast frame");
  // Switches to Pick History first, then Players, after the pre-click frame.
  assert.deepEqual(sim.stats.views.slice(-2), ["history", "players"]);
  assert.equal(result.after.history.length, 43);
  assert.equal(result.after.history_complete, true);
  assert.ok(result.after.roster.some((player) => player.player_id === ALLEN), "Allen resolved from an abbreviated cell without a title");
  assert.ok(result.after.warnings.every((warning) => !/^retained_history_|^fast_capture_/.test(warning)));
  assert.deepEqual(operations, ["prepare", "ack:submitted", "reconcile"]);

  // The same room read through the fast frame is virtualized and unconfirmable.
  clock.advance(15_000); // retained history is now stale, as in the diagnostic
  const fast = await sim.observer.captureFastFrame();
  assert.equal(fast.complete, false);
  assert.equal(fast.history.length, DIAGNOSTIC.raw_dom.players_view.feed_total);
  assert.ok(fast.warnings.includes("retained_history_incomplete"));
  assert.ok(fast.warnings.includes("fast_capture_not_confirmable"));
  assert.equal(hasAuthoritativeEvidence(command(), fast, { now: clock.now }).ok, false);
});

test("a fast frame is rejected as evidence even when it claims to be complete", async () => {
  const clock = fakeClock();
  const sim = makeSim({ clock });
  await sim.click({ player_id: ALLEN });
  const fast = await sim.observer.captureFastFrame();
  for (const doctored of [
    { ...fast, complete: true, history_complete: true },
    { ...fast, complete: true, history_complete: true, warnings: ["retained_history_stale"] },
  ]) {
    const verdict = hasAuthoritativeEvidence(command(), doctored, { now: clock.now });
    assert.equal(verdict.ok, false);
    assert.match(verdict.reason, /not confirmable/);
  }
});

test("Pick History grids that hydrate late confirm on the third poll within budget", async () => {
  const clock = fakeClock();
  // 41 picks fill 3 grids: only 2 render for the first two polls.
  const sim = makeSim({ clock, hydrate: (read, count) => (read <= 2 ? Math.min(2, count) : count), afterOwnPick: () => 0 });
  const operations = [];
  const runtime = runtimeFor(sim, clock, operations);
  const before = await sim.beforeClickFrame();
  const startedAt = clock.now();

  const result = await runtime.runTurn({ sessionId: "session-1", observation: before });
  assert.equal(result.outcome, OUTCOMES.CONFIRMED, JSON.stringify(result.detail || result.reason));
  assert.equal(sim.stats.historyReads - sim.stats.historyReadsAtClick, 3);
  assert.ok(clock.now() - startedAt < 10_000);
  assert.equal(sim.stats.clicks, 1);
});

test("budget exhaustion leaves the action uncertain with zero additional clicks", async () => {
  const clock = fakeClock();
  const sim = makeSim({ clock, hydrate: (_read, count) => Math.min(1, count), afterOwnPick: () => 0 });
  const operations = [];
  const runtime = runtimeFor(sim, clock, operations);
  const before = await sim.beforeClickFrame();
  const startedAt = clock.now();

  const result = await runtime.runTurn({ sessionId: "session-1", observation: before });
  assert.equal(result.outcome, OUTCOMES.UNCERTAIN);
  assert.match(result.reason, /budget .* exhausted/);
  assert.equal(sim.stats.clicks, 1, "no retry click");
  assert.equal(sim.stats.bodyEvaluates, 0);
  assert.ok(clock.now() - startedAt <= 10_000, "polling stayed inside the observation budget");
  assert.ok(!operations.includes("reconcile"));
  assert.deepEqual(operations.filter((operation) => operation === "prepare"), ["prepare"]);
  assert.ok(operations.includes("ack:uncertain"));
});

test("a pick that lands between the History snapshot and the Players read triggers another History poll, never a click", async () => {
  const clock = fakeClock();
  const sim = makeSim({ clock, afterOwnPick: () => 0 });
  await sim.click({ player_id: ALLEN });
  let raced = false;
  const original = sim.observer.captureFrame;
  const observer = { captureHistory: (options) => sim.observer.captureHistory(options), captureFrame: async (options) => { if (!raced) { raced = true; sim.advanceToOwnTurn(sim.events.length + 1); } return original(options); } };
  const racing = createPostClickConfirmation({ observer, showPickHistory: () => sim.showPickHistory(), showPlayers: () => sim.showPlayers(), now: clock.now, sleepFn: clock.sleep });
  const frame = await racing({ command: command() });
  assert.equal(frame.history_complete, true);
  assert.equal(frame.history.length, sim.events.length);
  assert.equal(sim.stats.clicks, 1);
});

test("abbreviated roster cell resolves by headshot ID from the diagnostic fixture (Allen: 4066328)", async () => {
  const allenCell = DIAGNOSTIC.raw_dom.players_view.roster_cells.find((cell) => cell.text === "J. Allen");
  const headshot = DIAGNOSTIC.raw_dom.players_view.feed_tail.find((row) => row.player_name === "Jarrett Allen").headshot_id;
  assert.equal(headshot, ALLEN);
  const clock = fakeClock();
  const sim = makeSim({ clock, upTo: 43 });
  await sim.showPickHistory();
  const history = (await sim.observer.captureHistory({ expectedCurrentPick: 44 })).history;
  await sim.showPlayers();
  const rosterOf = async (rows) => {
    const tab = { ...sim.tab, playwright: { ...sim.tab.playwright, locator: (selector) => selector === "table" ? { evaluateAll: async () => [{ headers: ["POS", "PLAYER"], rows }] } : sim.tab.playwright.locator(selector) } };
    const observer = createBrowserObserver({ tab, sessionId: "session-1", room: ROOM, freshlyVerifiedRules: RULES, projectionCatalog: catalogFor(FIXTURE.history), counter: { n: 0 }, now: clock.now });
    return observer.readRoster({ history });
  };

  // Title attribute missing: the headshot ID alone resolves the roster slot.
  const byHeadshot = await rosterOf([{ cells: ["UTIL", allenCell.text], full_title: allenCell.title || "", player_id: headshot }]);
  assert.deepEqual(byHeadshot.roster.map((player) => player.player_id), [ALLEN]);
  assert.ok(!byHeadshot.warnings.includes("roster_full_title_missing"));

  // No headshot either: a unique abbreviation against catalog and history resolves.
  const byAbbreviation = await rosterOf([{ cells: ["UTIL", "J. Allen"], full_title: "", player_id: null }]);
  assert.deepEqual(byAbbreviation.roster.map((player) => player.player_id), [ALLEN]);

  // A headshot that contradicts the abbreviation never resolves.
  const contradictory = await rosterOf([{ cells: ["UTIL", "J. Allen"], full_title: "", player_id: "5061575" }]);
  assert.deepEqual(contradictory.roster, []);
  assert.ok(contradictory.warnings.includes("roster_full_title_missing"));
});

test("two catalog players sharing one abbreviation stay unresolved without a headshot", async () => {
  const clock = fakeClock();
  const sim = makeSim({ clock, upTo: 43 });
  await sim.showPickHistory();
  const history = (await sim.observer.captureHistory({ expectedCurrentPick: 44 })).history;
  await sim.showPlayers();
  const twins = [...catalogFor(FIXTURE.history), { player_id: "8002", name: "Jaylen Allen", eligibility: ["G"], stats: { FPTS: 30 }, projected_gp: 50 }];
  const rosterOf = async (rows) => {
    const tab = { ...sim.tab, playwright: { ...sim.tab.playwright, locator: (selector) => selector === "table" ? { evaluateAll: async () => [{ headers: ["POS", "PLAYER"], rows }] } : sim.tab.playwright.locator(selector) } };
    const observer = createBrowserObserver({ tab, sessionId: "session-1", room: ROOM, freshlyVerifiedRules: RULES, projectionCatalog: twins, counter: { n: 0 }, now: clock.now });
    return observer.readRoster({ history });
  };
  const ambiguous = await rosterOf([{ cells: ["UTIL", "J. Allen"], full_title: "", player_id: null }]);
  assert.deepEqual(ambiguous.roster, []);
  assert.ok(ambiguous.warnings.some((warning) => warning.startsWith("roster_abbreviation_ambiguous_")));
  assert.ok(ambiguous.warnings.includes("roster_full_title_missing"));
  // The headshot ID still disambiguates the same abbreviation.
  const withHeadshot = await rosterOf([{ cells: ["UTIL", "J. Allen"], full_title: "", player_id: ALLEN }]);
  assert.deepEqual(withHeadshot.roster.map((player) => player.player_id), [ALLEN]);
});

test("regression: no confirmation code path can reach the fast frame", async () => {
  // Structural: the adapter source never names either fast-capture entry point.
  const source = fs.readFileSync(path.join(__dirname, "operator_runtime.js"), "utf8");
  assert.doesNotMatch(source, /captureFastFrame|fastCapture/);

  // Behavioural: a trap observer proves the adapter and the runtime only ever
  // call captureHistory and captureFrame.
  const clock = fakeClock();
  const sim = makeSim({ clock });
  const calls = [];
  const trap = (name) => () => { calls.push(name); throw new Error(`${name} is forbidden on the confirmation path`); };
  const observer = {
    captureHistory: (options) => { calls.push("captureHistory"); return sim.observer.captureHistory(options); },
    captureFrame: (options) => { calls.push("captureFrame"); return sim.observer.captureFrame(options); },
    captureFastFrame: trap("captureFastFrame"),
    fastCapture: trap("fastCapture"),
  };
  const operations = [];
  const runtime = createOperatorRuntime({
    requestOperator: operatorResponses(operations),
    observeCurrent: async () => sim.beforeClickFrame(),
    clickPlayer: (picked) => sim.click(picked),
    postClickConfirmation: { observer, showPickHistory: () => sim.showPickHistory(), showPlayers: () => sim.showPlayers(), sleepFn: clock.sleep },
    now: clock.now,
  });
  const result = await runtime.runTurn({ sessionId: "session-1", observation: await sim.beforeClickFrame() });
  assert.equal(result.outcome, OUTCOMES.CONFIRMED);
  assert.ok(calls.length >= 2);
  assert.ok(calls.every((name) => name === "captureHistory" || name === "captureFrame"), calls.join(","));
  assert.equal(calls[calls.length - 1], "captureFrame");

  // The adapter refuses observers or view switchers it cannot drive safely.
  assert.throws(() => createPostClickConfirmation({ observer: { captureFastFrame() {} }, showPickHistory() {}, showPlayers() {} }), /captureHistory and captureFrame/);
  assert.throws(() => createPostClickConfirmation({ observer, showPlayers() {} }), /view switchers/);
});

test("confirmation passes the exact History snapshot to captureFrame and never reuses retained history", async () => {
  const clock = fakeClock();
  const sim = makeSim({ clock, afterOwnPick: () => 1 });
  await sim.click({ player_id: ALLEN });
  // A stale retained snapshot from before the click must not satisfy anything.
  const stale = await sim.observer.captureHistory({ expectedCurrentPick: 41 });
  sim.observer.setRetainedHistory({ ...stale, history: stale.history.slice(0, 20), history_complete: true });
  let handed = null;
  const observer = {
    captureHistory: (options) => sim.observer.captureHistory(options),
    captureFrame: (options) => { handed = options.historySnapshot; return sim.observer.captureFrame(options); },
  };
  const adapter = createPostClickConfirmation({ observer, showPickHistory: () => sim.showPickHistory(), showPlayers: () => sim.showPlayers(), now: clock.now, sleepFn: clock.sleep });
  const frame = await adapter({ command: command() });
  assert.ok(handed && handed.history_complete === true);
  assert.equal(handed.history.length, 42);
  assert.deepEqual(frame.history, handed.history);
});
