"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { CORE_STATS, createDraftUniverse } = require("./draft_universe.js");

function player(id, name, { complete = true, position = "G" } = {}) {
  const stats = Object.fromEntries(CORE_STATS.map((key, index) => [key, index + 1]));
  if (!complete) delete stats.AST;
  return { player_id: id, name, eligibility: [position], stats, projected_gp: 70, nba_team: "BOS" };
}

function pick(pickNumber, playerId, playerName = `Player ${playerId}`) {
  return { pick_number: pickNumber, player_id: playerId, player_name: playerName, team: `Team ${pickNumber}` };
}

test("catalog is keyed by stable id and exposes explicit projection completeness", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two", { complete: false }), player("3", "Three", { position: "C" })], catalogComplete: true });
  const state = universe.snapshot();
  assert.equal(state.catalog_count, 3);
  assert.equal(state.catalog_complete, true);
  assert.equal(state.eligible_count, 3);
  assert.equal(state.complete_eligible_count, 2);
  assert.deepEqual(state.incomplete_projection_ids, ["2"]);
  assert.equal(state.catalog_by_id["3"].name, "Three");
  assert.deepEqual(CORE_STATS, ["PTS", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"]);
});

test("overlapping and delayed feed observations deduplicate without drafting players", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two")], catalogComplete: true });
  universe.applyFeed([pick(1, "1"), pick(2, "2")]);
  universe.applyFeed([pick(1, "1"), pick(2, "2")]);
  const state = universe.snapshot();
  assert.equal(state.feed.length, 2);
  assert.equal(state.drafted_count, 0);
  assert.equal(state.available_players.length, 2);
  assert.equal(state.recommendation_ready, false, "feed evidence is not authoritative history");
});

test("only explicitly confirmed contiguous history removes drafted players", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two"), player("3", "Three")], catalogComplete: true });
  universe.applyHistory([pick(1, "1")], { currentPick: 2, historyComplete: true });
  let state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, ["1"]);
  assert.equal(state.history_complete_through, 1);
  assert.equal(state.recommendation_ready, true);
  universe.applyFeed([pick(2, "2")]);
  state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, ["1"], "feed never removes a player");
  universe.applyHistory([pick(2, "2")], { currentPick: 3, historyComplete: true });
  assert.deepEqual(universe.snapshot().drafted_ids, ["1", "2"]);
});

test("a delayed history gap blocks recommendations, then resolves when the missing pick arrives", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two"), player("3", "Three")], catalogComplete: true });
  universe.applyHistory([pick(1, "1")], { currentPick: 2, historyComplete: true });
  universe.applyHistory([pick(1, "1"), pick(3, "3")], { currentPick: 4, historyComplete: true });
  let state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, ["1"]);
  assert.deepEqual(state.history_gaps, [2]);
  assert.equal(state.recommendation_ready, false);
  universe.applyHistory([pick(2, "2"), pick(3, "3")], { currentPick: 4, historyComplete: true });
  state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, ["1", "2", "3"]);
  assert.deepEqual(state.history_gaps, []);
  assert.equal(state.blocked, false);
});

test("conflicting picks freeze the universe and never guess a removal", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two")], catalogComplete: true });
  universe.applyHistory([pick(1, "1")], { currentPick: 2, historyComplete: true });
  universe.applyHistory([pick(1, "2")], { currentPick: 2, historyComplete: true });
  const state = universe.snapshot();
  assert.equal(state.blocked, true);
  assert.ok(state.blocked_reasons.includes("history_conflict"));
  assert.deepEqual(state.drafted_ids, ["1"]);
  assert.equal(state.recommendation_ready, false);
});

test("catalog identity conflicts freeze instead of overwriting a stable id", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One")], catalogComplete: true });
  universe.ingestCatalog([player("1", "Different")]);
  const state = universe.snapshot();
  assert.equal(state.blocked, true);
  assert.ok(state.blocked_reasons.includes("catalog_conflict"));
  assert.equal(state.catalog_by_id["1"].name, "One");
});

test("unknown or id-less history stays blocked and does not make a row unavailable", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two")], catalogComplete: true });
  universe.applyHistory([{ pick_number: 1, player_name: "Mystery", team: "Team 1" }], { currentPick: 2, historyComplete: true });
  let state = universe.snapshot();
  assert.ok(state.blocked_reasons.includes("history_missing_player_id"));
  assert.deepEqual(state.drafted_ids, []);
  const unknown = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two")], catalogComplete: true });
  unknown.applyHistory([pick(1, "999", "Unknown")], { currentPick: 2, historyComplete: true });
  state = unknown.snapshot();
  assert.ok(state.blocked_reasons.includes("history_unknown_player"));
  assert.deepEqual(state.drafted_ids, []);
});

test("currentPick alone never confirms history", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two")], catalogComplete: true });
  universe.applyHistory([pick(1, "1")], { currentPick: 2 });
  const state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, []);
  assert.ok(state.blocked_reasons.includes("history_unconfirmed"));
});

test("history boundary commits atomically and preserves the last good prefix across a gap", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two"), player("3", "Three"), player("4", "Four")], catalogComplete: true });
  universe.applyHistory([pick(1, "1")], { currentPick: 2, historyComplete: true });
  universe.applyHistory([pick(2, "2"), pick(4, "4")], { currentPick: 5, historyComplete: true });
  let state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, ["1"], "pick 2 must not commit ahead of the gap at pick 3");
  assert.deepEqual(state.history_gaps, [3]);
  universe.applyHistory([pick(2, "2"), pick(3, "3"), pick(4, "4")], { currentPick: 5, historyComplete: true });
  state = universe.snapshot();
  assert.deepEqual(state.drafted_ids, ["1", "2", "3", "4"]);
  assert.equal(state.blocked, false);
});

test("feed conflicts with confirmed history freeze without changing drafted IDs", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two")], catalogComplete: true });
  universe.applyHistory([pick(1, "1")], { currentPick: 2, historyComplete: true });
  universe.applyFeed([pick(1, "2", "Two")]);
  const state = universe.snapshot();
  assert.ok(state.blocked_reasons.includes("feed_conflict"));
  assert.deepEqual(state.drafted_ids, ["1"]);
  assert.equal(state.feed.length, 0, "conflicting feed evidence is not admitted");
});

test("an incomplete projection does not block recommendation when another eligible row is complete", () => {
  const universe = createDraftUniverse({ catalog: [player("1", "One"), player("2", "Two", { complete: false })], catalogComplete: true });
  universe.applyHistory([], { currentPick: 1, historyComplete: true });
  const state = universe.snapshot();
  assert.equal(state.incomplete_projection_count, 1);
  assert.equal(state.available_complete_eligible_count, 1);
  assert.equal(state.recommendation_ready, true);
});

