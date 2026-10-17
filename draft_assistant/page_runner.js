"use strict";

/**
 * Harness-agnostic in-page runner for the attended ESPN practice.
 *
 * The canonical modules were written against a Playwright-style tab handle in
 * one agent REPL.  This file supplies the same handle shape backed by the live
 * page DOM, plus an operator transport to the loopback coordinator, so any
 * harness that can evaluate JavaScript in the visible ESPN tab drives the
 * unchanged `createCanonicalPracticeRunner`.  The decision, guard, dispatch and
 * confirmation logic all stay in the canonical modules.
 *
 * Gates are unchanged: preflight is read-only, `activate()` performs the one
 * native Resume only after the owner's explicit Start handoff, and the batch
 * runs in the page so tool-call gaps no longer stall frame publication.  The
 * coordinator accepts these page-origin requests only when it was started
 * with --page-runner, and only for a session that already has an owner whose
 * token the page presents.
 */

const { createBrowserObserver, normalizeUrlRoom } = require("./browser_observer.js");
const operatorRuntime = require("./operator_runtime.js");
const batchRecovery = require("./batch_recovery.js");
const liveInputs = require("./live_projection_inputs.js");
const nativeActivation = require("./native_activation.js");
const viewSwitch = require("./view_switch.js");
const { createCanonicalPracticeRunner } = require("./canonical_runner.js");
const capture = require("./page_capture.js");
// The observation-only bundle always includes the durable catalog/feed
// boundary used by recommendation mode.
const draftUniverse = require("./draft_universe.js");

const MAX_EVENTS = 400;
const INPUT_CACHE_VERSION = 1;
const INPUT_CACHE_SCHEMA = "fbl-advisor-input-cache";

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function copyJson(value) {
  if (value === undefined) return undefined;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_error) {
    return null;
  }
}

function cacheScope(room, ownTeam) {
  if (!room || !/^(?:\d+)$/.test(String(room.season_id || "")) || !/^(?:\d+)$/.test(String(room.team_id || "")) || !String(ownTeam || "").trim()) return null;
  return {
    season_id: String(room.season_id),
    team_id: String(room.team_id),
    own_team: String(ownTeam).trim(),
  };
}

function inputCacheKey(room, ownTeam) {
  const scope = cacheScope(room, ownTeam);
  if (!scope) return null;
  return [
    "fbl",
    "advisor",
    "input-cache",
    `v${INPUT_CACHE_VERSION}`,
    encodeURIComponent(scope.season_id),
    encodeURIComponent(scope.team_id),
    encodeURIComponent(scope.own_team),
  ].join(":");
}

function validCachedRules(rules) {
  return isRecord(rules)
    && Number.isInteger(rules.teams) && rules.teams > 0
    && Number.isInteger(rules.rounds) && rules.rounds > 0
    && Array.isArray(rules.starting_slots)
    && (rules.bench_slots === null || (Number.isInteger(rules.bench_slots) && rules.bench_slots >= 0))
    && (rules.ir_slots === null || (Number.isInteger(rules.ir_slots) && rules.ir_slots >= 0))
    && isRecord(rules.scoring) && Object.keys(rules.scoring).length > 0
    && Object.values(rules.scoring).every((value) => Number.isFinite(Number(value)));
}

function validCachedCatalog(catalog) {
  if (!Array.isArray(catalog) || catalog.length === 0) return false;
  const ids = new Set();
  return catalog.every((player) => {
    if (!isRecord(player)) return false;
    const playerId = String(player.player_id || "").trim();
    if (!playerId || ids.has(playerId)) return false;
    if (!String(player.name || "").trim() || !Array.isArray(player.eligibility) || player.eligibility.length === 0 || !isRecord(player.stats)) return false;
    if (!Object.values(player.stats).every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0)) return false;
    for (const field of ["projected_gp", "draft_rank", "adp"]) {
      if (player[field] !== null && player[field] !== undefined && (typeof player[field] !== "number" || !Number.isFinite(player[field]))) return false;
    }
    ids.add(playerId);
    return true;
  });
}

const CACHE_METADATA_FIELDS = Object.freeze([
  "artifact_version", "started_at", "captured_at", "steps", "skipped_without_position",
  "catalog_count", "catalog_complete", "termination_reason", "termination_count",
  "termination_limit", "termination_end_evidence", "termination_steps", "eligible_count",
  "complete_eligible_count", "incomplete_projection_count", "incomplete_projection_ids",
  "capture_metrics", "source",
]);

function safeCatalogSummary(value) {
  if (!isRecord(value)) return value === null || value === undefined ? null : undefined;
  const result = {};
  for (const key of CACHE_METADATA_FIELDS) {
    if (value[key] !== undefined) result[key] = copyJson(value[key]);
  }
  return result;
}

/** Validate and reduce persisted inputs to the fields the runner is allowed to consume. */
function validateInputCacheRecord(value, expectedScope) {
  if (!isRecord(value)) return { ok: false, reason: "cache record is not an object" };
  if (value.schema !== INPUT_CACHE_SCHEMA || value.version !== INPUT_CACHE_VERSION) return { ok: false, reason: "cache schema or version is unsupported" };
  if (!isRecord(value.scope) || !expectedScope
    || String(value.scope.season_id) !== expectedScope.season_id
    || String(value.scope.team_id) !== expectedScope.team_id
    || String(value.scope.own_team) !== expectedScope.own_team) {
    return { ok: false, reason: "cache scope does not match this season, team, and own team" };
  }
  if (value.source !== "captured") return { ok: false, reason: "cache source is not an explicit capture" };
  if (!String(value.captured_at || "") || !Number.isFinite(Date.parse(String(value.captured_at)))) return { ok: false, reason: "cache captured_at is invalid" };
  if (value.catalog_complete !== true || !validCachedCatalog(value.catalog)) return { ok: false, reason: "cache catalog is missing, partial, or has duplicate IDs" };
  if (!validCachedRules(value.rules)) return { ok: false, reason: "cache rules are missing or invalid" };
  if (value.catalog_summary !== undefined && value.catalog_summary !== null && !isRecord(value.catalog_summary)) return { ok: false, reason: "cache metadata is invalid" };
  const snapshot = {
    schema: INPUT_CACHE_SCHEMA,
    version: INPUT_CACHE_VERSION,
    scope: { ...expectedScope },
    captured_at: String(value.captured_at),
    source: "captured",
    capture_source: value.capture_source ? String(value.capture_source) : null,
    rules: copyJson(value.rules),
    catalog: copyJson(value.catalog),
    catalog_complete: true,
    catalog_summary: safeCatalogSummary(value.catalog_summary),
    rules_tables: value.rules_tables === undefined ? null : copyJson(value.rules_tables),
  };
  if (!snapshot.rules || !snapshot.catalog) return { ok: false, reason: "cache could not be copied safely" };
  return { ok: true, snapshot };
}

function readInputCache(storage, room, ownTeam) {
  const scope = cacheScope(room, ownTeam);
  const key = inputCacheKey(room, ownTeam);
  if (!storage || typeof storage.getItem !== "function" || !key || !scope) return { status: "unavailable", key, reason: "localStorage is unavailable" };
  let raw;
  try {
    raw = storage.getItem(key);
  } catch (_error) {
    return { status: "unavailable", key, reason: "localStorage read failed" };
  }
  if (raw === null || raw === undefined || raw === "") return { status: "missing", key };
  let parsed;
  try {
    parsed = JSON.parse(String(raw));
  } catch (_error) {
    return { status: "ignored", key, reason: "cache JSON is corrupt" };
  }
  const validation = validateInputCacheRecord(parsed, scope);
  return validation.ok
    ? { status: "loaded", key, snapshot: validation.snapshot }
    : { status: "ignored", key, reason: validation.reason };
}

function writeInputCache(storage, room, ownTeam, inputs, now = Date.now) {
  const scope = cacheScope(room, ownTeam);
  const key = inputCacheKey(room, ownTeam);
  if (!storage || typeof storage.setItem !== "function" || !key || !scope) return { status: "unavailable", key, reason: "localStorage is unavailable" };
  const source = inputs && inputs.catalog_summary && inputs.catalog_summary.source;
  const snapshot = {
    schema: INPUT_CACHE_SCHEMA,
    version: INPUT_CACHE_VERSION,
    scope,
    captured_at: inputs && inputs.captured_at ? String(inputs.captured_at) : new Date(now()).toISOString(),
    source: "captured",
    capture_source: source ? String(source) : "ESPN paused-room capture",
    rules: copyJson(inputs && inputs.rules),
    catalog: copyJson(inputs && inputs.catalog),
    catalog_complete: Boolean(inputs && inputs.catalog_complete === true),
    catalog_summary: safeCatalogSummary(inputs && inputs.catalog_summary),
    rules_tables: copyJson(inputs && inputs.rules_tables),
  };
  const validation = validateInputCacheRecord(snapshot, scope);
  if (!validation.ok) return { status: "rejected", key, reason: validation.reason };
  try {
    storage.setItem(key, JSON.stringify(validation.snapshot));
  } catch (_error) {
    return { status: "unavailable", key, reason: "localStorage write failed" };
  }
  return { status: "saved", key, snapshot: validation.snapshot };
}

