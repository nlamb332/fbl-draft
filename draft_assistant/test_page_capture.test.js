"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { parseHtml } = require("./dom_fixture.js");
const capture = require("./page_capture.js");
const { createPageTab } = require("./page_runner.js");
const { isHidden } = require("./dom_fixture.js");

// Rules tab as read from live practice room 1000000015 on 2026-10-13.
const LIVE_RULES = {
  rounds: 10,
  rounds_text: "RND 1 OF 10",
  tables: [
    { caption: "Basic Settings", rows: [["League Manager", ""], ["League Name", "Practice Draft for Example League"], ["Scoring Type", "Head to Head Points"], ["Teams in League", "14"]] },
    { caption: "Roster", rows: [["Center (C)", "1"], ["Guard (G)", "2"], ["Forward (F)", "2"], ["Util (UTIL)", "1"], ["Bench (BE)", "4"], ["Injured Reserve (IR)", "3"]] },
    { caption: "Scoring - All", rows: [
      ["Field Goals Made (FGM)", "2"], ["Field Goals Attempted (FGA)", "-1"], ["Free Throws Made (FTM)", "1"], ["Free Throws Attempted (FTA)", "-1"],
      ["Three Pointers Made (TPM)", "1"], ["Rebounds (REB)", "2"], ["Assists (AST)", "2"], ["Steals (STL)", "4"], ["Blocks (BLK)", "4"],
      ["Turnovers (TO)", "-2"], ["Ejections (E)", "-5"], ["Personal Fouls (PF)", "-1"], ["Technical Fouls (TF)", "-2"], ["Double Doubles (DD)", "3"],
      ["Triple Doubles (TD)", "5"], ["Quadruple Doubles (QD)", "100"], ["Points (PTS)", "1"]] },
    { caption: "League Rules", rows: [["Player Universe", "NBA"]] },
  ],
};

test("live Rules read-back normalizes to the coordinator's verified shape", () => {
  const { rules, problems } = capture.normalizeEspnRules(LIVE_RULES);
  assert.deepEqual(problems, []);
  assert.deepEqual(rules, {
    teams: 14, rounds: 10, starting_slots: ["C", "G", "G", "F", "F", "UTIL"], bench_slots: 4, ir_slots: 3,
    scoring: { FGM: 2, FGA: -1, FTM: 1, FTA: -1, "3PM": 1, REB: 2, AST: 2, STL: 4, BLK: 4, TO: -2, EJ: -5, PF: -1, TF: -2, DD: 3, TD: 5, QD: 100, PTS: 1 },
  });
});

test("missing rounds or an unreadable rules table is reported, never defaulted", () => {
  assert.match(capture.normalizeEspnRules({ ...LIVE_RULES, rounds: null }).problems.join(), /rounds/);
  const noScoring = { ...LIVE_RULES, tables: LIVE_RULES.tables.filter((table) => !/^scoring/i.test(table.caption)) };
  assert.match(capture.normalizeEspnRules(noScoring).problems.join(), /Scoring table/);
});

test("readRulesTables reads captions, rows and the header round count", () => {
  // Live header: the round text runs into its neighbours in page text ("Draft HelpRND 1 of 10--:--").
  const body = parseHtml(`<span>Draft Help</span><div class="clock__container"><div>RND 1 of 10</div><div><span>-</span><span>-</span></div></div><div role="tabpanel"><table><thead><tr><th>Roster</th></tr></thead><tbody><tr><td>Center (C)</td><td>1</td></tr></tbody></table></div>`);
  const read = capture.readRulesTables(body);
  assert.equal(read.rounds, 10);
  assert.equal(read.tables[0].caption, "Roster");
  assert.deepEqual(read.tables[0].rows, [["Center (C)", "1"]]);
});

