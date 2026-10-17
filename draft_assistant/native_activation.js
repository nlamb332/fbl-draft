"use strict";

/**
 * Native Resume activation for a paused ESPN practice room.
 *
 * Activation is one bounded operation: practice preflight, exactly one native
 * Resume click, the first fresh frame, and the local Start/Resume, all inside
 * the ten second freshness budget.  The Resume control is found by one
 * view-independent locator: role/name, case-insensitive, scoped to the League
 * Manager control region, requiring exactly one visible, enabled match.
 *
 * Three guarded activation attempts on practice room 1000000012 never clicked
 * Resume because the locator was view-dependent (Players) or case-sensitive
 * (League Manager).  The in-page callbacks below are read-only except for the
 * single `clickResumeInPage` call, which re-resolves the control and clicks it
 * only when the same uniqueness rule still holds at that instant.
 */

const DEFAULT_ACTIVATION_BUDGET_MS = 10_000;

class ActivationError extends Error {
  constructor(message, code = "activation_error", detail = {}) {
    super(message);
    this.name = "ActivationError";
    this.code = code;
    this.detail = detail;
  }
}

/**
 * Read-only in-page callback.  Self-contained: it is serialized into the page,
 * so it must not close over module state.  Returns control and timer facts.
 */
function inspectNativeControls(body) {
  const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
  const visible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
  };
  const enabled = (node) => !node.disabled && node.getAttribute("aria-disabled") !== "true";
  const REGION_SELECTOR = '[data-testid="league-manager-controls"], .league-manager-controls, .league-manager-container, .league-manager-control__container, [aria-label="League Manager" i], [aria-label="League Manager controls" i]';
  const regions = Array.from(body.querySelectorAll(REGION_SELECTOR));
  // Nested matches describe one region; keep only the outermost elements.
  const outer = regions.filter((region) => !regions.some((other) => other !== region && Array.from(other.querySelectorAll(REGION_SELECTOR)).includes(region)));
  const seen = new Set();
  const controls = [];
  for (const region of outer) {
    for (const node of Array.from(region.querySelectorAll('button, [role="button"]'))) {
      if (seen.has(node)) continue;
      seen.add(node);
      controls.push({
        name: clean(node.getAttribute("aria-label") || node.getAttribute("title") || node.textContent),
        visible: visible(node),
        enabled: enabled(node),
      });
    }
  }
  const matches = controls.filter((control) => /^resume(?:\s+draft)?$/i.test(control.name));
  const visibleMatches = matches.filter((control) => control.visible);
  const usable = visibleMatches.filter((control) => control.enabled);
  let status;
  let reason;
  if (!outer.length) { status = "no_region"; reason = "League Manager control region is not present"; }
  else if (!matches.length) { status = "missing"; reason = "no Resume control inside the League Manager control region"; }
  else if (!visibleMatches.length) { status = "hidden"; reason = "Resume control exists but is not visible"; }
  else if (usable.length > 1) { status = "ambiguous"; reason = `${usable.length} visible enabled Resume controls match; refusing to guess`; }
  else if (!usable.length) { status = "disabled"; reason = "visible Resume control is disabled"; }
  else { status = "unique"; reason = "exactly one visible enabled Resume control"; }

  // Configured per-pick timer read-back (not the running clock).
  const timerSelects = Array.from(body.querySelectorAll("select")).filter((select) => {
    const label = [select.getAttribute("aria-label"), select.getAttribute("name"), select.getAttribute("id"), select.getAttribute("title"), select.getAttribute("data-testid")].map(clean).join(" ");
    return /timer|time\s*per\s*pick|pick\s*clock|pick\s*time/i.test(label);
  });
  let timerSeconds = null;
  let timerLabel = null;
  if (timerSelects.length === 1) {
    const option = (timerSelects[0].selectedOptions || [])[0];
    const raw = option ? clean(option.getAttribute("value") !== null ? option.getAttribute("value") : option.textContent) : "";
    const number = /^\d+(?:\.\d+)?$/.test(raw) ? Number(raw) : Number.NaN;
    timerLabel = option ? clean(option.textContent) : null;
    if (Number.isFinite(number) && number > 0) timerSeconds = number >= 1000 ? number / 1000 : number;
  }
  return {
    resume: { status, reason, region_count: outer.length, match_count: matches.length, visible_count: visibleMatches.length, usable_count: usable.length },
    controls,
    native_paused: status === "unique" || status === "disabled" || status === "ambiguous" ? true : null,
    native_timer_seconds: timerSeconds,
    native_timer_label: timerLabel,
    timer_select_count: timerSelects.length,
  };
}

