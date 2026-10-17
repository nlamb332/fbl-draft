"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { mountPageControls, normalizeSummary } = require("./page_controls.js");

test("reads the backend full remaining universe independently of scorable candidates", () => {
  const state = normalizeSummary({ remaining_universe: 344, last_good: { candidate_count: 230 } });
  assert.equal(state.universe.remaining, 344);
  assert.equal(normalizeSummary({ remaining_universe: 0 }).universe.remaining, 0);
});

test("close dismisses expanded or collapsed advisor and stops observation once", async () => {
  for (const collapsed of [false, true]) {
    const ui = setup();
    if (collapsed) ui.controls.collapse();
    const close = ui.nodes.find(node => node["aria-label"] === "Close advisor");
    assert.ok(close);
    await close.click();
    assert.equal(ui.controls.root.removed, true);
    assert.deepEqual(ui.calls, ["stopRecommendations"]);
    await ui.controls.refresh();
    await close.click();
    assert.deepEqual(ui.calls, ["stopRecommendations"]);
  }
});

test("close still dismisses the advisor if observation is already unavailable", async () => {
  const ui = setup();
  ui.runner.stopRecommendations = async () => { throw new Error("stale connection"); };
  await ui.nodes.find(node => node["aria-label"] === "Close advisor").click();
  assert.equal(ui.controls.root.removed, true);
});

function setup({ summary = {}, captureResult = {}, startResult = {}, stopResult = {}, refreshResult, inputs = null, runnerSummary = null, viewport = { width: 640, height: 360 } } = {}) {
  const nodes = [];
  const documentListeners = {};
  const document = {
    defaultView: { innerWidth: viewport.width, innerHeight: viewport.height },
    getElementById: id => nodes.find(node => node.id === id),
    createElement(tag) {
      const node = {
        tag,
        tagName: tag.toUpperCase(),
        id: "",
        textContent: "",
        children: [],
        style: {},
        disabled: false,
        setAttribute(name, value) { this[name] = String(value); },
        appendChild(child) { this.children.push(child); this.firstChild = this.children[0]; return child; },
        removeChild(child) { const index = this.children.indexOf(child); if (index >= 0) this.children.splice(index, 1); this.firstChild = this.children[0]; },
        addEventListener(event, handler) { this[event] = handler; },
        removeEventListener(event, handler) { if (this[event] === handler) delete this[event]; },
        setPointerCapture(pointerId) { this.capturedPointerId = pointerId; },
        releasePointerCapture(pointerId) { this.releasedPointerId = pointerId; },
        getBoundingClientRect() {
          if (this.id !== "fbl-attended-controls") return { left: 0, top: 0, width: 100, height: 24 };
          const compact = String(this.style.width || "").includes("380");
          const width = compact ? 380 : 460;
          const height = compact ? 74 : 420;
          const px = value => Number.parseFloat(String(value || ""));
          const left = Number.isFinite(px(this.style.left)) ? px(this.style.left) : viewport.width - width - (Number.isFinite(px(this.style.right)) ? px(this.style.right) : 0);
          const top = Number.isFinite(px(this.style.top)) ? px(this.style.top) : viewport.height - height - (Number.isFinite(px(this.style.bottom)) ? px(this.style.bottom) : 0);
          return { left, top, width, height, right: left + width, bottom: top + height };
        },
        remove() { this.removed = true; },
      };
      nodes.push(node);
      return node;
    },
    addEventListener(event, handler) { documentListeners[event] = handler; this[event] = handler; },
    removeEventListener(event, handler) { if (documentListeners[event] === handler) delete documentListeners[event]; if (this[event] === handler) delete this[event]; },
    body: { appendChild(node) { this.child = node; } },
  };
  const calls = [];
  let currentSummary = summary;
  const runner = {
    summary: () => runnerSummary || (inputs === null ? null : { inputs }),
    captureInputs: async () => { calls.push("captureInputs"); return captureResult; },
    startRecommendations: async () => { calls.push("startRecommendations"); return startResult; },
    stopRecommendations: async () => { calls.push("stopRecommendations"); return stopResult; },
    recommendationSummary: async () => { calls.push("recommendationSummary"); return currentSummary; },
    refreshRecommendations: async () => { calls.push("refreshRecommendations"); return refreshResult === undefined ? currentSummary : refreshResult; },
  };
  const controls = mountPageControls({ document, runner, bundleHash: "test", pollMs: 60_000, setIntervalFn: () => 1, clearIntervalFn() {} });
  const button = label => nodes.find(node => node.tag === "button" && node.textContent === label);
  const allText = node => [node.textContent || "", ...(node.children || []).map(allText)].join(" ");
  return { calls, controls, runner, document, nodes, button, allText, setSummary: value => { currentSummary = value; } };
}

