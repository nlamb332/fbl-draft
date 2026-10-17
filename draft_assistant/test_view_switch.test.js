"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { createViewSwitcher, resolveViewTab, inspectGridKinds } = require("./view_switch.js");
const { createFixtureTab, parseHtml } = require("./dom_fixture.js");
const { EspnRoomSimulator } = require("./room_simulator.js");

const ORDER = ["Example Team", ...Array.from({ length: 13 }, (_, index) => `Team ${index + 1}`)];
const CATALOG = Array.from({ length: 40 }, (_, index) => ({
  player_id: String(1000 + index), name: `Player ${index}`, eligibility: ["G"], stats: { PTS: 50 - index }, projected_gp: 70, draft_rank: index + 1,
}));
const room = (options = {}) => new EspnRoomSimulator({ catalog: CATALOG, order: ORDER, ownTeam: "Example Team", paused: false, ...options });

test("switches Players <-> Pick History from every starting view, case-insensitively", async () => {
  for (const view of ["players", "history", "league_manager"]) {
    const simulator = room({ view });
    const switcher = createViewSwitcher({ tab: simulator.tab() });
    assert.equal((await switcher.showPickHistory()).view, "history");
    assert.equal(simulator.view, "history");
    assert.equal((await switcher.showPlayers()).view, "players");
    assert.equal(simulator.view, "players");
  }
});

test("switches to the unique League Manager tab for native Resume without requiring a grid", async () => {
  const simulator = room({ view: "players", paused: true });
  const switcher = createViewSwitcher({ tab: simulator.tab() });
  const shown = await switcher.showLeagueManager();
  assert.equal(shown.view, "league_manager");
  assert.equal(shown.ready, true);
  assert.equal(simulator.view, "league_manager");
});

test("a same-named control outside the view-tab region is ignored (Roster tablist)", async () => {
  const simulator = room();
  const probe = await simulator.tab().playwright.locator("body").evaluate(resolveViewTab, { pattern: "^players$", click: false });
  assert.equal(probe.status, "unique");
});

test("ambiguous or missing tabs refuse without clicking", async () => {
  for (const [tabsMode, code] of [["duplicate_players", "tab_ambiguous"], ["absent", "tab_no_region"]]) {
    const simulator = room({ tabsMode, view: "history" });
    const switcher = createViewSwitcher({ tab: simulator.tab() });
    await assert.rejects(() => switcher.showPlayers(), (error) => error.code === code);
    assert.equal(simulator.view, "history", "no view change on refusal");
  }
});

test("League Manager selector refuses when its anchored tab region is absent", async () => {
  const simulator = room({ tabsMode: "absent", view: "players" });
  const switcher = createViewSwitcher({ tab: simulator.tab() });
  await assert.rejects(() => switcher.showLeagueManager(), (error) => error.code === "tab_no_region");
  assert.equal(simulator.view, "players", "no view change on refusal");
});

test("throws when the clicked panel does not mount", async () => {
  const simulator = room({ view: "history" });
  const original = simulator.setView.bind(simulator);
  simulator.setView = () => original("league_manager"); // tab click lands on a grid-less panel
  const switcher = createViewSwitcher({ tab: simulator.tab(), polls: 2 });
  await assert.rejects(() => switcher.showPlayers(), (error) => error.code === "panel_not_mounted");
});

test("grid inspection ignores retained hidden panels", async () => {
  const body = parseHtml(`<div role="grid" class="players-grid" style="display:none"><div role="columnheader">RK</div><div role="columnheader">Player</div><div role="columnheader">FPTS</div><div role="columnheader">GP</div></div>
    <div role="grid" class="history-grid"><div role="columnheader">Pick</div><div role="columnheader">Player</div><div role="columnheader">Team</div></div>`);
  const tab = createFixtureTab({ getBody: () => body, url: "https://fantasy.espn.com/basketball/draft?leagueId=1000000012&seasonId=2027&teamId=15" });
  const kinds = await tab.playwright.locator("body").evaluate(inspectGridKinds);
  assert.deepEqual(kinds, ["history"]);
});

test("Players remains mounted when horizontal scrolling virtualizes FPTS away", async () => {
  const body = parseHtml(`<div role="grid"><div role="columnheader">RK</div><div role="columnheader">PLAYER</div><div role="columnheader">FTA</div><div role="columnheader">3PM</div><div role="columnheader">REB</div></div>`);
  const tab = createFixtureTab({ getBody: () => body, url: "https://fantasy.espn.com/basketball/draft?leagueId=1000000012&seasonId=2027&teamId=15" });
  assert.deepEqual(await tab.playwright.locator("body").evaluate(inspectGridKinds), ["players"]);
});
