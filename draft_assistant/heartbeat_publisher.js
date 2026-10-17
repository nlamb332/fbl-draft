"use strict";

/**
 * In-page heartbeat publisher for the attended ESPN draft room.
 *
 * Frames used to depend on the agent's tool-call cadence, which regularly
 * exceeds the coordinator's 10 s freshness guard.  This loop runs inside the
 * ESPN page: a MutationObserver on the Feed/clock plus an interval fallback
 * (<= 2 s) capture a frame with the page's own observer and POST it to the
 * local coordinator's /api/heartbeat endpoint with no agent involvement.
 *
 * Only COMPLETE frames are sent.  An incomplete frame sends nothing and so
 * never refreshes freshness.  Each post reports document.visibilityState,
 * because a background tab throttles timers (the tab must stay visible).
 *
 * Optional guarded dispatch (default OFF, needs `guardedDispatch.enabled`):
 * after a post the coordinator acknowledges as `running`, on our own turn,
 * the publisher hands the frame to an existing operator runtime's runTurn.
 * That is the unchanged claim -> dispatch -> confirm path (prepare, final
 * guard, one click, submitted ack, post-click confirmation, reconcile); the
 * runtime must be created with its own postClickConfirmation.  The publisher
 * attempts each pick at most once and never when the frame is stale, the tab
 * is hidden, or the coordinator is not `running`.
 *
 * Like the other browser modules, the CommonJS export wrapper is for tests;
 * the plain definitions are what a browser session evaluates.
 */

const DEFAULT_INTERVAL_MS = 2_000;
const DEFAULT_DEBOUNCE_MS = 150;
const DEFAULT_MAX_DISPATCH_FRAME_AGE_MS = 10_000;
const DEFAULT_POST_TIMEOUT_MS = 5_000;

class HeartbeatError extends Error {
  constructor(message, code = "heartbeat_error") {
    super(message);
    this.name = "HeartbeatError";
    this.code = code;
  }
}

function frameAgeMs(frame, nowMs) {
  const observed = Date.parse(frame && frame.observed_at);
  return Number.isFinite(observed) ? nowMs - observed : Number.NaN;
}

function createHeartbeatPublisher({
  capture,
  post,
  getVisibility = () => (typeof document !== "undefined" ? document.visibilityState : "unknown"),
  observeMutations = null,
  setIntervalFn = (typeof setInterval === "function" ? setInterval : null),
  clearIntervalFn = (typeof clearInterval === "function" ? clearInterval : null),
  setTimeoutFn = (typeof setTimeout === "function" ? setTimeout : null),
  clearTimeoutFn = (typeof clearTimeout === "function" ? clearTimeout : null),
  now = () => Date.now(),
  intervalMs = DEFAULT_INTERVAL_MS,
  debounceMs = DEFAULT_DEBOUNCE_MS,
  maxDispatchFrameAgeMs = DEFAULT_MAX_DISPATCH_FRAME_AGE_MS,
  guardedDispatch = null,
} = {}) {
  if (typeof capture !== "function") throw new HeartbeatError("a capture callback is required", "missing_capture");
  if (typeof post !== "function") throw new HeartbeatError("a post callback is required", "missing_post");
  if (!(intervalMs > 0) || intervalMs > DEFAULT_INTERVAL_MS) throw new HeartbeatError("intervalMs must be in (0, 2000]", "invalid_interval");

  const dispatchEnabled = Boolean(guardedDispatch && guardedDispatch.enabled === true && guardedDispatch.runtime && typeof guardedDispatch.runtime.runTurn === "function" && guardedDispatch.sessionId && guardedDispatch.ownTeam);
  const attemptedPicks = new Set();
  const stats = { ticks: 0, published: 0, skipped_incomplete: 0, capture_errors: 0, post_errors: 0, dispatch_attempts: 0 };
  let timer = null;
  let debounceTimer = null;
  let disconnect = null;
  let inFlight = false;
  let rerun = false;
  let last = null;
  let lastDispatch = null;

  async function maybeDispatch(frame, ack, visibility, postedAt) {
    if (!dispatchEnabled) return;
    if (!ack || ack.state !== "running" || (ack.status !== "touched" && ack.status !== "ingested")) return;
    if (visibility !== "visible") return;
    if (frame.on_clock_team !== guardedDispatch.ownTeam || frame.autopick !== false) return;
    const pick = frame.current_pick;
    if (!Number.isInteger(pick) || attemptedPicks.has(pick)) return;
    // Never act on a frame older than the freshness guard, measured at the
    // moment of dispatch (a slow post must not smuggle a stale frame through).
    const age = frameAgeMs(frame, now());
    if (!Number.isFinite(age) || age < 0 || age > maxDispatchFrameAgeMs) return;
    attemptedPicks.add(pick);
    stats.dispatch_attempts += 1;
    try {
      lastDispatch = await guardedDispatch.runtime.runTurn({
        sessionId: guardedDispatch.sessionId,
        observation: frame,
        expectedPick: pick,
        expectedTeam: guardedDispatch.ownTeam,
        // The heartbeat already delivered this board; the coordinator's own
        // latest observation (same board, possibly renumbered) is the one
        // claimed, so prepare must not re-send the page-numbered frame.
        observationAlreadyPublished: true,
      });
    } catch (error) {
      lastDispatch = { outcome: "not-attempted", reason: String(error && error.message ? error.message : error) };
    }
  }

  async function tick(reason = "interval") {
    if (inFlight) {
      rerun = true;
      return last;
    }
    inFlight = true;
    stats.ticks += 1;
    try {
      let frame;
      try {
        frame = await capture();
      } catch (error) {
        stats.capture_errors += 1;
        last = { status: "capture_error", reason, error: String(error && error.message ? error.message : error) };
        return last;
      }
      if (!frame || frame.complete !== true) {
        stats.skipped_incomplete += 1;
        last = { status: "skipped", reason: "incomplete_frame", trigger: reason };
        return last;
      }
      const visibility = String(getVisibility());
      let ack;
      try {
        ack = await post({ observation: frame, visibility_state: visibility, trigger: reason });
      } catch (error) {
        stats.post_errors += 1;
        last = { status: "post_error", reason, error: String(error && error.message ? error.message : error) };
        return last;
      }
      stats.published += 1;
      last = { status: "published", trigger: reason, visibility, ack };
      await maybeDispatch(frame, ack, visibility, now());
      return last;
    } finally {
      inFlight = false;
      if (rerun) {
        rerun = false;
        schedule("rerun");
      }
    }
  }

  function schedule(reason) {
    if (!setTimeoutFn || debounceTimer !== null) return;
    debounceTimer = setTimeoutFn(() => {
      debounceTimer = null;
      tick(reason);
    }, debounceMs);
  }

  function start() {
    if (timer !== null) return;
    if (!setIntervalFn) throw new HeartbeatError("no interval scheduler available", "missing_scheduler");
    timer = setIntervalFn(() => { tick("interval"); }, intervalMs);
    if (typeof observeMutations === "function") disconnect = observeMutations(() => schedule("mutation"));
    tick("start");
  }

  function stop() {
    if (timer !== null && clearIntervalFn) clearIntervalFn(timer);
    if (debounceTimer !== null && clearTimeoutFn) clearTimeoutFn(debounceTimer);
    if (typeof disconnect === "function") disconnect();
    timer = null;
    debounceTimer = null;
    disconnect = null;
  }

  return Object.freeze({
    start,
    stop,
    tick,
    stats: () => ({ ...stats }),
    last: () => last,
    lastDispatch: () => lastDispatch,
    guardedDispatchEnabled: dispatchEnabled,
  });
}

