"use strict";

/**
 * Offline ESPN draft-room simulator for dress rehearsals (issue #43).
 *
 * It renders the three views the live runbook switches between - Players,
 * Pick History and League Manager - as HTML parsed by `dom_fixture.js`, so the
 * canonical observer and activation callbacks run against real DOM structure.
 * The selectors and shapes mirror those the reviewed modules read from ESPN:
 * `li.pick-message__container` Feed rows, `[role="grid"]` Players/History grids
 * with `button[data-player-id]`, a POS roster table whose full name lives in
 * the `.player-column` title, `.on-the-clock` / `.clock__digits`, and the
 * `.autoPick-toggle` sidebar checkbox.
 *
 * Injectable faults reproduce the failure modes seen in live practice rooms:
 *   feedWindow            virtualized Feed that renders only the latest rows
 *   historyHydrationReads Pick History renders an empty grid for N reads
 *   abbreviateRoster      roster cells show "N. Lastname"; full name in title
 *   resumeMode            "present" | "absent" | "league_manager_only" | "duplicate" |
 *                         "hidden_duplicate" | "disabled"
 *   confirmationLagReads  after our pick the next N DOM reads still show the pre-click room
 *                         (a post-click read-back racing DOM hydration; see #42)
 *   strayResume           a visible Resume button OUTSIDE the League Manager region
 *   resumeLabel           native Resume label casing
 *
 * No network, browser, or ESPN access.  Time is virtual: nothing here sleeps.
 */

const { createFixtureTab, parseHtml } = require("./dom_fixture.js");

const HEADSHOT = (id) => `https://a.espncdn.com/i/headshots/nba/players/full/${id}.png`;
const STAT_COLUMNS = ["FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"];

