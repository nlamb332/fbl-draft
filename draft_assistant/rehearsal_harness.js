"use strict";

/**
 * Offline full dress rehearsal (issue #43).
 *
 * Drives the CANONICAL modules - browser_observer.js, operator_runtime.js,
 * native_activation.js, batch_recovery.js - through a complete 14-team,
 * 10-round practice draft against the room simulator, with the coordinator
 * reached over REAL HTTP (the same /api/operator and /api/control endpoints the
 * panel exposes).  It reproduces the live failure modes with injected faults:
 *
 *   virtualized_feed            31-row Feed (always on)
 *   late_history_hydration      Pick History renders empty for several reads
 *   abbreviated_roster_names    roster cells abbreviated; full name in title
 *   missing_resume              Resume absent from the first view: activation refuses
 *   observation_gap             a >10 s gap between frames (batch stops, explicit recovery)
 *   recommended_player_vanishes our recommended row vanishes before the click
 *                               (the opponent-claims-it race; pre-click guard refuses)
 *   agent_crash_handoff         the agent session crashes; a successor takes
 *                               ownership by session id + last confirmed revision
 *
 * The harness documents CURRENT behaviour: it neither implements #41's
 * heartbeat/staleness redesign nor #42's post-click confirmation changes.  It
 * writes a JSON report; the Python test turns the coordinator state plus this
 * report into the sanitized export.
 */

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { createBrowserObserver } = require("./browser_observer.js");
const { createOperatorRuntime, runForegroundBatch } = require("./operator_runtime.js");
const { activateDraft, inspectNativeControls } = require("./native_activation.js");
const { filterLiveAvailablePlayers } = require("./live_projection_inputs.js");
const { recoverAfterStop } = require("./batch_recovery.js");
const { createViewSwitcher } = require("./view_switch.js");
const { EspnRoomSimulator } = require("./room_simulator.js");
const { createFetchPost, createHeartbeatPublisher } = require("./heartbeat_publisher.js");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const OWN_TEAM = "Example Team";

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").toUpperCase();
}

function createHttp(base) {
  let csrf = null;
  async function token() {
    if (!csrf) csrf = (await (await fetch(`${base}/api/csrf`)).json()).csrf_token;
    return csrf;
  }
  async function post(route, payload) {
    const response = await fetch(base + route, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await token() },
      body: JSON.stringify(payload),
    });
    return response.json();
  }
  return {
    async csrfToken() { return token(); },
    // Same transport contract as the panel operator: stamps the receipt time locally.
    async operator(payload) {
      const result = await post("/api/operator", payload);
      return { ...result, status_received_at: new Date().toISOString() };
    },
    control: (payload) => post("/api/control", payload),
  };
}

