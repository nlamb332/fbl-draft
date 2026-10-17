"use strict";

/**
 * A small, observation-only control surface for the ESPN draft page.
 *
 * The page runner owns the observation lifecycle. This module deliberately
 * knows nothing about activation, Resume, Draft, batches, or recovery. That
 * separation matters when the browser tab is merely a read-only window into a
 * live draft: the panel can explain what it knows without ever taking a pick.
 */

const first = (source, ...keys) => {
  if (!source || typeof source !== "object") return undefined;
  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null && source[key] !== "") return source[key];
  }
  return undefined;
};

const valueOr = (source, key, fallback) => source && source[key] !== undefined && source[key] !== null ? source[key] : fallback;

const objectAt = (source, ...keys) => {
  const value = first(source, ...keys);
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
};

function text(value, fallback = "Unknown") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value ? "Healthy" : "Unavailable";
  return String(value);
}

function numberText(value, fallback = "Unknown") {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) return fallback;
  return String(value);
}

function scoreText(value, fallback = "Unknown") {
  if (value === undefined || value === null || value === "") return fallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return numeric.toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
}

function clockText(value, fallback = "Unknown") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "string" && /^\d{1,3}:\d{2}$/.test(value.trim())) return value.trim();
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return String(value);
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function formatTime(value) {
  if (value === undefined || value === null || value === "") return "Unknown";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

function normalizeSummary(summary) {
  const root = summary && typeof summary === "object" ? summary : {};
  const last = objectAt(root, "last");
  const lastGood = objectAt(root, "last_good");
  const nextTurn = objectAt(root, "next_turn");
  const candidates = Array.isArray(last.candidates) ? last.candidates : [];
  const candidate = candidates[0] && typeof candidates[0] === "object" ? candidates[0] : {};
  const blockedReason = valueOr(root, "blocked_reason", undefined);
  const error = valueOr(root, "error", undefined);
  const stale = root.stale === true || last.stale === true;
  const blocked = blockedReason !== undefined && blockedReason !== null && blockedReason !== "";
  const status = error ? "error" : blocked ? "blocked" : stale ? "stale" : root.running ? "observing" : root.mode === "recommendation" ? "ready" : valueOr(root, "status", "idle");
  const autopick = root.autopick === true ? true : root.autopick === false ? false : undefined;
  const catalogCount = valueOr(lastGood, "catalog_count", undefined);
  const rosterHealth = valueOr(root, "roster_health", valueOr(lastGood, "own_roster_count", undefined));
  const completeProjectionCount = valueOr(root, "complete_projection_count", valueOr(lastGood, "complete_projection_count", undefined));
  const projectionCatalogCount = valueOr(root, "catalog_count", valueOr(lastGood, "catalog_count", undefined));
  const draftedCount = valueOr(lastGood, "drafted_count", undefined);
  // candidate_count is the scorable/eligible pool. A full remaining count is
  // only derived when the complete catalog and drafted history are both known.
  const derivedRemaining = Number.isFinite(Number(catalogCount)) && Number.isFinite(Number(draftedCount))
    ? Math.max(0, Number(catalogCount) - Number(draftedCount))
    : undefined;
  const feedHealth = valueOr(root, "feed_health", undefined);
  const diagnostics = valueOr(root, "diagnostics", valueOr(root, "events", undefined));
  const ownTurns = Array.isArray(root.own_turns) ? root.own_turns : [];
  const captureProgress = captureProgressValue(valueOr(root, "capture_progress", valueOr(root, "capture", undefined)));
  const capture = objectAt(root, "capture");
  const captureSource = first(capture, "source", "input_source");
  const captureAt = first(capture, "captured_at", "timestamp", "observed_at");

  return {
    raw: root,
    status: text(status, "idle"),
    stale,
    blocked,
    error: error ? text(error) : "",
    blockedReason: blocked ? text(blockedReason) : "",
    recommendation: {
      player: valueOr(candidate, "name", valueOr(candidate, "player_name", undefined)),
      team: valueOr(candidate, "team", valueOr(candidate, "team_name", undefined)),
      eligibility: valueOr(candidate, "eligibility", valueOr(candidate, "position", undefined)),
      score: valueOr(candidate, "score", undefined),
      reason: valueOr(candidate, "explanation", valueOr(last, "explanation", undefined)),
      candidates,
    },
    nextTurn: {
      distance: valueOr(nextTurn, "distance", valueOr(root, "next_turn_distance", valueOr(lastGood, "next_turn_distance", undefined))),
      currentPick: valueOr(nextTurn, "current_pick", valueOr(root, "current_pick", valueOr(lastGood, "current_pick", undefined))),
      clock: valueOr(nextTurn, "clock", valueOr(nextTurn, "seconds_remaining", valueOr(root, "clock_seconds", valueOr(root, "clock", undefined)))),
      ownTeamTurn: typeof root.own_team_turn === "boolean" ? root.own_team_turn : typeof lastGood.own_team_turn === "boolean" ? lastGood.own_team_turn : undefined,
    },
    universe: {
      remaining: valueOr(root, "remaining_universe", valueOr(root, "remaining_count", valueOr(lastGood, "remaining_universe", valueOr(lastGood, "remaining_count", derivedRemaining)))),
      coverage: valueOr(root, "projection_coverage", Number.isFinite(Number(completeProjectionCount)) && Number.isFinite(Number(projectionCatalogCount)) ? `${completeProjectionCount}/${projectionCatalogCount}` : undefined),
    },
    health: feedHealth && typeof feedHealth === "object" ? valueOr(feedHealth, "status", valueOr(feedHealth, "label", undefined)) : valueOr(root, "feed_health", undefined),
    rosterHealth,
    updated: valueOr(root, "last_updated", valueOr(last, "generated_at", valueOr(lastGood, "observed_at", undefined))),
    autopick,
    captureProgress,
    captureMeta: captureSource !== undefined || captureAt !== undefined ? { source: captureSource, capturedAt: captureAt } : undefined,
    diagnostics: diagnostics === undefined && ownTurns.length === 0 && !root.capture ? undefined : { capture: root.capture || null, own_turns: ownTurns, events: diagnostics === undefined ? [] : diagnostics },
  };
}

function appendText(document, parent, tag, content, style = "") {
  const node = document.createElement(tag);
  node.textContent = content;
  if (style) node.style.cssText = style;
  parent.appendChild(node);
  return node;
}

function field(document, parent, label, value) {
  const item = document.createElement("div");
  item.style.cssText = "min-width:0;padding:7px 9px;border:1px solid #d9e2ec;border-radius:8px;background:#f7f9fc";
  appendText(document, item, "dt", label, "font-size:11px;color:#526273;font-weight:700;letter-spacing:.03em;text-transform:uppercase");
  appendText(document, item, "dd", value, "margin:3px 0 0;color:#172b4d;font-weight:650;overflow-wrap:anywhere");
  parent.appendChild(item);
  return item;
}

function button(document, parent, label, action, style = "") {
  const control = document.createElement("button");
  control.type = "button";
  control.textContent = label;
  control.setAttribute("aria-label", label);
  control.style.cssText = `border:1px solid #1677ff;border-radius:7px;background:#fff;color:#0755b5;padding:7px 10px;font:600 12px system-ui,sans-serif;cursor:pointer;${style}`;
  control.addEventListener("click", event => {
    if (control.disabled || control.getAttribute?.("aria-disabled") === "true") return undefined;
    return action(event);
  });
  parent.appendChild(control);
  return control;
}

function setDisabled(control, disabled) {
  if (!control) return;
  control.disabled = Boolean(disabled);
  control.setAttribute("aria-disabled", disabled ? "true" : "false");
  control.style.opacity = disabled ? "0.52" : "1";
  control.style.cursor = disabled ? "not-allowed" : "pointer";
}

function finiteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : undefined;
}