function playersHtml(rows, { headers = ["RK", "Player", "", "FPTS", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"], averages = true } = {}) {
  const head = headers.map((h) => `<div role="columnheader">${h}</div>`).join("");
  const body = rows.map((row) => `<div role="row"><div role="gridcell">${row.rk}</div><div role="gridcell"><span class="playerinfo__playername">${row.name}</span><span class="playerinfo__playerteam">${row.team}</span>${row.pos.map((p) => `<span class="positionPill">${p}</span>`).join("")}</div><div role="gridcell"><button data-player-id="${row.id}">Queue</button></div>${row.values.map((v) => `<div role="gridcell">${v}</div>`).join("")}</div>`).join("");
  return `<select><option selected>2027 Projected</option></select><label><input type="radio" name="averaged-stats" value="averaged"${averages ? " checked" : ""}>Averages</label><div role="grid"><div role="row">${head}</div>${body}</div>`;
}

test("readPlayersGrid reads identity, team, position pills and cells from live-shaped rows", () => {
  const body = parseHtml(playersHtml([{ rk: 12, id: "3155942", name: "Domantas Sabonis", team: "SAC", pos: ["C", "PF"], values: ["Queue", "4160", "69.0", "7.5", "12.8", "3.3", "4.6", "0.6", "13.6", "6.8", "0.8", "0.5", "3.1"] }]));
  const read = capture.readPlayersGrid(body);
  assert.equal(read.found, true);
  assert.equal(read.averages, true);
  assert.deepEqual(read.rows[0].positions, ["C", "PF"]);
  assert.equal(read.rows[0].nba_team, "SAC");
  assert.equal(read.rows[0].player_id, "3155942");
});

test("readPlayersGrid ignores retained hidden grids and reads the visible Players grid", () => {
  const hidden = playersHtml([{ rk: 1, id: "hidden", name: "Hidden", team: "BOS", pos: ["G"], values: ["Queue", "1", "70"] }]).replace('<div role="grid">', '<div role="grid" style="display:none">');
  const visible = playersHtml([{ rk: 2, id: "visible", name: "Visible", team: "NYK", pos: ["F"], values: ["Queue", "2", "71"] }]);
  const read = capture.readPlayersGrid(parseHtml(hidden + visible));
  assert.equal(read.rows[0].player_id, "visible");
});

test("drafted rows (Show Drafted on, no player button) take the stable id from the headshot", () => {
  // Live pick 23: drafted rows render a "Drafted" label instead of the Queue button.
  const body = parseHtml(`<div class="draft-players-toggle"><label><input type="checkbox" checked></label></div><div role="grid"><div role="row"><div role="columnheader">RK</div><div role="columnheader">Player</div></div><div role="row"><div role="gridcell">1</div><div role="gridcell"><img src="https://a.espncdn.com/i/headshots/nba/players/full/3112335.png"><span class="playerinfo__playername">Nikola Jokic</span><span class="positionPill">C</span><span>Drafted</span></div></div></div>`);
  const read = capture.readPlayersGrid(body);
  assert.equal(read.show_drafted, true);
  assert.equal(read.rows[0].player_id, "3112335");
  assert.equal(read.rows[0].drafted, true);
});

test("setShowDrafted clicks only when the toggle is in the other state", () => {
  const body = parseHtml(`<div class="draft-players-toggle"><label><input type="checkbox"></label></div>`);
  let clicks = 0;
  body.querySelector("label").onclick = () => { clicks += 1; };
  assert.deepEqual(capture.setShowDrafted(body, false), { found: true, clicked: false });
  assert.deepEqual(capture.setShowDrafted(body, true), { found: true, clicked: true });
  assert.equal(clicks, 1);
  assert.deepEqual(capture.setShowDrafted(parseHtml("<div></div>"), true), { found: false, clicked: false });
});

test("catalog capture merges the left and right column pages, maps positions, and never derives PTS", async () => {
  // A fake virtualized grid: 30 players, 10 rendered at a time; scrolling right swaps in PTS/ADP.
  const players = Array.from({ length: 30 }, (_, i) => ({ id: String(5000 + i), name: `Player ${i}`, pos: i % 3 === 0 ? ["C"] : i % 3 === 1 ? ["PG", "SG"] : ["SF"], rk: i + 1 }));
  let top = 0;
  let right = false;
  const scrolls = [];
  const read = async () => {
    const window = players.slice(top, top + 10);
    if (!right) {
      return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"],
        rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", injury_status: null, positions: p.pos, cells: [String(p.rk), p.name, "Queue", "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })) };
    }
    return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "TO", "EJ", "PF", "PTS", "ADP"],
      rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", injury_status: null, positions: p.pos, cells: [String(p.rk), p.name, "3", "0", "2", p.rk === 7 ? "--" : "22.1", String(p.rk + 0.5)] })) };
  };
  const scroll = async (dx, dy) => {
    scrolls.push([dx, dy]);
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 8, players.length - 10);
    if (dy < 0) top = Math.max(top - 32, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, stepPixels: 480, requireScrollEvidence: false });
  assert.equal(result.players.length, 30);
  const center = result.players.find((p) => p.player_id === "5000");
  assert.deepEqual(center.eligibility, ["C"]);
  assert.deepEqual(result.players.find((p) => p.player_id === "5001").eligibility, ["G"]);
  assert.equal(center.stats.PTS, 22.1);
  assert.equal(center.adp, 1.5);
  assert.equal(center.projected_gp, 70);
  const noPts = result.players.find((p) => p.draft_rank === 7);
  assert.equal("PTS" in noPts.stats, false, "a blank PTS cell stays missing");
  assert.ok(noPts.missing_stats.includes("PTS"));
  assert.ok(center.missing_stats.includes("EJ") && center.missing_stats.includes("PF"), "bonus/foul columns stay untrusted");
  assert.equal(result.complete_core_stats, 29);
  assert.equal(result.catalog_complete, true, "reaching the end is independent of one incomplete projection");
  assert.equal(result.incomplete_projection_count, 1);
  assert.equal(top, 0, "the grid is returned to the top");
  assert.equal(right, false, "the grid is returned to the first column page");
});

