"use strict";

/**
 * Fixture-tested Pick History <-> Players view switch (replaces the
 * placeholder `getByRole("tab", {name})` in runtime-handoff.md).
 *
 * Same uniqueness rules as the activation locator: role/name, case-insensitive,
 * scoped to the draft-room view-tab region, exactly one visible enabled match
 * or refuse.  Resolve-and-click is one atomic in-page call; after the click the
 * expected grid kind must be mounted or the helper throws (a throw leaves an
 * in-flight action uncertain, never retried).
 *
 * The region selector is an assumption validated only by fixtures; the live
 * checklist captures the real tab DOM once (issue #43).
 */

class ViewSwitchError extends Error {
  constructor(message, code = "view_switch_error", detail = {}) {
    super(message);
    this.name = "ViewSwitchError";
    this.code = code;
    this.detail = detail;
  }
}

const VIEW_NAMES = Object.freeze({ history: "^pick\\s+history$", players: "^players$", league_manager: "^league\\s+manager$" });

/** In-page, self-contained. Returns {status, count, clicked}. `act` clicks the unique match. */
function resolveViewTab(body, spec) {
  const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
  };
  const REGION = '[role="tablist"], [data-testid="draft-view-tabs"], .draft-view-tabs';
  const regions = Array.from(body.querySelectorAll(REGION));
  const outer = regions.filter((region) => !regions.some((other) => other !== region && Array.from(other.querySelectorAll(REGION)).includes(region)));
  if (!outer.length) return { status: "no_region", count: 0, clicked: false };
  const pattern = new RegExp(spec.pattern, "i");
  const matches = [];
  for (const region of outer) {
    for (const node of Array.from(region.querySelectorAll('[role="tab"], button, [role="button"]'))) {
      if (matches.includes(node)) continue;
      // ESPN renders the active panel inside the tablist wrapper; its buttons are not view tabs.
      if (node.closest('[role="tabpanel"]')) continue;
      if (pattern.test(clean(node.getAttribute("aria-label") || node.getAttribute("title") || node.textContent))) matches.push(node);
    }
  }
  // ESPN nests <button role="tab"> inside <li role="tab">: one tab, so keep the innermost match.
  const innermost = matches.filter((node) => !matches.some((other) => other !== node && node.contains(other)));
  const usable = innermost.filter((node) => visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true");
  const status = usable.length === 1 ? "unique" : usable.length > 1 ? "ambiguous" : matches.length ? "unusable" : "missing";
  if (status === "unique" && spec.click) {
    usable[0].click();
    return { status, count: 1, clicked: true };
  }
  return { status, count: usable.length, clicked: false };
}

function inspectGridKinds(body) {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase().replace(/[^a-z0-9 ]/g, "");
  const visible = (node) => {
    if (!node) return false;
    for (let current = node; current; current = current.parentElement) {
      if (current.getAttribute && current.getAttribute("aria-hidden") === "true") return false;
      const style = window.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    return typeof node.getClientRects !== "function" || node.getClientRects().length > 0;
  };
  return Array.from(body.querySelectorAll('[role="grid"]')).filter(visible).map((grid) => {
    const headers = Array.from(grid.querySelectorAll('[role="columnheader"], thead th')).map((node) => clean(node.textContent));
    const joined = headers.join(" ");
    if (joined.includes("pick") && joined.includes("player") && joined.includes("team")) return "history";
    // Rank and Player stay pinned. ESPN virtualizes the other columns, so
    // FPTS disappears from the DOM after a horizontal projection read.
    if (headers.includes("rk") && headers.includes("player")) return "players";
    return "other";
  });
}

/**
 * In-page, self-contained.  League Manager has no grid; it is ready once a
 * visible Resume/Pause control renders in its control region.  Live ESPN
 * mounts that control ~150 ms after the tab click (2026-10-13 probe).
 */
function inspectLeagueManagerPanel(body) {
  const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
  };
  const REGION = '[data-testid="league-manager-controls"], .league-manager-controls, .league-manager-container, .league-manager-control__container, [aria-label="League Manager" i], [aria-label="League Manager controls" i]';
  const controls = Array.from(body.querySelectorAll(REGION)).flatMap((region) => Array.from(region.querySelectorAll('button, [role="button"]')))
    .filter((node) => /^(resume|pause)(?:\s+draft)?$/i.test(clean(node.getAttribute("aria-label") || node.getAttribute("title") || node.textContent)));
  return { controls: controls.length, visible_controls: controls.filter(visible).length };
}

function createViewSwitcher({ tab, settleMs = 50, polls = 10, leagueManagerPolls = 40, leagueManagerSettleMs = 50 } = {}) {
  if (!tab || !tab.playwright || typeof tab.playwright.locator !== "function") throw new ViewSwitchError("an active draft tab is required", "missing_tab");
  const body = () => tab.playwright.locator("body");

  async function show(view) {
    const pattern = VIEW_NAMES[view];
    const probe = await body().evaluate(resolveViewTab, { pattern, click: false });
    if (probe.status !== "unique") throw new ViewSwitchError(`${view} view tab: ${probe.status} (${probe.count} usable matches); refusing to click`, `tab_${probe.status}`, { view, probe });
    const clicked = await body().evaluate(resolveViewTab, { pattern, click: true });
    if (!clicked.clicked) throw new ViewSwitchError(`${view} view tab was no longer unique at click time`, "tab_changed", { view, clicked });
    for (let attempt = 0; attempt < polls; attempt += 1) {
      const kinds = await body().evaluate(inspectGridKinds);
      if (kinds.includes(view)) return { view, kinds };
      if (settleMs > 0) await new Promise((resolve) => setTimeout(resolve, settleMs));
    }
    throw new ViewSwitchError(`${view} panel did not mount after the tab click`, "panel_not_mounted", { view });
  }
  return Object.freeze({
    showPickHistory: () => show("history"),
    showPlayers: () => show("players"),
    showLeagueManager: async () => {
      const pattern = VIEW_NAMES.league_manager;
      const probe = await body().evaluate(resolveViewTab, { pattern, click: false });
      if (probe.status !== "unique") throw new ViewSwitchError(`League Manager tab: ${probe.status} (${probe.count} usable matches); refusing to click`, `tab_${probe.status}`, { view: "league_manager", probe });
      const clicked = await body().evaluate(resolveViewTab, { pattern, click: true });
      if (!clicked.clicked) throw new ViewSwitchError("League Manager tab was no longer unique at click time", "tab_changed", { view: "league_manager", clicked });
      // Wait for the native control to render.  Not ready is reported, not
      // thrown: the activation locator then refuses without clicking.
      for (let attempt = 0; attempt < leagueManagerPolls; attempt += 1) {
        const panel = await body().evaluate(inspectLeagueManagerPanel);
        if (panel.visible_controls > 0) return { view: "league_manager", kinds: [], ready: true, polls: attempt + 1 };
        if (leagueManagerSettleMs > 0) await new Promise((resolve) => setTimeout(resolve, leagueManagerSettleMs));
      }
      return { view: "league_manager", kinds: [], ready: false, polls: leagueManagerPolls };
    },
  });
}

module.exports = { ViewSwitchError, createViewSwitcher, inspectGridKinds, inspectLeagueManagerPanel, resolveViewTab };