test("capture progress and truncation evidence are visible without starting the actor", async () => {
  const ui = setup({ summary: { capture: { catalog_players: 460, catalog_complete: false, termination_reason: "player_limit" } } });
  let finish;
  ui.runner.captureInputs = () => new Promise(resolve => { finish = resolve; });
  const pending = ui.button("Capture inputs").click();
  assert.match(ui.allText(ui.controls.root), /Capturing/);
  assert.equal(ui.button("Start observing").disabled, true);
  finish({});
  await pending;
  assert.match(ui.allText(ui.controls.root), /player_limit/);
  assert.match(ui.allText(ui.controls.root), /460/);
  assert.equal(ui.calls.includes("startRecommendations"), false);
});

test("normalizes the recommendation contract while preserving zero values and unknown autopick", () => {
  const normalized = normalizeSummary({
    mode: "recommendation",
    running: true,
    next_turn_distance: 0,
    current_pick: 0,
    clock_seconds: 0,
    last_good: { candidate_count: 0, remaining_count: 0, feed_count: 0, catalog_count: 0 },
    last: { candidates: [{ name: "A'ja Wilson", score: 0, explanation: "best category fit" }] },
  });
  assert.equal(normalized.status, "observing");
  assert.deepEqual(normalized.nextTurn, { distance: 0, currentPick: 0, clock: 0, ownTeamTurn: undefined });
  assert.equal(normalized.universe.remaining, 0);
  assert.equal(normalized.recommendation.player, "A'ja Wilson");
  assert.equal(normalized.recommendation.score, 0);
  assert.equal(normalized.autopick, undefined);
  const unknownCounts = normalizeSummary({ mode: "recommendation", last_good: { candidate_count: 7 } });
  assert.equal(unknownCounts.universe.remaining, undefined);
  assert.equal(unknownCounts.universe.coverage, undefined);
  assert.equal(unknownCounts.rosterHealth, undefined);
});

test("preserves capture source and timestamp for cached-input messaging", () => {
  const normalized = normalizeSummary({ capture: { source: "cached", captured_at: "2026-10-14T14:05:00.000Z", catalog_players: 439 } });
  assert.deepEqual(normalized.captureMeta, { source: "cached", capturedAt: "2026-10-14T14:05:00.000Z" });
});

test("shows cached capture provenance in the notice and compact metadata", async () => {
  const ui = setup({
    inputs: { source: "cached", catalog_players: 439 },
    summary: { capture: { source: "cached", captured_at: "2026-10-14T14:05:00.000Z" }, last: { candidates: [] } },
  });
  await ui.controls.refresh();
  assert.match(ui.allText(ui.controls.root), /Inputs source cached/);
  ui.controls.collapse();
  const metadata = ui.nodes.find(node => node.title && node.title.includes("source cached"));
  assert.ok(metadata);
  assert.match(metadata.title, /captured/);
});

