"use strict";

// Served-bundle restart regression.  The first page runner is deliberately
// stopped after two confirmed owned picks; the coordinator and native fixture
// are paused, then a fresh FBL.attach() must rebuild a validated checkpoint
// from coordinator actions before it resumes the remaining draft.
const fs = require("node:fs");
const vm = require("node:vm");
const { EspnRoomSimulator } = require("../room_simulator.js");
const { isHidden } = require("../dom_fixture.js");

async function main(config) {
  const ownTeam = "Example Team";
  const order = Array.from({ length: 14 }, (_, index) => (index === 12 ? ownTeam : `Opponent ${index + 1}`));
  const simulator = new EspnRoomSimulator({
    catalog: config.catalog,
    order,
    ownTeam,
    room: config.room,
    timerSeconds: 30,
    feedWindow: 31,
    historyHydrationReads: 2,
    resumeMode: "league_manager_only",
    resumeSettleReads: config.resumeSettleReads ?? 2,
    pickSettleReads: config.pickSettleReads ?? 1,
    historyWindow: config.historyWindow ?? 14,
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
  if (!FBL || FBL.bundle_hash !== config.bundleHash) throw new Error("bundle did not install window.FBL with the expected hash");
  FBL.configure({ baseUrl: config.base, csrfToken: config.csrfToken });
  const pageRunner = FBL.modules["page_runner.js"];
  const canonical = pageRunner.canonicalModules();
  const modules = {
    ...canonical,
    runForegroundBatch: (options) => canonical.runForegroundBatch({
      ...options,
      observe: async (observeContext) => {
        if (observeContext.stage === "batch-state") simulator.stepOpponent();
        return options.observe(observeContext);
      },
    }),
  };
  const attach = () => FBL.attach({
    sessionId: config.sessionId,
    ownerToken: config.ownerToken,
    ownTeam,
    expectedSeat: 13,
    rules: config.rules,
    catalog: config.catalog,
    modules,
    historyPollMs: 2,
    historyScroller: async (deltaY) => simulator.scrollHistory(deltaY),
  });

  attach();
  let runner = FBL.runner();
  const firstPreflight = await runner.preflight();
  if (firstPreflight.status !== "preflight_ok") throw new Error(`first preflight: ${JSON.stringify(firstPreflight)}`);
  const firstActivation = await runner.activate();
  if (firstActivation.status !== "activated") throw new Error(`first activation: ${JSON.stringify(firstActivation)}`);
  runner.startBatch({ pollIntervalMs: 2, maxCycles: config.firstMaxCycles ?? 30 });
  const firstResult = await runner.awaitBatch();
  if (!firstResult || firstResult.status !== "stopped" || firstResult.own_picks_confirmed < 2) {
    throw new Error(`first batch did not stop after two confirmations: ${JSON.stringify(firstResult)}`);
  }

  simulator.pause();
  const pauseResponse = await pageFetch(`${config.base}/api/operator`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": config.csrfToken },
    body: JSON.stringify({ operation: "pause", session_id: config.sessionId, owner_token: config.ownerToken, reason: "restart regression" }),
  }).then((response) => response.json());
  if (pauseResponse.error || pauseResponse.status === "error") throw new Error(`pause: ${JSON.stringify(pauseResponse)}`);

  const firstStatus = await pageFetch(`${config.base}/api/operator`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": config.csrfToken },
    body: JSON.stringify({ operation: "status", session_id: config.sessionId, owner_token: config.ownerToken }),
  }).then((response) => response.json());
  const carriedActionIds = firstStatus.actions.filter((action) => action.status === "confirmed" && action.claimed && action.dispatched).map((action) => action.action_id);

  // A new attach recreates the runner while retaining the same page and
  // coordinator owner.  Its activation builds and stores the checkpoint.
  attach();
  runner = FBL.runner();
  const secondPreflight = await runner.preflight();
  if (secondPreflight.status !== "preflight_ok") throw new Error(`second preflight: ${JSON.stringify(secondPreflight)}`);
  const secondActivation = await runner.activate();
  if (secondActivation.status !== "activated") throw new Error(`second activation: ${JSON.stringify(secondActivation)}`);
  const attachment = runner.summary().attachment;
  if (!attachment || attachment.own_picks_confirmed !== carriedActionIds.length) throw new Error(`checkpoint did not carry exact count: ${JSON.stringify({ attachment, carriedActionIds })}`);
  runner.startBatch({ pollIntervalMs: 2 });
  const secondResult = await runner.awaitBatch();
  const finalStatus = await runner.coordinatorStatus();
  const finalActionIds = finalStatus.actions.filter((action) => action.status === "confirmed" && action.claimed && action.dispatched).map((action) => action.action_id);
  return {
    first_result: firstResult,
    second_result: secondResult,
    first_status: { state: firstStatus.state, confirmed_action_ids: carriedActionIds },
    attachment,
    final_status: { state: finalStatus.state, confirmed_action_ids: finalActionIds },
    picks: simulator.picks.length,
    unique: new Set(simulator.picks.map((pick) => pick.player_id)).size,
    clicks: simulator.clicks.length,
    native_resume_clicks: simulator.resumeClicks,
  };
}

main(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((report) => {
  fs.writeFileSync(process.argv[3], JSON.stringify(report));
}).catch((error) => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });

