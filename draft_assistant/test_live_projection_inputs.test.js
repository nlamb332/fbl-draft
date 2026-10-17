"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DEFAULT_CORE_FIELDS,
  filterLiveAvailablePlayers,
  validateLiveProjectionInputs,
} = require("./live_projection_inputs.js");

function player(id, overrides = {}) {
  const stats = Object.fromEntries(DEFAULT_CORE_FIELDS.map((field) => [field, 1]));
  return {
    player_id: id,
    name: id.toUpperCase(),
    eligibility: ["G"],
    stats,
    projected_gp: 80,
    source: "catalog",
    draft_rank: 3,
    availability: 0.9,
    ...overrides,
  };
}

test("default validation requires all eleven core projections and positive GP", () => {
  const observation = {
    available_players: [
      player("complete"),
      player("gp-only", { stats: {}, projected_gp: 72 }),
      player("zero-gp", { projected_gp: 0 }),
      player("bool-stat", { stats: { ...player("tmp").stats, PTS: true } }),
      player("nan-stat", { stats: { ...player("tmp").stats, REB: Infinity } }),
      player("negative-stat", { stats: { ...player("tmp").stats, AST: -1 } }),
    ],
  };
  const result = validateLiveProjectionInputs(observation);
  assert.equal(result.ready, true);
  assert.deepEqual(result.eligible_ids, ["complete"]);
  assert.deepEqual(result.required_core_fields, DEFAULT_CORE_FIELDS);

  const gpOnly = result.rows.find((row) => row.player_id === "gp-only");
  assert.deepEqual(gpOnly.missing_core_fields, DEFAULT_CORE_FIELDS);
  assert.equal(gpOnly.missing_gp, false);
  assert.equal(gpOnly.eligible, false);

  const zeroGp = result.rows.find((row) => row.player_id === "zero-gp");
  assert.equal(zeroGp.invalid_gp, true);
  assert.equal(zeroGp.eligible, false);

  const boolStat = result.rows.find((row) => row.player_id === "bool-stat");
  assert.deepEqual(boolStat.invalid_core_fields, ["PTS"]);
  assert.equal(boolStat.eligible, false);

  const nanStat = result.rows.find((row) => row.player_id === "nan-stat");
  assert.deepEqual(nanStat.nonfinite_core_fields, ["REB"]);
  assert.equal(nanStat.eligible, false);

  const negativeStat = result.rows.find((row) => row.player_id === "negative-stat");
  assert.deepEqual(negativeStat.invalid_core_fields, ["AST"]);
  assert.equal(negativeStat.eligible, false);
});

test("missing GP and nonfinite GP are reported without using availability fallback", () => {
  const result = validateLiveProjectionInputs({
    available_players: [
      player("missing-gp", { projected_gp: null, availability: 1 }),
      player("nan-gp", { projected_gp: Infinity, availability: 1 }),
      player("string-gp", { projected_gp: "80" }),
    ],
  });
  assert.deepEqual(result.eligible_ids, ["string-gp"]);
  assert.equal(result.rows[0].missing_gp, true);
  assert.equal(result.rows[1].nonfinite_gp, true);
});

test("bonus and foul fields remain optional and cannot substitute for core fields", () => {
  const complete = player("complete", {
    stats: {
      ...Object.fromEntries(DEFAULT_CORE_FIELDS.map((field) => [field, 1])),
      PF: 0,
      TF: 0,
      EJ: 0,
      DD: 0,
      TD: 0,
      QD: 0,
    },
  });
  const bonusOnly = player("bonus-only", { stats: { PF: 0, DD: 2, TD: 1, QD: 1 } });
  const result = validateLiveProjectionInputs({ available_players: [complete, bonusOnly] });
  assert.deepEqual(result.eligible_ids, ["complete"]);
  assert.deepEqual(result.rows[0].missing_core_fields, []);
  assert.deepEqual(result.rows[1].missing_core_fields, DEFAULT_CORE_FIELDS);
});

test("custom core field set is explicit and case normalized", () => {
  const result = validateLiveProjectionInputs({ available_players: [player("p", { stats: { PTS: 22 } })] }, {
    core_fields: ["pts", "REB"],
  });
  assert.deepEqual(result.required_core_fields, ["PTS", "REB"]);
  assert.deepEqual(result.eligible_ids, []);
  assert.deepEqual(result.rows[0].missing_core_fields, ["REB"]);
});

