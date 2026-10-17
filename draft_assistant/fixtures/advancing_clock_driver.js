"use strict";

// Offline driver for the advancing-clock fixture.  It intentionally reports
// accelerated, controlled-clock evidence and never represents wall-clock ESPN
// endurance or a browser run.
const fs = require("node:fs");
const { AdvancingClockFixture } = require("./advancing_clock_fixture.js");

function catalog() {
  return Array.from({ length: 160 }, (_, index) => ({
    player_id: String(1000 + index),
    name: `Player ${index + 1}`,
    eligibility: [index % 2 ? "G" : "F"],
    stats: { PTS: 100 - index, REB: 10, FGA: 1, FGM: 1, FTA: 1, FTM: 1, "3PM": 1, AST: 1, STL: 1, BLK: 1, TO: 1 },
    projected_gp: 70,
    draft_rank: index + 1,
  }));
}

async function main(config = {}) {
  const ownTeam = "Example Team";
  const order = [ownTeam, ...Array.from({ length: 13 }, (_, index) => `Opponent ${index + 1}`)];
  const room = new AdvancingClockFixture({
    catalog: catalog(),
    order,
    ownTeam,
    room: { league_id: "1000000012", season_id: "2027", team_id: "15" },
    timerSeconds: config.timerSeconds || 30,
    opponentDelayMs: config.opponentDelayMs || 25,
    paused: true,
  });
  room.resume();
  let guard = 0;
  while (!room.complete && guard++ < 500) {
    if (room.ownTurn) {
      const player = room.simulator.available()[0];
      room.simulator.draft(player.player_id, ownTeam);
      room.sync();
    } else {
      room.advance(config.opponentDelayMs || 25, { reason: "driver_opponent_schedule" });
    }
  }
  if (!room.complete) throw new Error(`accelerated fixture did not complete after ${guard} cycles`);
  const details = room.report();
  const event_counts = details.events.reduce((counts, event) => {
    counts[event.type] = (counts[event.type] || 0) + 1;
    return counts;
  }, {});
  const report = {
    status: "completed",
    driver: "advancing_clock_driver",
    synthetic: true,
    canonical_runner: false,
    served_coordinator: false,
    evidence_scope: "simulator_only_direct_public_api_run",
    evidence_note: "Controlled virtual clock with accelerated opponent schedule; no real 30-second endurance claim.",
    ...details,
    events: undefined,
    event_counts,
    last_events: details.events.slice(-5),
    unique_players: new Set(room.picks.map((pick) => pick.player_id)).size,
    synthetic_opponent_events: room.events.filter((event) => event.type === "synthetic_opponent_pick").length,
  };
  if (config.output) fs.writeFileSync(config.output, JSON.stringify(report, null, 2));
  return report;
}

if (require.main === module) {
  const configPath = process.argv[2];
  const config = configPath ? JSON.parse(fs.readFileSync(configPath, "utf8")) : {};
  main(config).then((report) => {
    if (!config.output) process.stdout.write(`${JSON.stringify(report)}\n`);
  }).catch((error) => {
    process.stderr.write(String(error.stack || error));
    process.exitCode = 1;
  });
}

module.exports = { main };