function viewportSize(document) {
  const view = document && document.defaultView ? document.defaultView : globalThis;
  const width = finiteNumber(view && view.innerWidth) || finiteNumber(document && document.documentElement && document.documentElement.clientWidth) || 1024;
  const height = finiteNumber(view && view.innerHeight) || finiteNumber(document && document.documentElement && document.documentElement.clientHeight) || 768;
  return { width: Math.max(1, width), height: Math.max(1, height) };
}

function captureProgressValue(value) {
  if (!value || typeof value !== "object") return undefined;
  const progress = first(value, "capture_progress", "progress_state", "capture");
  return progress && typeof progress === "object" && !Array.isArray(progress) ? progress : value;
}

function captureProgressText(progress) {
  const current = captureProgressValue(progress);
  if (!current) return "";
  const rawPhase = text(first(current, "phase", "status"), "Scanning player IDs");
  const phaseKey = rawPhase.toLowerCase().replace(/[_-]+/g, " ");
  const phase = phaseKey.includes("wait") || phaseKey.includes("start") ? "Waiting for practice draft to start" : phaseKey.includes("paus") ? "Pausing practice draft" : phaseKey.includes("repair") ? "Repairing player IDs" : phaseKey.includes("complete") || phaseKey.includes("done") ? "Capture complete" : phaseKey.includes("scan") || phaseKey.includes("catalog") || phaseKey.includes("player") ? "Scanning player IDs" : phaseKey.includes("rule") ? "Reading scoring rules" : rawPhase;
  const captured = first(current, "catalog_count", "captured", "catalog_players", "player_count");
  const percentage = finiteNumber(first(current, "progress", "fraction", "percent"));
  const percentValue = percentage === undefined ? undefined : percentage <= 1 ? Math.round(percentage * 100) : Math.round(percentage);
  const parts = [phase];
  if (captured !== undefined) parts.push(`${numberText(captured)} captured`);
  if (percentValue !== undefined) parts.push(`${Math.max(0, Math.min(100, percentValue))}%`);
  const repairable = first(current, "repairable_count", "repairable");
  if (repairable !== undefined && phaseKey.includes("repair")) parts.push(`${numberText(repairable)} repairable`);
  const pass = first(current, "pass", "attempt");
  if (pass !== undefined && phaseKey.includes("repair")) parts.push(`pass ${numberText(pass)}`);
  const steps = Array.isArray(current.steps) ? current.steps.length : finiteNumber(current.steps);
  if (steps !== undefined && phaseKey.includes("repair")) parts.push(`${numberText(steps)} steps`);
  return parts.join(" · ");
}

