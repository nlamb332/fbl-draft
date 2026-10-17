"use strict";

// Offline adapter: only opponents and DOM are simulated. All runner, observer,
// activation, transport, dispatch and confirmation code is the checked-in code.
const fs = require("node:fs");
const { EspnRoomSimulator } = require("../room_simulator.js");
const observer = require("../browser_observer.js");
const operator = require("../operator_runtime.js");
const native = require("../native_activation.js");
const views = require("../view_switch.js");
const filter = require("../live_projection_inputs.js");
const recovery = require("../batch_recovery.js");
const { createCanonicalPracticeRunner } = require("../canonical_runner.js");

async function main(config) {
  const ownTeam = "Example Team";
  const order = Array.from({ length: 14 }, (_, i) => i === 12 ? ownTeam : `Opponent ${i + 1}`);
  const simulator = new EspnRoomSimulator({ catalog: config.catalog, order, ownTeam, room: config.room,
    timerSeconds: 90, feedWindow: 31, historyHydrationReads: 2, resumeMode: "league_manager_only" });
  const tab = simulator.tab();
  const token = (await (await fetch(`${config.base}/api/csrf`)).json()).csrf_token;
  const request = async (payload) => {
    const response = await fetch(`${config.base}/api/operator`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify(payload),
    });
    return { ...await response.json(), status_received_at: new Date().toISOString() };
  };
  const owner = await request({ operation: "claim-owner", session_id: config.sessionId });
  if (owner.error) throw new Error(owner.error);
  const modules = { ...observer, ...operator, ...native, ...views, ...filter, ...recovery,
    runForegroundBatch: (options) => operator.runForegroundBatch({ ...options,
      observe: async (context) => {
        if (context.stage === "batch-state") simulator.stepOpponent();
        return options.observe(context);
      },
    }),
  };
  const runner = createCanonicalPracticeRunner({ modules, draftTab: tab, panelTab: tab,
    room: { ...config.room, practice: true }, freshlyVerifiedRules: config.rules,
    projectionCatalog: config.catalog, sessionId: config.sessionId, ownerToken: owner.owner_token,
    ownTeam, expectedSeat: 13, requestOperator: request, historyPollMs: 2 });
  await runner.prepareActivation();
  const activation = await runner.activate();
  if (activation.status !== "activated") throw new Error(JSON.stringify(activation));
  const result = await runner.runBatch({ pollIntervalMs: 2 });
  return { status: result.status, reason: result.reason, error: result.error, picks: simulator.picks.length,
    unique: new Set(simulator.picks.map(p => p.player_id)).size,
    clicks: simulator.clicks.length, native_resume_clicks: simulator.resumeClicks,
    evidence: runner.evidenceSnapshot() };
}

main(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((report) => {
  fs.writeFileSync(process.argv[3], JSON.stringify(report));
}).catch((error) => { process.stderr.write(String(error.stack || error)); process.exitCode = 1; });