/**
 * The only mutating in-page callback.  It re-applies the exact uniqueness rule
 * of `inspectNativeControls` and performs at most one click.
 */
function clickResumeInPage(body) {
  if (typeof document !== "undefined" && document.visibilityState !== undefined && document.visibilityState !== "visible") {
    return { clicked: false, usable_count: 0, reason: "document_hidden" };
  }
  const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
  };
  const REGION_SELECTOR = '[data-testid="league-manager-controls"], .league-manager-controls, .league-manager-container, .league-manager-control__container, [aria-label="League Manager" i], [aria-label="League Manager controls" i]';
  const regions = Array.from(body.querySelectorAll(REGION_SELECTOR));
  const outer = regions.filter((region) => !regions.some((other) => other !== region && Array.from(other.querySelectorAll(REGION_SELECTOR)).includes(region)));
  const candidates = [];
  for (const region of outer) {
    for (const node of Array.from(region.querySelectorAll('button, [role="button"]'))) {
      if (candidates.includes(node)) continue;
      const name = clean(node.getAttribute("aria-label") || node.getAttribute("title") || node.textContent);
      if (/^resume(?:\s+draft)?$/i.test(name) && visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true") candidates.push(node);
    }
  }
  if (candidates.length !== 1) return { clicked: false, usable_count: candidates.length };
  candidates[0].click();
  return { clicked: true, usable_count: 1 };
}

