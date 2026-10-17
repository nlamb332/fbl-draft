"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  BrowserObserverError,
  createBrowserObserver,
  extractGridSnapshots,
  extractFastSnapshot,
} = require("./browser_observer.js");

const ROOM = { league_id: "1000000000", season_id: "2027", team_id: "15", practice: true };
const RULES = {
  teams: 14,
  rounds: 10,
  starting_slots: ["C", "G", "G", "F", "F", "UTIL"],
  bench_slots: 4,
  ir_slots: 3,
  scoring: { PTS: 1, REB: 1 },
};

function catalog() {
  return [
    { player_id: "101", name: "Alice Guard", eligibility: ["G"], stats: { FPTS: 100 }, projected_gp: 80 },
    { player_id: "102", name: "Bob Center", eligibility: ["C"], stats: { FPTS: 90 }, projected_gp: 78 },
    { player_id: "103", name: "Cara Forward", eligibility: ["F"], stats: { FPTS: 88 }, projected_gp: 79 },
  ];
}

function historyRows(count = 12) {
  return Array.from({ length: count }, (_, index) => ({
    cells: [String(index + 1), index === 0 ? "Alice Guard" : `Player ${index + 1}`, index === 0 ? "Example Team" : `Team ${index + 1}`],
    player_id: index === 0 ? "101" : String(200 + index),
    player_name: index === 0 ? "Alice Guard" : `Player ${index + 1}`,
  }));
}

function playersGrid() {
  return {
    kind: "players",
    headers: ["RK", "Player", "FPTS", "GP"],
    rows: [
      { cells: ["1", "Alice Guard", "100", "80"], player_id: "101", player_name: "Alice Guard", visible: true, enabled: true },
      { cells: ["2", "Uncatalogued Player", "", ""], player_id: "999", player_name: "Uncatalogued Player", visible: true, enabled: true },
      { cells: ["3", "Disabled Player", "", ""], player_id: "888", player_name: "Disabled Player", visible: true, enabled: false },
      { cells: ["4", "Hidden Player", "", ""], player_id: "777", player_name: "Hidden Player", visible: false, enabled: true },
    ],
  };
}

