"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  HeartbeatError,
  createFetchPost,
  createHeartbeatPublisher,
  createMutationSource,
  createPageTab,
} = require("./heartbeat_publisher.js");

function manualScheduler() {
  const timers = [];
  return {
    timers,
    setIntervalFn: (fn, ms) => { timers.push({ fn, ms, kind: "interval", live: true }); return timers.length - 1; },
    clearIntervalFn: (id) => { timers[id].live = false; },
    setTimeoutFn: (fn, ms) => { timers.push({ fn, ms, kind: "timeout", live: true }); return timers.length - 1; },
    clearTimeoutFn: (id) => { timers[id].live = false; },
    fire(kind) {
      for (const timer of timers.filter((t) => t.kind === kind && t.live)) {
        if (kind === "timeout") timer.live = false;
        timer.fn();
      }
    },
  };
}

const frame = (extra = {}) => ({ complete: true, current_pick: 7, on_clock_team: "Opponent", autopick: false, observed_at: new Date(1_000_000).toISOString(), ...extra });
const flush = () => new Promise((resolve) => setImmediate(resolve));

function harness(options = {}) {
  const scheduler = manualScheduler();
  const posts = [];
  let mutate = null;
  const publisher = createHeartbeatPublisher({
    capture: options.capture || (async () => frame()),
    post: options.post || (async (body) => { posts.push(body); return { status: "touched", state: "running", revision: 1 }; }),
    getVisibility: options.getVisibility || (() => "visible"),
    observeMutations: (cb) => { mutate = cb; return () => { mutate = null; }; },
    now: options.now || (() => 1_000_500),
    ...scheduler,
    ...options.rest,
  });
  return { publisher, scheduler, posts, mutate: () => mutate() };
}

test("a simulated Feed mutation publishes within 2 s and well inside the freshness guard", async () => {
  const h = harness();
  h.publisher.start();
  await flush();
  assert.equal(h.posts.length, 1, "start publishes immediately");
  h.mutate();
  const pending = h.scheduler.timers.filter((t) => t.kind === "timeout" && t.live);
  assert.equal(pending.length, 1);
  assert.ok(pending[0].ms <= 2000, `mutation publish delay ${pending[0].ms}ms`);
  h.scheduler.fire("timeout");
  await flush();
  assert.equal(h.posts.length, 2);
  assert.equal(h.posts[1].trigger, "mutation");
});

test("a burst of mutations coalesces into one publish", async () => {
  const h = harness();
  h.publisher.start();
  await flush();
  h.mutate(); h.mutate(); h.mutate();
  assert.equal(h.scheduler.timers.filter((t) => t.kind === "timeout" && t.live).length, 1);
  h.scheduler.fire("timeout");
  await flush();
  assert.equal(h.posts.length, 2);
});

test("the interval fallback is at most 2 s and publishes without any mutation", async () => {
  const h = harness();
  h.publisher.start();
  const interval = h.scheduler.timers.find((t) => t.kind === "interval");
  assert.ok(interval.ms <= 2000);
  await flush();
  h.scheduler.fire("interval");
  await flush();
  assert.equal(h.posts.length, 2);
  assert.throws(() => harness({ rest: { intervalMs: 5000 } }), (error) => error instanceof HeartbeatError && error.code === "invalid_interval");
});

test("a real timer mutation publishes within 2 s", async () => {
  const posts = [];
  let mutate;
  const publisher = createHeartbeatPublisher({
    capture: async () => frame(),
    post: async (body) => { posts.push([Date.now(), body]); return { status: "touched", state: "running" }; },
    observeMutations: (cb) => { mutate = cb; return () => {}; },
    intervalMs: 2000,
  });
  publisher.start();
  await flush();
  const before = posts.length;
  const mutatedAt = Date.now();
  mutate();
  await new Promise((resolve) => setTimeout(resolve, 400));
  publisher.stop();
  assert.equal(posts.length, before + 1);
  assert.ok(posts[before][0] - mutatedAt < 2000);
});

test("nothing is sent while the frame is incomplete", async () => {
  const h = harness({ capture: async () => frame({ complete: false }) });
  h.publisher.start();
  await flush();
  h.mutate();
  h.scheduler.fire("timeout");
  h.scheduler.fire("interval");
  await flush();
  assert.equal(h.posts.length, 0);
  assert.ok(h.publisher.stats().skipped_incomplete >= 2);
  const missing = harness({ capture: async () => null });
  missing.publisher.start();
  await flush();
  assert.equal(missing.posts.length, 0);
});

