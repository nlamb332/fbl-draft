"use strict";

// Recommendation-only page-bundle driver.  Every ESPN pick is an external
// manual pick in the simulator; the FBL page attachment only reads frames and
// requests in-memory scoring previews.
const fs = require("node:fs");
const vm = require("node:vm");
const { EspnRoomSimulator } = require("../room_simulator.js");
const { isHidden } = require("../dom_fixture.js");

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(predicate, timeoutMs = 8_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await wait(10);
  }
  return Boolean(predicate());
}

async function main(config) {
  const ownTeam = "Example Team";
  const order = [ownTeam, ...Array.from({ length: 13 }, (_, index) => `Opponent ${index + 1}`)];
  const simulator = new EspnRoomSimulator({
    catalog: config.catalog,
    order,
    ownTeam,
    room: config.room,
    timerSeconds: 30,
    feedWindow: 31,
    historyWindow: 14,
    resumeMode: "league_manager_only",
    view: "players",
    paused: true,
  });
  const pageDocument = {
    get body() { return simulator.liveBody(); },
    querySelector: (selector) => simulator.liveBody().querySelector(selector),
    querySelectorAll: (selector) => simulator.liveBody().querySelectorAll(selector),
    visibilityState: "visible",
  };
  const pageFetch = (url, init = {}) => fetch(url, { ...init, headers: { ...(init.headers || {}), Origin: "https://fantasy.espn.com" } });
  const context = vm.createContext({
    document: pageDocument,
    location: { get href() { return simulator.url(); } },
    fetch: pageFetch,
    getComputedStyle: (element) => ({ display: isHidden(element) ? "none" : "block", visibility: isHidden(element) ? "hidden" : "visible" }),
    setTimeout, clearTimeout, AbortController, URL, URLSearchParams, console,
  });
  context.window = context;
  vm.runInContext(fs.readFileSync(config.bundlePath, "utf8"), context, { filename: "page-runner.js" });
  const FBL = context.FBL;
  if (!FBL || FBL.bundle_hash !== config.bundleHash) throw new Error("bundle did not install the expected page API");
  FBL.configure({ baseUrl: config.base, csrfToken: config.csrfToken });
  FBL.attach({
    mode: "recommendation",
    sessionId: config.sessionId,
    ownerToken: config.ownerToken,
    ownTeam,
    expectedSeat: 1,
    rules: config.rules,
    catalog: config.catalog,
    catalogComplete: config.catalogComplete === true,
    historyPollMs: 2,
    historyScroller: async (deltaY) => simulator.scrollHistory(deltaY),
  });
  const runner = FBL.runner();
  runner.startRecommendations({ intervalMs: 100 });
  if (!(await waitFor(() => runner.recommendationSummary().successes >= 1))) throw new Error(`initial recommendation did not arrive: ${JSON.stringify(runner.summary())}`);

  simulator.resume(); // external/manual room action; FBL never sends start/resume.
  const ownRecommendations = [];
  let guard = 0;
  while (!simulator.complete && guard++ < 200) {
    const team = simulator.onClockTeam;
    const player = simulator.available()[0];
    if (team === ownTeam) {
      // The manual pick is made only after a successful advisory frame for
      // this exact own-turn pick.  This gives the HTTP fixture evidence for
      // all ten own turns without creating any FBL action or click state.
      const targetPick = simulator.currentPick;
      const targetSuccesses = simulator.ownPickNumbers.length + 1;
      if (!(await waitFor(() => {
        const current = runner.recommendationSummary();
        return current.successes >= Math.min(10, targetSuccesses)
          && current.last_good
          && current.last_good.current_pick === targetPick
          && current.last
          && Array.isArray(current.last.candidates)
          && current.last.candidates.length > 0;
      }))) {
        throw new Error(`recommendation did not precede manual own pick ${targetPick}: ${JSON.stringify(runner.summary())}`);
      }
      const current = runner.recommendationSummary();
      const candidate = current.last.candidates[0];
      ownRecommendations.push({
        pick: targetPick,
        player_id: candidate.player_id || null,
        player_name: candidate.name || candidate.player_name || null,
        remaining_count: current.last_good.candidate_count,
        observed_at: current.last_good.observed_at,
        policy_status: current.last.lookahead_status,
      });
    }
    simulator.draft(player.player_id, team); // external human pick in this fixture
  }
  if (!simulator.complete) throw new Error(`manual fixture did not complete after ${guard} picks`);
  if (!(await waitFor(() => {
    const observed = runner.recommendationSummary().last_good;
    return observed && observed.feed_count === 140 && observed.own_roster_count === 10;
  }))) throw new Error(`terminal manual picks were not observed: ${JSON.stringify(runner.summary())}`);
  if (runner.recommendationSummary().running) runner.stopRecommendations();
  const summary = runner.summary();
  return {
    summary,
    picks: simulator.picks.length,
    unique: new Set(simulator.picks.map((pick) => pick.player_id)).size,
    clicks: simulator.clicks.length,
    native_resume_clicks: simulator.resumeClicks,
    recommendation_successes: summary.recommendation.successes,
    own_recommendations: ownRecommendations,
    own_turns: summary.recommendation.own_turns,
    observed_history_count: summary.recommendation.last_good.feed_count,
    observed_roster_count: summary.recommendation.last_good.own_roster_count,
  };
}

if (require.main === module) {
  main(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((report) => {
    fs.writeFileSync(process.argv[3], JSON.stringify(report));
  }).catch((error) => {
    process.stderr.write(String(error.stack || error));
    process.exitCode = 1;
  });
}

module.exports = { main };