function fakeTab({
  url = "https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15",
  grids = [{ kind: "history", headers: ["Pick", "Player", "Team"], rows: historyRows() }, playersGrid()],
  tables = [{ headers: ["POS", "PLAYER"], rows: [{ cells: ["G", "Alice Guard"], full_title: "Alice Guard", player_id: "101" }] }],
  showDrafted = false,
  pickOrder = ["Example Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)],
  onClock = { text: "On the Clock: Pick 13", team: "Example Team" },
  clock = "01:23",
  autopick = false,
  nativeHeading = null,
} = {}) {
  const calls = [];
  const tab = {
    calls,
    async url() {
      calls.push("url");
      return url;
    },
    playwright: {
      getByRole(role) {
        calls.push(`role:${role}`);
        if (role !== "grid") throw new Error(`unexpected role ${role}`);
        return { evaluateAll: async () => grids };
      },
      locator(selector) {
        calls.push(`locator:${selector}`);
        const values = {
          ".draft-players-toggle": showDrafted,
          table: tables,
          select: pickOrder,
          ".on-the-clock": onClock,
          ".clock__digits": clock,
          ".autoPick-toggle input": autopick,
          'h1, h2, h3, [role="heading"]': nativeHeading ? [nativeHeading] : [],
        };
        return { evaluateAll: async () => values[selector] };
      },
    },
  };
  return tab;
}

function observerFor(tab, overrides = {}) {
  return createBrowserObserver({
    tab,
    sessionId: "session-1",
    room: ROOM,
    freshlyVerifiedRules: RULES,
    projectionCatalog: catalog(),
    counter: { n: 0 },
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
    ...overrides,
  });
}

function fastTab(rawSnapshot, url = "https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15") {
  const calls = [];
  return {
    calls,
    async url() {
      calls.push("url");
      return url;
    },
    playwright: {
      locator(selector) {
        calls.push(`locator:${selector}`);
        return {
          evaluate: async () => {
            calls.push("evaluate:body");
            return rawSnapshot;
          },
        };
      },
    },
  };
}

test("captureFrame derives and verifies the room URL, maps visible cells, and advances the mutable revision", async () => {
  const tab = fakeTab();
  const observer = observerFor(tab);
  const frame = await observer.captureFrame();
  assert.deepEqual(frame.room, ROOM);
  assert.equal(frame.session_id, "session-1");
  assert.equal(frame.revision, 1);
  assert.equal(frame.current_pick, 13);
  assert.equal(frame.on_clock_team, "Example Team");
  assert.equal(frame.seconds_remaining, 83);
  assert.equal(frame.autopick, false);
  assert.deepEqual(frame.pick_order.slice(0, 2), ["Example Team", "Team 1"]);
  assert.deepEqual(frame.history[0], { pick_number: 1, team: "Example Team", player_id: "101", player_name: "Alice Guard" });
  assert.deepEqual(frame.available_players.map((player) => player.player_id), ["101"]);
  assert.ok(frame.known_players[0].missing_stats.includes("PF"));
  assert.equal(frame.complete, true);
  assert.equal(frame.history_complete, true);
  assert.equal(frame.available_pool_complete, false);
  assert.ok(tab.calls.includes("role:grid"));
});

test("captureFrame can combine a fresh retained history refresh with a Players-only panel", async () => {
  const history = historyRows();
  const tab = fakeTab({ grids: [playersGrid()] });
  const observer = observerFor(tab, {
    retainedHistory: {
      history,
      observed_at: "2026-10-11T12:00:00.000Z",
      history_complete: true,
    },
  });
  const frame = await observer.captureFrame();
  assert.equal(frame.history.length, 12);
  assert.equal(frame.history_complete, true);
  assert.equal(frame.complete, true);
  assert.deepEqual(frame.available_players.map((player) => player.player_id), ["101"]);
  assert.equal(tab.calls.filter((call) => call === "role:grid").length, 1);
});

test("captureFrame enriches an empty projection catalog from visible ESPN numeric fields", async () => {
  const visibleStatsGrid = {
    kind: "players",
    headers: ["RK", "Player", "FPTS", "GP", "MIN", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO", "EJ", "PF"],
    rows: [{
      cells: ["1", "Alice Guard", "100", "80.0", "34.0", "8.5", "16.0", "4.0", "4.5", "2.1", "5.5", "7.2", "1.4", "0.4", "2.1", "0.0", "0.0"],
      player_id: "101",
      player_name: "Alice Guard",
      visible: true,
      enabled: true,
    }],
  };
  const tab = fakeTab({ grids: [{ kind: "history", headers: ["Pick", "Player", "Team"], rows: historyRows() }, visibleStatsGrid] });
  const observer = createBrowserObserver({
    tab,
    sessionId: "session-1",
    room: ROOM,
    freshlyVerifiedRules: RULES,
    projectionCatalog: [{ player_id: "101", name: "Alice Guard", eligibility: ["G"], stats: {}, projected_gp: null, draft_rank: null }],
    counter: { n: 0 },
    now: () => Date.parse("2026-10-11T12:00:00.000Z"),
  });
  const frame = await observer.captureFrame();
  const player = frame.available_players[0];
  assert.equal(player.stats.FPTS, 100);
  assert.equal(player.stats.FGM, 8.5);
  assert.equal(player.stats.AST, 7.2);
  assert.equal(player.projected_gp, 80);
  assert.equal(player.draft_rank, 1);
  assert.ok(player.missing_stats.includes("PF"));
  assert.equal(frame.known_players[0].stats.FGM, 8.5);
});

test("captureFastFrame reads controls, history, roster, and Players from one supplied DOM evaluation", async () => {
  const tab = fastTab({
    grids: [playersGrid()],
    history: historyRows(),
    show_drafted: false,
    roster_tables: [{ headers: ["POS", "PLAYER"], rows: [{ cells: ["G", "Alice Guard"], full_title: "Alice Guard", player_id: "101" }] }],
    controls: {
      pick_order: ["Example Team", "Team 1"],
      current_pick: 13,
      on_clock_team: "Example Team",
      seconds_remaining: 83,
      autopick: false,
    },
  });
  const observer = observerFor(tab);
  const frame = await observer.captureFastFrame();
  assert.equal(frame.current_pick, 13);
  assert.equal(frame.seconds_remaining, 83);
  assert.equal(frame.autopick, false);
  assert.equal(frame.history_complete, true);
  assert.deepEqual(frame.available_players.map((player) => player.player_id), ["101"]);
  assert.equal(tab.calls.filter((call) => call === "evaluate:body").length, 1);
  assert.deepEqual(tab.calls.filter((call) => call.startsWith("locator:")), ["locator:body"]);
});

test("fast terminal evidence normalizes stale clock controls only with an explicit completion heading", async () => {
  const finalHistory = Array.from({ length: 140 }, (_, index) => ({
    pick_number: index + 1,
    player_id: String(300 + index),
    player_name: `Roster ${index + 1}`,
    team: index < 10 ? "Example Team" : `Team ${index + 1}`,
  }));
  const finalCatalog = Array.from({ length: 10 }, (_, index) => ({
    player_id: String(300 + index),
    name: `Roster ${index + 1}`,
    eligibility: [index % 2 ? "G" : "C"],
  }));
  const rosterRows = finalCatalog.map((player) => ({
    cells: [player.eligibility[0], player.name],
    full_title: player.name,
    player_id: player.player_id,
  }));
  const tab = fastTab({
    grids: [],
    history: finalHistory,
    roster_tables: [{ headers: ["POS", "PLAYER"], rows: rosterRows }],
    controls: {
      pick_order: ["Example Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)],
      current_pick: 140,
      on_clock_team: "Team 14",
      seconds_remaining: 1,
      autopick: false,
      native_complete: true,
      native_paused: false,
      native_heading: "Your Draft is Complete",
    },
  });
  const observer = observerFor(tab, { projectionCatalog: finalCatalog });
  const frame = await observer.captureFastFrame();
  assert.equal(frame.phase, "complete");
  assert.equal(frame.complete, true);
  assert.equal(frame.current_pick, null);
  assert.equal(frame.on_clock_team, null);
  assert.equal(frame.seconds_remaining, null);
  assert.equal(frame.history_complete, true);
  assert.equal(frame.roster.length, 10);
});

test("fast DOM feed converts round and pick labels into absolute picks", () => {
  const previousWindow = global.window;
  global.window = { getComputedStyle: () => ({ display: "", visibility: "" }) };
  try {
    const heading = { textContent: "Your Draft is Complete", getClientRects: () => [{}] };
    const resumeButton = {
      textContent: "Resume",
      getClientRects: () => [{}],
      getAttribute: (name) => name === "aria-label" ? "Resume draft" : null,
    };
    const playerNode = { getAttribute: () => "Feed Player", textContent: "Feed Player" };
    const image = { getAttribute: () => "/images/full/777.png" };
    const span = { textContent: " - Team X" };
    const pickInfo = { textContent: "R5, P12 - Team X", querySelector: () => span };
    const item = {
      getClientRects: () => [{}],
      querySelector(selector) {
        if (selector === ".playerinfo__playername") return playerNode;
        if (selector === 'img[src*="/full/"]') return image;
        if (selector === ".pick-info") return pickInfo;
        return null;
      },
    };
    const body = {
      querySelectorAll(selector) {
        if (selector === "li.pick-message__container") return [item];
        if (selector === '[role="grid"]' || selector === "table") return [];
        if (selector === 'h1, h2, h3, [role="heading"]') return [heading];
        if (selector === 'button, [role="button"]') return [resumeButton];
        return [];
      },
      querySelector() { return null; },
    };
    const snapshot = extractFastSnapshot(body);
    assert.deepEqual(snapshot.history, [{ pick_number: 68, player_name: "Feed Player", player_id: "777", team: "Team X" }]);
    assert.equal(snapshot.controls.native_complete, true);
    assert.equal(snapshot.controls.native_heading, "Your Draft is Complete");
    assert.equal(snapshot.controls.native_paused, true);
  } finally {
    global.window = previousWindow;
  }
});

test("captureFrame revalidates retained history against an advanced current pick", async () => {
  const observer = observerFor(fakeTab({ grids: [playersGrid()], onClock: { text: "On the Clock: Pick 14", team: "Example Team" } }), {
    retainedHistory: {
      history: historyRows(),
      observed_at: "2026-10-11T12:00:00.000Z",
      history_complete: true,
    },
  });
  const frame = await observer.captureFrame();
  assert.equal(frame.history_complete, false);
  assert.equal(frame.complete, false);
  assert.ok(frame.warnings.includes("retained_history_incomplete"));
});

test("history completeness permits an empty retained history at pick one", async () => {
  const observer = observerFor(fakeTab({ grids: [playersGrid()], onClock: { text: "On the Clock: Pick 1", team: "Example Team" } }), {
    retainedHistory: {
      history: [],
      observed_at: "2026-10-11T12:00:00.000Z",
      history_complete: true,
    },
  });
  const frame = await observer.captureFrame();
  assert.equal(frame.history_complete, true);
});

test("currentAvailable returns only visible enabled button IDs while ShowDrafted is explicitly off", async () => {
  const observer = observerFor(fakeTab());
  assert.deepEqual(await observer.currentAvailable(), ["101", "999"]);
});

test("grid extraction recognizes a button[data-player-id] that is itself the row", () => {
  const previousWindow = global.window;
  global.window = { getComputedStyle: () => ({ display: "", visibility: "" }) };
  try {
    const buttonRow = {
      disabled: false,
      matches: (selector) => selector === 'button[data-player-id]',
      getAttribute: (name) => name === "data-player-id" ? "101" : null,
      getClientRects: () => [{}],
      querySelector: (selector) => {
        if (selector === '[role="columnheader"]') return null;
        if (selector === ".playerinfo__playername") return { getAttribute: () => "Alice Guard", textContent: "Alice Guard" };
        return null;
      },
      querySelectorAll: (selector) => selector.includes("gridcell") || selector === "td" ? [{ textContent: "1" }, { textContent: "Alice Guard" }] : [],
    };
    const grid = {
      querySelectorAll: (selector) => {
        if (selector.includes("columnheader")) return [{ textContent: "RK" }, { textContent: "Player" }];
        if (selector.includes('[role="row"]') || selector === "tbody tr") return [buttonRow];
        return [];
      },
    };
    const snapshot = extractGridSnapshots([grid])[0];
    assert.equal(snapshot.rows[0].player_id, "101");
    assert.equal(snapshot.rows[0].enabled, true);
    assert.equal(snapshot.rows[0].visible, true);
  } finally {
    global.window = previousWindow;
  }
});

test("ShowDrafted on or unknown never broadens availability", async () => {
  const enabled = observerFor(fakeTab({ showDrafted: true }));
  await assert.rejects(() => enabled.currentAvailable(), (error) => error instanceof BrowserObserverError && error.code === "show_drafted_unknown");
  const unknown = observerFor(fakeTab({ showDrafted: null }));
  await assert.rejects(() => unknown.currentAvailable(), (error) => error instanceof BrowserObserverError && error.code === "show_drafted_unknown");
  const frame = await unknown.captureFrame();
  assert.deepEqual(frame.available_players, []);
  assert.ok(frame.warnings.includes("show_drafted_state_unknown"));
});

test("catalog rows without eligibility keep their exact ID but are not promoted to available players", async () => {
  const observer = observerFor(fakeTab(), {
    projectionCatalog: [
      { player_id: "101", name: "Alice Guard" },
      { player_id: "102", name: "Bob Center", eligibility: ["C"] },
    ],
  });
  const view = await observer.readVisiblePlayers();
  assert.deepEqual(view.current_available_ids, ["101", "999"]);
  assert.deepEqual(view.available_players, []);
  assert.ok(view.warnings.includes("available_projection_missing_101"));
});

test("room mismatch is rejected from the active tab URL instead of using configured identity", async () => {
  const observer = observerFor(fakeTab({ url: "https://fantasy.espn.com/basketball/draft?leagueId=999&seasonId=2027&teamId=15" }));
  await assert.rejects(() => observer.captureHistory(), (error) => error instanceof BrowserObserverError && error.code === "room_mismatch");
});

test("roster identity requires the full player-column title and independent history/catalog agreement", async () => {
  const tab = fakeTab({
    tables: [{ headers: ["POS", "PLAYER"], rows: [
      { cells: ["G", "Short Name"], full_title: "Alice Guard", player_id: "101" },
      { cells: ["C", "Bob"], full_title: "Bob Center", player_id: "999" },
    ] }],
  });
  const observer = observerFor(tab);
  const history = await observer.captureHistory();
  const roster = await observer.readRoster({ history: history.history });
  assert.deepEqual(roster.roster.map((player) => player.player_id), ["101"]);
  assert.ok(roster.warnings.includes("roster_headshot_id_mismatch_102"));
});

test("empty ESPN roster slots do not mask a hydrated player title", async () => {
  const tab = fakeTab({
    tables: [{ headers: ["POS", "PLAYER"], rows: [
      { cells: ["G", "Alice Guard"], full_title: "Alice Guard", player_id: "101" },
      { cells: ["G", "Empty"], full_title: "", player_id: null },
      { cells: ["F", "-"], full_title: "", player_id: null },
      { cells: ["C", "Hidden Center"], full_title: "", player_id: "102" },
    ] }],
  });
  const observer = observerFor(tab);
  const history = await observer.captureHistory();
  const roster = await observer.readRoster({ history: history.history });
  assert.deepEqual(roster.roster.map((player) => player.player_id), ["101"]);
  assert.ok(roster.warnings.includes("roster_full_title_missing"));
});

test("duplicate visible roster rows do not inflate the roster", async () => {
  const tab = fakeTab({
    tables: [{ headers: ["POS", "PLAYER"], rows: [
      { cells: ["G", "Alice Guard"], full_title: "Alice Guard", player_id: "101" },
      { cells: ["G", "Alice Guard"], full_title: "Alice Guard", player_id: "101" },
    ] }],
  });
  const observer = observerFor(tab);
  const history = await observer.captureHistory();
  const roster = await observer.readRoster({ history: history.history });
  assert.deepEqual(roster.roster.map((player) => player.player_id), ["101"]);
  assert.ok(roster.warnings.includes("roster_duplicate_row_101"));
});

test("room practice must remain a boolean", () => {
  assert.throws(
    () => observerFor(fakeTab(), { room: { ...ROOM, practice: "false" } }),
    (error) => error instanceof BrowserObserverError && error.code === "invalid_room",
  );
});

test("missing history IDs remain explicit and prevent a complete history claim", async () => {
  const tab = fakeTab({
    grids: [{ kind: "history", headers: ["Pick", "Player", "Team"], rows: [{ cells: ["1", "Unknown", "Team 1"], player_name: "Unknown", player_id: null }] }],
  });
  const observer = observerFor(tab);
  const history = await observer.captureHistory();
  assert.equal(history.history[0].player_id, null);
  assert.ok(history.warnings.includes("history_stable_id_missing_1"));
});

test("completeFrame requires no Players grid and a complete identified history and roster", async () => {
  const finalHistory = Array.from({ length: 140 }, (_, index) => ({
    cells: [String(index + 1), index < 10 ? `Roster ${index + 1}` : `Player ${index + 1}`, `Team ${index + 1}`],
    player_id: index < 10 ? String(300 + index) : String(500 + index),
    player_name: index < 10 ? `Roster ${index + 1}` : `Player ${index + 1}`,
  }));
  const finalCatalog = Array.from({ length: 10 }, (_, index) => ({ player_id: String(300 + index), name: `Roster ${index + 1}`, eligibility: [index % 2 ? "G" : "C"] }));
  const rosterRows = finalCatalog.map((player) => ({ cells: [player.eligibility[0], player.name], full_title: player.name, player_id: player.player_id }));
  const tab = fakeTab({
    grids: [{ kind: "history", headers: ["Pick", "Player", "Team"], rows: finalHistory }],
    tables: [{ headers: ["POS", "PLAYER"], rows: rosterRows }],
    onClock: null,
    clock: null,
    nativeHeading: "Your Draft is Complete",
  });
  const observer = observerFor(tab, { projectionCatalog: finalCatalog });
  const frame = await observer.completeFrame();
  assert.equal(frame.phase, "complete");
  assert.equal(frame.current_pick, null);
  assert.equal(frame.on_clock_team, null);
  assert.equal(frame.seconds_remaining, null);
  assert.equal(frame.history.length, 140);
  assert.equal(frame.roster.length, 10);
  assert.equal(frame.history_complete, true);
  assert.equal(frame.available_pool_complete, true);
  assert.equal(frame.complete, true);
});

test("captureFrame marks a fresh ten-player post-own-final read-back complete while Players is absent", async () => {
  const finalHistory = Array.from({ length: 12 }, (_, index) => ({
    cells: [String(index + 1), index < 10 ? `Roster ${index + 1}` : `Player ${index + 1}`, `Team ${index + 1}`],
    player_id: index < 10 ? String(300 + index) : String(500 + index),
    player_name: index < 10 ? `Roster ${index + 1}` : `Player ${index + 1}`,
  }));
  const finalCatalog = Array.from({ length: 10 }, (_, index) => ({
    player_id: String(300 + index),
    name: `Roster ${index + 1}`,
    eligibility: [index % 2 ? "G" : "C"],
  }));
  const rosterRows = finalCatalog.map((player) => ({
    cells: [player.eligibility[0], player.name],
    full_title: player.name,
    player_id: player.player_id,
  }));
  const observer = observerFor(fakeTab({
    grids: [{ kind: "history", headers: ["Pick", "Player", "Team"], rows: finalHistory }],
    tables: [{ headers: ["POS", "PLAYER"], rows: rosterRows }],
    onClock: { text: "On the Clock: Pick 13", team: "Team 1" },
    clock: "00:45",
  }), { projectionCatalog: finalCatalog });
  const frame = await observer.captureFrame();
  assert.equal(frame.phase, "draft");
  assert.equal(frame.complete, true);
  assert.equal(frame.history_complete, true);
  assert.equal(frame.roster.length, 10);
  assert.equal(frame.available_players.length, 0);
  assert.ok(frame.warnings.includes("players_grid_missing"));
});

test("completeFrame rejects a contiguous history that reuses a player ID", async () => {
  const finalHistory = Array.from({ length: 140 }, (_, index) => ({
    cells: [String(index + 1), `Roster ${(index % 10) + 1}`, `Team ${index + 1}`],
    player_id: String(300 + (index % 10)),
    player_name: `Roster ${(index % 10) + 1}`,
  }));
  const finalCatalog = Array.from({ length: 10 }, (_, index) => ({
    player_id: String(300 + index),
    name: `Roster ${index + 1}`,
    eligibility: [index % 2 ? "G" : "C"],
  }));
  const rosterRows = finalCatalog.map((player) => ({
    cells: [player.eligibility[0], player.name],
    full_title: player.name,
    player_id: player.player_id,
  }));
  const observer = observerFor(fakeTab({
    grids: [{ kind: "history", headers: ["Pick", "Player", "Team"], rows: finalHistory }],
    tables: [{ headers: ["POS", "PLAYER"], rows: rosterRows }],
    onClock: null,
    clock: null,
  }), { projectionCatalog: finalCatalog });
  await assert.rejects(
    () => observer.completeFrame(),
    (error) => error instanceof BrowserObserverError && error.code === "incomplete_final_frame",
  );
});