/** Mount an accessible observation panel over the live ESPN page. */
function mountPageControls({
  document,
  runner,
  bundleHash,
  setIntervalFn = globalThis.setInterval,
  clearIntervalFn = globalThis.clearInterval,
  pollMs = 1500,
} = {}) {
  if (!document || !runner) throw new Error("document and runner are required");
  if (document.getElementById("fbl-attended-controls")) throw new Error("FBL controls already mounted");

  const root = document.createElement("section");
  root.id = "fbl-attended-controls";
  root.setAttribute("aria-label", "FBL draft observation controls");
  root.style.cssText = [
    "position:fixed;bottom:12px;right:12px;z-index:2147483647;box-sizing:border-box;",
    "width:min(460px,calc(100vw - 24px));max-height:min(78vh,720px);overflow:auto;",
    "padding:16px;color:#172b4d;background:#fff;border:1px solid #b8c7d9;border-radius:14px;",
    "box-shadow:0 10px 35px rgba(20,45,80,.22);font:13px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;",
  ].join("");
  root.setAttribute("data-fbl-dock", "right");
  root.setAttribute("data-fbl-collapsed", "false");

  const header = document.createElement("header");
  header.style.cssText = "display:flex;justify-content:space-between;gap:10px;align-items:flex-start";
  const headingWrap = document.createElement("div");
  const headingRow = document.createElement("div");
  headingRow.style.cssText = "display:flex;align-items:center;gap:7px;min-width:0";
  const moveHandle = button(document, headingRow, "Move advisor", () => undefined, "border-color:#9db3cc;color:#294563;padding:5px 8px;cursor:grab;touch-action:none;white-space:nowrap");
  moveHandle.title = "Drag to move the advisor, or use the arrow keys";
  moveHandle.setAttribute("aria-label", "Move advisor. Drag this handle or use arrow keys");
  moveHandle.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight ArrowUp ArrowDown Home End");
  appendText(document, headingRow, "h2", "FBL draft advisor", "margin:0;font-size:18px;line-height:1.2;color:#102a43;overflow-wrap:anywhere");
  headingWrap.appendChild(headingRow);
  appendText(document, headingWrap, "p", "You make picks manually. FBL only observes and recommends.", "margin:4px 0 0;color:#526273;font-size:12px");
  header.appendChild(headingWrap);
  const headerActions = document.createElement("div");
  headerActions.style.cssText = "display:flex;align-items:center;gap:6px;flex-shrink:0";
  const status = appendText(document, headerActions, "span", "Ready", "display:inline-flex;align-items:center;min-height:24px;padding:3px 8px;border-radius:999px;background:#e7f5ed;color:#166534;font-size:11px;font-weight:750;white-space:nowrap");
  status.id = "fbl-attended-status";
  const collapseButton = button(document, headerActions, "Collapse advisor", () => setCollapsed(true), "border-color:#9db3cc;padding:5px 8px;white-space:nowrap");
  collapseButton.setAttribute("aria-controls", "fbl-attended-compact-summary");
  headerActions.appendChild(collapseButton);
  const closeButton = button(document, headerActions, "×", closeAdvisor, "border-color:#9db3cc;padding:3px 8px;font-size:20px;line-height:1;white-space:nowrap");
  closeButton.setAttribute("aria-label", "Close advisor");
  closeButton.title = "Close advisor and stop observing. Click the FBL Advisor bookmark to reopen.";
  header.appendChild(headerActions);
  root.appendChild(header);

  const notice = appendText(document, root, "p", "Capture the paused room to build the full remaining-player universe.", "margin:12px 0 10px;padding:9px 10px;border-left:3px solid #1677ff;background:#f3f7ff;color:#294563;line-height:1.4");
  const actionBar = document.createElement("div");
  actionBar.style.cssText = "display:flex;flex-wrap:wrap;gap:7px;margin:0 0 12px";
  root.appendChild(actionBar);
  const captureButton = button(document, actionBar, "Capture inputs", () => runAction("capture", async () => {
    const scan = typeof runner.scanInputs === "function" ? runner.scanInputs.bind(runner) : runner.captureInputs.bind(runner);
    return scan({
      onProgress: progress => {
        if (disposed) return;
        captureProgress = captureProgressValue(progress);
        applyStatus();
      },
    });
  }));
  const startButton = button(document, actionBar, "Start observing", () => runAction("start", async () => runner.startRecommendations()));
  const refreshButton = button(document, actionBar, "Refresh recommendation", () => runAction("refresh", refreshRecommendation));
  const stopButton = button(document, actionBar, "Stop observing", () => runAction("stop", async () => runner.stopRecommendations()));
  const dockLeftButton = button(document, actionBar, "Dock left", () => dock("left"), "border-color:#9db3cc");
  const dockRightButton = button(document, actionBar, "Dock right", () => dock("right"), "border-color:#9db3cc");

  const compactBar = document.createElement("div");
  compactBar.id = "fbl-attended-compact-summary";
  compactBar.setAttribute("role", "status");
  compactBar.style.cssText = "display:none;align-items:center;justify-content:space-between;gap:10px;padding:4px 0 0;color:#294563;font-size:12px;font-weight:700";
  const compactText = appendText(document, compactBar, "span", "No recommendation yet · Turn unknown", "min-width:0;overflow-wrap:anywhere");
  const expandButton = button(document, compactBar, "Expand advisor", () => setCollapsed(false), "border-color:#9db3cc;padding:5px 8px;white-space:nowrap;flex-shrink:0");
  root.appendChild(compactBar);

  const recommendation = document.createElement("article");
  recommendation.setAttribute("aria-live", "polite");
  recommendation.style.cssText = "padding:14px;border:1px solid #b9d6f8;border-radius:11px;background:linear-gradient(140deg,#f4f9ff,#fff);margin-bottom:12px";
  appendText(document, recommendation, "p", "RECOMMENDED PLAYER", "margin:0;color:#33679a;font-size:11px;font-weight:800;letter-spacing:.08em");
  const playerName = appendText(document, recommendation, "h3", "No recommendation yet", "margin:4px 0 2px;font-size:24px;line-height:1.12;color:#102a43;overflow-wrap:anywhere");
  const playerMeta = appendText(document, recommendation, "p", "Capture inputs and start observing", "margin:0;color:#526273;font-weight:600");
  const score = appendText(document, recommendation, "p", "Score · Unknown", "margin:10px 0 3px;color:#172b4d;font-weight:750");
  const reason = appendText(document, recommendation, "p", "Reason · Waiting for a recommendation.", "margin:0;color:#294563;line-height:1.4");
  const priorityLabel = appendText(document, recommendation, "p", "Priority list", "margin:12px 0 4px;color:#33679a;font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase");
  const priorityList = document.createElement("ol");
  priorityList.style.cssText = "margin:0;padding-left:22px;color:#294563;line-height:1.6";
  recommendation.appendChild(priorityList);
  root.appendChild(recommendation);

  const detailsGrid = document.createElement("dl");
  detailsGrid.style.cssText = "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin:0 0 12px";
  const nextTurn = field(document, detailsGrid, "Next turn", "Unknown");
  const currentPick = field(document, detailsGrid, "Current pick", "Unknown");
  const clock = field(document, detailsGrid, "Clock", "Unknown");
  const ownTurn = field(document, detailsGrid, "Your turn", "Unknown");
  const remaining = field(document, detailsGrid, "Remaining universe", "Unknown");
  const coverage = field(document, detailsGrid, "Projection coverage", "Unknown");
  const feedHealth = field(document, detailsGrid, "Feed health", "Unknown");
  const rosterHealth = field(document, detailsGrid, "Roster health", "Unknown");
  const updated = field(document, detailsGrid, "Last updated", "Unknown");
  const autopick = field(document, detailsGrid, "ESPN Autopick", "Unknown");
  root.appendChild(detailsGrid);

  const diagnostics = document.createElement("details");
  diagnostics.style.cssText = "border-top:1px solid #e1e8f0;padding-top:8px";
  appendText(document, diagnostics, "summary", "Diagnostics", "cursor:pointer;color:#526273;font-size:12px;font-weight:700");
  const diagnosticsBody = appendText(document, diagnostics, "p", "No diagnostics.", "margin:8px 0 0;color:#526273;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.35");
  root.appendChild(diagnostics);
  const footer = appendText(document, root, "p", bundleHash ? `Bundle ${bundleHash}` : "", "margin:9px 0 0;color:#8291a5;font-size:10px");
  document.body.appendChild(root);

  let busy = false;
  let activeAction = null;
  let disposed = false;
  let captured = false;
  let observing = false;
  let lastSummary = null;
  let lastError = "";
  try {
    const runnerState = typeof runner.summary === "function" ? runner.summary() : null;
    captured = Boolean(runnerState && (runnerState.inputs || runnerState.inputs_ready === true));
    const initialRecommendation = runnerState && runnerState.recommendation;
    if (initialRecommendation && typeof initialRecommendation === "object") {
      lastSummary = initialRecommendation;
      observing = initialRecommendation.running === true;
    }
  } catch (_error) {
    captured = false;
  }
  let captureProgress = null;
  let collapsed = false;
  let dragState = null;
  const cleanupHandlers = [];

  function listen(target, eventName, handler, options) {
    if (!target || typeof target.addEventListener !== "function") return;
    target.addEventListener(eventName, handler, options);
    cleanupHandlers.push(() => {
      if (typeof target.removeEventListener === "function") target.removeEventListener(eventName, handler, options);
    });
  }

  function panelMetrics() {
    const viewport = viewportSize(document);
    let rect = null;
    if (typeof root.getBoundingClientRect === "function") {
      try { rect = root.getBoundingClientRect(); } catch (_error) { rect = null; }
    }
    const styleWidth = finiteNumber(root.style && root.style.width);
    const styleHeight = finiteNumber(root.style && root.style.height);
    const width = Math.max(1, finiteNumber(rect && rect.width) || styleWidth || 460);
    const height = Math.max(1, finiteNumber(rect && rect.height) || styleHeight || (collapsed ? 80 : 420));
    const right = finiteNumber(root.style && root.style.right);
    const bottom = finiteNumber(root.style && root.style.bottom);
    const leftStyle = finiteNumber(root.style && root.style.left);
    const topStyle = finiteNumber(root.style && root.style.top);
    const left = finiteNumber(rect && rect.left) ?? (leftStyle !== undefined ? leftStyle : Math.max(0, viewport.width - width - (right || 0)));
    const top = finiteNumber(rect && rect.top) ?? (topStyle !== undefined ? topStyle : Math.max(0, viewport.height - height - (bottom || 0)));
    return { viewport, width, height, left, top };
  }

  function setPosition(left, top) {
    const metrics = panelMetrics();
    const maxLeft = Math.max(0, metrics.viewport.width - metrics.width);
    const maxTop = Math.max(0, metrics.viewport.height - metrics.height);
    const clampedLeft = Math.min(maxLeft, Math.max(0, finiteNumber(left) === undefined ? metrics.left : left));
    const clampedTop = Math.min(maxTop, Math.max(0, finiteNumber(top) === undefined ? metrics.top : top));
    root.style.left = `${Math.round(clampedLeft)}px`;
    root.style.top = `${Math.round(clampedTop)}px`;
    root.style.right = "auto";
    root.style.bottom = "auto";
    root.setAttribute("data-fbl-dock", "free");
    root.setAttribute("data-fbl-position", `${Math.round(clampedLeft)},${Math.round(clampedTop)}`);
    return { left: clampedLeft, top: clampedTop };
  }

  function dock(side) {
    if (disposed) return undefined;
    const metrics = panelMetrics();
    const margin = 12;
    root.style.left = side === "left" ? `${margin}px` : "auto";
    root.style.right = side === "right" ? `${margin}px` : "auto";
    root.style.top = `${Math.max(0, Math.min(metrics.viewport.height - metrics.height, metrics.top))}px`;
    root.style.bottom = "auto";
    root.setAttribute("data-fbl-dock", side === "left" ? "left" : "right");
    root.setAttribute("data-fbl-position", `${side}-edge`);
    return side;
  }

  function setCollapsed(next) {
    if (disposed) return undefined;
    collapsed = Boolean(next);
    root.setAttribute("data-fbl-collapsed", collapsed ? "true" : "false");
    root.setAttribute("aria-expanded", collapsed ? "false" : "true");
    compactBar.style.display = collapsed ? "flex" : "none";
    collapseButton.textContent = collapsed ? "Show full advisor" : "Collapse advisor";
    collapseButton.setAttribute("aria-label", collapsed ? "Show full advisor" : "Collapse advisor");
    collapseButton.style.cursor = "pointer";
    [notice, actionBar, recommendation, detailsGrid, diagnostics, footer].forEach(node => { node.style.display = collapsed ? "none" : ""; });
    root.style.width = collapsed ? "min(380px,calc(100vw - 24px))" : "min(460px,calc(100vw - 24px))";
    root.style.maxHeight = collapsed ? "none" : "min(78vh,720px)";
    updateCompactSummary();
    return collapsed;
  }

  function updateCompactSummary(current = normalizeSummary(lastSummary)) {
    const recommendationState = current.recommendation || {};
    const hasRecommendation = Boolean(recommendationState.player) && !current.blocked && !current.error && !current.stale;
    const player = hasRecommendation ? text(recommendationState.player) : "No recommendation yet";
    const distance = current.nextTurn && current.nextTurn.distance;
    const turn = distance === 0 ? "Your turn" : distance === undefined ? "Turn unknown" : `${text(distance)} picks away`;
    const capture = current.captureMeta;
    const source = capture && capture.source !== undefined ? `source ${text(capture.source)}` : "";
    const capturedAt = capture && capture.capturedAt !== undefined ? `captured ${formatTime(capture.capturedAt)}` : "";
    const metadata = [source, capturedAt].filter(Boolean).join(" · ");
    compactText.textContent = `${player} · ${turn}${metadata ? ` · ${metadata}` : ""}`;
  }

  function captureMetadataText(current) {
    const capture = current && current.captureMeta;
    if (!capture) return "";
    const source = capture.source === undefined ? "" : `source ${text(capture.source)}`;
    const capturedAt = capture.capturedAt === undefined ? "" : `captured ${formatTime(capture.capturedAt)}`;
    const metadata = [source, capturedAt].filter(Boolean).join(" · ");
    return metadata ? ` Inputs ${metadata}.` : "";
  }

  function captureUsesSavedScan(current) {
    const source = current && current.captureMeta && current.captureMeta.source;
    return source !== undefined && /cache/i.test(String(source));
  }

  function eventPointerId(event) {
    return event && event.pointerId === undefined ? 1 : event && event.pointerId;
  }

  function capturePointer(target, pointerId) {
    if (target && typeof target.setPointerCapture === "function" && pointerId !== undefined) {
      try { target.setPointerCapture(pointerId); return true; } catch (_error) { /* browser may reject stale pointers */ }
    }
    return false;
  }

  function releasePointer(target, pointerId) {
    if (target && typeof target.releasePointerCapture === "function" && pointerId !== undefined) {
      try { target.releasePointerCapture(pointerId); } catch (_error) { /* pointer already released */ }
    }
  }

  function beginDrag(event) {
    if (disposed || (event && event.button !== undefined && event.button !== 0)) return;
    const metrics = panelMetrics();
    const pointerId = eventPointerId(event);
    dragState = { pointerId, startX: finiteNumber(event && event.clientX) || 0, startY: finiteNumber(event && event.clientY) || 0, left: metrics.left, top: metrics.top };
    root.setAttribute("data-fbl-dragging", "true");
    moveHandle.setAttribute("aria-pressed", "true");
    capturePointer(root, pointerId);
    capturePointer(moveHandle, pointerId);
    if (event && typeof event.preventDefault === "function") event.preventDefault();
  }

  function moveDrag(event) {
    if (!dragState || disposed) return;
    const pointerId = eventPointerId(event);
    if (dragState.pointerId !== undefined && pointerId !== undefined && dragState.pointerId !== pointerId) return;
    setPosition(dragState.left + (finiteNumber(event && event.clientX) || 0) - dragState.startX, dragState.top + (finiteNumber(event && event.clientY) || 0) - dragState.startY);
    if (event && typeof event.preventDefault === "function") event.preventDefault();
  }

  function endDrag(event) {
    if (!dragState) return;
    const pointerId = eventPointerId(event);
    if (dragState.pointerId !== undefined && pointerId !== undefined && dragState.pointerId !== pointerId) return;
    releasePointer(root, dragState.pointerId);
    releasePointer(moveHandle, dragState.pointerId);
    dragState = null;
    root.setAttribute("data-fbl-dragging", "false");
    moveHandle.setAttribute("aria-pressed", "false");
  }

  function moveBy(deltaX, deltaY) {
    const metrics = panelMetrics();
    return setPosition(metrics.left + deltaX, metrics.top + deltaY);
  }

  function keyboardMove(event) {
    const key = event && event.key;
    const distance = event && event.shiftKey ? 96 : 24;
    if (key === "ArrowLeft") moveBy(-distance, 0);
    else if (key === "ArrowRight") moveBy(distance, 0);
    else if (key === "ArrowUp") moveBy(0, -distance);
    else if (key === "ArrowDown") moveBy(0, distance);
    else if (key === "Home") dock("left");
    else if (key === "End") dock("right");
    else return;
    if (event && typeof event.preventDefault === "function") event.preventDefault();
  }

  listen(moveHandle, "pointerdown", beginDrag);
  listen(moveHandle, "keydown", keyboardMove);
  listen(headingWrap, "pointerdown", event => {
    // The heading is a friendly drag surface; the explicit handle remains the
    // keyboard target and avoids starting a drag when a status/control is used.
    if (event && event.target === moveHandle) return;
    beginDrag(event);
  });
  listen(document, "pointermove", moveDrag);
  listen(document, "pointerup", endDrag);
  listen(document, "pointercancel", endDrag);
  listen(root, "pointermove", moveDrag);
  listen(root, "pointerup", endDrag);

  function summaryState() {
    const current = normalizeSummary(lastSummary);
    return { current };
  }

  function applyStatus() {
    const { current } = summaryState();
    if (current.captureProgress) captureProgress = current.captureProgress;
    const hasError = Boolean(lastError || current.error);
    status.textContent = busy && activeAction === "capture" ? "Capturing" : hasError ? "Error" : current.blocked ? "Blocked" : current.stale ? "Stale" : observing ? "Observing" : (captured ? "Captured" : "Ready");
    status.style.background = hasError || current.blocked ? "#fff0f0" : current.stale ? "#fff7df" : observing ? "#e7f5ed" : "#edf2f7";
    status.style.color = hasError || current.blocked ? "#a12622" : current.stale ? "#8a5b00" : observing ? "#166534" : "#526273";
    const capturedNotice = captureUsesSavedScan(current) ? "Loaded saved scan. Start observing when the ESPN room is ready." : "Inputs captured. Start observing when the ESPN room is ready.";
    const observingNotice = captureUsesSavedScan(current) ? "Watching the full remaining player universe. No draft action is enabled. Loaded saved scan." : "Watching the full remaining player universe. No draft action is enabled.";
    notice.textContent = hasError ? `Observation error: ${lastError || current.error}${current.stale ? " The latest frame is stale." : ""}` : current.blocked ? (current.blockedReason || "The recommendation is blocked until the feed is complete.") : current.stale ? "The latest recommendation is stale; refresh the observation before acting on it." : observing ? observingNotice : captured ? capturedNotice : "Capture the paused room to build the full remaining-player universe.";
    if (!busy || activeAction !== "capture") notice.textContent += captureMetadataText(current);
    if (current.autopick) notice.textContent += " ESPN Autopick is on; that is native ESPN behavior and is separate from this advisor.";
    if (busy && activeAction === "capture") {
      const progressText = captureProgressText(captureProgress || current.captureProgress);
      notice.textContent = progressText ? `Capture in progress · ${progressText}. Keep ESPN paused while capture finishes.` : "Reading the player universe and projections. Keep ESPN paused while capture finishes.";
    }

    const rec = current.recommendation;
    const available = Boolean(rec.player) && !current.blocked && !hasError && !current.stale;
    playerName.textContent = available ? text(rec.player) : hasError ? "Recommendation unavailable" : current.blocked ? "Recommendation blocked" : current.stale ? "Recommendation is stale" : "No recommendation yet";
    playerMeta.textContent = available ? [rec.team ? text(rec.team) : "Team unknown", rec.eligibility ? `Eligibility · ${text(rec.eligibility)}` : "Eligibility · Unknown"].join("  ·  ") : current.error || current.blockedReason || (current.stale ? "Refresh to request a current recommendation." : "Capture inputs and start observing");
    score.textContent = available ? `Score · ${scoreText(rec.score)}` : "Score · Unavailable";
    reason.textContent = `Reason · ${text(rec.reason, available ? "No explanation supplied." : "Waiting for a recommendation.")}`;
    priorityLabel.style.display = available && rec.candidates.length ? "block" : "none";
    while (priorityList.firstChild) priorityList.removeChild(priorityList.firstChild);
    (available ? rec.candidates : []).slice(0, 3).forEach((candidate) => {
      const item = document.createElement("li");
      const name = typeof candidate === "string" ? candidate : valueOr(candidate, "name", valueOr(candidate, "player_name", "Unknown player"));
      const candidateScore = typeof candidate === "object" ? valueOr(candidate, "score", undefined) : undefined;
      item.textContent = candidateScore === undefined ? text(name) : `${text(name)} · ${scoreText(candidateScore)}`;
      priorityList.appendChild(item);
    });
    // Keep the lightweight DOM fixture used by offline tests compatible with
    // real DOM semantics while avoiding innerHTML entirely.
    const setField = (node, value) => { const dd = node.children && Array.from(node.children).find(child => String(child.tagName || child.tag || "").toLowerCase() === "dd"); if (dd) dd.textContent = value; else if (node.children && node.children.length) node.children[node.children.length - 1].textContent = value; };
    setField(nextTurn, current.nextTurn.distance === undefined ? "Unknown" : Number(current.nextTurn.distance) === 0 ? "Your turn" : `${text(current.nextTurn.distance)} picks away`);
    setField(currentPick, numberText(current.nextTurn.currentPick));
    setField(clock, clockText(current.nextTurn.clock));
    setField(ownTurn, current.nextTurn.ownTeamTurn === undefined ? "Unknown" : current.nextTurn.ownTeamTurn ? "Yes · your pick" : "No · another team");
    setField(remaining, numberText(current.universe.remaining));
    setField(coverage, text(current.universe.coverage));
    setField(feedHealth, text(current.health));
    setField(rosterHealth, text(current.rosterHealth));
    setField(updated, formatTime(current.updated));
    setField(autopick, current.autopick === undefined ? "Unknown" : current.autopick ? "ON · native ESPN" : "OFF · manual picks");
    diagnosticsBody.textContent = current.diagnostics === undefined ? "No diagnostics." : (typeof current.diagnostics === "string" ? current.diagnostics : JSON.stringify(current.diagnostics, null, 2));
    updateCompactSummary(current);
    captureButton.textContent = captured ? "Rescan players" : "Capture inputs";
    captureButton.setAttribute("aria-label", captured ? "Rescan players" : "Capture inputs");
    setDisabled(captureButton, busy || observing);
    setDisabled(startButton, busy || observing || !captured);
    setDisabled(refreshButton, busy || !captured);
    setDisabled(stopButton, busy || !observing);
  }

  async function readRecommendation() {
    if (typeof runner.recommendationSummary !== "function") throw new Error("recommendation summary is unavailable");
    const result = await runner.recommendationSummary();
    lastSummary = result || {};
    lastError = "";
    return result;
  }

  async function refreshRecommendation() {
    if (typeof runner.refreshRecommendations === "function") {
      const result = await runner.refreshRecommendations();
      lastSummary = result || await runner.recommendationSummary();
      lastError = "";
      return result;
    }
    // Older runners expose only a read-only summary. Keep this button safe and
    // useful while the runtime rolls out the explicit refresh operation.
    return readRecommendation();
  }

  function extractCaptureProgress(value) {
    if (!value || typeof value !== "object") return undefined;
    if (value.capture_progress && typeof value.capture_progress === "object") return value.capture_progress;
    if (value.capture && typeof value.capture === "object") return value.capture;
    if (["phase", "progress", "catalog_count", "catalog_players", "repairable_count"].some(key => Object.prototype.hasOwnProperty.call(value, key))) return value;
    return undefined;
  }

  async function runAction(action, operation) {
    if (busy || disposed) return undefined;
    busy = true;
    activeAction = action;
    lastError = "";
    applyStatus();
    try {
      const result = await operation();
      if (action === "capture") {
        captured = true;
        captureProgress = extractCaptureProgress(result) || captureProgress;
      }
      if (action === "start") observing = true;
      if (action === "stop") observing = false;
      if ((action !== "capture" && action !== "refresh") || (action === "capture" && typeof runner.recommendationSummary === "function")) await readRecommendation();
      return result;
    } catch (error) {
      lastError = String(error && error.message ? error.message : error);
      return { error: lastError };
    } finally {
      busy = false;
      activeAction = null;
      if (!disposed) applyStatus();
    }
  }

  async function refresh() {
    if (disposed || busy || !captured || typeof runner.recommendationSummary !== "function") return undefined;
    try {
      await readRecommendation();
    } catch (error) {
      lastError = String(error && error.message ? error.message : error);
    }
    applyStatus();
    return lastSummary;
  }

  applyStatus();
  const timer = setIntervalFn(() => { void refresh(); }, pollMs);

  function dispose() {
    if (disposed) return;
    disposed = true;
    dragState = null;
    cleanupHandlers.splice(0).forEach(cleanup => cleanup());
    clearIntervalFn(timer);
    root.remove();
  }

  async function closeAdvisor() {
    if (disposed) return;
    dispose();
    if (typeof runner.stopRecommendations === "function") {
      try {
        return await runner.stopRecommendations();
      } catch (error) {
        return { error: String(error && error.message ? error.message : error) };
      }
    }
  }

  return Object.freeze({
    refresh,
    move: (left, top) => setPosition(left, top),
    dock,
    collapse: () => setCollapsed(true),
    expand: () => setCollapsed(false),
    dispose,
    root,
    summary: () => normalizeSummary(lastSummary),
  });
}

module.exports = { mountPageControls, normalizeSummary };
