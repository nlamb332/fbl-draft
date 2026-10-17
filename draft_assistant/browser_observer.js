"use strict";

/**
 * Read-only visible ESPN observer for the attended CUA agent.
 *
 * The factory accepts an already-open tab. It never discovers a browser,
 * opens a tab, calls a network API, scrolls native controls, or mutates ESPN.
 * Projection prefetch is intentionally outside this module; this observer
 * merges only the catalog supplied by the active agent into fast live frames.
 * When used in CUA REPL, inspect this repository file with normal file tools,
 * then provide its reviewed plain-JS definitions in that active session. The
 * CommonJS export wrapper below is for repository tests and is omitted from
 * the definitions supplied to a browser REPL.
 */

const REQUIRED_MISSING_STATS = Object.freeze(["PF", "TF", "EJ", "DD", "TD", "QD"]);

class BrowserObserverError extends Error {
  constructor(message, code = "browser_observer_error") {
    super(message);
    this.name = "BrowserObserverError";
    this.code = code;
  }
}

function text(value) {
  return value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim();
}

function own(value, key) {
  return value !== null && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, key);
}

function first(value, ...keys) {
  for (const key of keys) {
    if (own(value, key) && value[key] !== undefined && value[key] !== null) return value[key];
  }
  return undefined;
}

function finite(value) {
  if (value === undefined || value === null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function integer(value) {
  const number = finite(value);
  return number !== null && Number.isInteger(number) ? number : null;
}

function unique(values) {
  return [...new Set(values.filter((value) => value !== undefined && value !== null && value !== ""))];
}

function clone(value) {
  if (Array.isArray(value)) return value.map(clone);
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) result[key] = clone(item);
    return result;
  }
  return value;
}

function isoNow(now) {
  const value = typeof now === "function" ? now() : now;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) throw new BrowserObserverError("now() returned an invalid timestamp", "invalid_clock");
    return new Date(parsed).toISOString();
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new BrowserObserverError("now() returned an invalid clock", "invalid_clock");
  return new Date(parsed).toISOString();
}

function normalizeRoom(room) {
  if (!room || typeof room !== "object") throw new BrowserObserverError("an explicit room identity is required", "missing_room");
  const practiceValue = first(room, "practice");
  const practice = practiceValue === undefined ? false : practiceValue;
  if (typeof practice !== "boolean") throw new BrowserObserverError("room.practice must be boolean", "invalid_room");
  const normalized = {
    league_id: text(first(room, "league_id", "leagueId")),
    season_id: text(first(room, "season_id", "seasonId")),
    team_id: text(first(room, "team_id", "teamId")),
    practice,
  };
  if (![normalized.league_id, normalized.season_id, normalized.team_id].every((value) => /^\d+$/.test(value))) {
    throw new BrowserObserverError("room IDs must be numeric strings", "invalid_room");
  }
  return normalized;
}

function normalizeUrlRoom(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl) throw new BrowserObserverError("active tab URL is unavailable", "missing_tab_url");
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch (error) {
    throw new BrowserObserverError(`active tab URL is invalid: ${error.message}`, "invalid_tab_url");
  }
  if (parsed.protocol !== "https:" || parsed.hostname !== "fantasy.espn.com" || parsed.pathname.replace(/\/+$/, "") !== "/basketball/draft") {
    throw new BrowserObserverError("active tab is not an ESPN basketball draft room", "wrong_tab_url");
  }
  const getNumeric = (key) => {
    const values = parsed.searchParams.getAll(key);
    if (values.length !== 1 || !/^\d+$/.test(values[0])) throw new BrowserObserverError(`active tab URL has no unique numeric ${key}`, "invalid_room_url");
    return values[0];
  };
  return { league_id: getNumeric("leagueId"), season_id: getNumeric("seasonId"), team_id: getNumeric("teamId") };
}

function roomsEqual(left, right) {
  return left.league_id === right.league_id && left.season_id === right.season_id && left.team_id === right.team_id;
}

function catalogEntries(input) {
  if (input instanceof Map) return [...input.entries()];
  if (Array.isArray(input)) return input.map((item) => [first(item, "player_id", "playerId", "id"), item]);
  if (input && typeof input === "object") return Object.entries(input);
  throw new BrowserObserverError("projectionCatalog must be an array, Map, or object", "invalid_catalog");
}

function normalizePlayer(raw, fallbackId, source = "projection_catalog") {
  if (!raw || typeof raw !== "object") return null;
  const playerId = text(first(raw, "player_id", "playerId", "id")) || text(fallbackId);
  const name = text(first(raw, "name", "player_name", "playerName", "full_name", "fullName"));
  if (!playerId || !name) return null;
  const eligibilityRaw = first(raw, "eligibility", "positions", "position");
  const eligibility = Array.isArray(eligibilityRaw)
    ? unique(eligibilityRaw.map((item) => text(item).toUpperCase()))
    : unique(text(eligibilityRaw).split(/[,/ ]+/).map((item) => item.toUpperCase()));
  const missing = new Set([...(Array.isArray(raw.missing_stats) ? raw.missing_stats : []), ...REQUIRED_MISSING_STATS].map(text).filter(Boolean));
  if (!eligibility.length || eligibility.some((item) => !["C", "G", "F"].includes(item))) return null;
  const stats = {};
  if (raw.stats && typeof raw.stats === "object") {
    for (const [key, value] of Object.entries(raw.stats)) {
      const number = typeof value === "boolean" || value === "" ? NaN : Number(value);
      if (Number.isFinite(number) && number >= 0) stats[key] = number;
      else missing.add(text(key));
    }
  }
  const bounded = (key, lower, upper) => {
    const value = first(raw, key);
    if (value === undefined) return null;
    const number = typeof value === "boolean" || value === "" ? NaN : Number(value);
    if (!Number.isFinite(number) || number < lower || number > upper) {
      missing.add(key.toUpperCase());
      return null;
    }
    return number;
  };
  return {
    player_id: playerId,
    name,
    eligibility,
    stats,
    projected_gp: bounded("projected_gp", 0, 82) ?? bounded("projectedGp", 0, 82) ?? bounded("gp", 0, 82),
    draft_rank: bounded("draft_rank", 1, Number.POSITIVE_INFINITY) ?? bounded("draftRank", 1, Number.POSITIVE_INFINITY) ?? bounded("rk", 1, Number.POSITIVE_INFINITY),
    adp: bounded("adp", 1, Number.POSITIVE_INFINITY),
    availability: bounded("availability", 0, 1),
    nba_team: first(raw, "nba_team", "nbaTeam", "team") ?? null,
    injury_status: first(raw, "injury_status", "injuryStatus", "injury") ?? null,
    source: text(first(raw, "source")) || source,
    missing_stats: [...missing].sort(),
  };
}

