"use strict";

// Served-coordinator integration driver.  This uses the checked-in canonical
// runner, observer, operator runtime, activation and view-switch modules over
// real localhost HTTP.  ESPN is still represented by the synthetic DOM and
// advancing-clock adapter; this is not browser or live-draft evidence.
const fs = require("node:fs");
const { AdvancingClockFixture } = require("./advancing_clock_fixture.js");
const observer = require("../browser_observer.js");
const operator = require("../operator_runtime.js");
const native = require("../native_activation.js");
const views = require("../view_switch.js");
const filter = require("../live_projection_inputs.js");
const recovery = require("../batch_recovery.js");
const { createCanonicalPracticeRunner } = require("../canonical_runner.js");

async function main(config) {
  const ownTeam = "Example Team";
  const seat = Number(config.seat || 13);
  const order = Array.from({ length: 14 }, (_, index) => index === seat - 1 ? ownTeam : `Opponent ${index + 1}`);
  const fixture = new AdvancingClockFixture({
    catalog: config.catalog,
    order,
    ownTeam,
    room: config.room,
    timerSeconds: Number(config.timerSeconds || 30),
    opponentDelayMs: Number(config.opponentDelayMs || 100),
    // A small past skew keeps accelerated virtual timestamps behind the
    // real localhost coordinator while the fixture advances faster than wall
    // time. The skew is test plumbing, not a browser-clock claim.
    initialNowMs: Number(config.initialNowMs || (Date.now() - Number(config.clockSkewMs || 0))),
    paused: true,
    simulatorOptions: {
      feedWindow: 31,
      historyHydrationReads: Number(config.historyHydrationReads || 2),
      resumeMode: "league_manager_only",
      historyWindow: Number(config.historyWindow || 0),
    },
  });
  const tab = fixture.tab({ readLatencyMs: Number(config.readLatencyMs || 0) });
  const token = (await (await fetch(`${config.base}/api/csrf`)).json()).csrf_token;
  const request = async (payload) => {
    const response = await fetch(`${config.base}/api/operator`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify(payload),
    });
    return { ...await response.json(), status_received_at: new Date(fixture.now).toISOString() };
  };
  const owner = await request({ operation: "claim-owner", session_id: config.sessionId });
  if (owner.error) throw new Error(owner.error);

  const mode = String(config.mode || "clean");
  const delayBeforeClickMs = Number(config.delayBeforeClickMs || 4_000);
  let delayedOwnTurn = false;
  const modules = {
    ...observer,
    ...operator,
    ...native,
    ...views,
    ...filter,
    ...recovery,
    createBrowserObserver: (options) => observer.createBrowserObserver({ ...options, now: fixture.nowFn }),
    activateDraft: (options) => native.activateDraft({ ...options, now: fixture.nowFn }),
    createOperatorRuntime: (options) => operator.createOperatorRuntime({
      ...options,
      now: fixture.nowFn,
      postClickConfirmation: options.postClickConfirmation
        ? {
            ...options.postClickConfirmation,
            now: fixture.nowFn,
            sleepFn: async (milliseconds) => fixture.advance(milliseconds, { reason: "canonical_confirmation_poll" }),
          }
        : options.postClickConfirmation,
    }),
    // This remains the real checked-in foreground runner. The wrapper only
    // moves the synthetic room to the next scheduled opponent event before a
    // state read and injects one controllable delay in the expired-guard mode.
    runForegroundBatch: (options) => operator.runForegroundBatch({
      ...options,
      now: fixture.nowFn,
      observe: async (context) => {
        if (context.stage === "batch-state" && !fixture.ownTurn && !fixture.complete) {
          fixture.advance(fixture.opponentDelayMs, { reason: "independent_opponent_schedule" });
        }
        const frame = await options.observe(context);
        if (mode === "expired" && !delayedOwnTurn && frame.on_clock_team === ownTeam) {
          delayedOwnTurn = true;
          fixture.advance(delayBeforeClickMs, { reason: "delayed_runner_before_guard" });
        }
        return frame;
      },
    }),
  };

  const runner = createCanonicalPracticeRunner({
    modules,
    draftTab: tab,
    panelTab: tab,
    room: { ...config.room, practice: true },
    freshlyVerifiedRules: config.rules,
    projectionCatalog: config.catalog,
    sessionId: config.sessionId,
    ownerToken: owner.owner_token,
    ownTeam,
    expectedSeat: seat,
    requestOperator: request,
    historyPollMs: 0,
    hydrationPollMs: 0,
    firstFramePollMs: 0,
    now: fixture.nowFn,
  });

  await runner.prepareActivation();
  const activation = await runner.activate();
  if (activation.status !== "activated") throw new Error(JSON.stringify(activation));
  const result = await runner.runBatch({
    pollIntervalMs: 0,
    maxDurationMs: Number(config.maxDurationMs || 240_000),
    maxNoProgressMs: Number(config.maxNoProgressMs || 120_000),
  });
  const coordinator = await request({ operation: "status", session_id: config.sessionId });
  return {
    status: result.status,
    reason: result.reason,
    error: result.error,
    stop_detail: result.published || result.result || result.validation || null,
    mode,
    canonical_runner: true,
    served_coordinator: true,
    synthetic_room: true,
    guarded_click_blocked: mode === "expired" && fixture.clicks.length === 0,
    picks: fixture.picks.length,
    unique: new Set(fixture.picks.map((pick) => pick.player_id)).size,
    clicks: fixture.clicks.length,
    native_resume_clicks: fixture.simulator.resumeClicks,
    coordinator_state: coordinator.state,
    coordinator_unresolved_actions: coordinator.unresolved_actions || [],
    fixture: {
      ...fixture.report(),
      events: undefined,
    },
    evidence: runner.evidenceSnapshot(),
    scope: {
      canonical_runner: "checked-in createCanonicalPracticeRunner/runForegroundBatch",
      coordinator: "real localhost HTTP /api/operator",
      room: "EspnRoomSimulator DOM wrapped by controlled virtual clock",
      live_browser: false,
      real_timer_endurance: false,
    },
  };
}

if (require.main === module) {
  main(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((report) => {
    fs.writeFileSync(process.argv[3], JSON.stringify(report, null, 2));
  }).catch((error) => {
    process.stderr.write(String(error.stack || error));
    process.exitCode = 1;
  });
}

module.exports = { main };