test("observation UI captures, starts, refreshes, and stops without actor methods", async () => {
  const ui = setup({
    summary: {
      mode: "recommendation",
      running: true,
      autopick: false,
      remaining_count: 417,
      projection_coverage: "95%",
      roster_health: "matched",
      feed_health: "healthy",
      next_turn: { distance: 3, current_pick: 13, clock: 76 },
      last_good: { candidate_count: 417, feed_count: 417, catalog_count: 439, drafted_count: 12, observed_at: "2026-10-13T18:30:00.000Z" },
      last: {
        generated_at: "2026-10-13T18:30:00.000Z",
        explanation: "strong rebounds and assists fit",
        candidates: [{ name: "Jalen Johnson", score: 88.4 }, { name: "Evan Mobley", score: 86.1 }, { name: "Scottie Barnes", score: 84.7 }],
      },
    },
  });

  const initialText = ui.allText(ui.controls.root);
  assert.match(initialText, /FBL draft advisor/);
  assert.match(initialText, /FBL only observes and recommends/);
  assert.doesNotMatch(initialText, /Activate|Resume|Recover|Run batch|Local Pause/);
  assert.equal(ui.button("Start observing").disabled, true);
  assert.equal(ui.button("Stop observing").disabled, true);

  await ui.button("Capture inputs").click();
  assert.deepEqual(ui.calls.slice(0, 2), ["captureInputs", "recommendationSummary"]);
  assert.equal(ui.button("Start observing").disabled, false);
  await ui.button("Start observing").click();
  assert.deepEqual(ui.calls.slice(2, 4), ["startRecommendations", "recommendationSummary"]);
  assert.equal(ui.button("Stop observing").disabled, false);
  assert.match(ui.allText(ui.controls.root), /Jalen Johnson/);
  assert.match(ui.allText(ui.controls.root), /Remaining universe/);
  assert.match(ui.allText(ui.controls.root), /417/);
  assert.match(ui.allText(ui.controls.root), /95%/);
  assert.match(ui.allText(ui.controls.root), /matched/);
  assert.doesNotMatch(ui.allText(ui.controls.root), /12 confirmed/);
  assert.match(ui.allText(ui.controls.root), /OFF · manual picks/);
  await ui.button("Refresh recommendation").click();
  assert.equal(ui.calls.at(-1), "refreshRecommendations");
  await ui.button("Stop observing").click();
  assert.equal(ui.calls.at(-2), "stopRecommendations");
  assert.equal(ui.button("Stop observing").disabled, true);
});

test("Scan/Rescan delegates to the lifecycle-aware scan when the runner provides it", async () => {
  const ui = setup();
  ui.runner.scanInputs = async () => {
    ui.calls.push("scanInputs");
    return { catalog_players: 12, catalog_complete: true };
  };
  await ui.button("Capture inputs").click();
  assert.equal(ui.calls.includes("scanInputs"), true);
  assert.equal(ui.calls.includes("captureInputs"), false);
});

test("pre-supplied inputs enable observation without forcing another capture", () => {
  const ui = setup({ inputs: { source: "supplied", catalog_players: 439 } });
  assert.equal(ui.button("Rescan players").disabled, false);
  assert.equal(ui.button("Start observing").disabled, false);
});

test("remounts an already-running observer as observing and labels cached inputs as a saved scan", async () => {
  const ui = setup({
    runnerSummary: {
      inputs: { source: "cache", captured_at: "2026-10-14T14:05:00.000Z", catalog_players: 439 },
      recommendation: {
        mode: "recommendation",
        running: true,
        capture: { source: "cache", captured_at: "2026-10-14T14:05:00.000Z" },
        next_turn: { distance: 1 },
      },
    },
  });
  assert.match(ui.allText(ui.controls.root), /Observing/);
  assert.match(ui.allText(ui.controls.root), /Loaded saved scan/);
  assert.equal(ui.button("Rescan players").disabled, true);
  assert.equal(ui.button("Start observing").disabled, true);
  assert.equal(ui.button("Stop observing").disabled, false);
  await ui.button("Stop observing").click();
  assert.equal(ui.button("Stop observing").disabled, true);
});