test("capture and post failures are contained and counted", async () => {
  const bad = harness({ capture: async () => { throw new Error("dom unavailable"); } });
  bad.publisher.start();
  await flush();
  assert.equal(bad.publisher.stats().capture_errors, 1);
  const down = harness({ post: async () => { throw new Error("connection refused"); } });
  down.publisher.start();
  await flush();
  assert.equal(down.publisher.stats().post_errors, 1);
  assert.equal(down.publisher.last().status, "post_error");
});

test("each post reports the document visibility state", async () => {
  let visibility = "visible";
  const h = harness({ getVisibility: () => visibility });
  h.publisher.start();
  await flush();
  visibility = "hidden";
  h.scheduler.fire("interval");
  await flush();
  assert.deepEqual(h.posts.map((p) => p.visibility_state), ["visible", "hidden"]);
});

test("overlapping ticks never run two captures at once", async () => {
  let release;
  let active = 0;
  let maxActive = 0;
  const h = harness({
    capture: () => new Promise((resolve) => { active += 1; maxActive = Math.max(maxActive, active); release = () => { active -= 1; resolve(frame()); }; }),
  });
  h.publisher.start();
  h.publisher.tick("interval");
  h.publisher.tick("interval");
  assert.equal(maxActive, 1);
  release();
  await flush();
});

function dispatchHarness({ enabled = true, ack = { status: "touched", state: "running" }, frameExtra = {}, now, visibility = "visible" } = {}) {
  const turns = [];
  const runtime = { runTurn: async (options) => { turns.push(options); return { outcome: "confirmed" }; } };
  const h = harness({
    capture: async () => frame({ on_clock_team: "Example Team", ...frameExtra }),
    post: async () => ack,
    getVisibility: () => visibility,
    now,
    rest: { guardedDispatch: { enabled, runtime, sessionId: "s1", ownTeam: "Example Team" } },
  });
  return { ...h, turns };
}

test("guarded dispatch is off by default: no turn is ever run", async () => {
  const off = dispatchHarness({ enabled: false });
  off.publisher.start();
  await flush();
  assert.equal(off.publisher.guardedDispatchEnabled, false);
  assert.equal(off.turns.length, 0);
  const unset = harness({ capture: async () => frame({ on_clock_team: "Example Team" }) });
  unset.publisher.start();
  await flush();
  assert.equal(unset.publisher.guardedDispatchEnabled, false);
});

test("enabled guarded dispatch runs one runTurn per pick through the unchanged runtime path", async () => {
  const h = dispatchHarness();
  h.publisher.start();
  await flush();
  h.scheduler.fire("interval");
  h.scheduler.fire("interval");
  await flush();
  assert.equal(h.turns.length, 1, "one attempt per pick");
  assert.equal(h.turns[0].sessionId, "s1");
  assert.equal(h.turns[0].expectedPick, 7);
  assert.equal(h.turns[0].expectedTeam, "Example Team");
  assert.equal(h.turns[0].observationAlreadyPublished, true);
});

test("guarded dispatch never runs on a stale frame, a hidden tab, or a non-running coordinator", async () => {
  const stale = dispatchHarness({ now: () => 1_000_000 + 10_001 });
  stale.publisher.start();
  await flush();
  assert.equal(stale.turns.length, 0, "frame older than the freshness guard");
  const hidden = dispatchHarness({ visibility: "hidden" });
  hidden.publisher.start();
  await flush();
  assert.equal(hidden.turns.length, 0);
  for (const state of ["degraded", "paused", "observing"]) {
    const h = dispatchHarness({ ack: { status: "touched", state } });
    h.publisher.start();
    await flush();
    assert.equal(h.turns.length, 0, state);
  }
  const ignored = dispatchHarness({ ack: { status: "ignored", state: "running" } });
  ignored.publisher.start();
  await flush();
  assert.equal(ignored.turns.length, 0);
});

test("guarded dispatch ignores opponent turns and autopick-on frames", async () => {
  const opponent = dispatchHarness({ frameExtra: { on_clock_team: "Opponent" } });
  opponent.publisher.start();
  await flush();
  assert.equal(opponent.turns.length, 0);
  const autopick = dispatchHarness({ frameExtra: { autopick: true } });
  autopick.publisher.start();
  await flush();
  assert.equal(autopick.turns.length, 0);
});