async function runDressRehearsal(config) {
  const {
    base,
    sessionId,
    catalog,
    rules,
    seat = 13,
    faults = {},
    heartbeat = false,
    cleanMode = false,
    gapMs = 10_500,
    historyHydrationReads = 2,
    maxBatches = 12,
    agentIdleGapMs = 0,
  } = config;
  const order = Array.from({ length: 14 }, (_, index) => (index === seat - 1 ? OWN_TEAM : `Opponent ${index + 1}`));
  const room = { league_id: config.room.league_id, season_id: config.room.season_id, team_id: config.room.team_id };
  const simulator = new EspnRoomSimulator({
    catalog,
    order,
    ownTeam: OWN_TEAM,
    room,
    timerSeconds: 90,
    feedWindow: cleanMode ? 140 : 31,
    historyHydrationReads: cleanMode ? 0 : historyHydrationReads,
    abbreviateRoster: !cleanMode,
    resumeMode: faults.missing_resume === true ? "league_manager_only" : "present",
    resumeLabel: "RESUME DRAFT",
    view: "players",
  });
  const tab = simulator.tab();
  const http = createHttp(base);
  const switcher = createViewSwitcher({ tab });
  const counter = { n: 0 };
  const observer = createBrowserObserver({
    tab,
    sessionId,
    room: { ...room, practice: true },
    freshlyVerifiedRules: rules,
    projectionCatalog: catalog,
    counter,
  });
  const report = {
    harness: "canonical-dress-rehearsal-v1",
    seat,
    started_at: new Date().toISOString(),
    faults: [],
    activation_attempts: [],
    recoveries: [],
    latency_rows: [],
    batches: [],
    history_refreshes: 0,
    hydration_polls: 0,
    heartbeat: { enabled: Boolean(heartbeat), publish_gaps: [] },
    ownership: {},
    source_hashes_sha256: Object.fromEntries(["browser_observer.js", "operator_runtime.js", "heartbeat_publisher.js", "native_activation.js", "batch_recovery.js", "live_projection_inputs.js"].map((name) => [`draft_assistant/${name}`, sha256(path.join(__dirname, name))])),
    policy: config.policy || null,
  };
  const fault = (name, detail = {}) => report.faults.push({ fault: name, at_pick: simulator.currentPick, at: new Date().toISOString(), ...detail });
  if (!cleanMode) {
    fault("virtualized_feed", { feed_rows: 31 });
    fault("abbreviated_roster_names", { example: "N. Lastname" });
    fault("late_history_hydration", { empty_reads_per_history_view: historyHydrationReads });
  }

  // ---- agent-side frame capture (mirrors the runbook) ------------------------
  // Heartbeat and foreground reads share the canonical observer, serialized
  // so its frame counter and retained history cannot race across revisions.
  let observerReads = Promise.resolve();
  const serializedObserverRead = (read) => {
    const result = observerReads.then(read);
    observerReads = result.catch(() => {});
    return result;
  };
  const captureFastFrame = () => serializedObserverRead(() => observer.captureFastFrame());
  async function refreshHistory(currentPick) {
    report.history_refreshes += 1;
    await switcher.showPickHistory();
    let snapshot = null;
    for (let attempt = 0; attempt < 8; attempt += 1) {
      // retain:false - an unhydrated (empty) grid must never replace good retained history.
      snapshot = await serializedObserverRead(() => observer.captureHistory({ expectedCurrentPick: currentPick, retain: false }));
      if (snapshot.history_complete) break;
      report.hydration_polls += 1;
      await sleep(2);
    }
    if (snapshot && snapshot.history_complete) await serializedObserverRead(() => observer.setRetainedHistory(snapshot));
    await switcher.showPlayers();
    return Boolean(snapshot && snapshot.history_complete);
  }

  async function captureFrame() {
    if (simulator.view !== "players") await switcher.showPlayers();
    let frame = await captureFastFrame();
    if (!frame.history_complete) {
      await refreshHistory(frame.current_pick ?? null);
      frame = await captureFastFrame();
    }
    // Diagnostics stay outside DraftObservation (`_live_filter` regression).
    return filterLiveAvailablePlayers(frame).observation;
  }

  async function readPreflight(frame) {
    const inspection = await tab.playwright.locator("body").evaluate(inspectNativeControls);
    return { native_timer_seconds: inspection.native_timer_seconds, expected_seat: order.indexOf(OWN_TEAM) + 1 };
  }

  // ---- ownership + activation ------------------------------------------------
  const claimed = await http.operator({ operation: "claim-owner", session_id: sessionId, label: "agent-a" });
  if (claimed.error) throw new Error(`claim-owner failed: ${claimed.error}`);
  let ownerToken = claimed.owner_token;
  const staleTokens = [];
  report.ownership.claimed_epoch = claimed.epoch;
  const operator = (payload) => http.operator({ ...payload, owner_token: ownerToken });

  const preflight = await readPreflight();
  const activate = async () => activateDraft({
    tab,
    observe: captureFrame,
    observePaused: captureFastFrame,
    requestOperator: http.operator,
    sessionId,
    ownerToken,
    expectedSeat: preflight.expected_seat,
  });
  let activation = await activate();
  report.activation_attempts.push({ view: simulator.view, status: activation.status, reason: activation.reason, native_resume_clicked: activation.native_resume_clicked, elapsed_ms: activation.elapsed_ms, steps: activation.steps });
  if (activation.status === "refused") {
    fault("missing_resume", { reason: activation.reason, clicked: activation.native_resume_clicked });
    // Operator opens League Manager (where Resume renders) and activates again.
    simulator.setView("league_manager");
    activation = await activate();
    report.activation_attempts.push({ view: "league_manager", status: activation.status, reason: activation.reason, native_resume_clicked: activation.native_resume_clicked, elapsed_ms: activation.elapsed_ms, steps: activation.steps });
  }
  if (activation.status !== "activated") {
    report.outcome = "activation_failed";
    report.finished_at = new Date().toISOString();
    return report;
  }
  simulator.setView("players");

  // The clean combined gate runs the production heartbeat publisher against
  // the actual local HTTP endpoint. Fault scenarios keep explicit recovery
  // behavior isolated and therefore do not start this publisher.
  let heartbeatPublisher = null;
  if (heartbeat) {
    heartbeatPublisher = createHeartbeatPublisher({
      // The heartbeat is read-only and must not switch views while the
      // operator runtime performs its authoritative history-then-Players
      // post-click confirmation sequence.
      capture: async () => filterLiveAvailablePlayers(await captureFastFrame()).observation,
      post: createFetchPost({ baseUrl: base, csrfToken: await http.csrfToken() }),
      intervalMs: 2_000,
      getVisibility: () => "visible",
    });
    heartbeatPublisher.start();
    await heartbeatPublisher.tick("rehearsal-start");
    if (agentIdleGapMs > 0) {
      const before = heartbeatPublisher.stats();
      const started = Date.now();
      await sleep(agentIdleGapMs);
      report.heartbeat.agent_call_gap = {
        duration_ms: Date.now() - started,
        publishes_during_gap: heartbeatPublisher.stats().published - before.published,
      };
    }
  }

  // ---- the foreground loop with explicit recovery ----------------------------
  const state = { vanishDone: false, gapDone: false, crashDone: false, lagDone: false, lastOwnObservation: null, clickAt: new Map() };
  const clickPlayer = async (command, current, { draftTab }) => {
    state.clickAt.set(command.action_id, Date.now());
    const button = draftTab.playwright.locator(`button[data-player-id="${command.player_id}"]`);
    await button.click();
  };

  async function observe({ stage, command }) {
    if (stage === "batch-state") {
      if (!state.crashDone && faults.agent_crash_handoff && simulator.picks.length >= faults.agent_crash_handoff.at_pick && !simulator.ownTurn) {
        state.crashDone = true;
        fault("agent_crash_handoff", { note: "agent A observer crashed" });
        throw new Error("simulated agent session crash");
      }
      simulator.stepOpponent();
    }
    if (stage === "before-click" && command && !state.vanishDone && faults.recommended_player_vanishes && simulator.ownPickNumbers.length === faults.recommended_player_vanishes.own_pick_index) {
      state.vanishDone = true;
      simulator.vanishRow(command.player_id);
      fault("recommended_player_vanishes", { player_id: command.player_id, expected_pick: command.expected_pick });
    }
    if (stage === "before-click" && command && !state.lagDone && faults.post_click_hydration_lag && simulator.ownPickNumbers.length === faults.post_click_hydration_lag.own_pick_index) {
      state.lagDone = true;
      simulator.confirmationLagReads = 1;
      fault("post_click_hydration_lag", { player_id: command.player_id, expected_pick: command.expected_pick, reads: 1 });
    }
    if (stage === "after-click") simulator.confirmationLagReads = 0;
    const frame = await captureFrame();
    if (stage === "batch-state" && !state.gapDone && faults.observation_gap && simulator.picks.length >= faults.observation_gap.at_pick && !simulator.ownTurn) {
      state.gapDone = true;
      fault("observation_gap", { gap_ms: gapMs });
      await sleep(gapMs);
    }
    return frame;
  }

  const runtimeFor = () => createOperatorRuntime({
    draftTab: tab,
    observeCurrent: observe,
    observeAfter: observe,
    // Required (fail-closed) by PR #46 / #42; ignored by runtimes without it.
    postClickConfirmation: { observer, showPickHistory: switcher.showPickHistory, showPlayers: switcher.showPlayers },
    requestOperator: operator,
    clickPlayer,
  });

  let initialCheckpoint = null;
  let resumeCoordinatorStatus = null;
  let carriedOwnPicks = 0;
  let finalResult = null;
  try { for (let batchIndex = 1; batchIndex <= maxBatches; batchIndex += 1) {
    const runtime = runtimeFor();
    const batchStartedAt = Date.now();
    const result = await runForegroundBatch({
      runtime,
      sessionId,
      ownTeam: OWN_TEAM,
      observe,
      observeFinal: observe,
      draftTab: tab,
      pollIntervalMs: 2,
      initialCheckpoint,
      initialOwnPicksConfirmed: carriedOwnPicks,
      resumeCoordinatorStatus,
      readFinalCoordinatorStatus: () => http.operator({ operation: "status", session_id: sessionId }),
      onProgress: async (event) => {
        if (event.event === "own-turn") state.lastOwnObservation = event.observation;
        if (event.event === "own-result" && event.result && event.result.outcome === "confirmed") {
          const command = event.result.command;
          const clickAt = state.clickAt.get(command.action_id);
          const observedAt = Date.parse(state.lastOwnObservation.observed_at);
          report.latency_rows.push({
            pick: command.expected_pick,
            player: command.player_name,
            observation_to_click_ms: clickAt - observedAt,
            click_to_confirm_ms: Date.now() - clickAt,
            clock_seconds: state.lastOwnObservation.seconds_remaining,
            history_and_roster_confirmed: true,
          });
        }
      },
    });
    report.batches.push({ index: batchIndex, status: result.status, reason: result.reason || null, own_picks_confirmed: result.own_picks_confirmed, cycles: result.cycles, elapsed_ms: Date.now() - batchStartedAt });
    if (result.status === "completed") { finalResult = result; break; }

    // Agent crash: a successor takes the room by session id + last confirmed revision.
    if (/crash/i.test(result.error || result.reason || "")) {
      const before = (await http.operator({ operation: "status", session_id: sessionId })).latest_revision;
      const oldToken = ownerToken;
      const moved = await http.operator({ operation: "transfer-owner", session_id: sessionId, last_confirmed_revision: before, label: "agent-b" });
      if (moved.error) { report.outcome = "transfer_failed"; report.reason = moved.error; break; }
      staleTokens.push(oldToken);
      ownerToken = moved.owner_token;
      const lateWrite = await http.operator({ operation: "publish", observation: state.lastOwnObservation || (await captureFrame()), owner_token: oldToken });
      report.ownership.transfer = { from_epoch: claimed.epoch, to_epoch: moved.epoch, revision: before, paused_running_session: moved.paused_running_session, old_owner_write_rejected: lateWrite.status === "error" && /ownership claim/.test(lateWrite.error || "") };
      fault("agent_crash_handoff", { transferred: true, old_owner_write_rejected: report.ownership.transfer.old_owner_write_rejected });
    }
    const recovery = await recoverAfterStop({
      stop: result,
      sessionId,
      ownTeam: OWN_TEAM,
      ownerToken,
      requestOperator: http.operator,
      requestControl: http.control,
      capture: captureFrame,
      readPreflight,
    });
    report.recoveries.push({ batch: batchIndex, stop_reason: result.reason, recovered: recovery.recovered, reason: recovery.reason || null, log: recovery.log });
    if (!recovery.recovered) { report.outcome = "recovery_failed"; break; }
    initialCheckpoint = recovery.checkpoint;
    resumeCoordinatorStatus = recovery.resumeStatus;
    carriedOwnPicks = recovery.ownPicksConfirmed;
  } } finally {
    if (heartbeatPublisher) {
      heartbeatPublisher.stop();
      report.heartbeat.stats = heartbeatPublisher.stats();
      report.heartbeat.last = heartbeatPublisher.last();
    }
  }

  report.finished_at = new Date().toISOString();
  report.sim = {
    picks: simulator.picks.length,
    own_pick_numbers: simulator.ownPickNumbers,
    native_resume_clicks: simulator.resumeClicks,
    draft_clicks: simulator.clicks,
  };
  report.outcome = report.outcome || (finalResult ? "completed" : "incomplete");
  report.final_own_picks_confirmed = finalResult ? finalResult.own_picks_confirmed : null;
  const finalStatus = await http.operator({ operation: "status", session_id: sessionId });
  report.heartbeat.publish_gaps = finalStatus.publish_gaps || [];
  report.latest_recommendation = finalStatus.recommendation || null;
  report.canonical_modules = ["browser_observer.js", "operator_runtime.js", "heartbeat_publisher.js"];
  return report;
}

module.exports = { runDressRehearsal };

if (require.main === module) {
  const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  runDressRehearsal(config).then((report) => {
    fs.writeFileSync(process.argv[3], JSON.stringify(report, null, 2));
  }).catch((error) => {
    fs.writeFileSync(process.argv[3], JSON.stringify({ outcome: "harness_error", error: String(error && error.stack ? error.stack : error) }, null, 2));
    process.exitCode = 1;
  });
}
