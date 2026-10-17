"use strict";

/**
 * Paused-room capture of the verified rules and the projection catalog.
 *
 * Both run only while the native practice clock is paused (a horizontal
 * column scroll measured over ten seconds in the October 11 trial).  Rules
 * come from the visible Rules tab plus the header's "RND x OF N"; ESPN's
 * scoring aliases are mapped to the coordinator's names (TPM -> 3PM,
 * E -> EJ).  The catalog walks the virtualized Players grid with wheel events
 * (vertical for rows, horizontal for the PTS/ADP column page) and records only
 * values read from visible cells: nothing is derived, and bonus/foul columns
 * stay explicitly missing.
 *
 * The extractors take the live DOM and are self-contained, like the other
 * browser callbacks.  The loops take injected `scroll`/`sleep` so they are
 * testable offline.
 */

const SCORING_ALIASES = Object.freeze({ TPM: "3PM", E: "EJ" });
const ROSTER_SLOTS = Object.freeze(["C", "G", "F", "UTIL"]);
const CORE_STATS = Object.freeze(["PTS", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"]);
const UNTRUSTED_STATS = Object.freeze(["EJ", "PF", "TF", "DD", "TD", "QD"]);
const POSITION_GROUPS = Object.freeze({ PG: "G", SG: "G", G: "G", SF: "F", PF: "F", F: "F", C: "C" });

class PageCaptureError extends Error {
  constructor(message, code = "page_capture_error", detail = {}) {
    super(message);
    this.name = "PageCaptureError";
    this.code = code;
    this.detail = detail;
  }
}

/** In-page: the Rules tab tables plus the header round count. */
function readRulesTables(body) {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const tables = Array.from(body.querySelectorAll('[role="tabpanel"] table')).map((table) => ({
    caption: clean((table.querySelector("caption, thead") || {}).textContent),
    rows: Array.from(table.querySelectorAll("tbody tr, tr")).map((row) => Array.from(row.querySelectorAll("th, td")).map((cell) => clean(cell.textContent))).filter((cells) => cells.length >= 2),
  }));
  // The header element reads exactly "RND 1 of 10"; page text runs it into
  // neighbours ("Draft HelpRND 1 of 10--:--"), so match a whole element.
  const ROUNDS = /^RND\s+\d+\s+OF\s+(\d+)$/i;
  const header = Array.from(body.querySelectorAll("div, span")).find((node) => ROUNDS.test(clean(node.textContent)));
  const roundsMatch = header ? clean(header.textContent).match(ROUNDS) : null;
  return { tables, rounds_text: roundsMatch ? roundsMatch[0] : null, rounds: roundsMatch ? Number(roundsMatch[1]) : null };
}

/** Convert the visible Rules read-back into the coordinator's rules shape. */
function normalizeEspnRules(raw) {
  const problems = [];
  const byCaption = (pattern) => (raw.tables || []).find((table) => pattern.test(table.caption || ""));
  const abbreviation = (label) => {
    const match = String(label).match(/\(([^)]+)\)\s*$/);
    return match ? match[1].trim().toUpperCase() : null;
  };
  const number = (value) => {
    const parsed = Number(String(value).replace(/[^\d.+-]/g, ""));
    return String(value).trim() !== "" && Number.isFinite(parsed) ? parsed : null;
  };

  const basic = byCaption(/basic settings/i);
  const teamsRow = basic && basic.rows.find((row) => /^teams in league$/i.test(row[0]));
  const teams = teamsRow ? number(teamsRow[1]) : null;
  if (teams === null) problems.push("Teams in League was not read from Basic Settings");

  const roster = byCaption(/^roster$/i);
  const counts = {};
  for (const row of (roster && roster.rows) || []) {
    const slot = abbreviation(row[0]);
    const count = number(row[1]);
    if (!slot || count === null || !Number.isInteger(count) || count < 0) problems.push(`unreadable roster row: ${row.join(" | ")}`);
    else counts[slot] = count;
  }
  if (!roster) problems.push("Roster table was not found");
  const startingSlots = [];
  for (const slot of ROSTER_SLOTS) for (let index = 0; index < (counts[slot] || 0); index += 1) startingSlots.push(slot);
  const unknownSlots = Object.keys(counts).filter((slot) => !ROSTER_SLOTS.includes(slot) && slot !== "BE" && slot !== "IR");
  if (unknownSlots.length) problems.push(`unrecognized roster slots: ${unknownSlots.join(", ")}`);

  const scoringTable = byCaption(/^scoring/i);
  const scoring = {};
  for (const row of (scoringTable && scoringTable.rows) || []) {
    const espn = abbreviation(row[0]);
    const value = number(row[1]);
    if (!espn || value === null) {
      problems.push(`unreadable scoring row: ${row.join(" | ")}`);
      continue;
    }
    const key = SCORING_ALIASES[espn] || espn;
    if (Object.prototype.hasOwnProperty.call(scoring, key)) problems.push(`duplicate scoring category ${key}`);
    scoring[key] = value;
  }
  if (!scoringTable) problems.push("Scoring table was not found");

  const rounds = Number.isInteger(raw.rounds) ? raw.rounds : null;
  if (rounds === null) problems.push("draft rounds were not read from the RND x OF N header");

  return {
    rules: {
      teams,
      rounds,
      starting_slots: startingSlots,
      bench_slots: counts.BE ?? null,
      ir_slots: counts.IR ?? null,
      scoring,
    },
    problems,
  };
}

/** In-page: the Players grid's visible rows, headers, and view selection. */
function readPlayersGrid(body) {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (node) => {
    const inlineStyle = String(node.getAttribute && node.getAttribute("style") || "").toLowerCase();
    if (/display\s*:\s*none|visibility\s*:\s*hidden/.test(inlineStyle)) return false;
    const document = node.ownerDocument;
    const view = document && document.defaultView;
    const style = view && typeof view.getComputedStyle === "function" ? view.getComputedStyle(node) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    const rect = typeof node.getBoundingClientRect === "function" ? node.getBoundingClientRect() : null;
    return !rect || (rect.width !== 0 && rect.height !== 0);
  };
  const grids = Array.from(body.querySelectorAll('[role="grid"]')).filter(visible);
  const grid = grids.find((candidate) => {
    const headers = Array.from(candidate.querySelectorAll('[role="columnheader"]')).map((node) => clean(node.textContent).toUpperCase());
    return headers.includes("RK") && headers.includes("PLAYER");
  });
  if (!grid) return { found: false, headers: [], rows: [] };
  const gridRect = typeof grid.getBoundingClientRect === "function" ? grid.getBoundingClientRect() : null;
  const rowInViewport = (row) => {
    if (!visible(row)) return false;
    if (!gridRect || typeof row.getBoundingClientRect !== "function") return true;
    const rect = row.getBoundingClientRect();
    if (!rect || !Number.isFinite(rect.width) || !Number.isFinite(rect.height)) return true;
    return rect.width > 0 && rect.height > 0 && rect.bottom > gridRect.top && rect.top < gridRect.bottom && rect.right > gridRect.left && rect.left < gridRect.right;
  };
  const headerCells = Array.from(grid.querySelectorAll('[role="columnheader"]'));
  const headers = headerCells.map((node) => clean(node.textContent));
  const rows = Array.from(grid.querySelectorAll('[role="row"]')).filter((row) => !row.querySelector('[role="columnheader"]')).map((row) => {
    const cells = Array.from(row.querySelectorAll('[role="gridcell"]')).map((cell) => clean(cell.textContent));
    const button = row.querySelector("button[data-player-id]");
    // Drafted rows (Show Drafted on) have no player button; ESPN's headshot
    // URL carries the same stable id the observer resolves rosters by.
    const headshot = row.querySelector('img[src*="/full/"]');
    const headshotId = headshot ? ((headshot.getAttribute("src") || "").match(/\/full\/(\d+)\.png/) || [])[1] || null : null;
    const nameNode = row.querySelector(".playerinfo__playername");
    const teamNode = row.querySelector(".playerinfo__playerteam");
    const injuryNode = row.querySelector(".playerinfo__injurystatus, [class*='injury']");
    return {
      player_id: button ? button.getAttribute("data-player-id") : headshotId,
      drafted: !button,
      name: nameNode ? clean(nameNode.getAttribute("title") || nameNode.textContent) : null,
      nba_team: teamNode ? clean(teamNode.textContent) || null : null,
      injury_status: injuryNode ? clean(injuryNode.textContent) || null : null,
      positions: Array.from(row.querySelectorAll(".positionPill, [class*='positionPill']")).map((node) => clean(node.textContent).toUpperCase()).filter(Boolean),
      cells,
      visible: rowInViewport(row),
    };
  });
  const selected = Array.from(body.querySelectorAll("select")).map((select) => clean(((select.selectedOptions || [])[0] || {}).textContent));
  const showDraftedInput = body.querySelector(".draft-players-toggle input");
  const showDrafted = showDraftedInput ? Boolean(showDraftedInput.checked) : null;
  const averages = Array.from(body.querySelectorAll('input[type="radio"]')).some((input) => input.checked && /average/i.test(clean((input.closest("label") || input.parentElement || {}).textContent) + " " + (input.value || "") + " " + (input.id || "")));
  const scrollEvidence = readScrollGeometry(body, grid, visible);
  return { found: true, headers, rows, selected, averages, show_drafted: showDrafted, ...(scrollEvidence ? { scroll_evidence: scrollEvidence } : {}) };
}

/**
 * In-page: click the Players "Show Drafted" toggle when it is not already in
 * the wanted state.  Returns the state read back afterwards by the caller.
 */
function setShowDrafted(body, wanted) {
  const toggle = body.querySelector(".draft-players-toggle");
  const input = toggle ? toggle.querySelector("input") : null;
  if (!input) return { found: false, clicked: false };
  if (Boolean(input.checked) === Boolean(wanted)) return { found: true, clicked: false };
  (toggle.querySelector("label") || input).click();
  return { found: true, clicked: true };
}

// ESPN's Players grid is a React virtualized table.  Its native grid element
// reports scrollHeight === clientHeight even while the custom scrollbar thumb
// moves through the complete player universe.  Keep the geometry read-back
// small and serializable so the capture loop can distinguish a real bottom
// from a wheel event that was ignored by the rows container.
function readScrollGeometry(body, grid, visible) {
  const finite = (value) => Number.isFinite(Number(value));
  const number = (value) => finite(value) ? Number(value) : null;
  const rect = (node) => {
    if (!node || typeof node.getBoundingClientRect !== "function") return null;
    const value = node.getBoundingClientRect();
    if (!value || !["top", "bottom", "left", "right", "width", "height"].every((key) => finite(value[key]))) return null;
    return { top: Number(value.top), bottom: Number(value.bottom), left: Number(value.left), right: Number(value.right), width: Number(value.width), height: Number(value.height) };
  };
  const style = (node) => {
    const inline = String(node && node.getAttribute && node.getAttribute("style") || "");
    const view = body && body.ownerDocument && body.ownerDocument.defaultView;
    const computed = view && typeof view.getComputedStyle === "function" ? view.getComputedStyle(node) : null;
    return { inline, computed };
  };
  const parseTranslate = (node, axis) => {
    const styles = style(node);
    const text = `${styles.inline} ${styles.computed && styles.computed.transform || ""}`;
    const match = text.match(/translate3d\(\s*([-+\d.e]+)px\s*,\s*([-+\d.e]+)px(?:\s*,\s*[-+\d.e]+px)?\s*\)/i) || text.match(/translate\(\s*([-+\d.e]+)px\s*,\s*([-+\d.e]+)px/i);
    if (!match) return null;
    return Number(match[axis === "horizontal" ? 1 : 2]);
  };
  const findScrollbar = (axis) => {
    const suffix = axis === "vertical" ? "Vertical" : "Horizontal";
    const selector = `[class*="ScrollbarLayout_main${suffix}"], [class*="ScrollbarLayout_face${suffix}"]`;
    const gridRect = rect(grid);
    const candidates = Array.from(body.querySelectorAll(selector)).filter(visible).map((node) => ({ node, rect: rect(node) })).filter((entry) => entry.rect);
    if (!candidates.length) return null;
    const score = ({ rect: candidateRect }) => {
      if (!gridRect) return 0;
      const overlap = axis === "vertical"
        ? Math.max(0, Math.min(candidateRect.bottom, gridRect.bottom) - Math.max(candidateRect.top, gridRect.top))
        : Math.max(0, Math.min(candidateRect.right, gridRect.right) - Math.max(candidateRect.left, gridRect.left));
      const distance = axis === "vertical"
        ? Math.abs(candidateRect.left - gridRect.right)
        : Math.abs(candidateRect.top - gridRect.bottom);
      return overlap * 1000 - distance;
    };
    candidates.sort((left, right) => score(right) - score(left));
    const main = candidates.find((entry) => /ScrollbarLayout_main/i.test(entry.node.className || "")) || candidates[0];
    const face = candidates.find((entry) => /ScrollbarLayout_face/i.test(entry.node.className || "")) || null;
    if (!main || !face) return null;
    const track = main.rect;
    const thumb = face.rect;
    const trackLength = axis === "vertical" ? track.height : track.width;
    const thumbLength = axis === "vertical" ? thumb.height : thumb.width;
    const start = axis === "vertical" ? track.top : track.left;
    const end = axis === "vertical" ? track.bottom : track.right;
    const thumbStart = axis === "vertical" ? thumb.top : thumb.left;
    const thumbEnd = axis === "vertical" ? thumb.bottom : thumb.right;
    const travel = Math.max(0, trackLength - thumbLength);
    const translated = parseTranslate(face.node, axis);
    // ESPN leaves a four-pixel inset at both ends of the custom track.
    // Completion also requires a stable window after a downward wheel.
    const edgeTolerance = 4.5;
    const atTop = thumbStart <= start + edgeTolerance || (translated !== null && translated <= edgeTolerance);
    const atBottom = thumbEnd >= end - edgeTolerance || (travel > 0 && translated !== null && translated >= travel - edgeTolerance);
    return {
      reliable: trackLength > 0 && thumbLength > 0 && travel > 1,
      at_top: atTop,
      at_bottom: atBottom,
      track: { start, end, length: trackLength },
      thumb: { start: thumbStart, end: thumbEnd, length: thumbLength, translate: translated },
      progress: travel > 0 ? Math.max(0, Math.min(1, (thumbStart - start) / travel)) : null,
    };
  };
  const native = (node, axis) => {
    if (!node) return null;
    const top = number(node.scrollTop);
    const left = number(node.scrollLeft);
    const scrollLength = number(axis === "vertical" ? node.scrollHeight : node.scrollWidth);
    const clientLength = number(axis === "vertical" ? node.clientHeight : node.clientWidth);
    const offset = axis === "vertical" ? top : left;
    if (![offset, scrollLength, clientLength].every((value) => value !== null) || scrollLength <= clientLength + 1) return null;
    return {
      reliable: true,
      at_top: offset <= 1,
      at_bottom: offset + clientLength >= scrollLength - 1,
      offset,
      scroll_length: scrollLength,
      client_length: clientLength,
    };
  };
  const ancestors = [];
  for (let node = grid; node; node = node.parentElement) ancestors.push(node);
  const vertical = findScrollbar("vertical") || ancestors.map((node) => native(node, "vertical")).find(Boolean) || null;
  const horizontal = findScrollbar("horizontal") || ancestors.map((node) => native(node, "horizontal")).find(Boolean) || null;
  if (!vertical && !horizontal) return null;
  return { vertical, horizontal };
}

function meaningfulField(value) {
  return value !== undefined && value !== null && String(value).trim() !== "" && String(value).trim() !== "--";
}

function projectionComplete(record) {
  if (!record) return false;
  return [...CORE_STATS, "GP"].every((key) => {
    const raw = record.fields[key];
    return meaningfulField(raw) && Number.isFinite(Number(raw)) && Number(raw) > 0;
  });
}

function repairableProjection(record) {
  if (!record || !meaningfulField(record.fields.GP) || !Number.isFinite(Number(record.fields.GP)) || Number(record.fields.GP) <= 0) return false;
  return !projectionComplete(record);
}

function mergeCatalogReadDetailed(records, read, maxPlayers = Number.POSITIVE_INFINITY) {
  const added = [];
  const changed = [];
  const observed = [];
  for (const row of read.rows.filter((candidate) => candidate.visible !== false)) {
    if (!row.player_id || !meaningfulField(row.name)) continue;
    const existing = records.get(row.player_id);
    if (!existing && records.size >= maxPlayers) continue;
    observed.push(row.player_id);
    const fields = {};
    read.headers.forEach((header, index) => {
      if (header && index < row.cells.length) fields[header.toUpperCase()] = row.cells[index];
    });
    if (!existing) added.push(row.player_id);
    const mergedFields = { ...(existing ? existing.fields : {}) };
    let fieldChanged = false;
    for (const [key, value] of Object.entries(fields)) {
      // Virtualized grids can return blank/placeholder cells while a
      // horizontal page is hydrating. Never erase a value read elsewhere.
      if (!Object.prototype.hasOwnProperty.call(mergedFields, key) || meaningfulField(value)) {
        if (mergedFields[key] !== value) fieldChanged = true;
        if (meaningfulField(value) || !Object.prototype.hasOwnProperty.call(mergedFields, key)) mergedFields[key] = value;
      }
    }
    records.set(row.player_id, {
      player_id: row.player_id,
      name: meaningfulField(row.name) ? row.name : (existing && existing.name) || null,
      nba_team: meaningfulField(row.nba_team) ? row.nba_team : (existing && existing.nba_team) || null,
      injury_status: meaningfulField(row.injury_status) ? row.injury_status : (existing && existing.injury_status) || null,
      positions: row.positions.length ? row.positions : (existing ? existing.positions : []),
      fields: mergedFields,
    });
    if (!existing || fieldChanged) changed.push(row.player_id);
  }
  return { added, changed, observed };
}

/** Merge one grid read into the per-player record map; returns new ids. */
function mergeCatalogRead(records, read) {
  return mergeCatalogReadDetailed(records, read).added;
}

function catalogPlayer(record, source) {
  const value = (key) => {
    const raw = record.fields[key];
    if (raw === undefined || raw === "" || raw === "--") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const eligibility = [...new Set(record.positions.map((position) => POSITION_GROUPS[position]).filter(Boolean))];
  const stats = {};
  const missing = new Set(UNTRUSTED_STATS);
  for (const key of CORE_STATS) {
    const number = value(key);
    if (number === null || number < 0) missing.add(key);
    else stats[key] = number;
  }
  return {
    player_id: record.player_id,
    name: record.name,
    eligibility,
    stats,
    projected_gp: value("GP"),
    draft_rank: value("RK"),
    adp: value("ADP"),
    nba_team: record.nba_team,
    injury_status: record.injury_status,
    source,
    missing_stats: [...missing].sort(),
  };
}

/**
 * Walk the paused Players grid and return a catalog of every rendered player.
 * `read()` returns readPlayersGrid output; `scroll(dx, dy)` moves the grid.
 */
async function captureProjectionCatalog({
  read,
  scroll,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  maxPlayers = 1500,
  maxSteps = 300,
  settleMs = 250,
  stepPixels = 480,
  horizontalRetries = 3,
  repairPasses = 2,
  maxStalledSteps = 3,
  requireScrollEvidence = true,
  onProgress = null,
  source = "ESPN Players grid (paused room)",
  now = () => new Date(),
} = {}) {
  if (typeof read !== "function" || typeof scroll !== "function") throw new PageCaptureError("read and scroll callbacks are required", "invalid_capture");
  const first = await read();
  if (!first.found) throw new PageCaptureError("the Players grid is not visible; switch to Players while paused", "players_grid_missing");
  if (!first.selected.some((text) => /projected/i.test(text)) || !first.averages) {
    throw new PageCaptureError("the Players grid must show the Projected stat view with Averages selected", "wrong_stat_view", { selected: first.selected, averages: first.averages });
  }
  const startedAt = now();
  const records = new Map();
  let steps = 0;
  let reachedEnd = false;
  let terminationReason = null;
  let terminationEndEvidence = null;
  let hasScrollEvidence = Object.prototype.hasOwnProperty.call(first, "scroll_evidence");
  let catalogBoundary = null;
  let repairBoundary = null;
  const captureMetrics = {
    scan_passes: 0,
    identity_windows: 0,
    projection_windows: 0,
    projection_reads: 0,
    horizontal_attempts: 0,
    skipped_no_data_windows: 0,
    repair_windows_skipped_complete: 0,
    repair_windows_skipped_unscorable: 0,
    repair_passes: 0,
    repair_no_progress: false,
  };

  const notifyProgress = (phase, progress = null, pass = captureMetrics.scan_passes, reportedSteps = steps) => {
    if (typeof onProgress !== "function") return;
    try {
      onProgress({
        phase,
        catalog_count: records.size,
        repairable_count: [...records.values()].filter(repairableProjection).length,
        progress,
        pass,
        steps: reportedSteps,
      });
    } catch (_error) {
      // Progress is advisory UI telemetry and must never break a capture.
    }
  };

  const repairSignature = () => [...records.values()]
    .filter(repairableProjection)
    .map((record) => `${record.player_id}:${[...CORE_STATS, "GP"].filter((key) => !meaningfulField(record.fields[key]) || !Number.isFinite(Number(record.fields[key])) || Number(record.fields[key]) <= 0).join(",")}`)
    .sort()
    .join("|");

  async function restoreTopLeft() {
    await scroll(-4000, 0);
    await sleep(Math.min(settleMs, 120));
    for (let attempt = 0; attempt < maxSteps; attempt += 1) {
      const before = (await read()).rows.map((row) => row.player_id).join(",");
      await scroll(0, -stepPixels * 4);
      await sleep(Math.min(settleMs, 120));
      if ((await read()).rows.map((row) => row.player_id).join(",") === before) break;
    }
    await scroll(-4000, 0);
    await sleep(Math.min(settleMs, 120));
  }

  async function scanPass({ repairOnly = false } = {}) {
    let passSteps = 0;
    let lastBefore = null;
    let lastAfter = null;
    let stalledSteps = 0;
    captureMetrics.scan_passes += 1;
    reachedEnd = false;
    terminationReason = null;
    terminationEndEvidence = null;
    const windowSignature = (readResult) => readResult.rows
      .filter((row) => row.visible !== false && row.player_id && meaningfulField(row.name))
      .map((row) => `${row.player_id}:${row.name}:${row.cells && row.cells[0] || ""}`)
      .join(",");
    const scrollState = (readResult) => {
      const evidence = readResult && readResult.scroll_evidence;
      const vertical = evidence && evidence.vertical;
      return vertical && vertical.reliable === true ? vertical : null;
    };
    const horizontalState = (readResult) => readResult && readResult.scroll_evidence && readResult.scroll_evidence.horizontal || null;
    const positionChanged = (beforeRead, afterRead) => {
      const before = scrollState(beforeRead);
      const after = scrollState(afterRead);
      if (!before || !after) return null;
      const beforePosition = before.thumb && (before.thumb.translate ?? before.thumb.start);
      const afterPosition = after.thumb && (after.thumb.translate ?? after.thumb.start);
      if (Number.isFinite(Number(beforePosition)) && Number.isFinite(Number(afterPosition))) return Number(beforePosition) !== Number(afterPosition);
      return before.at_top !== after.at_top || before.at_bottom !== after.at_bottom;
    };
    const bottomProof = (readResult) => {
      const vertical = scrollState(readResult);
      if (!vertical) return { available: false, verified: !requireScrollEvidence && !hasScrollEvidence };
      return { available: true, verified: vertical.at_bottom === true, reliable: vertical.reliable === true, at_bottom: vertical.at_bottom === true, progress: vertical.progress ?? null };
    };
    for (; passSteps < maxSteps;) {
      let observed = [];
      let projectionIds = [];
      const leftRead = await read();
      let windowRead = leftRead;
      hasScrollEvidence = hasScrollEvidence || Object.prototype.hasOwnProperty.call(leftRead, "scroll_evidence");
      captureMetrics.identity_windows += 1;
      const leftMerge = mergeCatalogReadDetailed(records, leftRead, maxPlayers);
      observed = observed.concat(leftMerge.observed);
      const positiveGpIds = leftMerge.observed.filter((id) => {
        const record = records.get(id);
        return record && meaningfulField(record.fields.GP) && Number.isFinite(Number(record.fields.GP)) && Number(record.fields.GP) > 0;
      });
      projectionIds = positiveGpIds.filter((id) => !repairOnly || !projectionComplete(records.get(id)));
      if (!projectionIds.length) {
        if (!positiveGpIds.length) captureMetrics.skipped_no_data_windows += 1;
        if (repairOnly && positiveGpIds.length) captureMetrics.repair_windows_skipped_complete += 1;
        if (repairOnly && !positiveGpIds.length && leftMerge.observed.length) captureMetrics.repair_windows_skipped_unscorable += 1;
      }
      const projectionSet = new Set(projectionIds);
      for (let attempt = 0; projectionIds.length > 0 && attempt <= horizontalRetries; attempt += 1) {
        captureMetrics.horizontal_attempts += 1;
        if (attempt > 0) {
          // Re-read the cheap left page before retrying projection cells.  A
          // virtualized row can hydrate GP after the first pass; this also
          // lets a later positive-GP group resume projection collection.
          const retryLeft = await read();
          windowRead = retryLeft;
          captureMetrics.identity_windows += 1;
          const retryMerge = mergeCatalogReadDetailed(records, retryLeft, maxPlayers);
          observed = observed.concat(retryMerge.observed);
          projectionIds = retryMerge.observed.filter((id) => {
            const record = records.get(id);
            return record && meaningfulField(record.fields.GP) && Number.isFinite(Number(record.fields.GP)) && Number(record.fields.GP) > 0 && (!repairOnly || !projectionComplete(record));
          });
          projectionSet.clear();
          projectionIds.forEach((id) => projectionSet.add(id));
          if (!projectionIds.length) break;
        }
        captureMetrics.projection_windows += 1;
        await scroll(4000, 0);
        await sleep(settleMs);
        captureMetrics.projection_reads += 1;
        const rightRead = await read();
        hasScrollEvidence = hasScrollEvidence || Object.prototype.hasOwnProperty.call(rightRead, "scroll_evidence");
        const leftRows = windowSignature(windowRead);
        const rightRows = windowSignature(rightRead);
        const rowsMatch = leftRows === rightRows && leftRows !== "";
        if (rowsMatch) {
          const rightMerge = mergeCatalogReadDetailed(records, { ...rightRead, rows: rightRead.rows.filter((row) => projectionSet.has(row.player_id)) }, maxPlayers);
          observed = observed.concat(rightMerge.observed);
        }
        await scroll(-4000, 0);
        await sleep(settleMs);
        const resetRead = await read();
        const horizontal = horizontalState(rightRead);
        const resetHorizontal = horizontalState(resetRead);
        const resetAtLeft = !horizontal || !resetHorizontal || resetHorizontal.at_top === true || resetHorizontal.at_left === true;
        if (!rowsMatch || !resetAtLeft) continue;
        const windowIds = [...projectionSet];
        if (windowIds.length > 0 && windowIds.every((id) => projectionComplete(records.get(id)))) break;
      }
      // A repair pass sees known IDs by design.  End detection therefore uses
      // whether the rendered row window actually advances, never whether new
      // records were added during this pass.
      const beforeRead = await read();
      const before = windowSignature(beforeRead);
      await scroll(0, stepPixels);
      await sleep(settleMs);
      const afterRead = await read();
      const after = windowSignature(afterRead);
      lastBefore = before;
      lastAfter = after;
      passSteps += 1;
      const movedByGeometry = positionChanged(beforeRead, afterRead);
      const moved = before !== after || movedByGeometry === true;
      const bottom = bottomProof(afterRead);
      notifyProgress(repairOnly ? "repair" : "scan", bottom.progress, captureMetrics.scan_passes, steps + passSteps);
      if (!moved) stalledSteps += 1;
      else stalledSteps = 0;
      if (!moved && bottom.verified) {
        reachedEnd = true;
        terminationReason = "end_of_grid";
        terminationEndEvidence = {
          before_window: before,
          after_window: after,
          stable: true,
          vertical_bottom: bottom,
          scroll_target_reliable: bottom.available ? bottom.reliable === true : !requireScrollEvidence && !hasScrollEvidence,
          geometry_moved: movedByGeometry,
        };
        break;
      }
      if (!moved && stalledSteps >= Math.max(1, maxStalledSteps)) {
        terminationReason = "scroll_stalled";
        terminationEndEvidence = {
          before_window: before,
          after_window: after,
          stable: true,
          vertical_bottom: bottom,
          scroll_target_reliable: bottom.available ? bottom.reliable === true : !requireScrollEvidence && !hasScrollEvidence,
          geometry_moved: movedByGeometry,
          stalled_steps: stalledSteps,
        };
        break;
      }
      if (records.size >= maxPlayers) {
        terminationReason = "max_players";
        terminationEndEvidence = { before_window: before, after_window: after, stable: false, vertical_bottom: bottom, geometry_moved: movedByGeometry };
        break;
      }
    }
    if (!terminationReason) {
      terminationReason = "max_steps";
      terminationEndEvidence = { before_window: lastBefore, after_window: lastAfter, stable: false, vertical_bottom: { available: false, verified: false }, scroll_target_reliable: false };
    }
    return passSteps;
  }

  await restoreTopLeft();
  steps = await scanPass();
  catalogBoundary = {
    reached_end: reachedEnd,
    termination_reason: terminationReason,
    termination_evidence: terminationEndEvidence,
    steps,
    complete: terminationReason === "end_of_grid" && reachedEnd && terminationEndEvidence && terminationEndEvidence.vertical_bottom && terminationEndEvidence.vertical_bottom.verified === true,
  };
  for (let repair = 0; repair < repairPasses && reachedEnd && [...records.values()].some(repairableProjection); repair += 1) {
    const beforeRepair = repairSignature();
    if (!beforeRepair) break;
    await restoreTopLeft();
    steps += await scanPass({ repairOnly: true });
    captureMetrics.repair_passes += 1;
    repairBoundary = {
      pass: captureMetrics.repair_passes,
      reached_end: reachedEnd,
      termination_reason: terminationReason,
      termination_evidence: terminationEndEvidence,
      steps,
    };
    const afterRepair = repairSignature();
    notifyProgress("repair_complete", null, captureMetrics.scan_passes);
    if (afterRepair === beforeRepair) {
      captureMetrics.repair_no_progress = true;
      break;
    }
  }
  await restoreTopLeft();
  await scroll(-4000, 0);
  await sleep(Math.min(settleMs, 120));
  const capturedAt = now().toISOString();
  const label = `${source}; ${first.selected.filter((text) => /projected/i.test(text))[0]} averages; captured ${capturedAt}`;
  const players = [...records.values()].map((record) => catalogPlayer(record, label));
  const usable = players.filter((player) => player.eligibility.length);
  const complete = usable.filter((player) => CORE_STATS.every((key) => key in player.stats) && Number.isFinite(player.projected_gp) && player.projected_gp > 0);
  const incomplete = usable.filter((player) => !CORE_STATS.every((key) => key in player.stats) || !Number.isFinite(player.projected_gp) || player.projected_gp <= 0);
  notifyProgress("complete", terminationEndEvidence && terminationEndEvidence.vertical_bottom && terminationEndEvidence.vertical_bottom.progress, captureMetrics.scan_passes, steps);
  return {
    artifact_version: "page-capture-catalog-v1",
    started_at: startedAt.toISOString(),
    captured_at: capturedAt,
    steps,
    players: usable,
    skipped_without_position: players.length - usable.length,
    catalog_count: players.length,
    catalog_complete: Boolean(catalogBoundary && catalogBoundary.complete),
    termination_reason: terminationReason,
    termination_count: players.length,
    termination_limit: { max_players: maxPlayers, max_steps: maxSteps, repair_passes: repairPasses },
    termination_end_evidence: terminationEndEvidence,
    termination_steps: steps,
    catalog_boundary: catalogBoundary,
    repair_boundary: repairBoundary,
    eligible_count: usable.length,
    complete_eligible_count: complete.length,
    incomplete_projection_count: incomplete.length,
    incomplete_projection_ids: incomplete.map((player) => player.player_id),
    complete_core_stats: complete.length,
    capture_metrics: captureMetrics,
  };
}

/**
 * Wheel scroller for ESPN's fixed-data-table grids (live page only): the
 * Players grid (first header "RK") or Pick History (first header "PICK").
 */
function createGridWheelScroller({ document: pageDocument, WheelEventCtor = (typeof WheelEvent === "function" ? WheelEvent : null), firstHeader = "RK" }) {
  return async function scroll(deltaX, deltaY) {
    const visible = (node) => {
      const inlineStyle = String(node.getAttribute && node.getAttribute("style") || "").toLowerCase();
      if (/display\s*:\s*none|visibility\s*:\s*hidden/.test(inlineStyle)) return false;
      const view = pageDocument.defaultView;
      const style = view && typeof view.getComputedStyle === "function" ? view.getComputedStyle(node) : null;
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
      const rect = typeof node.getBoundingClientRect === "function" ? node.getBoundingClientRect() : null;
      return !rect || (rect.width !== 0 && rect.height !== 0);
    };
    const grid = Array.from(pageDocument.querySelectorAll('[role="grid"]')).filter(visible).find((candidate) => {
      const header = candidate.querySelector('[role="columnheader"]');
      return header && header.textContent.trim().toUpperCase() === firstHeader;
    });
    if (!grid || !WheelEventCtor) throw new PageCaptureError(`the ${firstHeader} grid cannot be scrolled`, "scroll_unavailable");
    const rect = grid.getBoundingClientRect();
    const point = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    // FixedDataTable's wheel handler is attached below the ARIA grid (on the
    // rows container/cell layer).  Dispatching on the grid root can therefore
    // bubble through the wrong React boundary and leave the custom thumb at
    // the same position.  A hit-tested descendant reproduces a real pointer
    // wheel while retaining a grid fallback for test documents.
    let target = grid.querySelector(".fixedDataTableLayout_rowsContainer") || grid;
    if (typeof pageDocument.elementFromPoint === "function") {
      const hit = pageDocument.elementFromPoint(point.x, point.y);
      if (hit && (hit === grid || (typeof grid.contains === "function" && grid.contains(hit)))) target = hit;
    }
    target.dispatchEvent(new WheelEventCtor("wheel", { deltaX, deltaY, bubbles: true, cancelable: true, clientX: point.x, clientY: point.y }));
  };
}

module.exports = {
  CORE_STATS,
  PageCaptureError,
  SCORING_ALIASES,
  captureProjectionCatalog,
  catalogPlayer,
  createGridWheelScroller,
  mergeCatalogRead,
  normalizeEspnRules,
  readPlayersGrid,
  readRulesTables,
  setShowDrafted,
};