test("catalog capture refuses the wrong stat view", async () => {
  const read = async () => ({ found: true, selected: ["2026 Stats"], averages: true, headers: [], rows: [] });
  await assert.rejects(() => capture.captureProjectionCatalog({ read, scroll: async () => {}, sleep: async () => {} }), (error) => error.code === "wrong_stat_view");
});

test("catalog capture retries delayed horizontal cells and preserves meaningful stats", async () => {
  const players = Array.from({ length: 12 }, (_, i) => ({ id: String(8000 + i), name: `Delayed ${i}`, pos: ["G"], rk: i + 1 }));
  let top = 0;
  let right = false;
  const rightReads = new Map();
  const read = async () => {
    const window = players.slice(top, top + 4);
    if (!right) {
      return {
        found: true,
        selected: ["2027 Projected"],
        averages: true,
        headers: ["RK", "Player", "", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"],
        rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", injury_status: null, positions: p.pos, cells: [String(p.rk), p.name, "Queue", "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })),
      };
    }
    const count = rightReads.get(top) || 0;
    rightReads.set(top, count + 1);
    return {
      found: true,
      selected: ["2027 Projected"],
      averages: true,
      headers: ["RK", "Player", "PTS", "ADP"],
      rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", injury_status: null, positions: p.pos, cells: [String(p.rk), p.name, count === 0 ? "--" : String(100 - p.rk), count === 0 ? "--" : String(p.rk + 0.25)] })),
    };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 3, players.length - 4);
    if (dy < 0) top = Math.max(top - 32, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, stepPixels: 480, horizontalRetries: 2, repairPasses: 0, requireScrollEvidence: false });
  assert.equal(result.catalog_count, 12);
  assert.equal(result.catalog_complete, true);
  assert.equal(result.complete_eligible_count, 12);
  assert.equal(result.incomplete_projection_count, 0);
  assert.equal(result.players.find((player) => player.player_id === "8000").stats.PTS, 99);
  assert.equal(result.players.find((player) => player.player_id === "8000").adp, 1.25);
});