test("a runtime failure is recorded and the pick is not retried", async () => {
  const turns = [];
  const h = harness({
    capture: async () => frame({ on_clock_team: "Example Team" }),
    rest: { guardedDispatch: { enabled: true, sessionId: "s1", ownTeam: "Example Team", runtime: { runTurn: async () => { turns.push(1); throw new Error("boom"); } } } },
  });
  h.publisher.start();
  await flush();
  h.scheduler.fire("interval");
  await flush();
  assert.equal(turns.length, 1);
  assert.equal(h.publisher.lastDispatch().outcome, "not-attempted");
});

test("the fetch poster sends the CSRF token as JSON and surfaces rejections", async () => {
  const calls = [];
  const okPost = createFetchPost({
    csrfToken: "tok",
    fetchFn: async (url, init) => { calls.push([url, init]); return { ok: true, json: async () => ({ status: "touched" }) }; },
  });
  assert.deepEqual(await okPost({ observation: {} }), { status: "touched" });
  assert.equal(calls[0][0], "http://127.0.0.1:8765/api/heartbeat");
  assert.equal(calls[0][1].headers["X-CSRF-Token"], "tok");
  assert.equal(calls[0][1].method, "POST");
  const badPost = createFetchPost({ csrfToken: "tok", fetchFn: async () => ({ ok: false, status: 403, json: async () => ({ error: "nope" }) }) });
  await assert.rejects(badPost({}), /heartbeat rejected: nope/);
  assert.throws(() => createFetchPost({ fetchFn: async () => ({}) }), /CSRF/);
});

test("the mutation source observes the Feed targets, else the body, and disconnects", () => {
  const observed = [];
  let disconnected = 0;
  class FakeObserver {
    constructor(cb) { this.cb = cb; FakeObserver.last = this; }
    observe(target, options) { observed.push([target, options]); }
    disconnect() { disconnected += 1; }
  }
  const feed = { id: "feed" };
  const root = { body: { id: "body" }, querySelector: (selector) => (selector === ".feed" ? feed : null) };
  let fired = 0;
  const stop = createMutationSource({ MutationObserverCtor: FakeObserver, root, selectors: [".feed", ".missing"] })(() => { fired += 1; });
  assert.equal(observed[0][0], feed);
  FakeObserver.last.cb();
  assert.equal(fired, 1);
  stop();
  assert.equal(disconnected, 1);
  createMutationSource({ MutationObserverCtor: FakeObserver, root })(() => {});
  assert.equal(observed[1][0], root.body);
});

test("runtime regression: a stale pre-prepare frame sends no request and clicks nothing", async () => {
  const { createOperatorRuntime, OUTCOMES } = require("./operator_runtime.js");
  const clock = 5_000_000;
  const requests = [];
  let clicks = 0;
  const runtime = createOperatorRuntime({
    now: () => clock,
    requestOperator: async (payload) => { requests.push(payload); return {}; },
    clickPlayer: async () => { clicks += 1; },
    // Dispatching runtimes must supply the #42 confirmation adapter; it is
    // never reached here because the stale frame is refused before prepare.
    postClickConfirmation: {
      observer: { captureHistory: async () => { throw new Error("unreachable"); }, captureFrame: async () => { throw new Error("unreachable"); } },
      showPickHistory: async () => {},
      showPlayers: async () => {},
    },
  });
  const staleFrame = frame({ session_id: "s1", on_clock_team: "Example Team", observed_at: new Date(clock - 10_001).toISOString() });
  const result = await runtime.runTurn({ sessionId: "s1", observation: staleFrame });
  assert.equal(result.outcome, OUTCOMES.NOT_ATTEMPTED);
  assert.match(result.reason, /old/);
  assert.equal(requests.length, 0);
  assert.equal(clicks, 0);
});

test("the page tab shim lets the observer evaluators run on the live DOM", async () => {
  const doc = { querySelector: (s) => ({ s }), querySelectorAll: (s) => [{ s }, { s }] };
  const tab = createPageTab({ document: doc, location: { href: "https://fantasy.espn.com/x" } });
  assert.equal(await tab.url(), "https://fantasy.espn.com/x");
  assert.deepEqual(await tab.playwright.locator("body").evaluate((node) => node.s), "body");
  assert.equal(await tab.playwright.locator("tr").evaluateAll((nodes) => nodes.length), 2);
});