/** POST callback for the local coordinator (HTTPS espn.com -> http://127.0.0.1). */
function createFetchPost({ fetchFn = (typeof fetch === "function" ? fetch : null), baseUrl = "http://127.0.0.1:8765", csrfToken, timeoutMs = DEFAULT_POST_TIMEOUT_MS } = {}) {
  if (!fetchFn) throw new HeartbeatError("fetch is required", "missing_fetch");
  if (!csrfToken) throw new HeartbeatError("the coordinator CSRF token is required", "missing_csrf");
  return async function postHeartbeat(body) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timer = controller && typeof setTimeout === "function" ? setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      const response = await fetchFn(`${baseUrl}/api/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(body),
        signal: controller ? controller.signal : undefined,
      });
      const result = await response.json();
      if (!response.ok) throw new HeartbeatError(`heartbeat rejected: ${result && result.error ? result.error : response.status}`, "heartbeat_rejected");
      return result;
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
  };
}

/** MutationObserver source on the Feed/clock (falls back to the whole body). */
function createMutationSource({ MutationObserverCtor = (typeof MutationObserver === "function" ? MutationObserver : null), root, selectors = [] } = {}) {
  if (!MutationObserverCtor || !root) return null;
  return function observe(onMutation) {
    const observer = new MutationObserverCtor(() => onMutation());
    const targets = selectors.map((selector) => root.querySelector(selector)).filter(Boolean);
    for (const target of targets.length ? targets : [root.body || root]) {
      observer.observe(target, { childList: true, subtree: true, characterData: true });
    }
    return () => observer.disconnect();
  };
}

/**
 * Tab-shaped shim so the unchanged browser observer can run inside the page:
 * its serialized extractors then receive the live DOM directly.
 */
function createPageTab({ document: pageDocument, location: pageLocation }) {
  return {
    url: async () => pageLocation.href,
    playwright: {
      locator(selector) {
        return {
          evaluate: async (fn, arg) => fn(pageDocument.querySelector(selector), arg),
          evaluateAll: async (fn, arg) => fn(Array.from(pageDocument.querySelectorAll(selector)), arg),
        };
      },
    },
  };
}

module.exports = {
  HeartbeatError,
  createFetchPost,
  createHeartbeatPublisher,
  createMutationSource,
  createPageTab,
};