test("catalog repair pass revisits known windows instead of stopping on known IDs", async () => {
  const players = Array.from({ length: 32 }, (_, i) => ({ id: String(9000 + i), name: `Repair ${i}`, pos: ["G"], rk: i + 1 }));
  let top = 0;
  let right = false;
  const rightReads = new Map();
  const read = async () => {
    const window = players.slice(top, top + 8);
    if (!right) return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"], rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: p.pos, cells: [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })) };
    const count = rightReads.get(top) || 0;
    rightReads.set(top, count + 1);
    const hydrated = count >= 2;
    return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "PTS"], rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: p.pos, cells: [String(p.rk), p.name, hydrated ? String(200 - p.rk) : "--"] })) };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 7, players.length - 8);
    if (dy < 0) top = Math.max(top - 64, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, stepPixels: 480, horizontalRetries: 1, repairPasses: 1, requireScrollEvidence: false });
  assert.equal(result.catalog_count, 32);
  assert.equal(result.catalog_complete, true);
  assert.equal(result.incomplete_projection_count, 0);
  assert.equal(result.players.find((player) => player.player_id === "9029").stats.PTS, 170);
});

test("catalog cap reports a partial catalog when rows remain below the configured limit", async () => {
  const players = Array.from({ length: 12 }, (_, i) => ({ id: String(9500 + i), name: `Cap ${i}`, pos: ["G"], rk: i + 1 }));
  let top = 0;
  let right = false;
  const read = async () => {
    const window = players.slice(top, top + 4);
    const headers = right ? ["RK", "Player", "PTS"] : ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"];
    return { found: true, selected: ["2027 Projected"], averages: true, headers, rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: p.pos, cells: right ? [String(p.rk), p.name, "100"] : [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })) };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 3, players.length - 4);
    if (dy < 0) top = Math.max(top - 32, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, maxPlayers: 5, repairPasses: 0, requireScrollEvidence: false });
  assert.equal(result.catalog_complete, false);
  assert.equal(result.termination_reason, "max_players");
  assert.equal(result.termination_limit.max_players, 5);
  assert.equal(result.termination_end_evidence.stable, false);
  assert.equal(result.catalog_count, 5, "the configured cap bounds the captured catalog while retaining a partial marker");
});

test("catalog capture default bound traverses a universe larger than the old 460-player cap", async () => {
  const players = Array.from({ length: 480 }, (_, i) => ({ id: String(10000 + i), name: `Large ${i}`, pos: ["G"], rk: i + 1 }));
  let top = 0;
  let right = false;
  const read = async () => {
    const window = players.slice(top, top + 10);
    const headers = right ? ["RK", "Player", "PTS"] : ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"];
    return {
      found: true,
      selected: ["2027 Projected"],
      averages: true,
      headers,
      rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: p.pos, cells: right ? [String(p.rk), p.name, String(1000 - p.rk)] : [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })),
    };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 9, players.length - 10);
    if (dy < 0) top = Math.max(top - 64, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, horizontalRetries: 0, repairPasses: 0, requireScrollEvidence: false });
  assert.equal(result.catalog_count, 480);
  assert.equal(result.catalog_complete, true);
  assert.equal(result.termination_reason, "end_of_grid");
  assert.equal(result.termination_count, 480);
  assert.equal(result.termination_limit.max_players, 1500);
  assert.equal(result.termination_end_evidence.stable, true);
  assert.equal(result.players.find((player) => player.player_id === "10479").stats.PTS, 520);
});

