"use strict";

/** Assemble the checked-in attended-practice modules around a visible ESPN tab.
 *
 * Pass module exports explicitly so this factory can also be loaded as a plain
 * JavaScript definition in the active CUA REPL. Creating the runner is
 * observation-only; native activation happens only when `activate()` is
 * explicitly called after the owner Start handoff.
 */
function createCanonicalPracticeRunner({
  modules,
  draftTab,
  panelTab,
  room,
  freshlyVerifiedRules,
  projectionCatalog,
  sessionId,
  ownerToken,
  ownTeam,
  expectedSeat,
  requestOperator,
  requestControl,
  onProgress,
  now = Date.now,
  historyPollMs = 200,
  historyPolls = 8,
  hydrationPollMs = 100,
  hydrationPolls = 30,
  firstFramePollMs = 100,
  firstFrameTimeoutMs = 5_000,
  historyScroll = null,
} = {}) {
  const required = [
    "createBrowserObserver", "createViewSwitcher", "inspectGridKinds", "inspectNativeControls",
    "activateDraft", "createPanelOperator", "clickExactDraftRow", "createOperatorRuntime",
    "runForegroundBatch", "recoverAfterStop", "filterLiveAvailablePlayers",
  ];
  if (!modules || required.some((key) => typeof modules[key] !== "function")) {
    throw new TypeError(`modules must provide: ${required.join(", ")}`);
  }
  if (!draftTab || !draftTab.playwright || !panelTab || !panelTab.playwright) throw new TypeError("visible draftTab and panelTab handles are required");
  if (!sessionId || !ownerToken || !ownTeam || !Number.isInteger(expectedSeat) || expectedSeat < 1 || expectedSeat > 14) {
    throw new TypeError("sessionId, private ownerToken, ownTeam, and verified expectedSeat (1-14) are required");
  }
  if (!room || typeof room.practice !== "boolean") throw new TypeError("this runner requires an explicit practice/real room identity");
  if (!freshlyVerifiedRules || !projectionCatalog) throw new TypeError("freshly verified rules and a captured projection catalog are required");

  const counter = { n: 0 };
  const observer = modules.createBrowserObserver({
    tab: draftTab,
    sessionId,
    room,
    freshlyVerifiedRules,
    projectionCatalog,
    counter,
    historyScroll,
  });
  const switcher = modules.createViewSwitcher({ tab: draftTab });
  const rawOperator = requestOperator || modules.createPanelOperator(panelTab, { now });
  const ownedOperator = (payload) => rawOperator({ ...payload, owner_token: ownerToken });
  const control = requestControl || ((payload) => ownedOperator({
    operation: "pause",
    session_id: payload.session_id || sessionId,
    reason: payload.reason || "canonical runner recovery",
  }));

  const reports = [];
  const latencyRows = [];
  const ownTurnFrames = new Map();
  const clickTimes = new Map();
  let activated = false;
  let activationAttempted = false;
  let activationInFlight = false;

  async function syncRevision() {
    const status = await ownedOperator({ operation: "status", session_id: sessionId });
    if (!status || status.error || status.status === "error") throw new Error(`could not seed observer revision from coordinator status: ${(status && status.error) || "no response"}`);
    const revision = Number(status.latest_revision);
    if (Number.isInteger(revision) && revision >= 0) counter.n = Math.max(counter.n, revision);
    return status;
  }

  const pause = (ms) => (ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve());

  // State-based wait: ESPN briefly renders an empty Players grid after a view
  // switch or a pick (live room 1000000015 published a candidate-less own-turn
  // frame at pick 16 and paused).  An in-page caller has no tool-call latency
  // to hide that render, so re-read until rows hydrate, bounded.
  async function untilCandidates(capture) {
    let frame = await capture();
    for (let attempt = 0; attempt < hydrationPolls && frame.phase !== "complete" && !(frame.available_players || []).length; attempt += 1) {
      await pause(hydrationPollMs);
      frame = await capture();
    }
    return frame;
  }

  async function captureFrame() {
    // A completed room may remove Players entirely. Preserve the observer's
    // explicit completion evidence instead of waiting for a vanished grid.
    let frame = await observer.captureFastFrame();
    if (frame.phase === "complete") return modules.filterLiveAvailablePlayers(frame).observation;
    let kinds = await draftTab.playwright.locator("body").evaluate(modules.inspectGridKinds);
    if (!kinds.includes("players")) await switcher.showPlayers();
    if (!kinds.includes("players") || !(frame.available_players || []).length) frame = await untilCandidates(() => observer.captureFastFrame());
    if (!frame.history_complete) {
      await switcher.showPickHistory();
      let snapshot = null;
      for (let attempt = 0; attempt < historyPolls; attempt += 1) {
        snapshot = await observer.captureHistory({ expectedCurrentPick: frame.current_pick ?? null, retain: false });
        if (snapshot && snapshot.history_complete) break;
        if (historyPollMs > 0) await new Promise((resolve) => setTimeout(resolve, historyPollMs));
      }
      if (!snapshot || !snapshot.history_complete) throw new Error("Pick History did not hydrate to a complete snapshot; stop and inspect the visible room");
      observer.setRetainedHistory(snapshot);
      await switcher.showPlayers();
      frame = await untilCandidates(() => observer.captureFrame({ historySnapshot: snapshot }));
    }
    const filtered = modules.filterLiveAvailablePlayers(frame);
    return filtered.observation;
  }

  async function readPreflight() {
    const controls = await draftTab.playwright.locator("body").evaluate(modules.inspectNativeControls);
    return { native_timer_seconds: controls.native_timer_seconds, expected_seat: expectedSeat };
  }

  let preparedPausedFrame = null;

  async function prepareActivation() {
    await syncRevision();
    preparedPausedFrame = await captureFrame();
    return preparedPausedFrame;
  }

  async function activate() {
    if (room.practice !== true) throw new Error("native activation is disabled for a real draft room");
    if (activated || activationInFlight || (activationAttempted && !activated)) {
      throw new Error("native activation is one-shot; inspect the returned result and room state before any further action");
    }
    const pausedObservation = preparedPausedFrame;
    if (!pausedObservation) throw new TypeError("call prepareActivation() while the practice room is paused before activating");
    if (pausedObservation) {
      if (pausedObservation.session_id !== sessionId) throw new Error("prepared activation observation belongs to another session");
      const observedAt = Date.parse(pausedObservation.observed_at || "");
      const currentValue = now();
      const currentTime = currentValue instanceof Date ? currentValue.getTime() : Number(currentValue);
      const ageMs = currentTime - observedAt;
      if (!Number.isFinite(ageMs) || ageMs < 0 || ageMs > 10_000) throw new Error("prepared activation observation is stale or dated in the future; capture a fresh paused frame");
    }
    activationInFlight = true;
    try {
      // The one-shot visible click moves to the anchored native-control panel.
      // The paused observation stays unchanged so activation freshness checks
      // cannot be defeated by re-stamping it after the view switch.
      await switcher.showLeagueManager();
      // After the native Resume, ESPN needs a moment to start the clock and
      // render Players; the 2026-10-13 live room was read 6 ms after the
      // click (clock "--:--", no rows) and local start was refused.  Wait
      // for a running frame, bounded well inside the activation budget.
      const firstRunningFrame = async () => {
        const clock = () => { const value = now(); return value instanceof Date ? value.getTime() : Number(value); };
        const deadline = clock() + firstFrameTimeoutMs;
        // ESPN retains the Players, Pick History, and League Manager grids in
        // the DOM while only one is visible.  After native Resume the click
        // leaves us on League Manager, so explicitly return to Players before
        // the first running capture; inspectGridKinds is visibility-aware but
        // cannot make the active tab render itself.
        await switcher.showPlayers();
        let frame = await captureFrame();
        while (!(Number.isFinite(frame.seconds_remaining) && frame.seconds_remaining >= 0 && (frame.available_players || []).length && frame.complete) && clock() < deadline) {
          await pause(firstFramePollMs);
          frame = await captureFrame();
        }
        return frame;
      };
      const result = await modules.activateDraft({
        tab: draftTab,
        observer,
        observe: firstRunningFrame,
        observePaused: () => pausedObservation,
        requestOperator: ownedOperator,
        sessionId,
        ownerToken,
        expectedSeat,
      });
      activationAttempted = Boolean(result && result.native_resume_clicked);
      activated = Boolean(result && result.status === "activated");
      return result;
    } finally {
      activationInFlight = false;
    }
  }

  async function runBatch(options = {}) {
    if (!activated) throw new Error("runBatch is disabled until activate() returns status=activated after the explicit Start handoff");
    await syncRevision();
    const runtime = modules.createOperatorRuntime({
      panelTab,
      draftTab,
      observeCurrent: captureFrame,
      postClickConfirmation: {
        observer,
        showPickHistory: switcher.showPickHistory,
        showPlayers: switcher.showPlayers,
        now,
      },
      requestOperator: ownedOperator,
      clickPlayer: async (command) => {
        clickTimes.set(command.action_id, now());
        return modules.clickExactDraftRow(draftTab, command);
      },
      now,
    });
    const startedAt = new Date(now()).toISOString();
    const result = await modules.runForegroundBatch({
      ...options,
      runtime,
      sessionId,
      ownTeam,
      observe: captureFrame,
      observeFinal: captureFrame,
      readFinalCoordinatorStatus: () => ownedOperator({ operation: "status", session_id: sessionId }),
      onProgress: async (event) => {
        if (event.event === "own-turn" && event.observation) ownTurnFrames.set(event.observation.current_pick, event.observation);
        if (event.event === "own-result" && event.result && event.result.outcome === "confirmed") {
          const command = event.result.confirmed_action || event.result.command || {};
          const frame = ownTurnFrames.get(command.expected_pick) || {};
          const clickAt = clickTimes.get(command.action_id);
          const observedAt = Date.parse(frame.observed_at || "");
          if (Number.isFinite(observedAt) && Number.isFinite(clickAt)) {
            latencyRows.push({
              pick: command.expected_pick,
              player: command.player_name,
              observation_to_click_ms: clickAt - observedAt,
              click_to_confirm_ms: now() - clickAt,
              clock_seconds: frame.seconds_remaining,
              history_and_roster_confirmed: true,
            });
          }
          clickTimes.delete(command.action_id);
          ownTurnFrames.delete(command.expected_pick);
        }
        if (typeof onProgress === "function") await onProgress(event);
      },
    });
    reports.push({ started_at: startedAt, ended_at: new Date(now()).toISOString(), status: result.status, reason: result.reason || null, cycles: result.cycles, own_picks_confirmed: result.own_picks_confirmed });
    return result;
  }

  async function recover(stop, { readPreflight: preflightReader } = {}) {
    if (room.practice !== true) throw new Error("native recovery is disabled for a real draft room");
    if (!activated) throw new Error("recovery is disabled until practice activation succeeds");
    if (typeof control !== "function") throw new TypeError("a coordinator control transport is required for explicit recovery");
    const recovered = await modules.recoverAfterStop({
      stop,
      sessionId,
      ownTeam,
      ownerToken,
      requestOperator: ownedOperator,
      requestControl: (payload) => control({ ...payload, session_id: sessionId }),
      capture: captureFrame,
      readPreflight: preflightReader || readPreflight,
      now,
    });
    if (recovered && recovered.recovered) await syncRevision();
    return recovered;
  }

  function evidenceSnapshot() {
    return {
      latency_rows: latencyRows.map((row) => ({ ...row })),
      foreground: {
        invocation_count: reports.length,
        invocations: reports.map((report) => ({ ...report })),
      },
      provenance: "observed canonical browser-runner callbacks; distinguish practice from real room in the report",
      release_limits: [],
      extra: { runner: "canonical-practice-runner-v1", batches: reports.map((report) => ({ ...report })) },
    };
  }

  return Object.freeze({ observer, switcher, captureFrame, readPreflight, prepareActivation, activate, runBatch, recover, evidenceSnapshot });
}

module.exports = { createCanonicalPracticeRunner };
