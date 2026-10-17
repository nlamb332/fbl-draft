"use strict";

// Offline end-to-end capture check.  Load the exact served page-runner bundle
// into a page-like VM and call FBL.attach().captureInputs() without supplying
// rules, a catalog, or either grid scroller.  The simulator supplies only the
// visible room DOM and the local coordinator transport.
const fs = require("node:fs");
const vm = require("node:vm");
const { EspnRoomSimulator } = require("../room_simulator.js");
const { isHidden } = require("../dom_fixture.js");

class FixtureWheelEvent {
  constructor(type, init = {}) {
    this.type = type;
    Object.assign(this, init);
    this.defaultPrevented = false;
  }

  preventDefault() { this.defaultPrevented = true; }
}

async function main(config) {
  const ownTeam = "Example Team";
  const order = Array.from({ length: 14 }, (_, index) => (index === 12 ? ownTeam : "Opponent " + (index + 1)));
  const simulator = new EspnRoomSimulator({
    catalog: config.catalog,
    order,
    ownTeam,
    room: config.room,
    timerSeconds: 30,
    view: "rules",
    paused: true,
    playersWindow: config.playersWindow || 14,
    initialPickCount: config.initialPickCount || 0,
    historyWindow: config.historyWindow || 14,
  });
  const pageDocument = {
    get body() { return simulator.liveBody(); },
    querySelector: (selector) => simulator.liveBody().querySelector(selector),
    querySelectorAll: (selector) => simulator.liveBody().querySelectorAll(selector),
    visibilityState: "visible",
  };
  const pageWindow = {
    getComputedStyle: (element) => ({ display: isHidden(element) ? "none" : "block", visibility: isHidden(element) ? "hidden" : "visible" }),
    WheelEvent: FixtureWheelEvent,
  };
  const pageFetch = (url, init = {}) => fetch(url, { ...init, headers: { ...(init.headers || {}), Origin: "https://fantasy.espn.com" } });
  const context = vm.createContext({
    document: pageDocument,
    window: null,
    location: { get href() { return simulator.url(); } },
    fetch: pageFetch,
    WheelEvent: FixtureWheelEvent,
    getComputedStyle: pageWindow.getComputedStyle,
    setTimeout,
    clearTimeout,
    AbortController,
    URL,
    URLSearchParams,
    console,
  });
  context.window = context;
  context.window.WheelEvent = FixtureWheelEvent;
  vm.runInContext(fs.readFileSync(config.bundlePath, "utf8"), context, { filename: "page-runner.js" });
  const FBL = context.FBL;
  if (!FBL || FBL.bundle_hash !== config.bundleHash) throw new Error("bundle did not install window.FBL with the expected hash");

  FBL.configure({ baseUrl: config.base, csrfToken: config.csrfToken });
  FBL.attach({ sessionId: config.sessionId, ownerToken: config.ownerToken, ownTeam, expectedSeat: 13 });
  const runner = FBL.runner();
  const inputs = await runner.captureInputs({ maxPlayers: config.catalog.length });
  // A paused preflight captures Pick History through the same default
  // WheelEvent-based scroller that a running frame uses.  No history scroller
  // is passed to FBL.attach.
  const preflight = await runner.preflight();
  const playersGrid = simulator.liveBody().querySelector('[role="grid"]');
  const finalRead = FBL.modules["page_capture.js"].readPlayersGrid(simulator.liveBody());
  const draftedIds = simulator.picks.map((pick) => pick.player_id);
  const capturedIds = finalRead.rows.map((row) => row.player_id);
  return {
    inputs,
    preflight,
    summary: runner.summary(),
    view: simulator.view,
    paused: simulator.paused,
    show_drafted: finalRead.show_drafted,
    players_top: simulator.playersTop,
    players_column_page: simulator.playersColumnPage,
    scroll_events: simulator.scrollEvents,
    drafted_ids: draftedIds,
    final_visible_ids: capturedIds,
    left_scrolls: simulator.scrollEvents.filter((event) => event.grid === "players" && event.deltaX < 0).length,
    right_scrolls: simulator.scrollEvents.filter((event) => event.grid === "players" && event.deltaX > 0).length,
    vertical_scrolls: simulator.scrollEvents.filter((event) => event.grid === "players" && event.deltaY !== 0).length,
    history_scrolls: simulator.scrollEvents.filter((event) => event.grid === "history").length,
    has_players_grid: Boolean(playersGrid),
    catalog_ids: (runner.summary().inputs || {}).catalog_players,
    rendered_drafted_ids: draftedIds.filter((playerId) => simulator.playersRowsRendered.has(playerId)),
  };
}

main(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((report) => {
  fs.writeFileSync(process.argv[3], JSON.stringify(report));
}).catch((error) => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
