"use strict";

/**
 * Deterministic accelerated clock for offline draft fixtures.
 *
 * EspnRoomSimulator intentionally has no wall clock.  This adapter supplies
 * one around its public API so a test can advance time while the observer is
 * capturing or confirming a pick.  Opponent turns are scheduled independently
 * of observer calls; native timeout/autopick is represented by explicit
 * synthetic events.  It does not model a real ESPN timer or background-tab
 * scheduling, and callers should keep the metadata returned by report().
 */

const { createFixtureTab } = require("../dom_fixture.js");
const { EspnRoomSimulator } = require("../room_simulator.js");

const DEFAULT_START = Date.parse("2026-10-13T00:00:00.000Z");

function finite(value, label) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new TypeError(`${label} must be finite`);
  return number;
}

function positive(value, label) {
  const number = finite(value, label);
  if (number <= 0) throw new RangeError(`${label} must be positive`);
  return number;
}

/**
 * Wraps an EspnRoomSimulator with a controlled virtual deadline.
 *
 * `opponentDelayMs` is an accelerated schedule: an opponent normally picks
 * before the deadline at that offset, while an opponent left pending at the
 * deadline receives a synthetic timeout pick.  If our team reaches a
 * deadline, `nativeAutopick` chooses the best remaining player through the
 * simulator's public `draft` method; otherwise the turn remains expired so
 * the operator's expired-clock guard can be tested.
 */
class AdvancingClockFixture {
  constructor({
    catalog,
    order,
    ownTeam,
    room = { league_id: "1000000012", season_id: "2027", team_id: "15" },
    timerSeconds = 30,
    opponentDelayMs = null,
    nativeAutopick = false,
    paused = true,
    initialNowMs = DEFAULT_START,
    readLatencyMs = 0,
    simulatorOptions = {},
  } = {}) {
    if (!Array.isArray(catalog) || catalog.length < 140) throw new TypeError("catalog must contain at least 140 players");
    if (!Array.isArray(order) || order.length !== 14) throw new TypeError("order must contain 14 teams");
    if (!order.includes(ownTeam)) throw new TypeError("ownTeam must be present in order");
    this.timerSeconds = positive(timerSeconds, "timerSeconds");
    this.timerMs = this.timerSeconds * 1000;
    this.opponentDelayMs = positive(opponentDelayMs === null ? Math.max(1, this.timerMs / 2) : opponentDelayMs, "opponentDelayMs");
    if (this.opponentDelayMs >= this.timerMs) throw new RangeError("opponentDelayMs must be less than timerSeconds");
    this.readLatencyMs = finite(readLatencyMs, "readLatencyMs");
    if (this.readLatencyMs < 0) throw new RangeError("readLatencyMs must be non-negative");
    this.nowMs = finite(initialNowMs, "initialNowMs");
    this.ownTeam = ownTeam;
    this.order = [...order];
    this.nativeAutopick = Boolean(nativeAutopick);
    this.events = [];
    this.controlEvents = [];
    this.expired = false;
    this.turnStartedAtMs = this.nowMs;
    this.turnDeadlineMs = this.nowMs + this.timerMs;
    this._lastPickCount = 0;
    this._processing = false;

    // The adapter only composes the simulator.  It deliberately does not
    // reach into private renderer state or alter room_simulator.js.
    this.simulator = new EspnRoomSimulator({
      ...simulatorOptions,
      catalog,
      order,
      ownTeam,
      room,
      timerSeconds: this.timerSeconds,
      paused,
    });
    this.simulator.autopick = this.nativeAutopick;
    this._syncClock();
  }

  get synthetic() { return true; }
  get clockMode() { return "controlled_virtual_accelerated"; }
  get now() { return this.nowMs; }
  get picks() { return this.simulator.picks; }
  get clicks() { return this.simulator.clicks; }
  get complete() { return this.simulator.complete; }
  get currentPick() { return this.simulator.currentPick; }
  get ownPickNumbers() { return this.simulator.ownPickNumbers; }
  get ownTurn() { return this.simulator.ownTurn; }
  get onClockTeam() { return this.simulator.onClockTeam; }
  get clock() { return this.simulator.clock; }
  get paused() { return this.simulator.paused; }
  get autopick() { return this.simulator.autopick; }
  get room() { return this.simulator.room; }

  /** Date.now-compatible clock for the checked-in observer/runtime modules. */
  nowFn = () => this.nowMs;

  _event(type, detail = {}) {
    const event = {
      type,
      synthetic: true,
      at_ms: this.nowMs,
      at: new Date(this.nowMs).toISOString(),
      ...detail,
    };
    this.events.push(event);
    return event;
  }

  _syncClock() {
    if (this.simulator.complete) {
      this.simulator.clock = 0;
      return;
    }
    const remaining = this.expired ? 0 : Math.max(0, this.turnDeadlineMs - this.nowMs);
    this.simulator.clock = Math.ceil(remaining / 1000);
  }

  _startTurn() {
    this.turnStartedAtMs = this.nowMs;
    this.turnDeadlineMs = this.nowMs + this.timerMs;
    this.expired = false;
    this._syncClock();
  }

  _adoptExternalPicks() {
    const count = this.simulator.picks.length;
    if (count < this._lastPickCount) throw new Error("simulator pick history moved backwards");
    if (count === this._lastPickCount) return false;
    const added = this.simulator.picks.slice(this._lastPickCount);
    this._lastPickCount = count;
    for (const pick of added) {
      this._event("external_pick_observed", {
        source: pick.team === this.ownTeam ? "runner_or_native_click" : "external_fixture",
        pick_number: pick.pick_number,
        team: pick.team,
        player_id: pick.player_id,
      });
    }
    this._startTurn();
    return true;
  }

