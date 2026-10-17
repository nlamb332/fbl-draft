"use strict";

// Offline harness-agnostic check: the bundle exactly as /page-runner.js serves
// it is loaded into an isolated context whose `document`, `window` and
// `location` are the simulated ESPN page.  Every request carries the ESPN page
// Origin, so the coordinator's page-origin allowlist and ownership gate are
// exercised.  Only opponents and DOM are simulated.
const fs = require("node:fs");
const vm = require("node:vm");
const { EspnRoomSimulator } = require("../room_simulator.js");
const { isHidden } = require("../dom_fixture.js");

async function main(config) {
  const ownTeam = "Example Team";
  const order = Array.from({ length: 14 }, (_, i) => (i === 12 ? ownTeam : `Opponent ${i + 1}`));
  const simulator = new EspnRoomSimulator({ catalog: config.catalog, order, ownTeam, room: config.room,
    timerSeconds: 30, feedWindow: 31, historyHydrationReads: 2, resumeMode: "league_manager_only",
    // In-page reads run back to back; live ESPN showed "--:--" and no Players rows right after Resume.
    resumeSettleReads: config.resumeSettleReads ?? 8,
    // ...and an empty Players grid for a few reads after a pick (live pick 16).
    pickSettleReads: config.pickSettleReads ?? 3,
    // ...and Pick History virtualized to the ~14 rows live ESPN renders.
    historyWindow: config.historyWindow ?? 14 });
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
  if (!FBL || FBL.bundle_hash !== config.bundleHash) throw new Error("bundle did not install window.FBL with the expected hash");

  FBL.configure({ baseUrl: config.base, csrfToken: config.csrfToken });
  const pageRunner = FBL.modules["page_runner.js"];
  const canonical = pageRunner.canonicalModules();
  const modules = { ...canonical,
    runForegroundBatch: (options) => canonical.runForegroundBatch({ ...options,
      observe: async (observeContext) => {
        if (observeContext.stage === "batch-state") simulator.stepOpponent();
        return options.observe(observeContext);
      },
    }),
  };
  FBL.attach({ sessionId: config.sessionId, ownerToken: config.ownerToken, ownTeam, expectedSeat: 13,
    rules: config.rules, catalog: config.catalog, modules, historyPollMs: 2,
    historyScroller: async (deltaY) => simulator.scrollHistory(deltaY) });
  const runner = FBL.runner();
  const preflight = await runner.preflight();
  if (preflight.status !== "preflight_ok") throw new Error(`preflight: ${JSON.stringify(preflight)}`);
  const activation = await runner.activate();
  if (activation.status !== "activated") throw new Error(`activation: ${JSON.stringify(activation)}`);
  runner.startBatch({ pollIntervalMs: 2 });
  await runner.awaitBatch();
  const summary = runner.summary();
  return { preflight, summary, picks: simulator.picks.length, unique: new Set(simulator.picks.map((p) => p.player_id)).size,
    clicks: simulator.clicks.length, native_resume_clicks: simulator.resumeClicks, evidence: runner.evidence() };
}

main(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((report) => {
  fs.writeFileSync(process.argv[3], JSON.stringify(report));
}).catch((error) => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
