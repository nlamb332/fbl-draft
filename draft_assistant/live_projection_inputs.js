"use strict";

/**
 * Validate the primitive projections that the live valuation engine consumes.
 *
 * This module is intentionally independent of the coordinator and valuation
 * implementation.  It is a preactivation guard for browser adapters: rows
 * that do not have the complete core projection set are excluded from live
 * writes, while the original observation remains available for diagnostics.
 * No missing value is filled or inferred from rank, FPTS, ADP, or row order.
 */

const DEFAULT_CORE_FIELDS = Object.freeze([
  "PTS",
  "FGM",
  "FGA",
  "FTM",
  "FTA",
  "3PM",
  "REB",
  "AST",
  "STL",
  "BLK",
  "TO",
]);

const TOTAL_GAMES = 82;

function own(value, key) {
  return value !== null && typeof value === "object"
    && Object.prototype.hasOwnProperty.call(value, key);
}

function first(value, ...keys) {
  for (const key of keys) {
    if (own(value, key) && value[key] !== undefined && value[key] !== null) return value[key];
  }
  return undefined;
}

function firstPresent(value, ...keys) {
  for (const key of keys) {
    if (own(value, key)) return value[key];
  }
  return undefined;
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

function text(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

function playerId(player) {
  return text(first(player, "player_id", "playerId", "data_player_id", "dataPlayerId"));
}

function availablePlayers(observation) {
  const rows = first(observation, "available_players", "availablePlayers");
  return Array.isArray(rows) ? rows : [];
}

function normalizedCoreFields(options) {
  const supplied = first(options, "core_fields", "coreFields");
  if (supplied === undefined) return [...DEFAULT_CORE_FIELDS];
  if (!Array.isArray(supplied)) throw new TypeError("core_fields must be an array");
  const result = [];
  for (const raw of supplied) {
    const field = text(raw).toUpperCase();
    if (field && !result.includes(field)) result.push(field);
  }
  return result;
}

function numericStatus(value) {
  if (value === undefined || value === null || (typeof value === "string" && value.trim() === "")) return "missing";
  if (typeof value === "boolean") return "invalid";
  if (typeof value !== "number" && typeof value !== "string") return "invalid";
  const number = typeof value === "number" ? value : Number(value.trim());
  if (!Number.isFinite(number)) return "nonfinite";
  return "valid";
}

function numericValue(value) {
  return typeof value === "number" ? value : Number(value.trim());
}

function validateLiveProjectionInputs(observation, options = {}) {
  const coreFields = normalizedCoreFields(options);
  const sourceRows = availablePlayers(observation);
  const idCounts = new Map();
  for (const player of sourceRows) {
    const id = playerId(player);
    if (id) idCounts.set(id, (idCounts.get(id) || 0) + 1);
  }
  const rows = sourceRows.map((player, index) => {
    const stats = player && player.stats && typeof player.stats === "object" && !Array.isArray(player.stats)
      ? player.stats
      : {};
    const normalizedStats = {};
    for (const [key, value] of Object.entries(stats)) normalizedStats[String(key).toUpperCase()] = value;

    const missingCoreFields = [];
    const nonfiniteCoreFields = [];
    const invalidCoreFields = [];
    for (const field of coreFields) {
      const status = numericStatus(normalizedStats[field]);
      if (status === "missing") missingCoreFields.push(field);
      else if (status === "nonfinite") nonfiniteCoreFields.push(field);
      else if (status === "invalid" || numericValue(normalizedStats[field]) < 0) invalidCoreFields.push(field);
    }

    const rawGp = firstPresent(player, "projected_gp", "projectedGp");
    const gpStatus = numericStatus(rawGp);
    const missingGp = gpStatus === "missing";
    const nonfiniteGp = gpStatus === "nonfinite";
    const invalidGp = gpStatus === "invalid"
      || (gpStatus === "valid" && !(numericValue(rawGp) > 0 && numericValue(rawGp) <= TOTAL_GAMES));
    const id = playerId(player);
    const duplicatePlayerId = Boolean(id && idCounts.get(id) > 1);
    const eligible = Boolean(id)
      && !duplicatePlayerId
      && missingCoreFields.length === 0
      && nonfiniteCoreFields.length === 0
      && invalidCoreFields.length === 0
      && !missingGp
      && !nonfiniteGp
      && !invalidGp;

    const missingFields = [...missingCoreFields];
    if (missingGp) missingFields.push("projected_gp");
    const nonfiniteFields = [...nonfiniteCoreFields];
    if (nonfiniteGp) nonfiniteFields.push("projected_gp");
    const invalidFields = [...invalidCoreFields];
    if (invalidGp) invalidFields.push("projected_gp");
    if (duplicatePlayerId) invalidFields.push("player_id");

    return {
      index,
      player_id: id || null,
      eligible,
      duplicate_player_id: duplicatePlayerId,
      missing_core_fields: missingCoreFields,
      nonfinite_core_fields: nonfiniteCoreFields,
      invalid_core_fields: invalidCoreFields,
      missing_gp: missingGp,
      nonfinite_gp: nonfiniteGp,
      invalid_gp: invalidGp,
      missing_fields: missingFields,
      nonfinite_fields: nonfiniteFields,
      invalid_fields: invalidFields,
    };
  });

  const eligibleIds = rows.filter((row) => row.eligible).map((row) => row.player_id);
  const warnings = [];
  for (const row of rows) {
    if (row.eligible) continue;
    const label = row.player_id || `row ${row.index}`;
    if (row.missing_fields.length) warnings.push(`${label}: missing ${row.missing_fields.join(", ")}`);
    if (row.nonfinite_fields.length) warnings.push(`${label}: nonfinite ${row.nonfinite_fields.join(", ")}`);
    if (row.invalid_fields.length) warnings.push(`${label}: invalid ${row.invalid_fields.join(", ")}`);
    if (row.duplicate_player_id) warnings.push(`${label}: duplicate player_id; all duplicate rows excluded`);
    if (!row.player_id) warnings.push(`${label}: missing player_id`);
  }
  if (eligibleIds.length === 0) warnings.push("no live candidate has complete core projections and GP");

  return {
    ready: eligibleIds.length > 0,
    eligible_ids: eligibleIds,
    required_core_fields: coreFields,
    rows,
    warnings,
  };
}

/**
 * Clone an observation and retain only rows safe for a live recommendation.
 * The known catalog, history, roster, and visible-pool metadata are preserved.
 */
function filterLiveAvailablePlayers(observation, options = {}) {
  const validation = validateLiveProjectionInputs(observation, options);
  const filtered = clone(observation && typeof observation === "object" ? observation : {});
  const sourceRows = availablePlayers(observation);
  const eligibleIndices = new Set(validation.rows.filter((row) => row.eligible).map((row) => row.index));
  const retained = sourceRows
    .filter((player, index) => eligibleIndices.has(index))
    .map(clone);
  if (own(filtered, "availablePlayers") && !own(filtered, "available_players")) filtered.availablePlayers = retained;
  else filtered.available_players = retained;

  const excludedRows = validation.rows.filter((row) => !row.eligible);
  const excluded = excludedRows.length > 0;
  const originalComplete = Boolean(firstPresent(observation, "available_pool_complete", "availablePoolComplete"));
  const filteredComplete = originalComplete && !excluded;
  if (excluded && originalComplete) {
    if (own(filtered, "available_pool_complete")) filtered.available_pool_complete = false;
    if (own(filtered, "availablePoolComplete")) filtered.availablePoolComplete = false;
    if (!own(filtered, "available_pool_complete") && !own(filtered, "availablePoolComplete")) {
      filtered.available_pool_complete = false;
    }
  }
  const originalWarnings = Array.isArray(filtered.warnings) ? filtered.warnings : [];
  const filterWarnings = [...validation.warnings];
  if (excluded) filterWarnings.push(`live projection filter excluded ${excludedRows.length} visible row(s)`);
  if (originalComplete && !filteredComplete) {
    filterWarnings.push("available_pool_complete reset false after live projection filtering");
  }
  filtered.warnings = [...new Set([...originalWarnings, ...filterWarnings])];
  const filterMetadata = {
    original_available_pool_complete: originalComplete,
    filtered_available_pool_complete: filteredComplete,
    original_available_count: sourceRows.length,
    retained_available_count: retained.length,
    excluded_row_indices: excludedRows.map((row) => row.index),
    excluded_player_ids: excludedRows.map((row) => row.player_id).filter(Boolean),
  };
  return {
    ...validation,
    observation: filtered,
    retained_available_players: retained,
    filter_metadata: filterMetadata,
  };
}

module.exports = {
  DEFAULT_CORE_FIELDS,
  filterLiveAvailablePlayers,
  validateLiveProjectionInputs,
};