function text(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

function createDeadline(now, budgetMs) {
  const startedAt = now();
  return {
    startedAt,
    elapsed: () => now() - startedAt,
    remaining: () => budgetMs - (now() - startedAt),
  };
}

function bounded(deadline, stage, promiseFactory, setTimer, clearTimer) {
  const remaining = deadline.remaining();
  if (remaining <= 0) return Promise.reject(new ActivationError(`activation budget exhausted before ${stage}`, "budget_exceeded", { stage }));
  let handle;
  const timeout = new Promise((_resolve, reject) => {
    handle = setTimer(() => reject(new ActivationError(`activation budget exhausted during ${stage}`, "budget_exceeded", { stage })), remaining);
  });
  return Promise.race([Promise.resolve().then(promiseFactory), timeout]).finally(() => clearTimer(handle));
}

/**
 * Activate a verified, paused practice room.
 *
 * `observe` returns a fresh DraftObservation (default: observer.captureFastFrame).
 * `requestOperator` is the panel/HTTP operator transport.  Nothing is clicked
 * unless the locator is unique AND the coordinator preflight accepted the
 * paused frame.  After the click the operation never retries: any later
 * failure is reported with `native_resume_clicked: true` so the operator can
 * pause the native room explicitly.
 */
async function activateDraft({
  tab,
  observer,
  observe = null,
  observePaused = null,
  requestOperator,
  sessionId,
  ownerToken = null,
  expectedSeat,
  command = "start",
  now = Date.now,
  budgetMs = DEFAULT_ACTIVATION_BUDGET_MS,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  const steps = [];
  const deadline = createDeadline(now, budgetMs);
  let clicked = false;
  const mark = (name, extra = {}) => steps.push({ step: name, at_ms: deadline.elapsed(), ...extra });
  const finish = (status, reason, extra = {}) => ({
    status,
    outcome: status,
    reason,
    native_resume_clicked: clicked,
    elapsed_ms: deadline.elapsed(),
    budget_ms: budgetMs,
    steps,
    ...extra,
  });

  if (!tab || !tab.playwright || typeof tab.playwright.locator !== "function") return finish("refused", "an active draft tab handle is required");
  if (!text(sessionId)) return finish("refused", "session_id is required");
  if (typeof requestOperator !== "function") return finish("refused", "an operator transport is required");
  if (!Number.isInteger(expectedSeat) || expectedSeat < 1 || expectedSeat > 14) return finish("refused", "expectedSeat (verified 1-14) is required");
  if (!["start", "resume"].includes(command)) return finish("refused", "command must be start or resume");
  const capture = observe || (observer && typeof observer.captureFastFrame === "function" ? () => observer.captureFastFrame() : null);
  if (!capture) return finish("refused", "an observer or observe callback is required");
  const withOwner = (payload) => (ownerToken ? { ...payload, owner_token: ownerToken } : payload);
  const run = (stage, factory) => bounded(deadline, stage, factory, setTimer, clearTimer);

  try {
    // 1. Read-only inspection: locator uniqueness and timer read-back.
    const inspection = await run("inspect", () => tab.playwright.locator("body").evaluate(inspectNativeControls));
    mark("inspect", { resume_status: inspection.resume.status });
    if (inspection.resume.status !== "unique") {
      return finish("refused", `Resume locator: ${inspection.resume.reason}`, { stage: "locator", inspection });
    }

    // 2. Paused frame + coordinator preflight. No click until this passes.
    // The paused frame is taken on the current view so the Resume control is
    // still the one just located; only the post-click frame needs Players.
    const paused = await run("paused-frame", observePaused || capture);
    mark("paused-frame");
    const preflightPayload = { native_timer_seconds: inspection.native_timer_seconds, expected_seat: expectedSeat };
    const preflight = await run("preflight", () => requestOperator(withOwner({
      operation: "preflight",
      session_id: sessionId,
      observation: paused,
      preflight: preflightPayload,
    })));
    mark("preflight", { status: preflight && preflight.status });
    if (!preflight || preflight.error || preflight.status === "error" || preflight.ok === false) {
      return finish("refused", `practice preflight refused: ${(preflight && (preflight.error || (preflight.reasons || []).join("; "))) || "no response"}`, { stage: "preflight", inspection, preflight });
    }

    // 3. The single native click (re-resolves atomically; never retried).
    await run("pre-click", async () => undefined);
    const click = await run("click", () => tab.playwright.locator("body").evaluate(clickResumeInPage));
    mark("click", { clicked: click.clicked });
    if (!click.clicked) {
      if (click.reason === "document_hidden") {
        let visibilityPause = null;
        try {
          visibilityPause = await run("visibility-pause", () => requestOperator(withOwner({
            operation: "pause",
            session_id: sessionId,
            reason: "draft page became hidden before native Resume",
          })));
        } catch (error) {
          visibilityPause = { error: String(error && error.message ? error.message : error) };
        }
        return finish("refused", "draft page became hidden before native Resume; coordinator paused", {
          stage: "visibility",
          inspection,
          click,
          visibility_pause: visibilityPause,
        });
      }
      return finish("refused", "Resume was no longer unique at click time; nothing clicked", { stage: "click", inspection, click });
    }
    clicked = true;

    // 4. First fresh frame after Resume, then local Start/Resume with it.
    const first = await run("first-frame", capture);
    mark("first-frame");
    const started = await run("start", () => requestOperator(withOwner({
      operation: command,
      session_id: sessionId,
      observation: first,
      preflight: preflightPayload,
    })));
    mark("start", { status: started && started.status });
    if (!started || started.error || started.status === "error") {
      return finish("failed", `native Resume was clicked but local ${command} failed: ${(started && started.error) || "no response"}`, { stage: "start", inspection, first_frame: first, started });
    }
    if (deadline.elapsed() > budgetMs) {
      return finish("failed", "activation exceeded the freshness budget", { stage: "budget", inspection, first_frame: first, started });
    }
    return finish("activated", "native Resume clicked, fresh frame published, session running", { inspection, first_frame: first, started });
  } catch (error) {
    const code = error && error.code ? error.code : "activation_failed";
    return finish(code === "budget_exceeded" ? "budget_exceeded" : "failed", String(error && error.message ? error.message : error), { stage: error && error.detail ? error.detail.stage : undefined });
  }
}

module.exports = {
  ActivationError,
  DEFAULT_ACTIVATION_BUDGET_MS,
  activateDraft,
  clickResumeInPage,
  inspectNativeControls,
};