function buildCatalog(input) {
  const byId = new Map();
  const byName = new Map();
  for (const [fallbackId, raw] of catalogEntries(input)) {
    const player = normalizePlayer(raw, fallbackId);
    if (!player) continue;
    byId.set(player.player_id, player);
    const names = byName.get(player.name) || [];
    names.push(player);
    byName.set(player.name, names);
  }
  if (!byId.size) throw new BrowserObserverError("projectionCatalog has no usable player identities", "empty_catalog");
  return { byId, byName };
}

function playerIdFromHeadshot(value) {
  const match = String(value || "").match(/\/full\/([^/?#]+)\.png(?:[?#].*)?$/i) || String(value || "").match(/\/full\/([^/?#]+)\.png/i);
  return match ? match[1] : null;
}

function historyIsComplete(events, expectedCurrentPick = null) {
  if (expectedCurrentPick === 1) return events.length === 0;
  if (!events.length || events.some((event) => !event.player_id)) return false;
  if (new Set(events.map((event) => event.player_id)).size !== events.length) return false;
  const picks = events.map((event) => event.pick_number).sort((left, right) => left - right);
  const end = expectedCurrentPick === null ? 141 : expectedCurrentPick;
  if (end < 2 || picks.length !== end - 1) return false;
  return picks.every((pick, index) => pick === index + 1);
}

function normalizeHistorySnapshot(snapshot, fallbackObservedAt = null) {
  if (!snapshot || typeof snapshot !== "object") return null;
  const history = Array.isArray(snapshot.history) ? snapshot.history.map(historyEvent).filter(Boolean) : [];
  const observedAt = text(first(snapshot, "observed_at", "observedAt")) || fallbackObservedAt;
  const historyComplete = first(snapshot, "history_complete", "historyComplete", "complete") === true;
  return {
    history,
    observed_at: observedAt || null,
    history_complete: historyComplete,
    warnings: Array.isArray(snapshot.warnings) ? unique(snapshot.warnings.map(text)) : [],
    grid_count: integer(first(snapshot, "grid_count", "gridCount")) || 0,
  };
}

function mergeHistoryEvents(...eventLists) {
  const byPick = new Map();
  const warnings = [];
  for (const events of eventLists) {
    for (const event of events || []) {
      const normalized = historyEvent(event);
      if (!normalized) continue;
      const existing = byPick.get(normalized.pick_number);
      if (existing) {
        if (existing.player_id !== normalized.player_id || existing.player_name !== normalized.player_name || existing.team !== normalized.team) {
          warnings.push(`history_conflict_pick_${normalized.pick_number}`);
        }
        continue;
      }
      byPick.set(normalized.pick_number, normalized);
    }
  }
  return { history: [...byPick.values()].sort((left, right) => left.pick_number - right.pick_number), warnings: unique(warnings) };
}

function parseClock(value) {
  const match = text(value).match(/^(\d{1,3}):(\d{2})$/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function compact(value) {
  return text(value).toLowerCase().replace(/[^a-z0-9]/g, "");
}

function gridIsHistory(grid) {
  if (grid && grid.kind === "history") return true;
  const headers = (grid && grid.headers) || [];
  const joined = headers.map(compact).join(" ");
  return joined.includes("pick") && joined.includes("player") && joined.includes("team");
}

function gridIsPlayers(grid) {
  if (grid && grid.kind === "players") return true;
  const headers = (grid && grid.headers) || [];
  const joined = headers.map(compact).join(" ");
  return joined.includes("rk") && joined.includes("player") && joined.includes("fpts") && joined.includes("gp");
}

// The projection catalog carries identity and forecast provenance, while the
// ESPN Players grid carries the current visible numeric fields.  A catalog
// captured from a different view may legitimately have an empty `stats`
// object, so merge only finite values that are actually present in the visible
// row.  Never synthesize missing values or turn the bonus columns into trusted
// inputs: PF/TF/EJ/DD/TD/QD remain explicitly untrusted forecast fields.
const VISIBLE_NUMERIC_FIELDS = Object.freeze(["FPTS", "MIN", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"]);

function mergeVisiblePlayerFields(player, fields) {
  if (!player || typeof player !== "object" || !fields || typeof fields !== "object") return player;
  const stats = { ...(player.stats && typeof player.stats === "object" ? player.stats : {}) };
  const missing = new Set(Array.isArray(player.missing_stats) ? player.missing_stats.map(text) : []);
  for (const key of VISIBLE_NUMERIC_FIELDS) {
    const number = finite(fields[key]);
    if (number === null || number < 0) continue;
    stats[key] = number;
    missing.delete(key);
  }
  const projectedGp = finite(fields.GP);
  const draftRank = finite(fields.RK);
  return {
    ...clone(player),
    stats,
    projected_gp: projectedGp !== null && projectedGp >= 0 && projectedGp <= 82 ? projectedGp : player.projected_gp,
    draft_rank: draftRank !== null && draftRank >= 1 ? draftRank : player.draft_rank,
    missing_stats: [...missing].filter(Boolean).sort(),
  };
}

function visiblePlayerRow(row, headers, catalog) {
  const cells = Array.isArray(row.cells) ? row.cells.map(text) : [];
  const fields = {};
  headers.forEach((header, index) => {
    const key = text(header);
    if (key) fields[key] = cells[index] ?? "";
  });
  const playerId = text(first(row, "player_id", "playerId", "button_id", "buttonId"));
  const name = text(first(row, "player_name", "playerName", "name", "full_title", "fullTitle")) || text(cells[1]);
  const visible = row.visible !== false;
  const enabled = row.enabled !== false && row.disabled !== true && row.aria_disabled !== true;
  const basePlayer = catalog.byId.get(playerId) || null;
  const player = mergeVisiblePlayerFields(basePlayer, fields);
  // Retain visible source fields for later roster/final frames and for the
  // known_players projection.  This stays within the supplied catalog and is
  // updated only by values read from the visible ESPN grid.
  if (player && basePlayer && player !== basePlayer) {
    catalog.byId.set(player.player_id, player);
    const names = catalog.byName.get(player.name) || [];
    catalog.byName.set(player.name, names.map((entry) => entry.player_id === player.player_id ? player : entry));
  }
  return { player_id: playerId || null, name, visible, enabled, fields, player, raw: row };
}

function historyEvent(row) {
  const cells = Array.isArray(row.cells) ? row.cells.map(text) : [];
  const pickNumber = integer(first(row, "pick_number", "pickNumber", "pick")) ?? integer(cells[0]);
  const playerName = text(first(row, "player_name", "playerName", "name", "full_title", "fullTitle")) || text(cells[1]);
  const team = text(first(row, "team")) || text(cells[2]);
  const playerId = text(first(row, "player_id", "playerId", "headshot_id", "headshotId")) || playerIdFromHeadshot(first(row, "headshot_url", "headshotUrl", "image_url", "imageUrl"));
  if (pickNumber === null || !playerName || !team) return null;
  return { pick_number: pickNumber, team, player_id: playerId || null, player_name: playerName };
}

function extractGridSnapshots(grids) {
  // This callback is serialized into the visible page by Playwright. Keep it
  // self-contained: no closure, network, storage, or hidden page state.
  return Array.from(grids).map((grid) => {
    const headerNodes = Array.from(grid.querySelectorAll('[role="columnheader"], thead th, [role="row"] [role="columnheader"]'));
    const headers = headerNodes.map((node) => (node.textContent || "").replace(/\s+/g, " ").trim());
    const rowNodes = Array.from(grid.querySelectorAll('[role="row"], tbody tr')).filter((row) => !row.querySelector('[role="columnheader"]'));
    const rows = rowNodes.map((row) => {
      const cellNodes = Array.from(row.querySelectorAll('[role="gridcell"], td'));
      const cells = cellNodes.map((cell) => (cell.textContent || "").replace(/\s+/g, " ").trim());
      const playerNode = row.querySelector('.playerinfo__playername');
      const playerColumn = row.querySelector('.player-column');
      const button = row.matches && row.matches('button[data-player-id]') ? row : row.querySelector('button[data-player-id]');
      const image = row.querySelector('img[src*="/full/"]');
      const playerName = playerNode ? (playerNode.getAttribute("title") || playerNode.textContent || "").replace(/\s+/g, " ").trim() : "";
      const fullTitle = playerColumn ? (playerColumn.getAttribute("title") || "").replace(/\s+/g, " ").trim() : "";
      const imageUrl = image ? image.getAttribute("src") || "" : "";
      const visible = (() => {
        const style = window.getComputedStyle(row);
        return style.display !== "none" && style.visibility !== "hidden" && row.getClientRects().length > 0;
      })();
      const enabled = Boolean(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true";
      return {
        cells,
        player_name: playerName || fullTitle,
        full_title: fullTitle,
        player_id: button ? button.getAttribute("data-player-id") : null,
        headshot_url: imageUrl,
        visible,
        enabled,
      };
    });
    return { headers, rows };
  });
}

function extractRosterTables(tables) {
  return Array.from(tables).map((table) => {
    const headerNodes = Array.from(table.querySelectorAll("thead th, tr:first-child th"));
    const headers = headerNodes.map((node) => (node.textContent || "").replace(/\s+/g, " ").trim());
    const rowNodes = Array.from(table.querySelectorAll("tbody tr, [role=\"row\"]")).filter((row) => !row.querySelector("th"));
    const rows = rowNodes.map((row) => {
      const cells = Array.from(row.querySelectorAll("td, [role=\"gridcell\"]")).map((cell) => (cell.textContent || "").replace(/\s+/g, " ").trim());
      const playerColumn = row.querySelector(".player-column");
      const image = row.querySelector('img[src*="/full/"]');
      const imageUrl = image ? image.getAttribute("src") || "" : "";
      const imageMatch = imageUrl.match(/\/full\/([^/?#]+)\.png(?:[?#].*)?$/i) || imageUrl.match(/\/full\/([^/?#]+)\.png/i);
      return {
        cells,
        full_title: playerColumn ? (playerColumn.getAttribute("title") || "").replace(/\s+/g, " ").trim() : "",
        player_id: imageMatch ? imageMatch[1] : null,
      };
    });
    return { headers, rows };
  });
}

function extractFastSnapshot(body) {
  // This callback is serialized into one read-only DOM evaluation. Keep all
  // selectors and parsing self-contained so a panel switch cannot interleave
  // history, controls, roster, and Players reads.
  const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
  const visible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
  };
  const idFromImage = (src) => {
    const match = String(src || "").match(/\/full\/([^/?#]+)\.png(?:[?#].*)?$/i) || String(src || "").match(/\/full\/([^/?#]+)\.png/i);
    return match ? match[1] : null;
  };
  const grids = Array.from(body.querySelectorAll('[role="grid"]')).map((grid) => {
    const headers = Array.from(grid.querySelectorAll('[role="columnheader"], thead th, [role="row"] [role="columnheader"]')).map((node) => clean(node.textContent));
    const rows = Array.from(grid.querySelectorAll('[role="row"], tbody tr')).filter((row) => !row.querySelector('[role="columnheader"]')).map((row) => {
      const cells = Array.from(row.querySelectorAll('[role="gridcell"], td')).map((cell) => clean(cell.textContent));
      const playerNode = row.querySelector('.playerinfo__playername');
      const playerColumn = row.querySelector('.player-column');
      const button = row.matches && row.matches('button[data-player-id]') ? row : row.querySelector('button[data-player-id]');
      const image = row.querySelector('img[src*="/full/"]');
      return {
        cells,
        player_name: playerNode ? clean(playerNode.getAttribute("title") || playerNode.textContent) : clean(playerColumn && playerColumn.getAttribute("title")),
        full_title: playerColumn ? clean(playerColumn.getAttribute("title")) : "",
        player_id: button ? button.getAttribute("data-player-id") : null,
        headshot_url: image ? image.getAttribute("src") || "" : "",
        visible: visible(row),
        enabled: Boolean(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true",
      };
    });
    return { headers, rows };
  });
  const history = Array.from(body.querySelectorAll("li.pick-message__container")).filter(visible).map((item) => {
    const playerNode = item.querySelector('.playerinfo__playername');
    const image = item.querySelector('img[src*="/full/"]');
    const info = item.querySelector('.pick-info');
    const infoText = clean(info && info.textContent);
    const match = infoText.match(/^R\s*(\d+)\s*,\s*P\s*(\d+)\s*(?:[-–—]\s*(.*))?$/i);
    const span = info && info.querySelector("span");
    const team = clean(span ? span.textContent : (match && match[3]));
    return {
      pick_number: match ? (Number(match[1]) - 1) * 14 + Number(match[2]) : null,
      player_name: playerNode ? clean(playerNode.getAttribute("title") || playerNode.textContent) : "",
      player_id: idFromImage(image ? image.getAttribute("src") : ""),
      team: team.replace(/^[-–—]\s*/, ""),
    };
  }).filter((event) => event.pick_number !== null && event.player_name && event.team);
  const toggle = body.querySelector(".draft-players-toggle");
  const toggleInput = toggle && (toggle.matches && toggle.matches("input") ? toggle : toggle.querySelector("input"));
  let showDrafted = null;
  if (toggleInput && typeof toggleInput.checked === "boolean") showDrafted = toggleInput.checked;
  else if (toggle) {
    const aria = toggle.getAttribute("aria-checked");
    if (aria === "true") showDrafted = true;
    if (aria === "false") showDrafted = false;
  }
  const playerGrid = grids.find((grid) => {
    const joined = grid.headers.map((header) => clean(header).toLowerCase().replace(/[^a-z0-9]/g, "")).join(" ");
    return joined.includes("rk") && joined.includes("player") && joined.includes("fpts") && joined.includes("gp");
  });
  const rosterTables = Array.from(body.querySelectorAll("table")).map((table) => {
    const headers = Array.from(table.querySelectorAll("thead th, tr:first-child th")).map((node) => clean(node.textContent));
    const rows = Array.from(table.querySelectorAll("tbody tr, [role=\"row\"]")).filter((row) => !row.querySelector("th")).map((row) => {
      const cells = Array.from(row.querySelectorAll("td, [role=\"gridcell\"]")).map((cell) => clean(cell.textContent));
      const playerColumn = row.querySelector(".player-column");
      const image = row.querySelector('img[src*="/full/"]');
      return { cells, full_title: playerColumn ? clean(playerColumn.getAttribute("title")) : "", player_id: idFromImage(image ? image.getAttribute("src") : "") };
    });
    return { headers, rows };
  });
  const select = body.querySelector("select");
  const pickOrder = select ? Array.from(select.options || []).map((option) => clean(option.textContent)).filter(Boolean) : [];
  const headingTexts = Array.from(body.querySelectorAll('h1, h2, h3, [role="heading"]'))
    .filter(visible)
    .map((node) => clean(node.textContent))
    .filter(Boolean);
  const nativeHeading = headingTexts.find((value) => /\b(?:your\s+)?draft\s+(?:is\s+)?complete\b|\bdraft(?:ing)?\s+complete\b/i.test(value)) || null;
  const nativeComplete = nativeHeading !== null;
  const nativeControlLabels = Array.from(body.querySelectorAll('button, [role="button"]'))
    .filter(visible)
    .map((node) => clean(node.getAttribute("aria-label") || node.getAttribute("title") || node.textContent))
    .filter(Boolean);
  const nativePaused = nativeControlLabels.length
    ? nativeControlLabels.some((value) => /^resume(?:\s+draft)?\b/i.test(value))
    : null;
  const onClockNode = body.querySelector(".on-the-clock");
  const teamNode = onClockNode && onClockNode.parentElement && onClockNode.parentElement.querySelector(".team-name");
  const onClockText = clean(onClockNode && onClockNode.textContent);
  const clockNode = body.querySelector(".clock__digits");
  const autoInput = body.querySelector(".autoPick-toggle input");
  const clockMatch = onClockText.match(/(?:pick\s*#?|on\s+the\s+clock\s*)?(\d{1,3})/i);
  const clockText = clean(clockNode && clockNode.textContent);
  const clockParts = clockText.match(/^(\d{1,3}):(\d{2})$/);
  return {
    grids,
    history,
    players: playerGrid || null,
    show_drafted: showDrafted,
    roster_tables: rosterTables,
    controls: {
      pick_order: pickOrder,
      current_pick: clockMatch ? Number(clockMatch[1]) : null,
      on_clock_team: clean(teamNode && teamNode.textContent) || null,
      seconds_remaining: clockParts ? Number(clockParts[1]) * 60 + Number(clockParts[2]) : null,
      autopick: autoInput && typeof autoInput.checked === "boolean" ? autoInput.checked : null,
      native_paused: nativePaused,
      native_complete: nativeComplete,
      native_heading: nativeHeading,
    },
  };
}

function isExplicitOff(value) {
  return value === false;
}

const FAST_CAPTURE_WARNING = "fast_capture_not_confirmable";

function foldName(value) {
  return text(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

// ESPN abbreviates roster cells ("K. Towns") and sometimes omits the title
// attribute. Only an initial plus a surname is treated as an abbreviation.
function abbreviatedNameKey(value) {
  const match = text(value).match(/^([A-Za-z\u00c0-\u024f])\.\s*(\S.*)$/);
  return match ? `${foldName(match[1])} ${foldName(match[2])}` : null;
}

function fullNameAbbreviationKey(name) {
  const parts = text(name).split(/\s+/).filter(Boolean);
  if (parts.length < 2) return null;
  return `${foldName(parts[0][0])} ${foldName(parts.slice(1).join(" "))}`;
}

// Resolve a roster cell whose title is missing: headshot ID first, then a
// unique abbreviated-name match across the catalog and history. An ambiguous
// or unknown abbreviation never guesses.
function rosterPlayerWithoutTitle(row, catalog, history, warnings) {
  const cells = Array.isArray(row.cells) ? row.cells : [];
  const abbreviation = [cells[1], ...cells].map(abbreviatedNameKey).find(Boolean) || null;
  const observedId = text(row.player_id);
  const label = abbreviation ? abbreviation.replace(/ /g, "_") : "";
  let player = null;
  if (observedId) {
    player = catalog.byId.get(observedId) || null;
    if (!player) {
      warnings.push(`roster_id_not_in_projection_catalog_${observedId}`);
      return null;
    }
    if (abbreviation && fullNameAbbreviationKey(player.name) !== abbreviation) {
      warnings.push(`roster_headshot_abbreviation_mismatch_${player.player_id}`);
      return null;
    }
  } else if (abbreviation) {
    const ids = new Set();
    for (const candidate of catalog.byId.values()) if (fullNameAbbreviationKey(candidate.name) === abbreviation) ids.add(candidate.player_id);
    for (const event of history) {
      if (event.player_id && fullNameAbbreviationKey(event.player_name) === abbreviation) ids.add(event.player_id);
    }
    if (ids.size !== 1) {
      warnings.push(ids.size ? `roster_abbreviation_ambiguous_${label}` : `roster_abbreviation_unmatched_${label}`);
      return null;
    }
    player = catalog.byId.get([...ids][0]) || null;
    if (!player) {
      warnings.push(`roster_abbreviation_not_in_projection_catalog_${label}`);
      return null;
    }
  } else {
    return null;
  }
  if (!history.some((event) => event.player_id === player.player_id)) {
    warnings.push(`roster_not_in_history_${player.player_id}`);
    return null;
  }
  return { ...clone(player), source: "visible_roster" };
}

function rosterPlayerFromRow(row, catalog, history, warnings) {
  const title = text(row.full_title);
  if (!title) {
    // ESPN renders unused roster slots as ordinary rows with an empty player
    // title. They are structural placeholders, not malformed player rows and
    // should not hide a valid hydrated roster read-back behind a warning.
    const slotText = Array.isArray(row.cells) ? text(row.cells[1]) : "";
    if (!text(row.player_id) && /^(?:empty|[-\u2013\u2014])$/i.test(slotText)) return null;
    const resolved = rosterPlayerWithoutTitle(row, catalog, history, warnings);
    if (resolved) return resolved;
    warnings.push("roster_full_title_missing");
    return null;
  }
  const matches = catalog.byName.get(title) || [];
  if (matches.length !== 1) {
    warnings.push(matches.length ? `roster_name_ambiguous_${title}` : `roster_name_not_in_projection_catalog_${title}`);
    return null;
  }
  const player = matches[0];
  const observedId = text(row.player_id);
  if (observedId && observedId !== player.player_id) {
    warnings.push(`roster_headshot_id_mismatch_${player.player_id}`);
    return null;
  }
  const historyMatch = history.some((event) => event.player_id === player.player_id && event.player_name === title);
  if (!historyMatch) {
    warnings.push(`roster_not_in_history_${player.player_id}`);
    return null;
  }
  return { ...clone(player), source: "visible_roster" };
}

function contiguousHistory(events, endExclusive) {
  if (!events.length || events.some((event) => !event.player_id)) return false;
  const byPick = new Map(events.map((event) => [event.pick_number, event]));
  const end = endExclusive === null ? null : endExclusive;
  if (end === null) return false;
  for (let pick = 1; pick < end; pick += 1) if (!byPick.has(pick)) return false;
  return true;
}

function makeEvalAllLocator(tab, selector) {
  if (!tab || !tab.playwright || typeof tab.playwright.locator !== "function") {
    throw new BrowserObserverError("active tab does not expose visible locator controls", "missing_tab_controls");
  }
  const locator = tab.playwright.locator(selector);
  if (!locator || typeof locator.evaluateAll !== "function") {
    throw new BrowserObserverError(`visible locator ${selector} cannot be evaluated`, "missing_evaluate_all");
  }
  return locator;
}

function makeEvaluateLocator(tab, selector) {
  if (!tab || !tab.playwright || typeof tab.playwright.locator !== "function") {
    throw new BrowserObserverError("active tab does not expose visible locator controls", "missing_tab_controls");
  }
  const locator = tab.playwright.locator(selector);
  if (!locator || typeof locator.evaluate !== "function") {
    throw new BrowserObserverError(`visible locator ${selector} cannot be evaluated`, "missing_evaluate");
  }
  return locator;
}

function createBrowserObserver({
  tab,
  sessionId,
  room,
  freshlyVerifiedRules,
  verifiedRules,
  projectionCatalog,
  counter = { n: 0 },
  now = () => Date.now(),
  retainedHistory: initialRetainedHistory = null,
  maxHistoryAgeMs = 10_000,
  // Optional `async (deltaY) => void` that scrolls the visible Pick History
  // grid; without it captureHistory reads only the rendered rows.
  historyScroll = null,
  historyScrollStepPx = 400,
  historyMaxScrollSteps = 30,
  historySettleMs = 120,
} = {}) {
  const pauseFor = (ms) => (ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve());
  if (!tab || typeof tab.url !== "function") throw new BrowserObserverError("an active tab with url() is required", "missing_tab");
  if (!text(sessionId)) throw new BrowserObserverError("sessionId is required", "missing_session");
  const expectedRoom = normalizeRoom(room);
  const rules = clone(freshlyVerifiedRules || verifiedRules);
  if (!rules || typeof rules !== "object" || !Object.keys(rules).length) throw new BrowserObserverError("freshly verified rules are required", "missing_rules");
  if (!counter || typeof counter !== "object" || !Number.isInteger(counter.n) || counter.n < 0) throw new BrowserObserverError("counter must be a mutable {n} object", "invalid_counter");
  const catalog = buildCatalog(projectionCatalog);
  let retainedHistory = normalizeHistorySnapshot(initialRetainedHistory);

  function setRetainedHistory(snapshot) {
    const normalized = normalizeHistorySnapshot(snapshot);
    if (!normalized) throw new BrowserObserverError("retained history must include a history snapshot object", "invalid_retained_history");
    retainedHistory = normalized;
    return clone(normalized);
  }

  async function readRoom() {
    const actual = normalizeUrlRoom(await tab.url());
    if (!roomsEqual(actual, expectedRoom)) throw new BrowserObserverError("active tab room does not match the verified room", "room_mismatch");
    return { ...actual, practice: expectedRoom.practice };
  }

  async function gridSnapshots() {
    const locator = typeof tab.playwright.getByRole === "function"
      ? tab.playwright.getByRole("grid")
      : makeEvalAllLocator(tab, '[role="grid"]');
    if (!locator || typeof locator.evaluateAll !== "function") throw new BrowserObserverError("visible grid locator cannot be evaluated", "missing_grid_locator");
    return locator.evaluateAll(extractGridSnapshots);
  }

  function visiblePlayersFromGrids(grids, showDrafted) {
    const grid = (grids || []).find(gridIsPlayers);
    const warnings = [];
    if (showDrafted !== false) warnings.push(showDrafted === true ? "show_drafted_enabled" : "show_drafted_state_unknown");
    if (!grid) {
      warnings.push("players_grid_missing");
      return { grid_present: false, headers: [], rows: [], current_available_ids: [], available_players: [], show_drafted: showDrafted, warnings, grids: grids || [] };
    }
    const headers = Array.isArray(grid.headers) ? grid.headers.map(text) : [];
    const rows = (grid.rows || []).map((row) => visiblePlayerRow(row, headers, catalog));
    const currentRows = showDrafted === false ? rows.filter((row) => row.player_id && row.visible && row.enabled) : [];
    const currentAvailableIds = unique(currentRows.map((row) => row.player_id));
    const availablePlayers = [];
    for (const row of currentRows) {
      if (!row.player) {
        warnings.push(`available_projection_missing_${row.player_id}`);
        continue;
      }
      if (!row.player.eligibility.length) {
        warnings.push(`available_projection_eligibility_missing_${row.player_id}`);
        continue;
      }
      availablePlayers.push(clone(row.player));
    }
    if (!headers.length) warnings.push("players_headers_missing");
    if (!rows.length) warnings.push("players_rows_missing");
    return {
      grid_present: true,
      headers,
      rows,
      current_available_ids: currentAvailableIds,
      available_players: availablePlayers,
      show_drafted: showDrafted,
      warnings: unique(warnings),
      grids: grids || [],
    };
  }

  async function captureHistory({ skipRoom = false, expectedCurrentPick = null, retain = true } = {}) {
    if (!skipRoom) await readRoom();
    const warnings = [];
    const byPick = new Map();
    const collect = (historyGrids) => {
      let added = 0;
      for (const grid of historyGrids) {
        for (const row of grid.rows || []) {
          const event = historyEvent(row);
          if (!event) {
            warnings.push("history_row_identity_missing");
            continue;
          }
          const existing = byPick.get(event.pick_number);
          if (existing) {
            if (existing.player_id !== event.player_id || existing.team !== event.team || existing.player_name !== event.player_name) warnings.push(`history_conflict_pick_${event.pick_number}`);
            continue;
          }
          byPick.set(event.pick_number, event);
          added += 1;
          if (!event.player_id) warnings.push(`history_stable_id_missing_${event.pick_number}`);
        }
      }
      return added;
    };
    let grids = await gridSnapshots();
    let historyGrids = grids.filter(gridIsHistory);
    collect(historyGrids);
    // ESPN's Pick History grid is virtualized (~14 rendered rows in live room
    // 1000000015).  With a scroller, walk it from the top and merge rows by
    // pick until the history is complete or scrolling stops adding picks.
    if (typeof historyScroll === "function" && historyGrids.length && !historyIsComplete([...byPick.values()].sort((left, right) => left.pick_number - right.pick_number), expectedCurrentPick)) {
      await historyScroll(-1_000_000);
      await pauseFor(historySettleMs);
      let stale = 0;
      for (let step = 0; step < historyMaxScrollSteps; step += 1) {
        grids = await gridSnapshots();
        historyGrids = grids.filter(gridIsHistory);
        stale = collect(historyGrids) ? 0 : stale + 1;
        if (historyIsComplete([...byPick.values()].sort((left, right) => left.pick_number - right.pick_number), expectedCurrentPick) || stale >= 2) break;
        await historyScroll(historyScrollStepPx);
        await pauseFor(historySettleMs);
      }
    }
    const history = [...byPick.values()].sort((left, right) => left.pick_number - right.pick_number);
    if (!historyGrids.length) warnings.push("history_grid_missing");
    const observedAt = isoNow(now);
    const result = {
      history,
      grid_count: historyGrids.length,
      observed_at: observedAt,
      history_complete: historyIsComplete(history, expectedCurrentPick),
      warnings: unique(warnings),
      grids,
    };
    if (retain && historyGrids.length) retainedHistory = normalizeHistorySnapshot(result);
    return result;
  }

  async function readShowDrafted({ skipRoom = false } = {}) {
    if (!skipRoom) await readRoom();
    const value = await makeEvalAllLocator(tab, ".draft-players-toggle").evaluateAll((elements) => {
      const element = elements[0];
      if (!element) return null;
      const input = element.matches && element.matches("input") ? element : element.querySelector("input");
      if (input && typeof input.checked === "boolean") return input.checked;
      const aria = (input || element).getAttribute && (input || element).getAttribute("aria-checked");
      if (aria === "true") return true;
      if (aria === "false") return false;
      return null;
    });
    return typeof value === "boolean" ? value : null;
  }

  async function readVisiblePlayers({ skipRoom = false } = {}) {
    if (!skipRoom) await readRoom();
    const showDrafted = await readShowDrafted({ skipRoom: true });
    const grids = await gridSnapshots();
    return visiblePlayersFromGrids(grids, showDrafted);
  }

  async function currentAvailable() {
    const view = await readVisiblePlayers();
    if (!isExplicitOff(view.show_drafted)) throw new BrowserObserverError("ShowDrafted must be explicitly off before reading available IDs", "show_drafted_unknown");
    return view.current_available_ids;
  }

  function rosterFromTables(tables, history) {
    const rosterTable = (tables || []).find((table) => compact((table.headers || [])[0]) === "pos");
    const warnings = [];
    if (!rosterTable) return { table_present: false, rows: [], roster: [], warnings: ["roster_pos_table_missing"] };
    const rows = rosterTable.rows || [];
    const roster = [];
    const seen = new Set();
    for (const row of rows) {
      const player = rosterPlayerFromRow(row, catalog, history, warnings);
      if (!player) continue;
      const key = player.player_id || player.name;
      if (seen.has(key)) {
        warnings.push(`roster_duplicate_row_${key}`);
        continue;
      }
      seen.add(key);
      roster.push(player);
    }
    return { table_present: true, rows, roster, warnings: unique(warnings) };
  }

  async function readRoster({ history = [], skipRoom = false } = {}) {
    if (!skipRoom) await readRoom();
    const tables = await makeEvalAllLocator(tab, "table").evaluateAll(extractRosterTables);
    return rosterFromTables(tables, history);
  }

  async function readControls({ skipRoom = false } = {}) {
    if (!skipRoom) await readRoom();
    const pickOrder = await makeEvalAllLocator(tab, "select").evaluateAll((elements) => {
      const firstSelect = elements[0];
      return firstSelect ? Array.from(firstSelect.options || []).map((option) => (option.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean) : [];
    });
    const onClock = await makeEvalAllLocator(tab, ".on-the-clock").evaluateAll((elements) => {
      const element = elements[0];
      if (!element) return null;
      const textValue = (element.textContent || "").replace(/\s+/g, " ").trim();
      const teamNode = element.parentElement && element.parentElement.querySelector(".team-name");
      return { text: textValue, team: teamNode ? (teamNode.textContent || "").replace(/\s+/g, " ").trim() : null };
    });
    const clockText = await makeEvalAllLocator(tab, ".clock__digits").evaluateAll((elements) => elements[0] ? (elements[0].textContent || "").trim() : null);
    const autopick = await makeEvalAllLocator(tab, ".autoPick-toggle input").evaluateAll((elements) => {
      const input = elements[0];
      return input && typeof input.checked === "boolean" ? input.checked : null;
    });
    const nativeHeading = await makeEvalAllLocator(tab, 'h1, h2, h3, [role="heading"]').evaluateAll((elements) => {
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
      };
      const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
      return Array.from(elements || []).filter(visible).map((element) => clean(element.textContent)).find((value) => /\b(?:your\s+)?draft\s+(?:is\s+)?complete\b|\bdraft(?:ing)?\s+complete\b/i.test(value)) || null;
    });
    const nativePaused = await makeEvalAllLocator(tab, 'button, [role="button"]').evaluateAll((elements) => {
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
      };
      const clean = (value) => (value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim());
      const labels = Array.from(elements || []).filter(visible).map((element) => clean(element.getAttribute("aria-label") || element.getAttribute("title") || element.textContent)).filter(Boolean);
      return labels.length ? labels.some((value) => /^resume(?:\s+draft)?\b/i.test(value)) : null;
    });
    const clockPick = onClock && text(onClock.text).match(/(?:pick\s*#?|on\s+the\s+clock\s*)?(\d{1,3})/i);
    const nativeHeadingValue = text(nativeHeading) || null;
    return {
      pick_order: Array.isArray(pickOrder) ? pickOrder : [],
      current_pick: clockPick ? Number(clockPick[1]) : null,
      on_clock_team: onClock && text(onClock.team) ? text(onClock.team) : null,
      seconds_remaining: parseClock(clockText),
      autopick: typeof autopick === "boolean" ? autopick : null,
      native_paused: typeof nativePaused === "boolean" ? nativePaused : null,
      native_complete: nativeHeadingValue !== null,
      native_heading: nativeHeadingValue,
    };
  }

  function frameFromParts({ actualRoom, historyResult, playersResult, rosterResult, controls, frameObservedAt }) {
    const observedTimestamp = historyResult.observed_at ? Date.parse(historyResult.observed_at) : Number.NaN;
    const frameTimestamp = Date.parse(frameObservedAt);
    const historyAgeMs = Number.isFinite(observedTimestamp) ? frameTimestamp - observedTimestamp : Number.NaN;
    const historyFresh = Number.isFinite(historyAgeMs) && historyAgeMs >= 0 && historyAgeMs <= maxHistoryAgeMs;
    // A retained snapshot's flag is metadata only. Revalidate its actual
    // contiguous, unique coverage against the current pick every time.
    const fullHistory = historyIsComplete(historyResult.history, null);
    const terminalEvidence = controls.native_complete === true && fullHistory;
    const effectiveControls = terminalEvidence
      ? { ...controls, current_pick: null, on_clock_team: null, seconds_remaining: null }
      : controls;
    const historyComplete = historyFresh && (terminalEvidence || historyIsComplete(historyResult.history, effectiveControls.current_pick));
    const startingSlots = Array.isArray(rules.starting_slots) ? rules.starting_slots : Array.isArray(rules.slots) ? rules.slots : [];
    const expectedRoster = startingSlots.length + (finite(rules.bench_slots) || 0);
    const noPlayersGrid = !playersResult.grid_present;
    const rosterComplete = expectedRoster > 0 && rosterResult.roster.length === expectedRoster;
    const postOwnFinalFrame = noPlayersGrid && rosterComplete && historyComplete;
    const terminalFrame = terminalEvidence && noPlayersGrid && rosterComplete && historyComplete;
    const warnings = unique([
      ...historyResult.warnings,
      ...playersResult.warnings,
      ...rosterResult.warnings,
      ...(historyResult.observed_at ? [] : ["retained_history_timestamp_missing"]),
      ...(historyFresh ? [] : ["retained_history_stale"]),
      ...(historyComplete ? [] : ["retained_history_incomplete"]),
      ...(playersResult.show_drafted === false ? [] : ["available_ids_not_authorized_until_show_drafted_off"]),
    ]);
    const hasFastFrame = historyComplete && rosterResult.table_present && (playersResult.grid_present || postOwnFinalFrame);
    counter.n += 1;
    return {
      session_id: sessionId,
      room: actualRoom,
      observed_at: frameObservedAt,
      revision: counter.n,
      phase: terminalFrame ? "complete" : effectiveControls.current_pick === null ? "unknown" : "draft",
      current_pick: effectiveControls.current_pick,
      on_clock_team: effectiveControls.on_clock_team,
      seconds_remaining: effectiveControls.seconds_remaining,
      pick_order: effectiveControls.pick_order,
      history: historyResult.history,
      roster: rosterResult.roster,
      available_players: playersResult.available_players,
      known_players: [...catalog.byId.values()].map(clone),
      autopick: effectiveControls.autopick,
      rules: clone(rules),
      complete: hasFastFrame,
      history_complete: historyComplete,
      available_pool_complete: false,
      warnings,
      schema_version: 1,
    };
  }

  async function captureFrame({ historySnapshot = null, retainedHistory: injectedRetainedHistory = null } = {}) {
    const actualRoom = await readRoom();
    let historyResult = normalizeHistorySnapshot(historySnapshot || injectedRetainedHistory);
    if (historySnapshot || injectedRetainedHistory) retainedHistory = historyResult;
    if (!historyResult) historyResult = retainedHistory;
    if (!historyResult) historyResult = await captureHistory({ skipRoom: true });
    const playersResult = await readVisiblePlayers({ skipRoom: true });
    const rosterResult = await readRoster({ history: historyResult.history, skipRoom: true });
    const controls = await readControls({ skipRoom: true });
    return frameFromParts({ actualRoom, historyResult, playersResult, rosterResult, controls, frameObservedAt: isoNow(now) });
  }

  async function captureFastFrame({ historySnapshot = null, retainedHistory: injectedRetainedHistory = null } = {}) {
    const actualRoom = await readRoom();
    const raw = await makeEvaluateLocator(tab, "body").evaluate(extractFastSnapshot);
    if (!raw || typeof raw !== "object") throw new BrowserObserverError("fast DOM snapshot is missing", "missing_fast_snapshot");
    const frameObservedAt = isoNow(now);
    let retained = normalizeHistorySnapshot(historySnapshot || injectedRetainedHistory) || retainedHistory;
    if (historySnapshot || injectedRetainedHistory) retainedHistory = retained;
    const retainedTimestamp = retained && retained.observed_at ? Date.parse(retained.observed_at) : Number.NaN;
    const frameTimestamp = Date.parse(frameObservedAt);
    const retainedAge = Number.isFinite(retainedTimestamp) ? frameTimestamp - retainedTimestamp : Number.NaN;
    const retainedFresh = Boolean(retained) && Number.isFinite(retainedAge) && retainedAge >= 0 && retainedAge <= maxHistoryAgeMs;
    const feedHistory = Array.isArray(raw.history) ? raw.history.map(historyEvent).filter(Boolean) : [];
    const merged = mergeHistoryEvents(retainedFresh && retained ? retained.history : [], feedHistory);
    const historyResult = {
      history: merged.history,
      grid_count: feedHistory.length ? 1 : 0,
      observed_at: retainedFresh && retained && retained.observed_at ? retained.observed_at : frameObservedAt,
      history_complete: false,
      warnings: unique([
        ...(retainedFresh && retained ? retained.warnings : []),
        ...merged.warnings,
        ...(feedHistory.length ? [] : ["fast_history_feed_empty"]),
        ...(retained && !retainedFresh ? ["retained_history_stale"] : []),
        // Fast frames are virtualized and may reuse retained history. The marker
        // makes them unconfirmable: the operator runtime rejects it.
        FAST_CAPTURE_WARNING,
      ]),
      grids: raw.grids || [],
    };
    const playersResult = visiblePlayersFromGrids(raw.grids || [], raw.show_drafted);
    const rosterResult = rosterFromTables(raw.roster_tables || [], historyResult.history);
    const rawControls = raw.controls || {};
    const controls = {
      pick_order: Array.isArray(rawControls.pick_order) ? rawControls.pick_order.map(text) : [],
      current_pick: integer(rawControls.current_pick),
      on_clock_team: text(rawControls.on_clock_team) || null,
      seconds_remaining: finite(rawControls.seconds_remaining),
      autopick: typeof rawControls.autopick === "boolean" ? rawControls.autopick : null,
      native_paused: typeof rawControls.native_paused === "boolean" ? rawControls.native_paused : null,
      native_complete: rawControls.native_complete === true,
      native_heading: text(rawControls.native_heading) || null,
    };
    return frameFromParts({ actualRoom, historyResult, playersResult, rosterResult, controls, frameObservedAt });
  }

  async function completeFrame(options = {}) {
    const frame = await captureFrame(options);
    const noPlayersGrid = frame.available_players.length === 0 && frame.warnings.includes("players_grid_missing");
    const picks = frame.history.map((event) => event.pick_number);
    const uniquePlayerIds = new Set(frame.history.map((event) => event.player_id)).size === 140;
    const completeHistory = picks.length === 140 && picks.every((pick, index) => pick === index + 1) && uniquePlayerIds && frame.history.every((event) => event.player_id);
    const startingSlots = Array.isArray(rules.starting_slots) ? rules.starting_slots : Array.isArray(rules.slots) ? rules.slots : [];
    const expectedRoster = startingSlots.length + (finite(rules.bench_slots) || 0);
    const rosterComplete = frame.roster.length === expectedRoster && expectedRoster > 0;
    if (!noPlayersGrid || !completeHistory || !rosterComplete || frame.current_pick !== null || frame.on_clock_team !== null || frame.seconds_remaining !== null) {
      throw new BrowserObserverError("complete frame requires no Players grid, 140 contiguous identified picks, a complete roster, and no active clock", "incomplete_final_frame");
    }
    return {
      ...frame,
      phase: "complete",
      current_pick: null,
      on_clock_team: null,
      seconds_remaining: null,
      complete: true,
      history_complete: true,
      available_pool_complete: true,
      warnings: unique(frame.warnings),
    };
  }

  return Object.freeze({
    readRoom,
    setRetainedHistory,
    captureHistory,
    readVisiblePlayers,
    currentAvailable,
    readRoster,
    readControls,
    captureFrame,
    captureFastFrame,
    fastCapture: captureFastFrame,
    completeFrame,
    counter,
  });
}

module.exports = {
  BrowserObserverError,
  FAST_CAPTURE_WARNING,
  REQUIRED_MISSING_STATS,
  createBrowserObserver,
  extractGridSnapshots,
  extractFastSnapshot,
  extractRosterTables,
  normalizeUrlRoom,
};