function recommendationError(reason, detail = {}) {
  return { ok: false, reason, ...detail };
}

function expectedTeamForPick(pick, order) {
  if (!Number.isInteger(pick) || pick < 1 || !Array.isArray(order) || order.length !== 14) return null;
  const round = Math.floor((pick - 1) / 14);
  const offset = (pick - 1) % 14;
  return order[round % 2 === 0 ? offset : 13 - offset] || null;
}

/** Validate the authoritative feed before a full-catalog advisory preview. */
function validateRecommendationFrame(frame, ownTeam) {
  if (!frame || typeof frame !== "object") return recommendationError("recommendation frame is missing");
  if (frame.history_complete !== true || frame.complete !== true) return recommendationError("recommendation requires a fresh complete feed and roster frame");
  const history = Array.isArray(frame.history) ? frame.history : [];
  const order = Array.isArray(frame.pick_order) ? frame.pick_order.map((team) => String(team || "").trim()) : [];
  if (order.length !== 14 || order.some((team) => !team) || new Set(order).size !== order.length) return recommendationError("draft team order is missing or ambiguous");
  const seenPicks = new Set();
  const seenPlayers = new Set();
  const byPlayer = new Map();
  for (let index = 0; index < history.length; index += 1) {
    const event = history[index] || {};
    const pick = Number(event.pick_number);
    const playerId = String(event.player_id || "").trim();
    const team = String(event.team || "").trim();
    if (!Number.isInteger(pick) || pick !== index + 1) return recommendationError(`feed gap or out-of-order pick at index ${index + 1}`);
    if (!playerId) return recommendationError(`feed pick ${pick} has no stable player ID`);
    if (!team) return recommendationError(`feed pick ${pick} has no team identity`);
    if (seenPicks.has(pick)) return recommendationError(`feed pick ${pick} is duplicated`);
    if (seenPlayers.has(playerId)) return recommendationError(`feed player ${playerId} is duplicated`);
    const expected = expectedTeamForPick(pick, order);
    if (team !== expected) return recommendationError(`feed team order conflicts at pick ${pick}`);
    seenPicks.add(pick);
    seenPlayers.add(playerId);
    byPlayer.set(playerId, event);
  }
  const currentPick = frame.current_pick === null || frame.current_pick === undefined ? null : Number(frame.current_pick);
  if (currentPick !== null && (!Number.isInteger(currentPick) || currentPick !== history.length + 1)) {
    return recommendationError("feed length does not match the current pick");
  }
  const warningList = Array.isArray(frame.warnings) ? frame.warnings.map((warning) => String(warning)) : [];
  if (warningList.some((warning) => /history_(?:conflict|stable_id_missing|grid_missing)|retained_history_incomplete/i.test(warning))) {
    return recommendationError("feed contains an unresolved history conflict or incomplete read-back");
  }
  const roster = Array.isArray(frame.roster) ? frame.roster : [];
  const seenRoster = new Set();
  for (const player of roster) {
    const playerId = String(player && player.player_id || "").trim();
    if (!playerId) return recommendationError("roster contains a player without a stable ID");
    if (seenRoster.has(playerId)) return recommendationError(`roster player ${playerId} is duplicated`);
    const event = byPlayer.get(playerId);
    if (!event || event.team !== ownTeam) return recommendationError(`roster player ${playerId} is not confirmed in our authoritative feed`);
    seenRoster.add(playerId);
  }
  const ownHistoryIds = history
    .filter((event) => String(event.team || "").trim() === String(ownTeam).trim())
    .map((event) => String(event.player_id));
  if (ownHistoryIds.length !== seenRoster.size || ownHistoryIds.some((playerId) => !seenRoster.has(playerId))) {
    return recommendationError("roster and authoritative history are still reconciling");
  }
  const knownPlayers = Array.isArray(frame.known_players) ? frame.known_players : [];
  if (!knownPlayers.length) return recommendationError("the preloaded projection catalog is empty");
  const seenCatalog = new Set();
  for (const player of knownPlayers) {
    const playerId = String(player && player.player_id || "").trim();
    if (!playerId) return recommendationError("the projection catalog contains a player without a stable ID");
    if (seenCatalog.has(playerId)) return recommendationError(`the projection catalog repeats player ${playerId}`);
    seenCatalog.add(playerId);
  }
  return { ok: true, history, roster, knownPlayers, seenPlayers, seenRoster, feed_count: history.length, catalog_count: knownPlayers.length };
}

function buildRecommendationPreview(frame, validation, universeSnapshot = null) {
  const drafted = new Set([...validation.seenPlayers, ...validation.seenRoster]);
  const fullRemaining = validation.knownPlayers.filter((player) => !drafted.has(String(player.player_id)));
  const recommendationPool = universeSnapshot && Array.isArray(universeSnapshot.recommendation_pool)
    ? universeSnapshot.recommendation_pool
    : fullRemaining;
  const fullIds = new Set(fullRemaining.map((player) => String(player.player_id)));
  const poolComplete = recommendationPool.length === fullRemaining.length
    && recommendationPool.every((player) => fullIds.has(String(player.player_id)));
  return {
    ...frame,
    // This full remaining pool is sent only to recommend-preview and is never
    // published as the coordinator's visible actionable pool.
    available_players: recommendationPool,
    available_pool_complete: poolComplete,
    warnings: [
      ...(Array.isArray(frame.warnings) ? frame.warnings : []),
      "recommendation_preview_full_catalog",
      ...(poolComplete ? [] : ["recommendation_catalog_incomplete"]),
    ],
  };
}

class PageRunnerError extends Error {
  constructor(message, code = "page_runner_error", detail = {}) {
    super(message);
    this.name = "PageRunnerError";
    this.code = code;
    this.detail = detail;
  }
}

/**
 * Playwright-shaped handle over the live page.  `evaluate` passes the first
 * match (or body), `evaluateAll` passes every match, and `click` requires
 * exactly one visible enabled match, like the offline fixture tab.
 */
function createPageTab({ document: pageDocument, window: pageWindow, location: pageLocation, onHidden = null }) {
  // A visible element can retain its layout rect while the whole tab is
  // backgrounded.  Treat the document as foreground only at the instant of
  // the mutating click; reads remain available for recovery diagnostics.
  const foreground = () => pageDocument.visibilityState === undefined || pageDocument.visibilityState === "visible";
  const visible = (element) => {
    const style = pageWindow.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
  };
  function locatorFor(selector) {
    const all = () => Array.from(pageDocument.querySelectorAll(selector));
    return {
      selector,
      async evaluate(fn, arg) {
        const target = selector === "body" ? pageDocument.body : all()[0];
        if (!target) throw new PageRunnerError(`locator ${selector} resolved no element`, "locator_empty");
        return fn(target, arg);
      },
      async evaluateAll(fn, arg) { return fn(all(), arg); },
      async click() {
        if (!foreground()) {
          let pauseResult = null;
          if (typeof onHidden === "function") {
            try { pauseResult = await onHidden(); } catch (error) { pauseResult = { error: String(error && error.message ? error.message : error) }; }
          }
          throw new PageRunnerError("the draft page is hidden; refusing a visible click", "page_hidden", { pause: pauseResult });
        }
        const targets = all().filter((element) => visible(element) && !element.disabled && element.getAttribute("aria-disabled") !== "true");
        if (targets.length !== 1) throw new PageRunnerError(`click locator ${selector} resolved ${targets.length} visible enabled elements`, "click_not_unique");
        targets[0].click();
      },
      async innerText() {
        const first = all()[0];
        return first ? first.textContent : "";
      },
      async count() { return all().length; },
    };
  }
  return {
    async url() { return pageLocation.href; },
    playwright: {
      locator: locatorFor,
      getByRole(role) { return locatorFor(role === "grid" ? '[role="grid"]' : `[role="${role}"]`); },
    },
  };
}