test("formats finite score and clock values and calls a zero-distance turn your turn", async () => {
  const ui = setup({ summary: {
    mode: "recommendation",
    running: true,
    next_turn: { distance: 0, current_pick: 0, clock: 95 },
    own_team_turn: true,
    last_good: { candidate_count: 0, feed_count: 0, catalog_count: 0, drafted_count: 0 },
    last: { candidates: [{ name: "Zero Value", score: 1.239 }] },
  } });
  await ui.button("Capture inputs").click();
  await ui.button("Start observing").click();
  const rendered = ui.allText(ui.controls.root);
  assert.match(rendered, /Zero Value · 1.24/);
  assert.match(rendered, /Your turn/);
  assert.match(rendered, /1:35/);
  assert.match(rendered, /Yes · your pick/);
  assert.match(rendered, /Roster health Unknown/);
});

test("keeps own-turn snapshots and feed events available in collapsed diagnostics", async () => {
  const ui = setup({ summary: {
    mode: "recommendation",
    running: true,
    own_turns: [{ pick: 13, player_name: "Jalen Johnson", policy_status: "advisory" }],
    diagnostics: [{ event: "feed-health", status: "healthy", pick: 13 }],
    last: { candidates: [] },
  } });
  await ui.button("Capture inputs").click();
  await ui.button("Start observing").click();
  const rendered = ui.allText(ui.controls.root);
  assert.match(rendered, /Diagnostics/);
  assert.match(rendered, /own_turns/);
  assert.match(rendered, /Jalen Johnson/);
  assert.match(rendered, /feed-health/);
  assert.doesNotMatch(rendered, /catalog_players|available_players/);
});

test("clean, stale, blocked, and error states remain readable", async () => {
  const ui = setup({ summary: { mode: "recommendation", running: true, stale: true, error: "feed lag", last: { candidates: [{ name: "Old pick", score: 99 }] } } });
  await ui.button("Capture inputs").click();
  await ui.controls.refresh();
  assert.match(ui.allText(ui.controls.root), /stale|Stale/i);
  assert.match(ui.allText(ui.controls.root), /feed lag/);
  assert.doesNotMatch(ui.allText(ui.controls.root), /Old pick|99/);

  ui.setSummary({ mode: "recommendation", running: true, blocked_reason: "coverage gap", last: { candidates: [{ name: "Old pick", score: 99 }] } });
  await ui.controls.refresh();
  assert.match(ui.allText(ui.controls.root), /Recommendation blocked/);
  assert.match(ui.allText(ui.controls.root), /coverage gap/);
  assert.doesNotMatch(ui.allText(ui.controls.root), /Old pick|99/);

  ui.runner.recommendationSummary = async () => { throw new Error("feed unavailable"); };
  await ui.controls.refresh();
  assert.match(ui.allText(ui.controls.root), /Recommendation unavailable/);
  assert.match(ui.allText(ui.controls.root), /feed unavailable/);
});

test("dispose clears polling and removes the panel", () => {
  let cleared;
  const ui = setup();
  // The fake timer id is deterministic in setup; dispose must own it.
  ui.controls.dispose();
  assert.equal(ui.controls.root.removed, true);
  assert.equal(ui.document.pointermove, undefined);
  assert.equal(ui.document.pointerup, undefined);
  assert.equal(cleared, undefined);
});

