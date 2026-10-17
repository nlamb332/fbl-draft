"use strict";

/**
 * Read-only live-room probe (#43).
 *
 * The activation, view-switch, and timer locators were built from fixtures.
 * This probe runs those exact in-page functions against the real ESPN room
 * without clicking, captures a sanitized structural view of the controls the
 * locators look for, and checks that the page can reach the local
 * coordinator (CSP connect-src, Chrome Local Network Access, CORS/PNA).
 *
 * Usage: `node draft_assistant/live_probe.js [--base URL]` prints a snippet to
 * evaluate in the draft-room tab.  The snippet never clicks and never sends a
 * draft observation; its coordinator request has no observation and is
 * rejected without side effects.
 */

const { inspectNativeControls } = require("./native_activation.js");
const { inspectGridKinds, resolveViewTab } = require("./view_switch.js");

const VIEW_PATTERNS = Object.freeze({ players: "^players$", history: "^pick\\s+history$" });

/** In-page, self-contained. Structural descriptors only: no player data. */
function captureLocatorContext(body) {
  const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim()).slice(0, 80);
  const describe = (node) => ({
    tag: node.tagName.toLowerCase(),
    role: node.getAttribute("role"),
    aria_label: node.getAttribute("aria-label"),
    testid: node.getAttribute("data-testid"),
    classes: clean(node.getAttribute("class")),
  });
  const ancestry = (node) => {
    const chain = [];
    for (let current = node.parentElement; current && current !== body && chain.length < 6; current = current.parentElement) chain.push(describe(current));
    return chain;
  };
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
  };
  const CONTROL = /^(resume|resume draft|pause|pause draft|start|start practice draft|players|pick history|draft board|league manager|my team|autopick|auto pick)$/i;
  const controls = [];
  for (const node of Array.from(body.querySelectorAll('button, [role="button"], [role="tab"], a'))) {
    const name = clean(node.getAttribute("aria-label") || node.getAttribute("title") || node.textContent);
    if (!CONTROL.test(name)) continue;
    controls.push({ name, ...describe(node), visible: visible(node), disabled: Boolean(node.disabled) || node.getAttribute("aria-disabled") === "true", ancestry: ancestry(node) });
  }
  const selects = Array.from(body.querySelectorAll("select")).map((select) => ({
    ...describe(select),
    name: select.getAttribute("name"),
    id: select.getAttribute("id"),
    title: select.getAttribute("title"),
    selected: clean(((select.selectedOptions || [])[0] || {}).textContent),
    options: Array.from(select.options || []).slice(0, 12).map((option) => clean(option.textContent)),
    ancestry: ancestry(select),
  }));
  const tablists = Array.from(body.querySelectorAll('[role="tablist"]')).map((list) => ({
    ...describe(list),
    tabs: Array.from(list.querySelectorAll('[role="tab"], button')).map((tab) => clean(tab.getAttribute("aria-label") || tab.textContent)),
  }));
  return {
    controls,
    selects,
    tablists,
    player_buttons: body.querySelectorAll("button[data-player-id]").length,
    grids: body.querySelectorAll('[role="grid"]').length,
  };
}

/** In-page. Classifies the path from this page to the local coordinator. */
async function probeCoordinator({ base, token, timeoutMs = 5000 }) {
  const violations = [];
  const onViolation = (event) => violations.push({ directive: event.violatedDirective, blocked: event.blockedURI });
  document.addEventListener("securitypolicyviolation", onViolation);
  const attempt = async (init) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${base}/api/heartbeat`, { ...init, method: "POST", signal: controller.signal });
      let readable = null;
      try { readable = init.mode === "no-cors" ? null : await response.json(); } catch (_error) { readable = null; }
      return { ok: true, http_status: response.status, type: response.type, body: readable };
    } catch (error) {
      return { ok: false, error: String((error && error.name) || error) };
    } finally {
      clearTimeout(timer);
    }
  };
  // 1. Simple no-cors request: proves network reachability (CSP + Local Network Access) only.
  const reach = await attempt({ mode: "no-cors", headers: { "Content-Type": "text/plain" }, body: "probe" });
  // 2. Real CORS request with the token and no observation: proves CORS/PNA preflight and token.
  const cors = token ? await attempt({ mode: "cors", headers: { "Content-Type": "application/json", "X-CSRF-Token": token }, body: JSON.stringify({ probe: true }) }) : null;
  await new Promise((resolve) => setTimeout(resolve, 50));
  document.removeEventListener("securitypolicyviolation", onViolation);
  let verdict;
  if (violations.length) verdict = "csp_blocked";
  else if (!reach.ok) verdict = "network_blocked";
  else if (!cors) verdict = "reachable_token_untested";
  else if (!cors.ok) verdict = "cors_or_pna_blocked";
  else if (cors.http_status === 403) verdict = "token_or_origin_rejected";
  else verdict = "ok";
  return { verdict, csp_violations: violations, no_cors: reach, cors };
}

function buildProbeSnippet({ base = "http://127.0.0.1:8765", token = null } = {}) {
  return `(async () => {
  const inspectNativeControls = ${inspectNativeControls.toString()};
  const resolveViewTab = ${resolveViewTab.toString()};
  const inspectGridKinds = ${inspectGridKinds.toString()};
  const captureLocatorContext = ${captureLocatorContext.toString()};
  const probeCoordinator = ${probeCoordinator.toString()};
  const body = document.body;
  const views = {};
  for (const [view, pattern] of Object.entries(${JSON.stringify(VIEW_PATTERNS)})) views[view] = resolveViewTab(body, { pattern, click: false });
  return {
    probe: "fbl-live-probe-v1",
    captured_at: new Date().toISOString(),
    origin: location.origin,
    visibility_state: document.visibilityState,
    heading: (document.querySelector("h1") || {}).textContent || null,
    native_controls: inspectNativeControls(body),
    view_tabs: views,
    grid_kinds: inspectGridKinds(body),
    context: captureLocatorContext(body),
    coordinator: await probeCoordinator({ base: ${JSON.stringify(base)}, token: ${JSON.stringify(token)} }),
  };
})()`;
}

module.exports = { VIEW_PATTERNS, buildProbeSnippet, captureLocatorContext, probeCoordinator };

if (require.main === module) {
  const args = process.argv.slice(2);
  const option = (name) => { const index = args.indexOf(name); return index >= 0 ? args[index + 1] : undefined; };
  const base = option("--base") || "http://127.0.0.1:8765";
  // The CSRF token is read from the running local server (same-origin GET).
  fetch(`${base}/api/csrf`, { headers: { Host: new URL(base).host } })
    .then((response) => response.json())
    .then((payload) => process.stdout.write(`${buildProbeSnippet({ base, token: payload.csrf_token })}\n`))
    .catch((error) => {
      process.stderr.write(`coordinator not reachable at ${base} (${error.message}); emitting a snippet without the token\n`);
      process.stdout.write(`${buildProbeSnippet({ base })}\n`);
    });
}
