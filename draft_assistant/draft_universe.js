"use strict";

/**
 * Durable, observation-only draft universe.
 *
 * The catalog is keyed by ESPN's stable player id.  A feed may be virtualized,
 * duplicated, delayed, or out of order, so feed observations are retained as
 * evidence and never make a player unavailable.  Only a contiguous,
 * explicitly confirmed history can remove a player from the recommendation
 * pool.  Any ambiguity freezes recommendation readiness instead of guessing.
 */

const CORE_STATS = Object.freeze(["PTS", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"]);
const REQUIRED_PROJECTION_FIELDS = Object.freeze([...CORE_STATS, "GP"]);
const POSITION_GROUPS = Object.freeze({ PG: "G", SG: "G", G: "G", SF: "F", PF: "F", F: "F", C: "C", UTIL: "UTIL" });

class DraftUniverseError extends Error {
  constructor(message, code = "draft_universe_error", detail = {}) {
    super(message);
    this.name = "DraftUniverseError";
    this.code = code;
    this.detail = detail;
  }
}

function text(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

function number(value) {
  if (value === undefined || value === null || value === "" || value === "--" || typeof value === "boolean") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function clone(value) {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

function unique(values) {
  return [...new Set(values)];
}

function normalizePositions(raw) {
  const source = raw && typeof raw === "object" ? (raw.eligibility ?? raw.positions ?? raw.position) : raw;
  const values = Array.isArray(source) ? source : text(source).split(/[,/ ]+/).filter(Boolean);
  return unique(values.map((value) => POSITION_GROUPS[text(value).toUpperCase()] || text(value).toUpperCase()).filter((value) => ["C", "G", "F"].includes(value)));
}

function normalizeCatalogPlayer(raw) {
  if (!raw || typeof raw !== "object") return null;
  const playerId = text(raw.player_id ?? raw.playerId ?? raw.id);
  const name = text(raw.name ?? raw.player_name ?? raw.playerName ?? raw.full_name ?? raw.fullName);
  const eligibility = normalizePositions(raw);
  if (!playerId || !name) return null;
  const stats = {};
  if (raw.stats && typeof raw.stats === "object") {
    for (const [key, value] of Object.entries(raw.stats)) {
      const parsed = number(value);
      if (parsed !== null && parsed >= 0) stats[text(key).toUpperCase()] = parsed;
    }
  }
  for (const key of CORE_STATS) {
    const parsed = number(raw[key] ?? raw[key.toLowerCase()]);
    if (parsed !== null && parsed >= 0) stats[key] = parsed;
  }
  const projectedGp = number(raw.projected_gp ?? raw.projectedGp ?? raw.gp ?? stats.GP);
  const record = {
    player_id: playerId,
    name,
    eligibility,
    stats,
    projected_gp: projectedGp,
    draft_rank: number(raw.draft_rank ?? raw.draftRank ?? raw.rk),
    adp: number(raw.adp),
    availability: number(raw.availability),
    nba_team: text(raw.nba_team ?? raw.nbaTeam ?? raw.team) || null,
    injury_status: text(raw.injury_status ?? raw.injuryStatus ?? raw.injury) || null,
    source: text(raw.source) || "projection_catalog",
    missing_stats: Array.isArray(raw.missing_stats) ? unique(raw.missing_stats.map((value) => text(value).toUpperCase()).filter(Boolean)) : [],
  };
  return record;
}

function normalizeHistoryEvent(raw) {
  if (!raw || typeof raw !== "object") return null;
  const pickNumber = number(raw.pick_number ?? raw.pickNumber ?? raw.pick);
  const playerId = text(raw.player_id ?? raw.playerId ?? raw.headshot_id ?? raw.headshotId) || null;
  const playerName = text(raw.player_name ?? raw.playerName ?? raw.name ?? raw.full_name ?? raw.fullName);
  const team = text(raw.team ?? raw.team_name ?? raw.teamName);
  if (pickNumber === null || !Number.isInteger(pickNumber) || pickNumber < 1 || !playerName || !team) return null;
  return { pick_number: pickNumber, player_id: playerId, player_name: playerName, team };
}

function sameEvent(left, right) {
  return left && right && left.pick_number === right.pick_number && left.player_id === right.player_id && left.player_name === right.player_name && left.team === right.team;
}

function normalizedName(value) {
  return text(value).toLowerCase().replace(/[^a-z0-9]/g, "");
}

class DraftUniverse {
  constructor({ catalog = [], catalogComplete = false, expectedCatalogCount = null, expectedPicks = null } = {}) {
    this.records = new Map();
    this.feed = new Map();
    this.historyCandidates = new Map();
    this.confirmedHistory = new Map();
    this.invalidCatalogCount = 0;
    this.catalogComplete = Boolean(catalogComplete);
    this.expectedCatalogCount = Number.isInteger(expectedCatalogCount) && expectedCatalogCount > 0 ? expectedCatalogCount : null;
    this.expectedPicks = Number.isInteger(expectedPicks) && expectedPicks > 0 ? expectedPicks : null;
    this.historyObserved = false;
    this.confirmedThrough = 0;
    this.blockedReasons = new Set();
    this.conflicts = [];
    this.gaps = [];
    this.ingestCatalog(catalog);
    this._refreshCatalogCompleteness();
  }

  _block(reason, detail = {}) {
    this.blockedReasons.add(reason);
    if (Object.keys(detail).length) this.conflicts.push({ reason, ...clone(detail) });
  }

  _unblock(reason) {
    this.blockedReasons.delete(reason);
  }

  _refreshCatalogCompleteness() {
    if (this.invalidCatalogCount > 0) this.catalogComplete = false;
    else if (this.expectedCatalogCount !== null) this.catalogComplete = this.records.size >= this.expectedCatalogCount;
  }

  ingestCatalog(input, { complete = undefined, expectedCount = undefined } = {}) {
    const values = input && !Array.isArray(input) && Array.isArray(input.players) ? input.players : input;
    const rows = values instanceof Map ? [...values.values()] : Array.isArray(values) ? values : values && typeof values === "object" ? Object.values(values) : [];
    for (const raw of rows) {
      const player = normalizeCatalogPlayer(raw);
      if (!player) {
        this.invalidCatalogCount += 1;
        continue;
      }
      const existing = this.records.get(player.player_id);
      if (existing) {
        if (normalizedName(existing.name) !== normalizedName(player.name) || (existing.nba_team && player.nba_team && existing.nba_team !== player.nba_team)) {
          this._block("catalog_conflict", { player_id: player.player_id, existing: { name: existing.name, nba_team: existing.nba_team }, incoming: { name: player.name, nba_team: player.nba_team } });
          continue;
        }
        const merged = { ...existing, ...player, name: existing.name, nba_team: existing.nba_team || player.nba_team, injury_status: existing.injury_status || player.injury_status, eligibility: existing.eligibility.length ? existing.eligibility : player.eligibility, stats: { ...existing.stats, ...player.stats }, projected_gp: existing.projected_gp ?? player.projected_gp, draft_rank: existing.draft_rank ?? player.draft_rank, adp: existing.adp ?? player.adp, missing_stats: unique([...(existing.missing_stats || []), ...(player.missing_stats || [])]) };
        this.records.set(player.player_id, merged);
      } else {
        this.records.set(player.player_id, player);
      }
    }
    if (Number.isInteger(expectedCount) && expectedCount > 0) this.expectedCatalogCount = expectedCount;
    if (complete === true || (input && !Array.isArray(input) && input.catalog_complete === true)) this.catalogComplete = true;
    this._refreshCatalogCompleteness();
    return this.snapshot();
  }

  addCatalog(input, options) {
    return this.ingestCatalog(input, options);
  }

  _stageEvents(map, rows, source) {
    const staged = new Map(map);
    const errors = [];
    const seenErrors = new Set();
    const otherMaps = source === "history" ? [this.confirmedHistory, this.feed] : [this.confirmedHistory, this.historyCandidates];
    const addError = (reason, detail) => {
      const key = `${reason}:${JSON.stringify(detail)}`;
      if (seenErrors.has(key)) return;
      seenErrors.add(key);
      errors.push({ reason, detail });
    };
    for (const raw of rows) {
      const event = normalizeHistoryEvent(raw);
      if (!event) {
        addError(`${source}_invalid_event`, {});
        continue;
      }
      const existing = staged.get(event.pick_number);
      if (existing && !sameEvent(existing, event)) {
        addError(`${source}_conflict`, { pick_number: event.pick_number, existing, incoming: event });
        continue;
      }
      for (const otherMap of otherMaps) {
        const other = otherMap.get(event.pick_number);
        if (other && !sameEvent(other, event)) {
          addError(`${source}_conflict`, { pick_number: event.pick_number, existing: other, incoming: event });
        }
      }
      if (!existing) staged.set(event.pick_number, event);
    }
    const maps = [staged, ...otherMaps];
    for (let mapIndex = 0; mapIndex < maps.length; mapIndex += 1) {
      for (const [pick, event] of maps[mapIndex]) {
        if (!event.player_id) continue;
        for (let otherMapIndex = 0; otherMapIndex < maps.length; otherMapIndex += 1) {
          for (const [otherPick, other] of maps[otherMapIndex]) {
            if (pick === otherPick && mapIndex === otherMapIndex) continue;
            if (other.player_id === event.player_id && pick !== otherPick) {
              addError(`${source}_player_repeated`, { player_id: event.player_id, picks: [pick, otherPick].sort((a, b) => a - b) });
            }
          }
        }
      }
    }
    return { staged, errors };
  }

  _recordErrors(errors) {
    for (const error of errors) this._block(error.reason, error.detail);
  }

  applyFeed(input, { events = undefined } = {}) {
    const rows = Array.isArray(events) ? events : Array.isArray(input) ? input : input && Array.isArray(input.events) ? input.events : input && Array.isArray(input.history) ? input.history : [];
    const { staged, errors } = this._stageEvents(this.feed, rows, "feed");
    if (errors.length) this._recordErrors(errors);
    else this.feed = staged;
    return this.snapshot();
  }

  applyHistory(input, { historyComplete = undefined, complete = undefined, currentPick = undefined } = {}) {
    const objectInput = input && !Array.isArray(input) ? input : {};
    const rows = Array.isArray(input) ? input : Array.isArray(objectInput.history) ? objectInput.history : Array.isArray(objectInput.events) ? objectInput.events : [];
    const isComplete = historyComplete === true || complete === true || objectInput.history_complete === true || objectInput.historyComplete === true;
    const observedCurrentPick = number(currentPick ?? objectInput.current_pick ?? objectInput.currentPick);
    this.historyObserved = true;
    const prepared = this._stageEvents(this.historyCandidates, rows, "history");
    if (prepared.errors.length) {
      this._recordErrors(prepared.errors);
      return this.snapshot();
    }
    // Preserve non-authoritative observations for a later complete read, but
    // never let currentPick alone make them eligible to remove players.
    this.historyCandidates = prepared.staged;
    if (!isComplete) {
      this._block("history_unconfirmed");
      return this.snapshot();
    }
    const candidatePicks = [...prepared.staged.keys()].sort((a, b) => a - b);
    const end = observedCurrentPick !== null && Number.isInteger(observedCurrentPick) && observedCurrentPick >= 1
      ? observedCurrentPick - 1
      : (candidatePicks.length ? candidatePicks[candidatePicks.length - 1] : 0);
    if (end < this.confirmedThrough) return this.snapshot();
    const gaps = [];
    let missingId = false;
    let unknownPlayer = false;
    let repeatedPlayer = false;
    let failedPick = null;
    const seenPlayers = new Map();
    for (let pick = 1; pick <= end; pick += 1) {
      const event = prepared.staged.get(pick);
      if (!event) {
        gaps.push(pick);
        failedPick = pick;
        break;
      }
      if (!event.player_id) {
        missingId = true;
        failedPick = pick;
        break;
      }
      if (!this.records.has(event.player_id)) {
        unknownPlayer = true;
        failedPick = pick;
        break;
      }
      const priorPick = seenPlayers.get(event.player_id);
      if (priorPick !== undefined) {
        repeatedPlayer = true;
        failedPick = pick;
        break;
      }
      seenPlayers.set(event.player_id, pick);
    }
    this.gaps = gaps;
    if (gaps.length) this._block("history_gap", { picks: gaps });
    else this._unblock("history_gap");
    if (missingId) this._block("history_missing_player_id", { pick_number: failedPick });
    else this._unblock("history_missing_player_id");
    if (unknownPlayer) this._block("history_unknown_player", { pick_number: failedPick });
    else this._unblock("history_unknown_player");
    if (repeatedPlayer) this._block("history_boundary_player_repeated", { pick_number: failedPick, picks: [...seenPlayers.values()] });
    else this._unblock("history_boundary_player_repeated");
    const boundaryValid = !gaps.length && !missingId && !unknownPlayer && !repeatedPlayer;
    if (!boundaryValid) return this.snapshot();
    // Commit the whole validated boundary atomically.  The previous confirmed
    // prefix remains intact when any row in the new boundary is missing,
    // unknown, or contradictory.
    const confirmed = new Map(this.confirmedHistory);
    for (let pick = 1; pick <= end; pick += 1) confirmed.set(pick, prepared.staged.get(pick));
    this.confirmedHistory = confirmed;
    this.confirmedThrough = Math.max(this.confirmedThrough, end);
    this._unblock("history_unconfirmed");
    if (this.expectedPicks !== null && end >= this.expectedPicks && this.confirmedThrough < this.expectedPicks) this._block("history_incomplete", { expected_picks: this.expectedPicks, confirmed_through: this.confirmedThrough });
    else this._unblock("history_incomplete");
    return this.snapshot();
  }

  observe({ catalog = undefined, catalogComplete = undefined, expectedCatalogCount = undefined, feed = undefined, history = undefined, historyComplete = undefined, currentPick = undefined } = {}) {
    if (catalog !== undefined) this.ingestCatalog(catalog, { complete: catalogComplete, expectedCount: expectedCatalogCount });
    else if (catalogComplete === true) this.catalogComplete = true;
    if (feed !== undefined) this.applyFeed(feed);
    if (history !== undefined) this.applyHistory(history, { historyComplete, currentPick });
    return this.snapshot();
  }

  _projectionComplete(record) {
    return REQUIRED_PROJECTION_FIELDS.every((key) => key === "GP" ? (number(record.projected_gp) ?? 0) > 0 : number(record.stats[key]) !== null);
  }

  _eligible(record) {
    return Array.isArray(record.eligibility) && record.eligibility.length > 0;
  }

  availablePlayers({ completeOnly = false } = {}) {
    const drafted = new Set([...this.confirmedHistory.values()].map((event) => event.player_id));
    return [...this.records.values()].filter((record) => !drafted.has(record.player_id) && this._eligible(record) && (!completeOnly || this._projectionComplete(record))).map(clone);
  }

  canRecommend() {
    return this.snapshot().recommendation_ready;
  }

  snapshot() {
    const catalog = [...this.records.values()].map(clone);
    const eligible = catalog.filter((record) => this._eligible(record));
    const completeEligible = eligible.filter((record) => this._projectionComplete(record));
    const draftedIds = unique([...this.confirmedHistory.values()].map((event) => event.player_id));
    const availableEligible = eligible.filter((record) => !draftedIds.includes(record.player_id));
    const availableComplete = completeEligible.filter((record) => !draftedIds.includes(record.player_id));
    const blockedReasons = [...this.blockedReasons].sort();
    const recommendationReady = this.catalogComplete && this.historyObserved && blockedReasons.length === 0 && availableComplete.length > 0;
    return {
      catalog,
      catalog_by_id: Object.fromEntries(catalog.map((record) => [record.player_id, clone(record)])),
      catalog_count: catalog.length,
      catalog_complete: this.catalogComplete,
      expected_catalog_count: this.expectedCatalogCount,
      invalid_catalog_count: this.invalidCatalogCount,
      eligible_count: eligible.length,
      complete_eligible_count: completeEligible.length,
      available_eligible_count: availableEligible.length,
      available_complete_eligible_count: availableComplete.length,
      incomplete_projection_count: eligible.length - completeEligible.length,
      incomplete_projection_ids: eligible.filter((record) => !this._projectionComplete(record)).map((record) => record.player_id),
      feed: [...this.feed.values()].sort((a, b) => a.pick_number - b.pick_number).map(clone),
      history: [...this.confirmedHistory.values()].sort((a, b) => a.pick_number - b.pick_number).map(clone),
      observed_history: [...this.historyCandidates.values()].sort((a, b) => a.pick_number - b.pick_number).map(clone),
      drafted_ids: draftedIds,
      drafted_count: draftedIds.length,
      available_players: this.availablePlayers(),
      recommendation_pool: this.availablePlayers({ completeOnly: true }),
      history_observed: this.historyObserved,
      history_complete_through: this.confirmedThrough,
      history_gaps: [...this.gaps],
      blocked: blockedReasons.length > 0,
      blocked_reasons: blockedReasons,
      conflicts: clone(this.conflicts),
      recommendation_ready: recommendationReady,
    };
  }
}

function createDraftUniverse(options) {
  return new DraftUniverse(options);
}

module.exports = {
  CORE_STATS,
  DraftUniverse,
  DraftUniverseError,
  REQUIRED_PROJECTION_FIELDS,
  createDraftUniverse,
  normalizeCatalogPlayer,
  normalizeHistoryEvent,
};