function escapeHtml(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function abbreviate(name) {
  const parts = String(name).split(" ");
  return parts.length > 1 ? `${parts[0][0]}. ${parts.slice(1).join(" ")}` : name;
}

class EspnRoomSimulator {
  constructor({
    catalog,
    order,
    ownTeam,
    room = { league_id: "1000000012", season_id: "2027", team_id: "15" },
    heading = "ESPN Fantasy Basketball Draft - Practice Draft for Example League",
    timerSeconds = 90,
    feedWindow = 31,
    historyHydrationReads = 0,
    abbreviateRoster = true,
    resumeMode = "present",
    resumeLabel = "Resume",
    strayResume = false,
    tabsMode = "normal",
    noRegion = false,
    view = "players",
    paused = true,
    playersWindow = 14,
    initialPickCount = 0,
    rules = null,
    confirmationLagReads = 0,
    resumeSettleReads = 0,
    pickSettleReads = 0,
    historyWindow = 0,
  } = {}) {
    this.catalog = catalog;
    this.order = order;
    this.ownTeam = ownTeam;
    this.room = room;
    this.heading = heading;
    this.timerSeconds = timerSeconds;
    this.feedWindow = feedWindow;
    this.historyHydrationReads = historyHydrationReads;
    this.abbreviateRoster = abbreviateRoster;
    this.resumeMode = resumeMode;
    this.resumeLabel = resumeLabel;
    this.strayResume = strayResume;
    this.tabsMode = tabsMode;
    this.noRegion = noRegion;
    this.view = view;
    this.paused = paused;
    this.playersWindow = playersWindow;
    this.initialPickCount = initialPickCount;
    this.rules = rules || {
      teams: 14,
      rounds: 10,
      roster: { C: 1, G: 2, F: 2, UTIL: 1, BE: 4, IR: 3 },
      scoring: { FGM: 2, FGA: -1, FTM: 1, FTA: -1, "3PM": 1, REB: 2, AST: 2, STL: 4, BLK: 4, TO: -2, EJ: -5, PF: -1, TF: -2, DD: 3, TD: 5, QD: 100, PTS: 1 },
    };
    this.confirmationLagReads = confirmationLagReads;
    // Reads after a native Resume that still show "--:--" and no Players rows (live ESPN, 2026-10-13).
    this.resumeSettleReads = resumeSettleReads;
    // Reads after any pick that render an empty Players grid (live ESPN pick 16, 2026-10-13).
    this.pickSettleReads = pickSettleReads;
    this.gridSettleRemaining = 0;
    // Rendered Pick History rows (0 = all); live ESPN virtualizes ~14 rows.
    this.historyWindow = historyWindow;
    this.historyTop = 0;
    this._gridEmpty = false;
    this.settleRemaining = 0;
    this._settling = false;
    this.lagRemaining = 0;
    this._lagging = false;
    this.picks = [];
    this.clock = timerSeconds;
    this.autopick = false;
    this.showDrafted = false;
    this.playersTop = 0;
    this.playersColumnPage = "left";
    this.scrollEvents = [];
    this.playersRowsRendered = new Set();
    this.vanished = new Set();
    this.hydrationRemaining = 0;
    this.clicks = [];
    this.resumeClicks = 0;
    this.version = 0;
    this._cache = null;
    this.rankOrder = [...catalog].sort((a, b) => a.draft_rank - b.draft_rank);
    for (let index = 0; index < initialPickCount; index += 1) {
      const player = this.available()[0];
      if (!player) break;
      this.draft(player.player_id, this.onClockTeam);
    }
  }

  // --- draft state ---------------------------------------------------------
  teamForPick(pick) {
    const round = Math.floor((pick - 1) / 14);
    const offset = (pick - 1) % 14;
    return this.order[round % 2 === 0 ? offset : 13 - offset];
  }

  get currentPick() { return this.picks.length + 1; }
  get complete() { return this.picks.length >= 140; }
  get onClockTeam() { return this.complete ? null : this.teamForPick(this.currentPick); }
  get ownTurn() { return !this.complete && this.onClockTeam === this.ownTeam; }
  get ownPickNumbers() { return this.picks.filter((pick) => pick.team === this.ownTeam).map((pick) => pick.pick_number); }

  available(picks = this.picks) {
    const taken = new Set(picks.map((pick) => pick.player_id));
    return this.rankOrder.filter((player) => !taken.has(player.player_id));
  }

  /** Rows ESPN shows in the Players grid: top of the rank order, minus vanished rows. */
  visibleAvailable(picks = this.picks) {
    return this.available(picks).filter((player) => !this.vanished.has(player.player_id)).slice(0, this.playersWindow);
  }

  player(playerId) { return this.catalog.find((player) => player.player_id === playerId) || null; }

  draft(playerId, team) {
    if (this.complete) throw new Error("draft is complete");
    if (team !== this.onClockTeam) throw new Error(`${team} is not on the clock (${this.onClockTeam})`);
    const player = this.player(playerId);
    if (!player || !this.available().some((entry) => entry.player_id === playerId)) throw new Error(`player ${playerId} is not available`);
    this.picks.push({ pick_number: this.currentPick, team, player_id: player.player_id, player_name: player.name });
    if (team === this.ownTeam && this.confirmationLagReads > 0) this.lagRemaining = this.confirmationLagReads;
    this.clock = this.timerSeconds;
    if (this.pickSettleReads > 0) this.gridSettleRemaining = this.pickSettleReads;
    this._touch();
  }

  /** Opponent bots take the best remaining player when the clock is theirs and the room is running. */
  stepOpponent() {
    if (this.paused || this.complete || this.ownTurn) return false;
    this.draft(this.available()[0].player_id, this.onClockTeam);
    return true;
  }

  resume() { this.paused = false; this.settleRemaining = this.resumeSettleReads; this._touch(); }
  pause() { this.paused = true; this._touch(); }
  /** Wheel-scroll the virtualized Pick History grid (30 px per row). */
  scrollHistory(deltaY) {
    this.scrollEvents.push({ grid: "history", deltaX: 0, deltaY });
    this.historyTop = Math.max(0, Math.min(this.historyTop + Math.trunc(deltaY / 30), Math.max(0, this._shown.length - this.historyWindow)));
    this._touch();
  }

  /** Wheel-scroll the virtualized Players grid used by paused catalog capture. */
  scrollPlayers(deltaX, deltaY) {
    this.scrollEvents.push({ grid: "players", deltaX, deltaY, column: this.playersColumnPage });
    if (deltaX > 0) this.playersColumnPage = "right";
    else if (deltaX < 0) this.playersColumnPage = "left";
    const sourceCount = this.showDrafted ? this.rankOrder.length : this.available(this._shown).length;
    const maxTop = Math.max(0, sourceCount - this.playersWindow);
    // Keep adjacent virtualized windows overlapping, as a real fixed-data-table
    // does.  The capture loop merges rows by stable player id and must not skip
    // rows between wheel events.
    if (deltaY > 0) this.playersTop = Math.min(maxTop, this.playersTop + Math.max(1, Math.trunc(deltaY / 96)));
    else if (deltaY < 0) this.playersTop = Math.max(0, this.playersTop - Math.max(1, Math.trunc(-deltaY / 96)));
    this._touch();
  }

  setView(view) {
    if (!["players", "history", "league_manager", "rules"].includes(view)) throw new Error(`unknown view ${view}`);
    if (view === "history" && this.view !== "history") this.hydrationRemaining = this.historyHydrationReads;
    if (view === "history" && this.view !== "history") this.historyTop = 0;
    if (view === "players" && this.view !== "players") {
      this.playersTop = 0;
      this.playersColumnPage = "left";
    }
    this.view = view;
    this._touch();
  }
  vanishRow(playerId) { this.vanished.add(playerId); this._touch(); }
  _touch() { this.version += 1; this._cache = null; }

  // --- rendering -----------------------------------------------------------
  _resumeControls(place) {
    if (!this.paused) return '<button type="button">Pause</button>';
    const label = escapeHtml(this.resumeLabel);
    const resume = `<button type="button" class="resume-btn">${label}</button>`;
    if (this.resumeMode === "absent") return '<button type="button">Draft Settings</button>';
    if (this.resumeMode === "league_manager_only") return place === "league_manager" ? resume : '<button type="button">Draft Settings</button>';
    if (this.resumeMode === "duplicate") return resume + resume;
    if (this.resumeMode === "hidden_duplicate") return resume + `<button type="button" style="display:none">${label}</button>`;
    if (this.resumeMode === "disabled") return `<button type="button" disabled>${label}</button>`;
    return resume;
  }

  _controlRegion() {
    const options = [30, 60, 90, 120].map((seconds) => `<option value="${seconds * 1000}"${seconds === this.timerSeconds ? " selected" : ""}>${seconds} seconds</option>`).join("");
    const stray = this.strayResume && this.paused ? '<div class="toast"><button type="button">Resume</button></div>' : "";
    const wrapper = this.noRegion ? "div" : 'div class="league-manager-controls" data-testid="league-manager-controls"';
    return `${stray}<${wrapper}>
      <label for="pickTimer">Pick Timer</label><select id="pickTimer" aria-label="Pick Timer">${options}</select>
      ${this._resumeControls(this.view)}
    </div>`;
  }

  _tabs() {
    if (this.tabsMode === "absent") return "";
    const tab = (label, view) => `<button type="button" role="tab" data-view="${view}">${label}</button>`;
    const extra = this.tabsMode === "duplicate_players" ? tab("Players", "players") : "";
    const stray = '<div class="sidebar-tabs" role="tablist"><button type="button" role="tab">Roster</button></div>';
    return `<div role="tablist" class="draft-view-tabs">${tab("Rules", "rules")}${tab("PLAYERS", "players")}${extra}${tab("Pick History", "history")}${tab("League Manager", "league_manager")}</div>${stray}`;
  }

  _feed() {
    const rows = this._shown.slice(-this.feedWindow).reverse().map((pick) => {
      const round = Math.floor((pick.pick_number - 1) / 14) + 1;
      const inRound = ((pick.pick_number - 1) % 14) + 1;
      return `<li class="pick-message__container"><img src="${HEADSHOT(pick.player_id)}"><div class="playerinfo__playername" title="${escapeHtml(pick.player_name)}">${escapeHtml(pick.player_name)}</div><div class="pick-info">R${round}, P${inRound}<span> - ${escapeHtml(pick.team)}</span></div></li>`;
    });
    return `<ul class="pick-feed">${rows.join("")}</ul>`;
  }

  _roster() {
    const mine = this._shown.filter((pick) => pick.team === this.ownTeam).map((pick) => this.player(pick.player_id));
    const slots = ["C", "G", "G", "F", "F", "UTIL", "BE", "BE", "BE", "BE"];
    const rows = slots.map((slot, index) => {
      const player = mine[index];
      if (!player) return `<tr><td>${slot}</td><td>Empty</td></tr>`;
      const shown = this.abbreviateRoster ? abbreviate(player.name) : player.name;
      return `<tr><td>${slot}</td><td><div class="player-column" title="${escapeHtml(player.name)}"><img src="${HEADSHOT(player.player_id)}"><span>${escapeHtml(shown)}</span></div></td></tr>`;
    });
    const ir = ["IR", "IR", "IR"].map((slot) => `<tr><td>${slot}</td><td>Empty</td></tr>`);
    return `<table class="roster-module"><thead><tr><th>POS</th><th>PLAYER</th></tr></thead><tbody>${rows.join("")}${ir.join("")}</tbody></table>`;
  }

  _playersGrid() {
    const headers = this.playersColumnPage === "right"
      ? ["RK", "Player", "TO", "EJ", "PF", "PTS", "ADP"]
      : ["RK", "Player", "FPTS", "GP", "FGM", "FGA", "FTM", "FTA", "3PM", "REB", "AST", "STL", "BLK", "TO"];
    const head = `<div role="row">${headers.map((header) => `<div role="columnheader">${header}</div>`).join("")}</div>`;
    const drafted = new Set(this._shown.map((pick) => pick.player_id));
    const source = this.showDrafted
      ? this.rankOrder.filter((player) => !this.vanished.has(player.player_id))
      : this.available(this._shown).filter((player) => !this.vanished.has(player.player_id));
    const visible = source.slice(this.playersTop, this.playersTop + this.playersWindow);
    if (this.showDrafted) for (const player of visible) this.playersRowsRendered.add(player.player_id);
    const body = (this._settling || this._gridEmpty ? [] : visible).map((player) => {
      const stats = player.stats || {};
      const isDrafted = drafted.has(player.player_id);
      const playerCell = `<div role="gridcell"><span class="playerinfo__playername" title="${escapeHtml(player.name)}">${escapeHtml(player.name)}</span><span class="playerinfo__playerteam">${escapeHtml(player.nba_team || "NBA")}</span>${(player.eligibility || []).map((position) => `<span class="positionPill">${escapeHtml(position)}</span>`).join("")}<img src="${HEADSHOT(player.player_id)}">${isDrafted ? "<span>Drafted</span>" : `<button type="button" data-player-id="${escapeHtml(player.player_id)}">Queue</button>`}</div>`;
      const cells = this.playersColumnPage === "right"
        ? [player.draft_rank, playerCell, stats.TO ?? "--", stats.EJ ?? "--", stats.PF ?? "--", stats.PTS ?? "--", player.adp ?? "--"]
        : [player.draft_rank, playerCell, stats.PTS ?? "--", player.projected_gp ?? "--", ...STAT_COLUMNS.map((key) => stats[key] ?? "--")];
      const rendered = cells.map((value, index) => (index === 1
        ? value
        : `<div role="gridcell">${value}</div>`)).join("");
      return `<div role="row">${rendered}</div>`;
    });
    const maxTop = Math.max(0, source.length - this.playersWindow);
    const thumbHeight = maxTop ? 22 : 480;
    const thumbTop = maxTop ? (480 - thumbHeight) * this.playersTop / maxTop : 0;
    return `<div class="draft-players-toggle"><label>Show Drafted<input type="checkbox" aria-label="Show drafted"${this.showDrafted ? " checked" : ""}></label></div><select aria-label="Stat View"><option selected>2027 Projected</option><option>2026 Stats</option></select><label><input type="radio" name="averaged-stats" value="averaged" checked>Averages</label><div role="grid" class="players-grid"><div class="fixedDataTableLayout_rowsContainer">${head}${body.join("")}</div><div class="ScrollbarLayout_mainVertical public_Scrollbar_main" style="height:480px"><div class="ScrollbarLayout_faceVertical public_Scrollbar_face" style="height:${thumbHeight}px;transform:translate3d(0px,${thumbTop}px,0px)"></div></div></div>`;
  }

  _rulesPanel() {
    const roster = this.rules.roster || {};
    const scoringLabels = {
      FGM: "Field Goals Made (FGM)", FGA: "Field Goals Attempted (FGA)", FTM: "Free Throws Made (FTM)", FTA: "Free Throws Attempted (FTA)",
      "3PM": "Three Pointers Made (TPM)", REB: "Rebounds (REB)", AST: "Assists (AST)", STL: "Steals (STL)", BLK: "Blocks (BLK)",
      TO: "Turnovers (TO)", EJ: "Ejections (E)", PF: "Personal Fouls (PF)", TF: "Technical Fouls (TF)", DD: "Double Doubles (DD)",
      TD: "Triple Doubles (TD)", QD: "Quadruple Doubles (QD)", PTS: "Points (PTS)",
    };
    const table = (caption, rows) => `<table><thead><tr><th>${caption}</th></tr></thead><tbody>${rows.map(([label, value]) => `<tr><td>${escapeHtml(label)}</td><td>${escapeHtml(value)}</td></tr>`).join("")}</tbody></table>`;
    return `<div class="clock__container"><div>RND 1 OF ${this.rules.rounds}</div><div><span>-</span><span>-</span></div></div><div role="tabpanel">${table("Basic Settings", [["League Manager", ""], ["League Name", this.heading.replace(/^ESPN Fantasy Basketball Draft - /, "")], ["Scoring Type", "Head to Head Points"], ["Teams in League", this.rules.teams]])}${table("Roster", [["Center (C)", roster.C ?? 1], ["Guard (G)", roster.G ?? 2], ["Forward (F)", roster.F ?? 2], ["Util (UTIL)", roster.UTIL ?? 1], ["Bench (BE)", roster.BE ?? 4], ["Injured Reserve (IR)", roster.IR ?? 3]])}${table("Scoring - All", Object.entries(this.rules.scoring || {}).map(([key, value]) => [scoringLabels[key] || key, value]))}${table("League Rules", [["Player Universe", "NBA"]])}</div>`;
  }

  _historyGrid() {
    const head = '<div role="row"><div role="columnheader">Pick</div><div role="columnheader">Player</div><div role="columnheader">Team</div></div>';
    const hydrated = this.hydrationRemaining <= 0;
    const shown = this.historyWindow > 0 ? this._shown.slice(Math.min(this.historyTop, Math.max(0, this._shown.length - this.historyWindow)), Math.min(this.historyTop, Math.max(0, this._shown.length - this.historyWindow)) + this.historyWindow) : this._shown;
    const rows = hydrated ? shown.map((pick) => `<div role="row"><div role="gridcell">${pick.pick_number}</div><div role="gridcell"><span class="playerinfo__playername" title="${escapeHtml(pick.player_name)}">${escapeHtml(pick.player_name)}</span><img src="${HEADSHOT(pick.player_id)}"></div><div role="gridcell">${escapeHtml(pick.team)}</div></div>`) : [];
    return `<div role="grid" class="history-grid">${head}${rows.join("")}</div>`;
  }

  /** Picks the DOM currently shows; lags the room by one pick right after ours when configured. */
  get _shown() { return this._lagging ? this.picks.slice(0, -1) : this.picks; }

  html() {
    const clock = this._settling ? "--:--" : `${String(Math.floor(this.clock / 60)).padStart(2, "0")}:${String(this.clock % 60).padStart(2, "0")}`;
    const header = this.complete && !this._lagging
      ? '<h2 role="heading">Your draft is complete</h2>'
      : `<div class="clock-wrap"><span class="on-the-clock">On the Clock: Pick ${this._shown.length + 1}</span><span class="team-name">${escapeHtml(this.teamForPick(this._shown.length + 1))}</span><span class="clock__digits">${clock}</span></div>`;
    let main = "";
    if (this.view === "league_manager") main = `<section class="league-manager-panel"><h3>League Manager</h3></section>`;
    else if (this.view === "rules") main = this._rulesPanel();
    else if (this.view === "history") main = this._historyGrid();
    else if (!this.complete || this._lagging) main = this._playersGrid();
    return `<h1>${escapeHtml(this.heading)}</h1>
      <select aria-label="Draft order">${this.order.map((team) => `<option>${escapeHtml(team)}</option>`).join("")}</select>
      ${header}
      <div class="autoPick-toggle"><input type="checkbox"${this.autopick ? " checked" : ""}></div>
      ${this._controlRegion()}
      ${this._tabs()}
      ${this._roster()}
      ${this._feed()}
      ${main}`;
  }

  /** Re-parse only when state changed. A history read consumes one hydration tick. */
  body() {
    this._settling = this.settleRemaining > 0;
    if (this._settling) {
      this.settleRemaining -= 1;
      this._cache = null;
      const settling = parseHtml(this.html());
      this._settling = false;
      return settling;
    }
    if (this.gridSettleRemaining > 0 && this.view === "players") {
      this.gridSettleRemaining -= 1;
      this._gridEmpty = true;
      this._cache = null;
      const empty = parseHtml(this.html());
      this._gridEmpty = false;
      return empty;
    }
    this._lagging = this.lagRemaining > 0;
    if (this._lagging) {
      this.lagRemaining -= 1;
      this._cache = null;
      return parseHtml(this.html());
    }
    if (this.view === "history" && this.hydrationRemaining > 0) {
      const rendered = parseHtml(this.html());
      this.hydrationRemaining -= 1;
      this._cache = null;
      return rendered;
    }
    if (!this._cache) this._cache = parseHtml(this.html());
    return this._cache;
  }

  url() {
    return `https://fantasy.espn.com/basketball/draft?leagueId=${this.room.league_id}&seasonId=${this.room.season_id}&teamId=${this.room.team_id}`;
  }

  /** Current body with the room's click handlers installed (what a live page exposes). */
  liveBody() {
    const body = this.body();
    for (const button of body.querySelectorAll("button[data-player-id]")) {
      button.onclick = () => {
        const playerId = button.getAttribute("data-player-id");
        this.clicks.push({ player_id: playerId, pick: this.currentPick, team: this.onClockTeam });
        if (!this.paused && this.ownTurn) this.draft(playerId, this.ownTeam);
      };
    }
    for (const button of body.querySelectorAll("button[data-view]")) {
      button.onclick = () => this.setView(button.getAttribute("data-view"));
    }
    for (const button of body.querySelectorAll("button.resume-btn")) {
      button.onclick = () => { this.resumeClicks += 1; this.resume(); };
    }
    for (const toggle of body.querySelectorAll(".draft-players-toggle")) {
      const handler = () => {
        this.showDrafted = !this.showDrafted;
        this.playersTop = 0;
        this.playersColumnPage = "left";
        this._touch();
      };
      const input = toggle.querySelector("input");
      const label = toggle.querySelector("label");
      if (input) input.onclick = handler;
      if (label) label.onclick = handler;
    }
    for (const grid of body.querySelectorAll('[role="grid"]')) {
      const header = grid.querySelector('[role="columnheader"]');
      if (header && header.textContent.trim().toUpperCase() === "RK" && !grid._wheelBound) {
        grid._wheelBound = true;
        // ESPN handles wheels inside the rows container. Dispatching on the
        // outer grid does not travel down to this listener.
        const wheelTarget = grid.querySelector(".fixedDataTableLayout_rowsContainer");
        wheelTarget.addEventListener("wheel", (event) => this.scrollPlayers(Number(event.deltaX) || 0, Number(event.deltaY) || 0));
        const track = grid.querySelector(".ScrollbarLayout_mainVertical");
        const face = grid.querySelector(".ScrollbarLayout_faceVertical");
        const maxTop = Math.max(0, (this.showDrafted ? this.rankOrder : this.available(this._shown)).filter((player) => !this.vanished.has(player.player_id)).length - this.playersWindow);
        const height = maxTop ? 22 : 480;
        const top = maxTop ? (480 - height) * this.playersTop / maxTop : 0;
        track.getBoundingClientRect = () => ({ left: 785, right: 800, top: 0, bottom: 480, width: 15, height: 480 });
        face.getBoundingClientRect = () => ({ left: 785, right: 800, top, bottom: top + height, width: 15, height });
      } else if (header && header.textContent.trim().toUpperCase() === "PICK" && !grid._wheelBound) {
        grid._wheelBound = true;
        grid.addEventListener("wheel", (event) => this.scrollHistory(Number(event.deltaY) || 0));
      }
    }
    return body;
  }

  /** Playwright-like tab over the live simulator. Clicks reach the DOM handlers installed here. */
  tab({ onClick = null } = {}) {
    const calls = [];
    return createFixtureTab({
      getBody: () => this.liveBody(),
      url: () => this.url(),
      onClick,
      calls,
    });
  }
}

module.exports = { EspnRoomSimulator, HEADSHOT };