test("duplicate stable IDs make every occurrence ineligible", () => {
  const result = validateLiveProjectionInputs({
    available_players: [player("duplicate"), player("duplicate", { source: "visible_dom" })],
  });
  assert.equal(result.ready, false);
  assert.deepEqual(result.eligible_ids, []);
  assert.deepEqual(result.rows.map((row) => row.duplicate_player_id), [true, true]);
  assert.ok(result.warnings.some((warning) => warning.includes("duplicate player_id")));

  const filtered = filterLiveAvailablePlayers({
    available_players: [player("duplicate"), player("duplicate", { stats: {} })],
  });
  assert.deepEqual(filtered.observation.available_players, []);
  assert.deepEqual(filtered.filter_metadata.excluded_row_indices, [0, 1]);
});

test("filter clones only eligible visible rows and preserves catalog and metadata", () => {
  const known = [player("catalog-only", { source: "known_catalog" })];
  const available = [
    player("ready", { source: "visible_dom", availability: 0.8 }),
    player("incomplete", { stats: {}, projected_gp: 72, source: "visible_dom" }),
  ];
  const observation = {
    session_id: "session",
    known_players: known,
    available_players: available,
    available_pool_complete: false,
    warnings: ["visible_pool_partial"],
    history: [{ player_id: "already-drafted", team: "Other" }],
    roster: [],
  };
  const result = filterLiveAvailablePlayers(observation);
  assert.equal(result.ready, true);
  assert.deepEqual(result.eligible_ids, ["ready"]);
  assert.deepEqual(result.observation.available_players.map((row) => row.player_id), ["ready"]);
  assert.deepEqual(result.observation.known_players, known);
  assert.equal(result.observation.available_pool_complete, false);
  assert.deepEqual(result.observation.warnings, [
    "visible_pool_partial",
    "incomplete: missing PTS, FGM, FGA, FTM, FTA, 3PM, REB, AST, STL, BLK, TO",
    "live projection filter excluded 1 visible row(s)",
  ]);
  assert.deepEqual(result.observation.history, observation.history);
  assert.deepEqual(observation.available_players.map((row) => row.player_id), ["ready", "incomplete"]);
  assert.equal(result.observation.available_players[0], result.retained_available_players[0]);
  result.observation.available_players[0].stats.PTS = 99;
  assert.equal(observation.available_players[0].stats.PTS, 1);
});

test("empty or unidentified rows never become drafted and cannot pass the filter", () => {
  const result = filterLiveAvailablePlayers({
    known_players: [player("known")],
    available_players: [player("", { player_id: "" }), null, player("missing-gp", { projected_gp: null })],
    history: [],
    roster: [],
  });
  assert.equal(result.ready, false);
  assert.deepEqual(result.eligible_ids, []);
  assert.deepEqual(result.observation.available_players, []);
  assert.deepEqual(result.observation.history, []);
  assert.deepEqual(result.observation.roster, []);
  assert.equal(result.rows.length, 3);
  assert.ok(result.warnings.some((warning) => warning.includes("missing player_id")));
});

test("filtering an originally complete visible pool downgrades completeness with metadata", () => {
  const observation = {
    available_players: [player("ready"), player("incomplete", { stats: {}, projected_gp: 72 })],
    available_pool_complete: true,
    known_players: [player("catalog-only", { source: "known_catalog" })],
    warnings: ["catalog-captured"],
  };
  const result = filterLiveAvailablePlayers(observation);
  assert.equal(result.ready, true);
  assert.deepEqual(result.observation.available_players.map((row) => row.player_id), ["ready"]);
  assert.equal(result.observation.available_pool_complete, false);
  assert.equal(result.filter_metadata.original_available_pool_complete, true);
  assert.equal(result.filter_metadata.filtered_available_pool_complete, false);
  assert.equal(result.filter_metadata.original_available_count, 2);
  assert.equal(result.filter_metadata.retained_available_count, 1);
  assert.ok(result.observation.warnings.includes("available_pool_complete reset false after live projection filtering"));
  assert.equal(observation.available_pool_complete, true);
  assert.deepEqual(result.observation.known_players, observation.known_players);
});