test("moves with pointer capture, clamps to the viewport, and docks without actor actions", () => {
  const ui = setup({ viewport: { width: 640, height: 360 } });
  const handle = ui.button("Move advisor");
  let prevented = false;
  handle.pointerdown({ button: 0, pointerId: 7, clientX: 520, clientY: 300, preventDefault() { prevented = true; } });
  assert.equal(ui.controls.root.capturedPointerId, 7);
  assert.equal(prevented, true);
  ui.document.pointermove({ pointerId: 7, clientX: -100, clientY: -100, preventDefault() {} });
  ui.document.pointerup({ pointerId: 7 });
  assert.equal(ui.controls.root.style.left, "0px");
  assert.equal(ui.controls.root.style.top, "0px");
  assert.equal(ui.calls.length, 0);

  handle.keydown({ key: "End", preventDefault() {} });
  assert.equal(ui.controls.root["data-fbl-dock"], "right");
  ui.button("Dock left").click();
  assert.equal(ui.controls.root["data-fbl-dock"], "left");
  assert.equal(ui.calls.length, 0);
});

test("collapse leaves a compact recommendation bar and keeps observing controls safe", async () => {
  const ui = setup({ summary: {
    mode: "recommendation",
    running: true,
    next_turn: { distance: 2 },
    last: { candidates: [{ name: "Compact player", score: 88 }] },
  } });
  await ui.button("Capture inputs").click();
  await ui.button("Start observing").click();
  const callsBeforeCollapse = ui.calls.length;
  ui.controls.collapse();
  assert.equal(ui.controls.root["data-fbl-collapsed"], "true");
  assert.equal(ui.nodes.find(node => node.id === "fbl-attended-compact-summary").style.display, "flex");
  assert.match(ui.allText(ui.controls.root), /Compact player/);
  assert.match(ui.allText(ui.controls.root), /2 picks away/);
  assert.equal(ui.nodes.find(node => node.tag === "div" && node.children.some(child => child.textContent === "Rescan players")).style.display, "none");
  assert.equal(ui.button("Stop observing").disabled, false);
  assert.equal(ui.calls.length, callsBeforeCollapse);
  assert.equal(ui.button("Collapse advisor").style.display, "none");
  assert.equal(ui.button("Show full advisor"), undefined);
  await ui.button("Expand advisor").click();
  assert.equal(ui.controls.root["data-fbl-collapsed"], "false");
  assert.equal(ui.nodes.find(node => node.id === "fbl-attended-compact-summary").style.display, "none");
  assert.equal(ui.calls.length, callsBeforeCollapse);
});

test("renders optional capture progress without exposing player IDs", async () => {
  const ui = setup();
  let finish;
  ui.runner.captureInputs = options => new Promise(resolve => {
    options.onProgress({ phase: "scanning", catalog_count: 123, progress: 0.42, steps: ["id-should-not-render"] });
    finish = resolve;
  });
  const pending = ui.button("Capture inputs").click();
  assert.match(ui.allText(ui.controls.root), /Scanning player IDs · 123 captured · 42%/);
  assert.doesNotMatch(ui.allText(ui.controls.root), /id-should-not-render/);
  finish({});
  await pending;
});

test("a new scan shows fresh progress instead of cached catalog metadata", async () => {
  const old = { capture: { source: "cache", catalog_players: 1095, captured_at: "2026-10-14T00:00:00Z" } };
  const ui = setup({ runnerSummary: { inputs: {}, recommendation: old } });
  let finish;
  let report;
  ui.runner.scanInputs = options => new Promise(resolve => { finish = resolve; report = options.onProgress; });
  const pending = ui.button("Rescan players").click();
  assert.match(ui.allText(ui.controls.root), /Preparing capture/);
  assert.doesNotMatch(ui.nodes.find(node => node.textContent.startsWith("Capture in progress")).textContent, /1095/);
  report({ phase: "waiting_for_start" });
  assert.match(ui.allText(ui.controls.root), /Waiting for practice draft to start/);
  report({ phase: "scan", catalog_count: 14 });
  const notice = ui.nodes.find(node => node.textContent.startsWith("Capture in progress"));
  assert.match(notice.textContent, /14 captured/);
  assert.doesNotMatch(notice.textContent, /1095/);
  finish({});
  await pending;
});