test("catalog step limit remains explicitly partial", async () => {
  const players = Array.from({ length: 20 }, (_, i) => ({ id: String(11000 + i), name: `Steps ${i}`, pos: ["G"], rk: i + 1 }));
  let top = 0;
  const read = async () => ({ found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"], rows: players.slice(top, top + 5).map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], cells: [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })) });
  const scroll = async (_dx, dy) => { if (dy > 0) top = Math.min(top + 4, players.length - 5); };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, maxSteps: 1, repairPasses: 0, requireScrollEvidence: false });
  assert.equal(result.catalog_complete, false);
  assert.equal(result.termination_reason, "max_steps");
  assert.equal(result.termination_limit.max_steps, 1);
  assert.equal(result.termination_end_evidence.stable, false);
});

test("catalog capture does not call a stable non-bottom virtual window complete", async () => {
  // Mirrors the live failure: ESPN can leave the same buffered row IDs in the
  // DOM while the custom thumb is still near the top.  A stable ID window is
  // only an end proof when the scrollbar geometry also says bottom.
  const players = Array.from({ length: 26 }, (_, i) => ({ id: String(13000 + i), name: `Buffered ${i}`, rk: i + 1 }));
  const read = async () => ({
    found: true,
    selected: ["2027 Projected"],
    averages: true,
    headers: ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"],
    rows: players.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })),
    scroll_evidence: {
      vertical: { reliable: true, at_top: true, at_bottom: false, progress: 0, thumb: { translate: 4, start: 4 } },
      horizontal: null,
    },
  });
  let verticalScrolls = 0;
  const scroll = async (_dx, dy) => { if (dy > 0) verticalScrolls += 1; };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, repairPasses: 0, maxStalledSteps: 2 });
  assert.equal(result.catalog_count, 26);
  assert.equal(result.catalog_complete, false);
  assert.equal(result.termination_reason, "scroll_stalled");
  assert.equal(result.termination_end_evidence.vertical_bottom.verified, false);
  assert.equal(result.termination_end_evidence.scroll_target_reliable, true);
  assert.equal(verticalScrolls, 2);
});

test("catalog keeps scanning IDs through no-data windows and resumes projections for later positive GP rows", async () => {
  const players = Array.from({ length: 20 }, (_, i) => ({ id: String(14000 + i), name: `Tail ${i}`, rk: i + 1 }));
  const positive = (index) => index < 4 || index >= 8 && index < 12;
  let top = 0;
  let right = false;
  let rightScrolls = 0;
  const progress = [];
  const read = async () => {
    const window = players.slice(top, top + 4);
    if (!right) {
      return {
        found: true, selected: ["2027 Projected"], averages: true,
        headers: ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"],
        rows: window.map((p) => positive(Number(p.id) - 14000)
          ? ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })
          : ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--"] })),
      };
    }
    return {
      found: true, selected: ["2027 Projected"], averages: true,
      headers: ["RK", "Player", "PTS", "ADP"],
      rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, String(200 - p.rk), String(p.rk + 0.5)] })),
    };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) { right = true; rightScrolls += 1; }
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 4, players.length - 4);
    if (dy < 0) top = Math.max(top - 32, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, horizontalRetries: 2, repairPasses: 0, requireScrollEvidence: false, onProgress: (event) => progress.push(event) });
  assert.equal(result.catalog_count, 20);
  assert.equal(result.catalog_complete, true);
  assert.equal(result.players.find((player) => player.player_id === "14008").stats.PTS, 191);
  assert.equal(rightScrolls, 2, "only positive-GP windows should trigger horizontal projection reads");
  assert.ok(result.capture_metrics.skipped_no_data_windows >= 3);
  assert.equal(result.capture_metrics.projection_windows, 2);
  assert.ok(progress.some((event) => event.phase === "scan" && event.catalog_count >= 20));
  assert.ok(progress.every((event) => Number.isInteger(event.steps) && event.steps >= 0));
});

