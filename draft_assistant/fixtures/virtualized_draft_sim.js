"use strict";

// Offline simulation of the ESPN draft room used by the post-click
// confirmation tests (Node) and the Python <-> JS replay (issue #42).
//
// Two views are modelled, exactly as the diagnostic shows them:
//  - Pick History: history grids of 14 rows each that may hydrate late.
//  - Players: a Feed virtualized to the 31 most recent picks, a Players grid
//    and a roster table whose cells are abbreviated ("J. Allen") and may lack
//    the title attribute.
// Nothing here touches a browser, the network or ESPN.

const { createBrowserObserver } = require("../browser_observer.js");

const FEED_LIMIT = 31;
const GRID_ROWS = 14;

function abbreviate(name) {
  const parts = String(name).split(/\s+/).filter(Boolean);
  return parts.length < 2 ? String(name) : `${parts[0][0]}. ${parts.slice(1).join(" ")}`;
}

function snakeTeam(teams, pick) {
  const round = Math.floor((pick - 1) / teams.length);
  const offset = (pick - 1) % teams.length;
  return teams[round % 2 === 0 ? offset : teams.length - 1 - offset];
}

function createDraftSim({
  room,
  rules,
  teams,
  ownTeam,
  catalog,
  history = [],
  rosterTitles = () => "",
  // hydrate(readIndex, gridCount) -> number of history grids rendered on the
  // readIndex-th Pick History read since the view was opened (1-based).
  hydrate = (_readIndex, gridCount) => gridCount,
  afterOwnPick = () => 0,
  now = () => Date.now(),
  sessionId = "session-1",
} = {}) {
  const events = history.map((event) => ({ ...event }));
  const byId = new Map(catalog.map((player) => [player.player_id, player]));
  const stats = { clicks: 0, bodyEvaluates: 0, historyReads: 0, historyReadsAtClick: 0, views: [] };
  let view = "players";
  let readsSinceOpen = 0;

  const currentPick = () => events.length + 1;

  function applyPick(playerId, team) {
    const player = byId.get(playerId);
    events.push({ pick_number: currentPick(), team, player_id: player.player_id, player_name: player.name });
  }

  function availablePlayers() {
    const taken = new Set(events.map((event) => event.player_id));
    return catalog.filter((player) => !taken.has(player.player_id));
  }

  function historyGrids() {
    const grids = [];
    for (let index = 0; index < events.length; index += GRID_ROWS) {
      grids.push({
        kind: "history",
        headers: ["Pick", "Player", "Team"],
        rows: events.slice(index, index + GRID_ROWS).map((event) => ({
          cells: [String(event.pick_number), event.player_name, event.team],
          player_id: event.player_id,
          player_name: event.player_name,
        })),
      });
    }
    return grids;
  }

  function playersGrid() {
    return {
      kind: "players",
      headers: ["RK", "Player", "FPTS", "GP"],
      rows: availablePlayers().map((player, index) => ({
        cells: [String(index + 1), player.name, "100", "80"],
        player_id: player.player_id,
        player_name: player.name,
        visible: true,
        enabled: true,
      })),
    };
  }

  function rosterTables() {
    const owned = events.filter((event) => event.team === ownTeam);
    const rows = owned.map((event) => ({
      cells: ["UTIL", abbreviate(event.player_name)],
      full_title: rosterTitles(event.player_id, event.player_name),
      player_id: event.player_id,
    }));
    for (let index = owned.length; index < 10; index += 1) rows.push({ cells: ["UTIL", "Empty"], full_title: "", player_id: null });
    return [{ headers: ["POS", "PLAYER"], rows }];
  }

  function controls() {
    return {
      pick_order: teams,
      current_pick: currentPick(),
      on_clock_team: snakeTeam(teams, currentPick()),
    };
  }

  const url = `https://fantasy.espn.com/basketball/draft?leagueId=${room.league_id}&seasonId=${room.season_id}&teamId=${room.team_id}`;
  const tab = {
    async url() { return url; },
    playwright: {
      getByRole(role) {
        if (role !== "grid") throw new Error(`unexpected role ${role}`);
        return {
          evaluateAll: async () => {
            if (view === "history") {
              stats.historyReads += 1;
              readsSinceOpen += 1;
              const grids = historyGrids();
              return grids.slice(0, hydrate(readsSinceOpen, grids.length));
            }
            return [playersGrid()];
          },
        };
      },
      locator(selector) {
        const c = controls();
        const values = {
          ".draft-players-toggle": false,
          table: rosterTables(),
          select: c.pick_order,
          ".on-the-clock": { text: `On the Clock: Pick ${c.current_pick}`, team: c.on_clock_team },
          ".clock__digits": "01:23",
          ".autoPick-toggle input": false,
          'h1, h2, h3, [role="heading"]': [],
        };
        return {
          evaluateAll: async () => values[selector],
          // Fast path: the Players view with a Feed virtualized to 31 rows.
          evaluate: async () => {
            stats.bodyEvaluates += 1;
            return {
              grids: [playersGrid()],
              history: events.slice(-FEED_LIMIT).map((event) => ({ ...event })),
              show_drafted: false,
              roster_tables: rosterTables(),
              controls: { ...c, seconds_remaining: 83, autopick: false },
            };
          },
        };
      },
    },
  };

  const observer = createBrowserObserver({
    tab,
    sessionId,
    room,
    freshlyVerifiedRules: rules,
    projectionCatalog: catalog,
    counter: { n: 0 },
    now,
  });

  return {
    tab,
    observer,
    stats,
    events,
    controls,
    async showPickHistory() { view = "history"; readsSinceOpen = 0; stats.views.push("history"); },
    async showPlayers() { view = "players"; stats.views.push("players"); },
    // The simulated visible click: our pick lands, then `afterOwnPick` bot picks
    // follow immediately (the race the diagnostic shows).
    async click(picked) {
      stats.clicks += 1;
      stats.historyReadsAtClick = stats.historyReads;
      applyPick(picked.player_id, ownTeam);
      const extra = afterOwnPick(currentPick());
      for (let index = 0; index < extra; index += 1) {
        const next = availablePlayers().find((player) => player.player_id !== picked.player_id);
        applyPick(next.player_id, snakeTeam(teams, currentPick()));
      }
    },
    // Bots draft until it is our turn again (or the draft has `until` picks).
    advanceToOwnTurn(until = 140) {
      while (currentPick() <= until && snakeTeam(teams, currentPick()) !== ownTeam) {
        applyPick(availablePlayers()[0].player_id, snakeTeam(teams, currentPick()));
      }
    },
    async beforeClickFrame() {
      await this.showPickHistory();
      // The pre-click read is allowed to wait for late hydration; only the
      // post-click path is under test for budget behaviour.
      let history = await observer.captureHistory({ expectedCurrentPick: currentPick() });
      for (let attempt = 0; attempt < 5 && !history.history_complete; attempt += 1) {
        history = await observer.captureHistory({ expectedCurrentPick: currentPick() });
      }
      await this.showPlayers();
      return observer.captureFrame({ historySnapshot: history });
    },
  };
}

module.exports = { FEED_LIMIT, GRID_ROWS, abbreviate, createDraftSim, snakeTeam };
