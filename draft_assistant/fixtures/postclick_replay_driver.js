"use strict";

// Node half of the Python <-> JS post-click replay (test_runtime_interop.py).
// It drives the real operator runtime and post-click adapter against the
// simulated virtualized draft room and forwards every operator request to the
// Python coordinator over stdin/stdout as one JSON object per line.

const readline = require("node:readline");
const { createOperatorRuntime } = require("../operator_runtime.js");
const { createDraftSim } = require("./virtualized_draft_sim.js");

const config = JSON.parse(process.env.REPLAY_CONFIG);
const lines = readline.createInterface({ input: process.stdin })[Symbol.asyncIterator]();

async function ask(payload) {
  process.stdout.write(`${JSON.stringify({ request: payload })}\n`);
  const { value, done } = await lines.next();
  if (done) throw new Error("python side closed the pipe");
  return JSON.parse(value);
}

async function main() {
  const catalog = Array.from({ length: 140 }, (_, index) => ({
    player_id: `p${index + 1}`,
    name: `First${index + 1} Last${index + 1}`,
    eligibility: ["C", "G", "F"],
    stats: { PTS: 20 },
    projected_gp: 80,
  }));
  const sim = createDraftSim({
    room: config.room,
    rules: config.rules,
    teams: config.teams,
    ownTeam: config.own,
    catalog,
    sessionId: config.session_id,
    // Pick History hydrates late: the first read of every visit renders all
    // but the last grid, so the first poll is always incomplete.
    hydrate: (read, count) => (read < 2 ? Math.max(0, count - 1) : count),
    // A bot picks the instant ours lands, as in the diagnostic.
    afterOwnPick: () => 1,
  });
  const runtime = createOperatorRuntime({
    requestOperator: async (payload) => (await ask(payload)).response,
    observeCurrent: () => sim.beforeClickFrame(),
    clickPlayer: (picked) => sim.click(picked),
    postClickConfirmation: { observer: sim.observer, showPickHistory: () => sim.showPickHistory(), showPlayers: () => sim.showPlayers() },
  });

  const outcomes = [];
  let started = false;
  while (outcomes.length < config.picks) {
    sim.advanceToOwnTurn();
    const frame = await sim.beforeClickFrame();
    if (!started) {
      const start = await ask({ operation: "start", session_id: config.session_id, observation: frame });
      if (start.response.status === "error") throw new Error(`start failed: ${start.response.error}`);
      started = true;
    }
    const result = await runtime.runTurn({ sessionId: config.session_id, observation: frame });
    outcomes.push(result.outcome);
    if (result.outcome !== "confirmed") break;
  }
  process.stdout.write(`${JSON.stringify({ done: { outcomes, clicks: sim.stats.clicks, bodyEvaluates: sim.stats.bodyEvaluates, historyReads: sim.stats.historyReads, picks: sim.events.length } })}\n`);
  process.exit(0);
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify({ failed: String(error && error.stack || error) })}\n`);
  process.exit(1);
});