test("catalog repair stops after one unchanged projection signature and skips complete windows", async () => {
  const players = Array.from({ length: 8 }, (_, i) => ({ id: String(15000 + i), name: `Repair tail ${i}`, rk: i + 1 }));
  let top = 0;
  let right = false;
  let rightScrolls = 0;
  const read = async () => {
    const window = players.slice(top, top + 4);
    if (!right) return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"], rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })) };
    return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "GP", "PTS"], rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "70", p.id === "15000" ? "--" : "200"] })) };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) { right = true; rightScrolls += 1; }
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 4, players.length - 4);
    if (dy < 0) top = Math.max(top - 32, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, horizontalRetries: 0, repairPasses: 2, requireScrollEvidence: false });
  assert.equal(result.catalog_complete, true);
  assert.equal(result.incomplete_projection_count, 1);
  assert.equal(result.capture_metrics.scan_passes, 2);
  assert.equal(result.capture_metrics.repair_passes, 1);
  assert.equal(result.capture_metrics.repair_no_progress, true);
  assert.ok(result.capture_metrics.repair_windows_skipped_complete >= 1);
  assert.equal(rightScrolls, 3, "repair should revisit only the unresolved window");
});

test("a later repair stall does not erase the initial verified catalog boundary", async () => {
  const players = Array.from({ length: 8 }, (_, i) => ({ id: String(16000 + i), name: `Boundary ${i}`, rk: i + 1 }));
  let top = 0;
  let right = false;
  let repairMode = false;
  const read = async () => {
    const window = players.slice(top, top + 4);
    const scrollEvidence = { vertical: { reliable: true, at_top: top === 0, at_bottom: !repairMode && top === players.length - 4, progress: top / (players.length - 4 || 1), thumb: { translate: top, start: top } }, horizontal: null };
    if (!right) return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"], rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "70", "8", "16", "4", "5", "2", "7", "5", "1", "1", "3"] })), scroll_evidence: scrollEvidence };
    return { found: true, selected: ["2027 Projected"], averages: true, headers: ["RK", "Player", "GP", "PTS"], rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], visible: true, cells: [String(p.rk), p.name, "70", p.id === "16000" ? "--" : "200"] })), scroll_evidence: scrollEvidence };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0 && !repairMode) top = Math.min(top + 4, players.length - 4);
    if (dy < 0 && top > 0) { top = 0; repairMode = true; }
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, horizontalRetries: 0, repairPasses: 1, maxStalledSteps: 2 });
  assert.equal(result.catalog_complete, true);
  assert.equal(result.catalog_boundary.complete, true);
  assert.equal(result.catalog_boundary.termination_reason, "end_of_grid");
  assert.equal(result.repair_boundary.termination_reason, "scroll_stalled");
  assert.equal(result.termination_reason, "scroll_stalled");
  assert.equal(result.incomplete_projection_count, 1);
});

test("unscorable rows with GP zero or missing do not trigger repeated repair passes", async () => {
  const players = Array.from({ length: 8 }, (_, i) => ({ id: String(12000 + i), name: `Unscorable ${i}`, rk: i + 1 }));
  let top = 0;
  let right = false;
  const read = async () => {
    const window = players.slice(top, top + 4);
    const headers = right ? ["RK", "Player", "PTS"] : ["RK", "Player", "GP"];
    return { found: true, selected: ["2027 Projected"], averages: true, headers, rows: window.map((p) => ({ player_id: p.id, name: p.name, nba_team: "BOS", positions: ["G"], cells: right ? [String(p.rk), p.name, "--"] : [String(p.rk), p.name, "--"] })) };
  };
  const scroll = async (dx, dy) => {
    if (dx > 0) right = true;
    if (dx < 0) right = false;
    if (dy > 0) top = Math.min(top + 3, players.length - 4);
    if (dy < 0) top = Math.max(top - 32, 0);
  };
  const result = await capture.captureProjectionCatalog({ read, scroll, sleep: async () => {}, horizontalRetries: 0, repairPasses: 2, requireScrollEvidence: false });
  assert.equal(result.catalog_complete, true);
  assert.equal(result.incomplete_projection_count, 8);
  assert.equal(result.termination_steps, 3);
});