  _recordSimulatorPick(type, source, beforeCount) {
    const added = this.simulator.picks.slice(beforeCount);
    this._lastPickCount = this.simulator.picks.length;
    const pick = added[added.length - 1] || null;
    this._event(type, {
      source,
      pick_number: pick && pick.pick_number,
      team: pick && pick.team,
      player_id: pick && pick.player_id,
    });
    this._startTurn();
    return pick;
  }

  _pickOpponent(type = "synthetic_opponent_pick", source = "accelerated_opponent_schedule") {
    const before = this.simulator.picks.length;
    const moved = this.simulator.stepOpponent();
    if (!moved) return false;
    this._recordSimulatorPick(type, source, before);
    return true;
  }

  _nativeAutopick() {
    if (!this.simulator.ownTurn || this.simulator.complete) return false;
    const available = this.simulator.available();
    if (!available.length) return false;
    const before = this.simulator.picks.length;
    this.simulator.draft(available[0].player_id, this.ownTeam);
    this._recordSimulatorPick("synthetic_native_autopick", "simulated_native_autopick", before);
    return true;
  }

  _nextDueMs() {
    if (this.simulator.paused || this.simulator.complete || this.expired) return Number.POSITIVE_INFINITY;
    if (this.simulator.ownTurn) return this.turnDeadlineMs;
    return Math.min(this.turnStartedAtMs + this.opponentDelayMs, this.turnDeadlineMs);
  }

  _processDue() {
    if (this.simulator.paused || this.simulator.complete) return false;
    if (this._adoptExternalPicks()) return true;
    if (this.simulator.ownTurn) {
      if (this.nowMs < this.turnDeadlineMs) return false;
      if (this.nativeAutopick) return this._nativeAutopick();
      this.expired = true;
      this._syncClock();
      this._event("synthetic_native_timeout", {
        source: "simulated_native_timer",
        pick_number: this.simulator.currentPick,
        team: this.ownTeam,
        autopick: false,
      });
      return true;
    }
    if (this.nowMs >= this.turnStartedAtMs + this.opponentDelayMs) {
      return this._pickOpponent();
    }
    if (this.nowMs >= this.turnDeadlineMs) {
      return this._pickOpponent("synthetic_opponent_timeout_autopick", "simulated_opponent_timeout");
    }
    return false;
  }

  /** Advance controlled time and process every due independent event. */
  advance(milliseconds, { reason = "test_clock_advance" } = {}) {
    const delta = finite(milliseconds, "milliseconds");
    if (delta < 0) throw new RangeError("milliseconds must be non-negative");
    if (this._processing) throw new Error("reentrant clock advance");
    this._processing = true;
    try {
      const target = this.nowMs + delta;
      let iterations = 0;
      while (this.nowMs < target) {
        if (iterations++ > 1000) throw new Error("synthetic clock exceeded event guard");
        this._adoptExternalPicks();
        const due = this._nextDueMs();
        if (!Number.isFinite(due) || due > target) {
          this.nowMs = target;
          break;
        }
        this.nowMs = Math.max(this.nowMs, due);
        const progressed = this._processDue();
        if (!progressed) {
          this.nowMs = target;
          break;
        }
      }
      if (delta === 0) this._processDue();
      this._syncClock();
      this._event("clock_advanced", { source: reason, elapsed_ms: delta });
      return this.nowMs;
    } finally {
      this._processing = false;
    }
  }

  /** Explicitly run the scheduler without advancing time. */
  sync() {
    this._adoptExternalPicks();
    this._processDue();
    this._syncClock();
    return this.nowMs;
  }

  resume() {
    this.simulator.resume();
    this.controlEvents.push(this._event("synthetic_native_resume", { source: "fixture_control" }));
    this._startTurn();
    return this.nowMs;
  }

  pause() {
    this.simulator.pause();
    this.controlEvents.push(this._event("synthetic_native_pause", { source: "fixture_control" }));
    this._syncClock();
    return this.nowMs;
  }

  setAutopick(enabled) {
    this.simulator.autopick = Boolean(enabled);
    this.nativeAutopick = this.simulator.autopick;
    this._event("synthetic_autopick_toggle", { source: "fixture_control", autopick: this.nativeAutopick });
    return this.nativeAutopick;
  }

  /** Return a current DOM body after the configured synthetic read latency. */
  read({ latencyMs = this.readLatencyMs, reason = "dom_read" } = {}) {
    if (latencyMs) this.advance(latencyMs, { reason });
    else this.sync();
    return this.simulator.liveBody();
  }

  /** Playwright-like visible tab; every DOM resolution passes through read(). */
  tab({ readLatencyMs = this.readLatencyMs, onClick = null } = {}) {
    const calls = [];
    return createFixtureTab({
      getBody: () => this.read({ latencyMs: readLatencyMs }),
      url: () => this.simulator.url(),
      onClick: async (element, selector) => {
        this.sync();
        if (typeof onClick === "function") await onClick(element, selector);
      },
      calls,
    });
  }

  report() {
    return {
      synthetic: true,
      clock_mode: this.clockMode,
      timer_seconds: this.timerSeconds,
      real_30_second_endurance_claim: false,
      now_ms: this.nowMs,
      current_pick: this.simulator.currentPick,
      complete: this.simulator.complete,
      picks: this.simulator.picks.length,
      own_picks: this.simulator.ownPickNumbers.length,
      native_autopick: this.simulator.autopick,
      expired: this.expired,
      events: [...this.events],
    };
  }
}

module.exports = { AdvancingClockFixture, DEFAULT_START };