/** Operator transport: POST /api/operator with the loopback CSRF token. */
function createPageOperator({ fetchFn, baseUrl, csrfToken, timeoutMs = 8_000 }) {
  if (typeof fetchFn !== "function") throw new PageRunnerError("fetch is required", "missing_fetch");
  if (!csrfToken) throw new PageRunnerError("call FBL.configure({csrfToken}) with the local panel token first", "missing_csrf");
  return async function requestOperator(payload) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      const response = await fetchFn(`${baseUrl}/api/operator`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(payload),
        signal: controller ? controller.signal : undefined,
      });
      const body = await response.json();
      // Same contract as the panel operator: stamp the receipt time locally.
      return { ...body, status_received_at: new Date().toISOString() };
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
  };
}

function canonicalModules() {
  return {
    createBrowserObserver,
    ...viewSwitch,
    ...nativeActivation,
    ...operatorRuntime,
    ...liveInputs,
    ...batchRecovery,
    draftUniverse,
  };
}

/**
 * One runner per room and session.  Rules and catalog are captured from the
 * paused room by `captureInputs()` unless supplied (offline tests).
 */
function createPageRunner({
  document: pageDocument,
  window: pageWindow,
  location: pageLocation,
  fetchFn,
  baseUrl = "http://127.0.0.1:8765",
  csrfToken,
  sessionId,
  ownerToken,
  ownTeam,
  expectedSeat,
  rules = null,
  catalog = null,
  catalogComplete = false,
  catalogSummary = null,
  modules = null,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  historyPollMs = 200,
  wheelScroller = null,
  historyScroller = null,
  runnerFactory = createCanonicalPracticeRunner,
  mode = "attended",
  setTimeoutFn = globalThis.setTimeout,
  clearTimeoutFn = globalThis.clearTimeout,
} = {}) {
  if (!pageDocument || !pageWindow || !pageLocation) throw new PageRunnerError("the live page document, window and location are required", "missing_page");
  if (!sessionId || !ownerToken || !ownTeam || !Number.isInteger(expectedSeat)) {
    throw new PageRunnerError("sessionId, ownerToken, ownTeam and a verified integer expectedSeat are required", "missing_identity");
  }
  if (!["attended", "recommendation"].includes(mode)) throw new PageRunnerError("mode must be attended or recommendation", "invalid_mode");
  let pauseForHidden = null;
  const tab = createPageTab({ document: pageDocument, window: pageWindow, location: pageLocation, onHidden: () => pauseForHidden && pauseForHidden() });
  const request = createPageOperator({ fetchFn, baseUrl, csrfToken });
  const owned = (payload) => request({ ...payload, owner_token: ownerToken });
  pauseForHidden = async () => owned({ operation: "pause", session_id: sessionId, reason: "draft page became hidden before visible Draft dispatch" });
  const switcher = viewSwitch.createViewSwitcher({ tab });
  const events = [];
  const log = (event, detail = {}) => {
    events.push({ at: new Date(now()).toISOString(), event, ...detail });
    if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS);
  };
  let pageStorage = null;
  try {
    pageStorage = pageWindow.localStorage || null;
  } catch (_error) {
    pageStorage = null;
  }
  let cacheState = { status: "not_attempted", key: null, reason: null };
  let cachedSnapshot = null;
  let inputs = rules && catalog ? {
    rules,
    catalog,
    catalog_complete: catalogComplete === true,
    catalog_summary: catalogSummary || null,
    source: "supplied",
    captured_at: null,
    cache_status: "supplied",
  } : null;
  if (mode === "recommendation" && !inputs) {
    let pageRoom = null;
    try {
      pageRoom = normalizeUrlRoom(pageLocation.href);
    } catch (_error) {
      // verifiedRoom() reports the actionable tab error later; cache lookup is
      // best effort and must never prevent a manual capture.
    }
    const loaded = readInputCache(pageStorage, pageRoom, ownTeam);
    cacheState = { status: loaded.status, key: loaded.key || null, reason: loaded.reason || null };
    if (loaded.status === "loaded" && loaded.snapshot) {
      cachedSnapshot = loaded.snapshot;
      inputs = {
        rules: copyJson(loaded.snapshot.rules),
        catalog: copyJson(loaded.snapshot.catalog),
        catalog_complete: true,
        catalog_summary: copyJson(loaded.snapshot.catalog_summary),
        rules_tables: copyJson(loaded.snapshot.rules_tables),
        source: "cache",
        captured_at: loaded.snapshot.captured_at,
        cache_status: "loaded",
        rules_verified: false,
      };
    }
  }
  let runner = null;
  let batch = null;
  let attachmentCheckpoint = null;
  let attachmentResumeStatus = null;
  // A page-attachment checkpoint is the boundary for the first batch only.
  // Subsequent batches must provide their own explicit checkpoint (for
  // example, recoverAndContinue's freshly recovered checkpoint) rather than
  // silently reusing the original attachment revision and pick count.
  let attachmentCheckpointConsumed = false;
  let recommendationTimer = null;
  let recommendationGeneration = 0;
  let recommendationInFlight = false;
  let recommendationUniverse = null;
  // Private feed identity used to reject a later truncated or conflicting
  // frame.  The public summary keeps only the last good metadata and the
  // recommendation; the full IDs never become an actionable coordinator
  // observation.
  let recommendationFeed = null;
  let captureProgress = null;
  const recommendation = {
    running: false,
    interval_ms: null,
    lookahead_enabled: false,
    polls: 0,
    successes: 0,
    blocked_reason: null,
    last_good: null,
    last: null,
    error: null,
    stale: false,
    own_turns: [],
    diagnostics: [],
  };

  function recommendationDiagnostic(entry) {
    recommendation.diagnostics.push({ at: new Date(now()).toISOString(), ...entry });
    if (recommendation.diagnostics.length > 60) recommendation.diagnostics.splice(0, recommendation.diagnostics.length - 60);
  }

  function actorDisabled(action) {
    if (mode !== "recommendation") return false;
    throw new PageRunnerError(`${action} is disabled in recommendation-only mode`, "recommendation_mode_actor_disabled", { action });
  }

  async function coordinatorStatus() {
    const status = await owned({ operation: "status", session_id: sessionId });
    if (!status || status.error || status.status === "error") throw new PageRunnerError(`coordinator status failed: ${(status && status.error) || "no response"}`, "status_failed");
    return status;
  }

  async function verifiedRoom() {
    const status = await coordinatorStatus();
    const pageRoom = normalizeUrlRoom(pageLocation.href);
    const sessionRoom = status.room || {};
    if (!pageRoom || ["league_id", "season_id", "team_id"].some((key) => String(pageRoom[key]) !== String(sessionRoom[key]))) {
      throw new PageRunnerError("this tab is not the room connected to the session", "room_mismatch", { pageRoom, sessionRoom });
    }
    if (typeof sessionRoom.practice !== "boolean") throw new PageRunnerError("the connected session does not identify practice status", "room_status_incomplete");
    if (sessionRoom.practice === true && !status.practice_attestation) throw new PageRunnerError("the practice session is not attested", "not_attested_practice");
    return { ...pageRoom, practice: sessionRoom.practice };
  }

  async function showTab(pattern) {
    const result = await tab.playwright.locator("body").evaluate(viewSwitch.resolveViewTab, { pattern, click: true });
    if (!result.clicked) throw new PageRunnerError(`view tab ${pattern} is ${result.status}; refusing to guess`, "view_tab_unresolved", result);
  }

  /** Re-read only the current room's Rules tab before using a cross-league cache. */
  async function verifyCachedRules() {
    if (!inputs || inputs.source !== "cache" || inputs.rules_verified === true) return;
    await verifiedRoom();
    await showTab("^rules$");
    let rulesRead = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      rulesRead = await tab.playwright.locator("body").evaluate(capture.readRulesTables);
      if (rulesRead.tables.some((table) => /^scoring/i.test(table.caption))) break;
      await sleep(150);
    }
    const normalized = capture.normalizeEspnRules(rulesRead);
    await switcher.showPlayers();
    if (normalized.problems.length) {
      throw new PageRunnerError(`cached catalog requires current-room rules verification: ${normalized.problems.join("; ")}`, "cached_rules_unverified", normalized);
    }
    inputs = {
      ...inputs,
      rules: normalized.rules,
      rules_tables: rulesRead,
      rules_verified: true,
      rules_verified_at: new Date(now()).toISOString(),
      cache_status: "ready",
    };
    cacheState = { ...cacheState, status: "ready", reason: null };
    log("cached-rules-verified", { source: "current-room-rules" });
  }

  /** Paused-room capture of rules (Rules tab) and the projection catalog (Players). */
  async function setShowDrafted(wanted) {
    const body = tab.playwright.locator("body");
    await body.evaluate(capture.setShowDrafted, wanted);
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const read = await body.evaluate(capture.readPlayersGrid);
      if (read.show_drafted === wanted) return true;
      await sleep(100);
    }
    return false;
  }

  async function captureInputs({ maxPlayers = 1500, onProgress = null } = {}) {
    captureProgress = { phase: "rules", catalog_count: 0, progress: 0, pass: 0, steps: 0 };
    try {
      const room = await verifiedRoom();
      await showTab("^rules$");
      let rulesRead = null;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        rulesRead = await tab.playwright.locator("body").evaluate(capture.readRulesTables);
        if (rulesRead.tables.some((table) => /^scoring/i.test(table.caption))) break;
        await sleep(150);
      }
      const normalized = capture.normalizeEspnRules(rulesRead);
      await switcher.showPlayers();
      if (normalized.problems.length) throw new PageRunnerError(`rules read-back is incomplete: ${normalized.problems.join("; ")}`, "rules_incomplete", normalized);
      const scroller = wheelScroller || capture.createGridWheelScroller({ document: pageDocument, WheelEventCtor: pageWindow.WheelEvent });
      // Include drafted players so a mid-draft (re)start can resolve rostered
      // and history players; live pick 23 read our roster as empty without it.
      // Show Drafted is always restored to OFF, which availability requires.
      let catalogResult;
      try {
        if (!(await setShowDrafted(true))) throw new PageRunnerError("Show Drafted did not turn on for the paused catalog capture", "show_drafted_unresolved");
        catalogResult = await capture.captureProjectionCatalog({
          read: () => tab.playwright.locator("body").evaluate(capture.readPlayersGrid),
          scroll: scroller,
          sleep,
          maxPlayers,
          onProgress: (progress) => {
            captureProgress = { ...progress };
            if (typeof onProgress === "function") {
              try { onProgress({ ...progress }); } catch (_error) { /* advisory UI callback */ }
            }
          },
          source: `ESPN Players grid (Show Drafted) observed in paused room ${normalizeUrlRoom(pageLocation.href).league_id}`,
        });
      } finally {
        if (!(await setShowDrafted(false))) throw new PageRunnerError("Show Drafted did not read back OFF after capture; turn it off before continuing", "show_drafted_stuck_on");
      }
      inputs = {
        rules: normalized.rules,
        catalog: catalogResult.players,
        catalog_complete: catalogResult.catalog_complete === true,
        catalog_summary: { ...catalogResult, players: undefined },
        rules_tables: rulesRead,
        source: "captured",
        captured_at: catalogResult.captured_at || new Date(now()).toISOString(),
        cache_status: "uncached",
      };
      runner = null;
      if (inputs.catalog_complete === true) {
        const saved = writeInputCache(pageStorage, room, ownTeam, inputs, now);
        cacheState = { status: saved.status, key: saved.key || null, reason: saved.reason || null };
        if (saved.status === "saved" && saved.snapshot) {
          cachedSnapshot = saved.snapshot;
          inputs.cache_status = "saved";
        } else {
          inputs.cache_status = saved.status === "unavailable" ? "save_unavailable" : "save_failed";
        }
      } else {
        cacheState = { ...cacheState, status: "not_replaced", reason: "capture catalog is incomplete" };
        inputs.cache_status = "not_replaced";
      }
      log("inputs-captured", { players: catalogResult.players.length, complete_core_stats: catalogResult.complete_core_stats, steps: catalogResult.steps });
      return { rules: normalized.rules, catalog_players: catalogResult.players.length, complete_core_stats: catalogResult.complete_core_stats, skipped_without_position: catalogResult.skipped_without_position, catalog_complete: catalogResult.catalog_complete === true, termination_reason: catalogResult.termination_reason || null, captured_at: inputs.captured_at, cache_status: inputs.cache_status };
    } finally {
      captureProgress = null;
    }
  }

  async function readDraftLifecycle() {
    return tab.playwright.locator("body").evaluate(nativeActivation.inspectDraftLifecycle);
  }

  async function pausePracticeDraft({ onProgress = null, pausePollMs = 150, pausePolls = 40 } = {}) {
    let lifecycle = await readDraftLifecycle();
    if (lifecycle.paused) return lifecycle;
    if (!lifecycle.running) {
      throw new PageRunnerError("the practice draft started, but its native Pause/Resume state is not readable", "native_pause_state_unknown", { lifecycle });
    }

    // ESPN has rendered the control on League Manager in some room versions.
    // Use the current view first, then move once to the anchored control view.
    if (!lifecycle.native || lifecycle.native.pause.status !== "unique") {
      await switcher.showLeagueManager();
      lifecycle = await readDraftLifecycle();
    }
    if (!lifecycle.native || lifecycle.native.pause.status !== "unique") {
      throw new PageRunnerError(`native Pause locator: ${(lifecycle.native && lifecycle.native.pause && lifecycle.native.pause.reason) || "control unavailable"}`, "native_pause_unresolved", { lifecycle });
    }
    if (typeof onProgress === "function") onProgress({ phase: "pausing_draft" });
    const click = await tab.playwright.locator("body").evaluate(nativeActivation.clickPauseInPage);
    if (!click || !click.clicked) {
      throw new PageRunnerError(`native Pause was not clicked: ${(click && click.reason) || "control changed"}`, "native_pause_not_clicked", { lifecycle, click });
    }
    for (let attempt = 0; attempt < pausePolls; attempt += 1) {
      lifecycle = await readDraftLifecycle();
      if (lifecycle.paused) return lifecycle;
      if (pausePollMs > 0) await sleep(pausePollMs);
    }
    throw new PageRunnerError("native Pause was clicked but the room did not read back paused", "native_pause_unconfirmed", { lifecycle });
  }

  /**
   * User-facing Scan/Rescan orchestration. Practice rooms are the one case in
   * which scanning must control the native clock: wait for ESPN to actually
   * start, pause it once, and then capture the complete rules/player catalog.
   * A non-practice room is scanned immediately and is never paused here.
   */
  async function scanInputs({ maxPlayers = 1500, onProgress = null, waitForStartMs = 300_000, waitPollMs = 250, pausePollMs = 150, pausePolls = 40 } = {}) {
    const report = (progress) => {
      if (typeof onProgress === "function") {
        try { onProgress(progress); } catch (_error) { /* advisory UI callback */ }
      }
    };
    const room = await verifiedRoom();
    let lifecycle = await readDraftLifecycle();
    const practice = room.practice === true;
    if (practice) {
      if (!lifecycle || lifecycle.practice !== true) {
        throw new PageRunnerError("the verified practice room is missing its visible practice-draft heading", "practice_heading_missing", { lifecycle });
      }
      // The native Pause control is mounted in League Manager in some ESPN
      // room versions.  Anchor that view before the start wait so a running
      // practice draft can always be paused exactly once.
      await switcher.showLeagueManager();
      lifecycle = await readDraftLifecycle();
      if (!lifecycle.started) {
        report({ phase: "waiting_for_start" });
        lifecycle = await nativeActivation.waitForDraftStart({
          read: readDraftLifecycle,
          sleep,
          now,
          timeoutMs: waitForStartMs,
          pollMs: waitPollMs,
          onProgress: report,
        });
      }
      if (!lifecycle.paused) {
        lifecycle = await pausePracticeDraft({ onProgress: report, pausePollMs, pausePolls });
      }
    } else {
      report({ phase: "scan" });
    }
    const result = await captureInputs({ maxPlayers, onProgress: report });
    return { ...result, scan_mode: practice ? "practice_wait_pause_scan" : "immediate_scan" };
  }

  async function ensureRunner() {
    if (runner) return runner;
    if (!inputs) throw new PageRunnerError("capture or supply rules and catalog first (captureInputs while paused)", "missing_inputs");
    await verifyCachedRules();
    const room = await verifiedRoom();
    runner = runnerFactory({
      modules: modules || canonicalModules(),
      draftTab: tab,
      // Operator requests go through `requestOperator`; the panel tab handle
      // is required by the factory's shape check only.
      panelTab: { playwright: {} },
      room,
      freshlyVerifiedRules: inputs.rules,
      projectionCatalog: inputs.catalog,
      sessionId,
      ownerToken,
      ownTeam,
      expectedSeat,
      requestOperator: request,
      // Pick History is virtualized; the observer walks it with this scroller.
      historyScroll: historyScroller || (typeof pageWindow.WheelEvent === "function"
        ? ((scroll) => (deltaY) => scroll(0, deltaY))(capture.createGridWheelScroller({ document: pageDocument, WheelEventCtor: pageWindow.WheelEvent, firstHeader: "PICK" }))
        : null),
      onProgress: async (event) => log(event.event || "progress", { pick: event.observation ? event.observation.current_pick : undefined, outcome: event.result ? event.result.outcome : undefined }),
      now,
      historyPollMs,
    });
    if (mode === "recommendation" && draftUniverse && typeof draftUniverse.createDraftUniverse === "function") {
      const rulesTeams = Number(inputs.rules && inputs.rules.teams);
      const rulesRounds = Number(inputs.rules && inputs.rules.rounds);
      recommendationUniverse = draftUniverse.createDraftUniverse({
        catalog: inputs.catalog,
        catalogComplete: inputs.catalog_complete === true,
        expectedPicks: Number.isInteger(rulesTeams) && Number.isInteger(rulesRounds) ? rulesTeams * rulesRounds : null,
      });
    }
    return runner;
  }

  /**
   * Recommendation polling reads the Feed/roster/control snapshot directly so
   * it does not depend on the visible Players viewport.  Only an incomplete
   * virtualized feed gets the bounded Pick History fallback; the Players view
   * is restored after that read and the preloaded catalog remains authoritative
   * for the recommendation pool.
   */
  function recommendationHistoryIsComplete(history, currentPick, phase) {
    const expectedEnd = currentPick === null || currentPick === undefined
      ? (phase === "complete" ? 140 : null)
      : Number(currentPick) - 1;
    if (!Number.isInteger(expectedEnd) || expectedEnd < 0 || expectedEnd > 140 || history.length !== expectedEnd) return false;
    const seenIds = new Set();
    return history.every((event, index) => {
      if (!event || event.pick_number !== index + 1 || !event.player_id || seenIds.has(String(event.player_id))) return false;
      seenIds.add(String(event.player_id));
      return true;
    });
  }

  function mergeRecommendationHistory(frame) {
    if (!recommendationUniverse || typeof recommendationUniverse.observe !== "function") return frame;
    const incoming = Array.isArray(frame.history) ? frame.history : [];
    const observed = recommendationUniverse.observe({
      feed: incoming,
      history: incoming,
      historyComplete: false,
      currentPick: frame.current_pick,
    });
    const merged = observed && Array.isArray(observed.observed_history)
      ? observed.observed_history
      : observed && Array.isArray(observed.history) ? observed.history : [];
    if (!recommendationHistoryIsComplete(merged, frame.current_pick, frame.phase)) return frame;
    return {
      ...frame,
      // Keep this boundary staged until the normal frame validator has
      // checked snake order, roster agreement, and catalog identity. A
      // continuous but wrong-team event must never become a confirmed
      // drafted ID merely because it arrived before validation.
      history: merged,
      history_complete: true,
      // The durable universe replaces an expired ten-second retained snapshot
      // for advisory reads. Keep current-feed warnings while removing only
      // markers that describe that replaced retained snapshot.
      warnings: (Array.isArray(frame.warnings) ? frame.warnings : []).filter((warning) => !/^(?:retained_history_|fast_capture_not_confirmable)/i.test(String(warning))),
      complete: true,
    };
  }

  async function captureRecommendationFrame(active) {
    const observer = active && active.observer;
    if (!observer || typeof observer.captureFastFrame !== "function") return active.captureFrame();
    let fast = mergeRecommendationHistory(await observer.captureFastFrame());
    // The observer parses the roster against the fast frame's own retained
    // history. Once that ten-second snapshot expires, an old roster player can
    // be dropped even though the durable universe has the full prefix. Reread
    // the same visible DOM with the merged prefix; this stays read-only and
    // avoids a Pick History tab switch on a healthy overlapping Feed.
    const ownHistoryIds = new Set((Array.isArray(fast.history) ? fast.history : [])
      .filter((event) => String(event.team) === String(ownTeam))
      .map((event) => String(event.player_id || "")));
    const rosterIds = new Set((Array.isArray(fast.roster) ? fast.roster : []).map((player) => String(player.player_id || "")));
    if (fast.history_complete === true && [...ownHistoryIds].some((playerId) => !rosterIds.has(playerId))) {
      const refreshed = await observer.captureFastFrame({
        historySnapshot: {
          history: fast.history,
          history_complete: true,
          observed_at: fast.observed_at,
          warnings: [],
        },
      });
      fast = mergeRecommendationHistory(refreshed);
    }
    const warnings = Array.isArray(fast.warnings) ? fast.warnings : [];
    // ``fast_capture_unconfirmable`` is an action-runtime warning. A complete
    // fast feed is sufficient for advisory ranking; only a missing/partial
    // feed needs the bounded Pick History fallback.
    const fastFeedIncomplete = !fast.history_complete || warnings.some((warning) => /retained_history_incomplete|fast_history_feed_empty/i.test(String(warning)));
    if (!fastFeedIncomplete) {
      const rosterMissing = warnings.some((warning) => /roster_pos_table_missing/i.test(String(warning)));
      return fast.complete || rosterMissing ? fast : { ...fast, complete: true };
    }
    if (!active.switcher || typeof active.switcher.showPickHistory !== "function" || typeof observer.captureHistory !== "function") return fast;
    let snapshot = null;
    try {
      await active.switcher.showPickHistory();
      snapshot = await observer.captureHistory({ expectedCurrentPick: fast.current_pick, retain: false });
      if (!snapshot || snapshot.history_complete !== true) {
        const current = await observer.captureFastFrame();
        if (current && current.current_pick !== fast.current_pick) {
          snapshot = await observer.captureHistory({ expectedCurrentPick: current.current_pick, retain: false });
        }
      }
    } finally {
      try {
        await active.switcher.showPlayers();
      } catch (error) {
        // A completed ESPN room may remove Players and its tab. There is no
        // mutation to guard in recommendation mode, so restoration is best
        // effort when the view no longer exists.
        if (!error || !/tab_|panel_/.test(String(error.code || ""))) throw error;
      }
    }
    if (!snapshot || snapshot.history_complete !== true) {
      return {
        ...fast,
        history: snapshot && Array.isArray(snapshot.history) ? snapshot.history : fast.history,
        history_complete: false,
        complete: false,
        warnings: [...warnings, ...(snapshot && Array.isArray(snapshot.warnings) ? snapshot.warnings : []), "retained_history_incomplete"],
      };
    }
    // The history walk can outlive a pick. Re-read Feed, controls, and roster
    // against the fresh snapshot so the recommendation frame has one current
    // room identity and never mixes old clock/roster state with new history.
    const refreshed = await observer.captureFastFrame({ historySnapshot: snapshot });
    const refreshedWarnings = Array.isArray(refreshed.warnings) ? refreshed.warnings : [];
    const retainedWarnings = refreshedWarnings.filter((warning) => !/fast_capture_unconfirmable|retained_history_incomplete|fast_history_feed_empty/i.test(String(warning)));
    const rosterMissing = retainedWarnings.some((warning) => /roster_pos_table_missing/i.test(String(warning)));
    return {
      ...refreshed,
      history_complete: refreshed.history_complete === true,
      complete: refreshed.complete || (!rosterMissing && refreshed.history_complete === true),
      warnings: [...retainedWarnings, ...(Array.isArray(snapshot.warnings) ? snapshot.warnings : [])],
    };
  }

  function confirmedOwnActions(status) {
    return (status && Array.isArray(status.actions) ? status.actions : []).filter((action) => (
      action && action.status === "confirmed" && action.action_type === "draft"
      && action.claimed === true && action.dispatched === true
      && String(action.expected_team || "").toLowerCase() === String(ownTeam).toLowerCase()
    ));
  }

  /**
   * Establish a durable boundary before a fresh page attachment resumes.
   * Coordinator actions, rather than native history, define the carried
   * confirmation count.  Publishing the frame first aligns the local
   * observer revision with the coordinator's authoritative revision.
   */
  async function checkpointFreshAttachment(frame) {
    let status = await coordinatorStatus();
    // A brand-new connected room has no durable coordinator frame or guarded
    // action to carry.  Leave it in its normal observing state; the ordinary
    // activation path will publish its first running frame.  A stopped or
    // paused mid-draft attachment still goes through the durable boundary
    // below, even when the carried count is zero.
    if (status.state === "observing" && confirmedOwnActions(status).length === 0) {
      return null;
    }
    if (status.state !== "paused") {
      if (!["observing", "ready", "degraded"].includes(status.state)) {
        throw new PageRunnerError(`fresh attachment requires a paused coordinator; current state is ${status.state || "unknown"}`, "attachment_state_invalid", { status });
      }
      const paused = await owned({ operation: "pause", session_id: sessionId, reason: "fresh page attachment checkpoint" });
      if (!paused || paused.error || paused.status === "error") {
        throw new PageRunnerError(`could not pause coordinator for fresh attachment: ${(paused && paused.error) || "no response"}`, "attachment_pause_failed", { paused });
      }
      status = await coordinatorStatus();
    }
    const published = await owned({ operation: "publish", session_id: sessionId, observation: frame });
    if (!published || published.error || published.status === "error") {
      throw new PageRunnerError(`fresh attachment frame publish failed: ${(published && published.error) || "no response"}`, "attachment_publish_failed", { published });
    }
    status = await coordinatorStatus();
    const ownPicksConfirmed = confirmedOwnActions(status).length;
    let checkpoint;
    try {
      checkpoint = operatorRuntime.createBatchCheckpoint(frame, {
        sessionId,
        ownTeam,
        ownPicksConfirmed,
        coordinatorStatus: status,
        now,
      });
    } catch (error) {
      throw new PageRunnerError(`fresh attachment checkpoint refused: ${error.message || error}`, "attachment_checkpoint_invalid", { status });
    }
    attachmentCheckpoint = checkpoint;
    return checkpoint;
  }

  function recommendationSummary() {
    // A stopped or throttled poll must not leave an old recommendation marked
    // fresh forever. Keep the stored error/blocked state, but also derive
    // freshness from the observation timestamp every time the summary is read.
    const observedAt = recommendation.last_good && Date.parse(String(recommendation.last_good.observed_at || ""));
    const currentNow = typeof now === "function" ? now() : Date.now();
    const currentMs = currentNow instanceof Date ? currentNow.getTime() : Number(currentNow);
    const ageMs = Number.isFinite(observedAt) && Number.isFinite(currentMs) ? currentMs - observedAt : null;
    const agedRecommendation = Boolean(recommendation.last_good)
      && (ageMs === null || ageMs < 0 || ageMs > 10_000);
    const stale = Boolean(recommendation.stale || agedRecommendation);
    return {
      mode,
      running: recommendation.running,
      interval_ms: recommendation.interval_ms,
      lookahead_enabled: recommendation.lookahead_enabled,
      polls: recommendation.polls,
      successes: recommendation.successes,
      capture: inputs ? {
        ...(inputs.catalog_summary || {}),
        source: inputs.source,
        captured_at: inputs.captured_at || null,
        cache_status: inputs.cache_status || cacheState.status,
        rules_verified: inputs.source === "cache" ? inputs.rules_verified === true : undefined,
        catalog_players: inputs.catalog.length,
        catalog_complete: inputs.catalog_complete === true,
      } : (mode === "recommendation" ? { source: null, captured_at: null, cache_status: cacheState.status } : null),
      capture_progress: captureProgress ? { ...captureProgress } : null,
      blocked_reason: recommendation.blocked_reason,
      error: recommendation.error,
      stale,
      stale_boolean: stale,
      own_team_turn: recommendation.last_good ? recommendation.last_good.own_team_turn : undefined,
      next_turn_distance: recommendation.last_good ? recommendation.last_good.next_turn_distance : undefined,
      current_pick: recommendation.last_good ? recommendation.last_good.current_pick : undefined,
      clock_seconds: recommendation.last_good ? recommendation.last_good.seconds_remaining : undefined,
      next_turn: recommendation.last_good ? {
        distance: recommendation.last_good.next_turn_distance,
        current_pick: recommendation.last_good.current_pick,
        clock: recommendation.last_good.seconds_remaining,
      } : null,
      remaining_universe: recommendation.last_good ? recommendation.last_good.remaining_universe : undefined,
      projection_coverage: recommendation.last_good ? recommendation.last_good.projection_coverage : undefined,
      universe: {
        remaining: recommendation.last_good ? recommendation.last_good.remaining_universe : undefined,
        projection_coverage: recommendation.last_good ? recommendation.last_good.projection_coverage : undefined,
      },
      feed_health: recommendation.blocked_reason ? "blocked" : recommendation.last_good ? "healthy" : "waiting",
      roster_health: recommendation.last_good ? "matched" : "waiting",
      own_roster_count: recommendation.last_good ? recommendation.last_good.own_roster_count : undefined,
      global_drafted_count: recommendation.last_good ? recommendation.last_good.global_drafted_count : undefined,
      last_updated: recommendation.last_good ? recommendation.last_good.observed_at : undefined,
      autopick: recommendation.last_good ? recommendation.last_good.autopick : undefined,
      own_turns: recommendation.own_turns.slice(),
      last_good: recommendation.last_good ? { ...recommendation.last_good } : null,
      last: recommendation.last ? { ...recommendation.last } : null,
      diagnostics: recommendation.diagnostics.slice(),
    };
  }

  async function pollRecommendations(generation) {
    if (mode !== "recommendation" || !recommendation.running || generation !== recommendationGeneration) return;
    if (recommendationInFlight) return;
    recommendationInFlight = true;
    recommendation.polls += 1;
    try {
      const active = await ensureRunner();
      let frame = await captureRecommendationFrame(active);
      let validation = validateRecommendationFrame(frame, ownTeam);
      // ESPN can paint the authoritative roster table one render behind the
      // Feed after a manual pick. Give that read-only hydration a short,
      // bounded retry without switching tabs or broadening the catalog.
      if (!validation.ok && /roster and authoritative history are still reconciling/i.test(validation.reason || "")
        && active.observer && typeof active.observer.captureFastFrame === "function") {
        for (let attempt = 0; attempt < 3 && !validation.ok; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 40));
          frame = mergeRecommendationHistory(await active.observer.captureFastFrame());
          validation = validateRecommendationFrame(frame, ownTeam);
        }
      }
      if (!validation.ok) {
        recommendation.blocked_reason = validation.reason;
        recommendation.error = null;
        recommendation.stale = false;
        recommendationDiagnostic({
          event: "feed-health",
          status: "blocked",
          reason: validation.reason,
          history_count: Array.isArray(frame.history) ? frame.history.length : 0,
          roster_ids: Array.isArray(frame.roster) ? frame.roster.map((player) => String(player.player_id || "")) : [],
          own_history_ids: Array.isArray(frame.history) ? frame.history.filter((event) => String(event.team) === String(ownTeam)).map((event) => String(event.player_id || "")) : [],
        });
        log("recommendation-blocked", { reason: validation.reason });
        return;
      }
      let universeSnapshot = null;
      if (recommendationUniverse && typeof recommendationUniverse.observe === "function") {
        universeSnapshot = recommendationUniverse.observe({
          feed: frame.history,
          history: frame.history,
          historyComplete: true,
          currentPick: frame.current_pick,
        });
        if (universeSnapshot && universeSnapshot.blocked === true) {
          const reason = `draft universe blocked: ${(universeSnapshot.blocked_reasons || []).join(", ") || "unresolved feed conflict"}`;
          recommendation.blocked_reason = reason;
          recommendation.error = null;
          recommendation.stale = false;
          recommendationDiagnostic({ event: "feed-health", status: "blocked", reason, pick: frame.current_pick, feed_count: validation.feed_count });
          log("recommendation-blocked", { reason });
          return;
        }
        if (universeSnapshot && universeSnapshot.catalog_complete !== true) {
          const reason = `projection catalog completeness is unverified (${inputs.catalog.length} captured; ${(inputs.catalog_summary && inputs.catalog_summary.termination_reason) || "end not verified"}); holding recommendations`;
          recommendation.blocked_reason = reason;
          recommendation.error = null;
          recommendation.stale = false;
          recommendationDiagnostic({ event: "feed-health", status: "blocked", reason, pick: frame.current_pick, feed_count: validation.feed_count });
          log("recommendation-blocked", { reason });
          return;
        }
      }
      if (recommendationFeed) {
        const priorHistory = recommendationFeed.history;
        const currentHistory = validation.history;
        const prefixMatches = priorHistory.every((event, index) => {
          const candidate = currentHistory[index];
          return candidate
            && candidate.pick_number === event.pick_number
            && String(candidate.player_id) === String(event.player_id)
            && String(candidate.team) === String(event.team);
        });
        const catalogMatches = recommendationFeed.catalog_ids.length === validation.catalog_count
          && recommendationFeed.catalog_ids.every((playerId) => validation.knownPlayers.some((player) => String(player.player_id) === playerId));
        const revision = Number(frame.revision);
        if (currentHistory.length < priorHistory.length || !prefixMatches) {
          const reason = "authoritative feed regressed or conflicted; holding the last good universe";
          recommendation.blocked_reason = reason;
          recommendation.error = null;
          recommendation.stale = false;
          recommendationDiagnostic({ event: "feed-health", status: "blocked", reason, pick: frame.current_pick, feed_count: validation.feed_count });
          log("recommendation-blocked", { reason });
          return;
        }
        if (Number.isFinite(revision) && revision < recommendationFeed.revision) {
          const reason = "authoritative feed revision regressed; holding the last good universe";
          recommendation.blocked_reason = reason;
          recommendation.error = null;
          recommendation.stale = false;
          recommendationDiagnostic({ event: "feed-health", status: "blocked", reason, pick: frame.current_pick, feed_count: validation.feed_count });
          log("recommendation-blocked", { reason });
          return;
        }
        if (!catalogMatches) {
          const reason = "projection catalog changed during observation; holding the last good universe";
          recommendation.blocked_reason = reason;
          recommendation.error = null;
          recommendation.stale = false;
          recommendationDiagnostic({ event: "feed-health", status: "blocked", reason, pick: frame.current_pick, feed_count: validation.feed_count });
          log("recommendation-blocked", { reason });
          return;
        }
      }
      const preview = buildRecommendationPreview(frame, validation, universeSnapshot);
      const result = await owned({
        operation: "recommend-preview",
        session_id: sessionId,
        observation: preview,
        lookahead_enabled: recommendation.lookahead_enabled,
      });
      if (!result || result.error || result.status === "error") {
        throw new PageRunnerError(`recommendation preview failed: ${(result && result.error) || "no response"}`, "recommendation_preview_failed", { result });
      }
      const rec = result.recommendation || result;
      if (!recommendation.running || generation !== recommendationGeneration) return;
      recommendation.successes += 1;
      const globalDraftedCount = universeSnapshot && Number.isInteger(universeSnapshot.drafted_count)
        ? universeSnapshot.drafted_count
        : validation.feed_count;
      const catalogCount = universeSnapshot && Number.isInteger(universeSnapshot.catalog_count)
        ? universeSnapshot.catalog_count
        : validation.catalog_count;
      const eligibleCount = universeSnapshot && Number.isInteger(universeSnapshot.eligible_count)
        ? universeSnapshot.eligible_count
        : catalogCount;
      const completeEligibleCount = universeSnapshot && Number.isInteger(universeSnapshot.complete_eligible_count)
        ? universeSnapshot.complete_eligible_count
        : catalogCount;
      const projectionCoverage = `${completeEligibleCount}/${eligibleCount} eligible`;
      recommendation.last_good = {
        observed_at: frame.observed_at,
        revision: frame.revision,
        current_pick: frame.current_pick,
        feed_count: validation.feed_count,
        catalog_count: validation.catalog_count,
        candidate_count: Array.isArray(preview.available_players) ? preview.available_players.length : 0,
        drafted_count: globalDraftedCount,
        global_drafted_count: globalDraftedCount,
        own_roster_count: validation.roster.length,
        remaining_universe: Math.max(0, catalogCount - globalDraftedCount),
        eligible_count: eligibleCount,
        complete_eligible_count: completeEligibleCount,
        projection_coverage: projectionCoverage,
        blocked_reasons: universeSnapshot && Array.isArray(universeSnapshot.blocked_reasons) ? [...universeSnapshot.blocked_reasons] : [],
      };
      const currentPick = Number(frame.current_pick);
      const ownTeamTurn = frame.current_pick !== null && frame.current_pick !== undefined
        && expectedTeamForPick(currentPick, frame.pick_order) === ownTeam;
      let nextTurnDistance = null;
      let nextTurnPick = null;
      if (Number.isInteger(currentPick) && currentPick >= 1 && currentPick <= 140) {
        for (let pick = currentPick; pick <= 140; pick += 1) {
          if (expectedTeamForPick(pick, frame.pick_order) === ownTeam) {
            nextTurnPick = pick;
            nextTurnDistance = pick - currentPick;
            break;
          }
        }
      }
      recommendation.last_good.own_team_turn = ownTeamTurn;
      recommendation.last_good.next_turn_distance = nextTurnDistance;
      recommendation.last_good.next_turn_pick = nextTurnPick;
      recommendation.last_good.seconds_remaining = frame.seconds_remaining;
      recommendation.last_good.autopick = frame.autopick;
      recommendationFeed = {
        history: validation.history.map((event) => ({
          pick_number: event.pick_number,
          player_id: String(event.player_id),
          team: String(event.team),
        })),
        catalog_ids: validation.knownPlayers.map((player) => String(player.player_id)),
        revision: Number(frame.revision),
      };
      const catalogById = new Map(validation.knownPlayers.map((player) => [String(player.player_id), player]));
      const candidates = Array.isArray(rec.candidates) ? rec.candidates.slice(0, 10).map((candidate) => {
        const catalogPlayer = catalogById.get(String(candidate && candidate.player_id || ""));
        if (!catalogPlayer) return candidate;
        return {
          ...candidate,
          name: candidate.name || candidate.player_name || catalogPlayer.name,
          player_name: candidate.player_name || candidate.name || catalogPlayer.name,
          ...(catalogPlayer.team !== undefined ? { team: catalogPlayer.team } : {}),
          ...(catalogPlayer.eligibility !== undefined ? { eligibility: catalogPlayer.eligibility } : {}),
        };
      }) : [];
      recommendation.last = {
        revision: rec.revision,
        generated_at: rec.generated_at,
        lookahead_status: rec.lookahead_status,
        explanation: rec.explanation,
        candidates,
      };
      const top = recommendation.last.candidates[0] || {};
      if (ownTeamTurn) {
        const ownTurnEvidence = {
          pick: frame.current_pick,
          player_id: top.player_id || null,
          player_name: top.name || top.player_name || null,
          remaining_count: preview.available_players.length,
          observed_at: frame.observed_at,
          policy_status: rec.lookahead_status,
        };
        recommendation.own_turns = [
          ...recommendation.own_turns.filter((item) => item.pick !== ownTurnEvidence.pick),
          ownTurnEvidence,
        ].sort((left, right) => left.pick - right.pick).slice(0, 10);
      }
      recommendationDiagnostic({
        event: "recommendation",
        pick: frame.current_pick,
        player_id: top.player_id || null,
        player_name: top.name || top.player_name || null,
        remaining_count: preview.available_players.length,
        observed_at: frame.observed_at,
        policy_status: rec.lookahead_status,
        revision: frame.revision,
      });
      recommendationDiagnostic({
        event: "feed-health",
        status: "healthy",
        pick: frame.current_pick,
        feed_count: validation.feed_count,
        catalog_count: validation.catalog_count,
      });
      recommendation.blocked_reason = null;
      recommendation.error = null;
      recommendation.stale = false;
      log("recommendation", { revision: rec.revision, candidates: Array.isArray(rec.candidates) ? rec.candidates.length : 0 });
    } catch (error) {
      recommendation.error = String(error && error.message ? error.message : error);
      recommendation.blocked_reason = recommendation.error;
      recommendation.stale = /stale|fresh|age/i.test(recommendation.error);
      recommendationDiagnostic({ event: "feed-health", status: "error", reason: recommendation.error });
      log("recommendation-error", { error: recommendation.error });
    } finally {
      recommendationInFlight = false;
    }
  }

  function startRecommendations({ intervalMs = 1_000, lookaheadEnabled = false } = {}) {
    if (mode !== "recommendation") actorDisabled("startRecommendations");
    if (recommendation.running) throw new PageRunnerError("recommendation polling is already running", "recommendation_running");
    if (!Number.isFinite(Number(intervalMs)) || Number(intervalMs) < 100) throw new PageRunnerError("recommendation interval must be at least 100 ms", "invalid_recommendation_interval");
    if (!runner && !inputs) throw new PageRunnerError("capture or supply rules and catalog before starting recommendations", "missing_inputs");
    recommendation.running = true;
    recommendation.interval_ms = Math.trunc(Number(intervalMs));
    recommendation.lookahead_enabled = Boolean(lookaheadEnabled);
    recommendation.blocked_reason = null;
    recommendation.error = null;
    recommendation.stale = false;
    const generation = ++recommendationGeneration;
    const loop = async () => {
      await pollRecommendations(generation);
      if (!recommendation.running || generation !== recommendationGeneration) return;
      recommendationTimer = setTimeoutFn(loop, recommendation.interval_ms);
    };
    loop().catch((error) => {
      recommendation.error = String(error && error.message ? error.message : error);
      recommendation.blocked_reason = recommendation.error;
    });
    return recommendationSummary();
  }

  function stopRecommendations() {
    if (mode !== "recommendation") actorDisabled("stopRecommendations");
    recommendationGeneration += 1;
    recommendation.running = false;
    if (recommendationTimer !== null) {
      clearTimeoutFn(recommendationTimer);
      recommendationTimer = null;
    }
    return recommendationSummary();
  }

  async function refreshRecommendations() {
    if (mode !== "recommendation") actorDisabled("refreshRecommendations");
    if (!runner && !inputs) throw new PageRunnerError("capture or supply rules and catalog before refreshing recommendations", "missing_inputs");
    const wasRunning = recommendation.running;
    const generation = wasRunning ? recommendationGeneration : ++recommendationGeneration;
    if (!wasRunning) recommendation.running = true;
    try {
      await pollRecommendations(generation);
      return recommendationSummary();
    } finally {
      if (!wasRunning) recommendation.running = false;
    }
  }

  /** Read-only: fresh paused frame + League Manager timer read-back -> coordinator preflight. */
  async function preflight() {
    actorDisabled("preflight");
    const active = await ensureRunner();
    const frame = await active.prepareActivation();
    await switcher.showLeagueManager();
    try {
      const controls = await tab.playwright.locator("body").evaluate(nativeActivation.inspectNativeControls);
      const result = await owned({
        operation: "preflight",
        session_id: sessionId,
        observation: frame,
        preflight: { native_timer_seconds: controls.native_timer_seconds, expected_seat: expectedSeat },
      });
      log("preflight", { ok: Boolean(result && result.ok), status: result && result.status });
      return {
        ...result,
        read_back: {
          native_timer_seconds: controls.native_timer_seconds,
          native_timer_label: controls.native_timer_label,
          resume: controls.resume.status,
          autopick: frame.autopick,
          current_pick: frame.current_pick,
          on_clock_team: frame.on_clock_team,
          pick_order_seat: frame.pick_order.indexOf(ownTeam) + 1,
          history: frame.history.length,
          roster: frame.roster.length,
          available_players: frame.available_players.length,
          warnings: frame.warnings,
        },
      };
    } finally {
      await switcher.showPlayers();
    }
  }

  /** After the owner's explicit Start: one native Resume and the local start. */
  async function activate() {
    actorDisabled("activate");
    const active = await ensureRunner();
    const pausedFrame = await active.prepareActivation();
    if (!attachmentCheckpoint) await checkpointFreshAttachment(pausedFrame);
    const result = await active.activate();
    if (result && result.status === "activated") attachmentResumeStatus = await coordinatorStatus();
    log("activate", { status: result.status, reason: result.reason, native_resume_clicked: result.native_resume_clicked });
    return result;
  }

  /** Start the awaited foreground batch inside the page; returns immediately. */
  function startBatch(options = {}) {
    actorDisabled("startBatch");
    if (batch && batch.running) throw new PageRunnerError("a batch is already running", "batch_running");
    if (!runner) throw new PageRunnerError("activate() must succeed before a batch", "not_activated");
    batch = { running: true, started_at: new Date(now()).toISOString(), result: null, error: null };
    const current = batch;
    const batchOptions = { ...options };
    const hasExplicitCheckpoint = Object.prototype.hasOwnProperty.call(options, "initialCheckpoint");
    if (!attachmentCheckpointConsumed && !hasExplicitCheckpoint && attachmentCheckpoint) {
      batchOptions.initialCheckpoint = attachmentCheckpoint;
      batchOptions.initialOwnPicksConfirmed = attachmentCheckpoint.own_picks_confirmed;
      batchOptions.resumeCoordinatorStatus = attachmentResumeStatus;
    }
    // Mark the attachment boundary consumed even when the caller supplied an
    // explicit checkpoint or explicitly chose null. This prevents a later
    // direct startBatch() from silently falling back to the stale attachment
    // checkpoint after the first batch has advanced the draft.
    attachmentCheckpointConsumed = true;
    current.promise = runner.runBatch(batchOptions).then((result) => {
      current.result = result;
      log("batch-finished", { status: result.status, reason: result.reason || null, own_picks_confirmed: result.own_picks_confirmed });
      return result;
    }, (error) => {
      current.error = String(error && error.message ? error.message : error);
      log("batch-error", { error: current.error });
      return null;
    }).finally(() => { current.running = false; });
    log("batch-started");
    return { started_at: current.started_at };
  }

  async function recover(stop) {
    actorDisabled("recover");
    if (!runner) throw new PageRunnerError("nothing to recover before activation", "not_activated");
    if (batch && batch.running) throw new PageRunnerError("a batch is still running", "batch_running");
    const result = await runner.recover(stop || (batch && batch.result));
    log("recover", { recovered: Boolean(result && result.recovered), reason: result && (result.reason || null) });
    return result;
  }

  /**
   * Recover a stopped batch and, if the explicit resume succeeded, continue in
   * the same call with the checkpoint, carried own-pick count and the fresh
   * coordinator status the resumed batch requires.
   */
  async function recoverAndContinue(options = {}) {
    actorDisabled("recoverAndContinue");
    const result = await recover();
    if (!result || !result.recovered) return { recovered: false, reason: result && (result.reason || result.error || null), log: result && result.log };
    startBatch({ ...options, initialCheckpoint: result.checkpoint, initialOwnPicksConfirmed: result.ownPicksConfirmed, resumeCoordinatorStatus: result.resumeStatus });
    return { recovered: true, own_picks_confirmed: result.ownPicksConfirmed, resumed_state: result.resumeStatus && result.resumeStatus.state };
  }

  /** Read-only export for future upgrades; excludes credentials and live feed state. */
  function cacheSnapshot() {
    if (mode !== "recommendation") throw new PageRunnerError("cacheSnapshot is available only in recommendation mode", "recommendation_mode_only", { action: "cacheSnapshot" });
    return cachedSnapshot ? copyJson(cachedSnapshot) : null;
  }

  function summary() {
    return {
      session_id: sessionId,
      inputs: inputs ? {
        source: inputs.source,
        captured_at: inputs.captured_at || null,
        cache_status: inputs.cache_status || cacheState.status,
        catalog_players: inputs.catalog.length,
        rules: inputs.rules,
      } : null,
      runner_ready: Boolean(runner),
      mode,
      attachment: attachmentCheckpoint ? { checkpoint_id: attachmentCheckpoint.checkpoint_id, own_picks_confirmed: attachmentCheckpoint.own_picks_confirmed, revision: attachmentCheckpoint.revision } : null,
      batch: batch ? { running: batch.running, started_at: batch.started_at, status: batch.result && batch.result.status, reason: batch.result && (batch.result.reason || null), own_picks_confirmed: batch.result && batch.result.own_picks_confirmed, error: batch.error } : null,
      recommendation: recommendationSummary(),
      events: events.slice(-25),
    };
  }

  return Object.freeze({
    captureInputs,
    scanInputs,
    preflight,
    activate,
    startBatch,
    awaitBatch: async () => (batch ? batch.promise : null),
    recover,
    recoverAndContinue,
    startRecommendations,
    stopRecommendations,
    refreshRecommendations,
    recommendationSummary,
    cacheSnapshot,
    summary,
    events: () => events.slice(),
    evidence: () => (runner ? runner.evidenceSnapshot() : null),
    coordinatorStatus,
  });
}