test("page tab click requires exactly one visible enabled match", async () => {
  const body = parseHtml(`<button class="a">One</button><button class="b">Two</button><button class="b">Three</button><button class="c" style="display:none">Hidden</button>`);
  const clicked = [];
  for (const button of body.querySelectorAll("button")) button.onclick = () => clicked.push(button.textContent);
  const pageDocument = { body, querySelector: (s) => body.querySelector(s), querySelectorAll: (s) => body.querySelectorAll(s) };
  const pageWindow = { getComputedStyle: (element) => ({ display: isHidden(element) ? "none" : "block", visibility: "visible" }) };
  const tab = createPageTab({ document: pageDocument, window: pageWindow, location: { href: "https://fantasy.espn.com/basketball/draft?leagueId=1&seasonId=2027&teamId=15" } });
  await tab.playwright.locator("button.a").click();
  await assert.rejects(() => tab.playwright.locator("button.b").click(), (error) => error.code === "click_not_unique");
  await assert.rejects(() => tab.playwright.locator("button.c").click(), (error) => error.code === "click_not_unique");
  assert.deepEqual(clicked, ["One"]);
  assert.equal(await tab.url(), "https://fantasy.espn.com/basketball/draft?leagueId=1&seasonId=2027&teamId=15");
});

test("grid wheel scroller dispatches from a hit-tested cell so rows-container handlers receive it", async () => {
  const body = parseHtml(`<div role="grid"><div role="columnheader">RK</div><div role="columnheader">Player</div><div role="gridcell">Jokic</div></div>`);
  const grid = body.querySelector('[role="grid"]');
  const cell = grid.querySelector('[role="gridcell"]');
  let received = null;
  cell.addEventListener("wheel", (event) => { received = event; });
  const pageDocument = {
    querySelectorAll: (selector) => body.querySelectorAll(selector),
    elementFromPoint: () => cell,
  };
  class TestWheelEvent {
    constructor(type, init) { this.type = type; Object.assign(this, init); }
  }
  const scroll = capture.createGridWheelScroller({ document: pageDocument, WheelEventCtor: TestWheelEvent });
  await scroll(0, 480);
  assert.equal(received.deltaY, 480);
  assert.equal(received.clientX, 400);
  assert.equal(received.clientY, 240);
});

test("Players scrollbar bottom evidence respects ESPN's four-pixel track inset", () => {
  const body = parseHtml(`<div role="grid"><div role="columnheader">RK</div><div role="columnheader">Player</div><div class="ScrollbarLayout_mainVertical"><div class="ScrollbarLayout_faceVertical" style="transform:translate3d(0px,493px,0px)"></div></div></div>`);
  const track = body.querySelector('.ScrollbarLayout_mainVertical');
  const face = body.querySelector('.ScrollbarLayout_faceVertical');
  track.getBoundingClientRect = () => ({left:785,right:800,top:24,bottom:543,width:15,height:519});
  face.getBoundingClientRect = () => ({left:785,right:800,top:517,bottom:539,width:15,height:22});
  const bottom = capture.readPlayersGrid(body).scroll_evidence.vertical;
  assert.equal(bottom.reliable, true);
  assert.equal(bottom.at_bottom, true);
  face.getBoundingClientRect = () => ({left:785,right:800,top:28,bottom:50,width:15,height:22});
  face.attrs.style = 'transform:translate3d(0px,4px,0px)';
  const top = capture.readPlayersGrid(body).scroll_evidence.vertical;
  assert.equal(top.at_top, true);
  assert.equal(top.at_bottom, false);
});
