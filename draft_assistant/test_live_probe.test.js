"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");

const { FixtureElement } = require("./dom_fixture.js");
const { buildProbeSnippet, captureLocatorContext, probeCoordinator, VIEW_PATTERNS } = require("./live_probe.js");
const { inspectNativeControls } = require("./native_activation.js");
const { resolveViewTab } = require("./view_switch.js");
const { EspnRoomSimulator } = require("./room_simulator.js");

const ORDER = ["Example Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
const CATALOG = Array.from({ length: 30 }, (_, index) => ({
  player_id: String(2000 + index), name: `Player ${index}`, eligibility: ["F"], stats: { PTS: 40 - index }, projected_gp: 70, draft_rank: index + 1,
}));

function countClicks(run) {
  const original = FixtureElement.prototype.click;
  let clicks = 0;
  FixtureElement.prototype.click = function counted(...args) { clicks += 1; return original.apply(this, args); };
  return Promise.resolve(run()).finally(() => { FixtureElement.prototype.click = original; }).then((value) => ({ value, clicks }));
}

test("probe locators run read-only against every room view", async () => {
  for (const view of ["players", "history", "league_manager"]) {
    const simulator = new EspnRoomSimulator({ catalog: CATALOG, order: ORDER, ownTeam: "Example Team", paused: true, view });
    const body = simulator.tab().playwright.locator("body");
    const { value, clicks } = await countClicks(async () => ({
      native: await body.evaluate(inspectNativeControls),
      players: await body.evaluate(resolveViewTab, { pattern: VIEW_PATTERNS.players, click: false }),
      history: await body.evaluate(resolveViewTab, { pattern: VIEW_PATTERNS.history, click: false }),
      context: await body.evaluate(captureLocatorContext),
    }));
    assert.equal(clicks, 0, `${view}: the probe must never click`);
    assert.equal(value.players.status, "unique");
    assert.equal(value.history.status, "unique");
    assert.ok(value.context.controls.some((control) => /^players$/i.test(control.name)), `${view}: context records the Players tab`);
    for (const control of value.context.controls) assert.ok(Array.isArray(control.ancestry));
  }
});

test("the snippet compiles, embeds the locators, and only resolves tabs with click disabled", () => {
  const snippet = buildProbeSnippet({ base: "http://127.0.0.1:8765", token: "t" });
  assert.doesNotThrow(() => new vm.Script(snippet));
  assert.match(snippet, /const inspectNativeControls = function inspectNativeControls/);
  assert.match(snippet, /resolveViewTab\(body, \{ pattern, click: false \}\)/);
  // The only click call in the snippet is resolveViewTab's guarded branch.
  assert.equal((snippet.match(/\.click\(/g) || []).length, (resolveViewTab.toString().match(/\.click\(/g) || []).length);
});

async function withPageGlobals({ fetchImpl, violation = null }, run) {
  const listeners = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch };
  globalThis.document = {
    addEventListener: (name, listener) => { listeners.push(listener); if (violation) listener(violation); },
    removeEventListener: () => {},
  };
  globalThis.fetch = fetchImpl;
  try {
    return await run();
  } finally {
    globalThis.document = saved.document;
    globalThis.fetch = saved.fetch;
  }
}

test("coordinator probe classifies ok, CSP, network, CORS, and token failures", async () => {
  const response = (status, type = "cors") => ({ status, type, json: async () => ({ error: "x" }) });
  const cases = [
    { name: "ok", fetchImpl: async (_url, init) => response(init.mode === "no-cors" ? 0 : 409, init.mode === "no-cors" ? "opaque" : "cors"), expect: "ok" },
    { name: "csp", violation: { violatedDirective: "connect-src", blockedURI: "http://127.0.0.1:8765" }, fetchImpl: async () => { throw new TypeError("Failed to fetch"); }, expect: "csp_blocked" },
    { name: "network", fetchImpl: async () => { throw new TypeError("Failed to fetch"); }, expect: "network_blocked" },
    { name: "cors", fetchImpl: async (_url, init) => { if (init.mode === "no-cors") return response(0, "opaque"); throw new TypeError("Failed to fetch"); }, expect: "cors_or_pna_blocked" },
    { name: "token", fetchImpl: async (_url, init) => response(init.mode === "no-cors" ? 0 : 403), expect: "token_or_origin_rejected" },
  ];
  for (const item of cases) {
    const result = await withPageGlobals(item, () => probeCoordinator({ base: "http://127.0.0.1:8765", token: "t", timeoutMs: 200 }));
    assert.equal(result.verdict, item.expect, item.name);
  }
});

test("the coordinator probe never sends an observation", async () => {
  const bodies = [];
  await withPageGlobals({ fetchImpl: async (_url, init) => { bodies.push(init.body); return { status: 409, type: "cors", json: async () => ({}) }; } }, () =>
    probeCoordinator({ base: "http://127.0.0.1:8765", token: "t", timeoutMs: 200 }));
  for (const body of bodies) assert.doesNotMatch(String(body), /observation/);
});