/**
 * The `window.FBL` object installed by the bundle.  `configure` keeps the CSRF
 * token in this closure; `attach` creates the one runner for this page.
 */
function createPageApi({ bundle, bundleHash, sourceHashes, modules }) {
  let config = null;
  let current = null;
  const pageGlobals = () => ({ document: globalThis.document, window: globalThis.window, location: globalThis.location, fetchFn: globalThis.fetch.bind(globalThis) });
  return Object.freeze({
    bundle,
    bundle_hash: bundleHash,
    source_hashes: Object.freeze({ ...sourceHashes }),
    modules,
    configure({ baseUrl = "http://127.0.0.1:8765", csrfToken } = {}) {
      if (!csrfToken) throw new PageRunnerError("csrfToken is required", "missing_csrf");
      config = { baseUrl, csrfToken };
      return { configured: true, baseUrl };
    },
    attach(options = {}) {
      if (!config) throw new PageRunnerError("call FBL.configure first", "not_configured");
      if (current && current.summary().batch && current.summary().batch.running) throw new PageRunnerError("a batch is running on the attached runner", "batch_running");
      current = createPageRunner({ ...pageGlobals(), ...config, ...options });
      return current.summary();
    },
    runner() {
      if (!current) throw new PageRunnerError("call FBL.attach first", "not_attached");
      return current;
    },
  });
}

module.exports = {
  PageRunnerError,
  INPUT_CACHE_SCHEMA,
  INPUT_CACHE_VERSION,
  buildRecommendationPreview,
  canonicalModules,
  createPageApi,
  createPageOperator,
  createPageRunner,
  createPageTab,
  inputCacheKey,
  readInputCache,
  validateInputCacheRecord,
  validateRecommendationFrame,
  writeInputCache,
};
