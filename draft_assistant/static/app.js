(() => {
  let sessionId = null;
  let editingSetup = false;
  let setupOptionsKey = null;
  // Checklist inputs: the last /api/setup reply plus local-only bookmark flag.
  let setupInfo = { configured: false, team: null, pending_source: null, team_options_ready: false, bookmarklet_seen: false };
  let bookmarkMarked = false;
  let setupPhaseText = "Loading…";
  const BOOKMARK_KEY = "fbl-advisor-bookmark-added";
  try { bookmarkMarked = Boolean(window.localStorage) && window.localStorage.getItem(BOOKMARK_KEY) === "1"; } catch (_error) { bookmarkMarked = false; }
  const $ = (id) => document.getElementById(id);
  const showError = (error) => { $("state").textContent = `Error: ${error}`; };
  function text(tag, value) { const node = document.createElement(tag); node.textContent = value == null ? "—" : String(value); return node; }
  async function api(path, body) {
    const headers = { "Content-Type": "application/json", "X-CSRF-Token": window.FBL_CSRF_TOKEN };
    const response = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || response.statusText);
    return value;
  }
  const summaryLine = (team) => `Set up for ${team.our_team} · league ${team.league_id} · season ${team.season_id} · team ${team.team_id}`;
  function renderChecklist() {
    const configured = Boolean(setupInfo.configured && setupInfo.team) && !editingSetup;
    const source = setupInfo.pending_source;
    const done = {
      1: bookmarkMarked || Boolean(setupInfo.bookmarklet_seen) || configured,
      2: Boolean(source) || configured,
      3: configured,
      4: false,
      5: false,
    };
    let current = 4;
    for (const step of [1, 2, 3]) { if (!done[step]) { current = step; break; } }
    for (let step = 1; step <= 5; step += 1) {
      const state = done[step] ? "done" : (step === current ? "current" : "upcoming");
      $(`step-${step}`).dataset.state = state;
      $(`step-${step}-badge`).textContent = state === "done" ? "Done" : state === "current" ? "Do this now" : (step === 5 ? "Later" : "Next");
    }
    $("bookmark-done").hidden = done[1];
    $("setup-team-status").hidden = !(Boolean(source) && !setupInfo.team_options_ready && !configured);
    if (source && !$("setup-url").dataset.touched) {
      $("setup-url").textContent = `Linked: league ${source.league_id}, season ${source.season_id}, team ${source.team_id}.`;
    }
    $("setup-fields").hidden = configured && !$("setup-toggle-steps").dataset.shown;
    $("setup-summary-bar").hidden = !configured;
    $("setup-edit").hidden = !configured;
    $("setup-toggle-steps").hidden = !configured;
    $("setup-help").hidden = configured;
    $("setup-title").textContent = configured ? "Setup complete" : (editingSetup ? "Change your league setup" : "Get started");
    if (configured) {
      $("setup-summary").textContent = summaryLine(setupInfo.team);
      $("setup-next").textContent = "Next: capture the player catalog in a practice draft (step 4), then use the bookmark in your real draft room on draft day.";
      setupPhaseText = "Setup is complete. Open your ESPN draft room and click the FBL Advisor bookmark when you are ready.";
    } else if (current === 1) setupPhaseText = "Setup step 1 of 3: add the FBL Advisor bookmark to Chrome.";
    else if (current === 2) setupPhaseText = "Setup step 2 of 3: paste your ESPN team page address to link your league.";
    else setupPhaseText = setupInfo.team_options_ready ? "Setup step 3 of 3: choose your team from the list below." : "Setup step 3 of 3: open an ESPN draft room and click the FBL Advisor bookmark, then pick your team in the box that appears there.";
    if (!sessionId) $("state").textContent = setupPhaseText;
  }
  function update(data) {
    if (data.setup_required) {
      document.querySelectorAll("#room-url, #practice, #connect, [data-control]").forEach((node) => { node.disabled = true; });
      renderChecklist();
      $("state").textContent = setupPhaseText;
      return;
    }
    document.querySelectorAll("#room-url, #practice, #connect").forEach((node) => { node.disabled = false; });
    const saved = data.configured_team;
    if (saved && !editingSetup) {
      setupInfo = { ...setupInfo, configured: true, team: saved };
      renderChecklist();
    }
    const status = data.sessions ? (data.sessions.find((item) => item.session_id === sessionId) || data.sessions[0]) : data;
    if (!status) { $("state").textContent = setupPhaseText; return; }
    sessionId = status.session_id || sessionId;
    const room = status.room || {};
    $("room-identity").textContent = `Connected league ${room.league_id || "unknown"}, season ${room.season_id || "unknown"}, team ${room.team_id || "unknown"}. Rules and observed identity must pass readiness below.`;
    $("state").textContent = `${status.state || "unknown"}: ${status.reason || ""}`;
    $("revision").textContent = status.latest_revision ?? "—";
    $("age").textContent = status.observation_age_seconds == null ? "—" : `${Number(status.observation_age_seconds).toFixed(1)}s`;
    const observation = status.observation || {};
    $("next-turn").textContent = observation.current_pick == null ? "—" : `${observation.current_pick} (${observation.on_clock_team || "unknown"})`;
    $("on-clock").textContent = observation.on_clock_team || "—";
    $("seconds").textContent = observation.seconds_remaining == null ? "—" : Number(observation.seconds_remaining).toFixed(1);
    const roster = observation.roster || [];
    const coverage = ["C", "G", "F"].map((position) => `${position}: ${roster.filter((player) => (player.eligibility || []).includes(position)).length}`).join(" · ");
    $("coverage").textContent = `${roster.length}/10 players · ${coverage} (eligibility counts)`;
    const readiness = status.readiness || {};
    const readinessNode = $("readiness");
    const freshness = status.projection_freshness;
    const freshnessNode = $("projection-freshness");
    freshnessNode.hidden = !freshness || (freshness.ok && !freshness.offline_fallback);
    freshnessNode.textContent = freshness ? "Projection data: " + freshness.status + (freshness.enforced ? " (blocks automation)" : " (warning only)") + ". " + (freshness.warnings || []).join("; ") : "";
    readinessNode.replaceChildren();
    if (readiness.ready) readinessNode.appendChild(text("li", "Ready for explicit start."));
    else (readiness.reasons || ["Readiness has not been evaluated."]).forEach((reason) => readinessNode.appendChild(text("li", reason)));
    const future = [];
    const pickOrder = observation.pick_order || [];
    const current = Number(observation.current_pick || 0);
    for (let offset = 0; offset < 4; offset += 1) {
      const index = current + offset;
      if (index > 0 && pickOrder.length) {
        const roundIndex = Math.floor((index - 1) / pickOrder.length);
        const seat = (roundIndex % 2 === 0) ? ((index - 1) % pickOrder.length) : (pickOrder.length - 1 - ((index - 1) % pickOrder.length));
        future.push(`${index}: ${pickOrder[seat] || "unknown"}`);
      }
    }
    $("next-turns").textContent = future.join("\n") || "—";
    const rosterNode = $("roster");
    rosterNode.replaceChildren();
    if (roster.length) {
      const rosterList = document.createElement("ul");
      roster.forEach((player) => rosterList.appendChild(text("li", `${player.name} (${(player.eligibility || []).join(", ") || "eligibility unknown"})`)));
      rosterNode.appendChild(rosterList);
    } else rosterNode.appendChild(text("span", "No roster observed."));
    const recommendation = status.recommendation || {};
    $("policy-note").textContent = (recommendation.lookahead_status || "foundation") + ": " + (recommendation.explanation || "No recommendation loaded.");
    const candidatesNode = $("components");
    candidatesNode.replaceChildren();
    const candidates = recommendation.candidates || [];
    if (!candidates.length) candidatesNode.appendChild(text("span", "No recommendation loaded."));
    candidates.slice(0, 10).forEach((candidate, index) => {
      const card = document.createElement("article");
      card.className = "card";
      card.appendChild(text("strong", `${index + 1}. ${candidate.name} — ${candidate.score}`));
      card.appendChild(text("p", candidate.explanation || "No explanation provided."));
      const components = Object.entries(candidate.components || {}).map(([key, value]) => `${key}: ${value}`).join(" · ");
      card.appendChild(text("small", components || "No component breakdown."));
      if ((candidate.warnings || []).length) card.appendChild(text("p", candidate.warnings.join("; ")));
      const player = (observation.available_players || []).find((item) => item.player_id === candidate.player_id);
      if (player) {
        const manual = document.createElement("button");
        manual.textContent = "Stage manual pick";
        manual.disabled = !(status.state === "paused" && status.manual_override);
        manual.addEventListener("click", async () => {
          try { update(await api("/api/control", { session_id: sessionId, command: "manual-pick", player_id: candidate.player_id })); } catch (error) { showError(error.message); }
        });
        card.appendChild(manual);
      }
      candidatesNode.appendChild(card);
    });
    const confirmed = (status.actions || []).filter((action) => action.status === "confirmed").pop();
    $("last-confirmed").textContent = confirmed ? `${confirmed.player_name} at pick ${confirmed.expected_pick} (revision ${confirmed.revision})` : "No confirmed action.";
    document.querySelectorAll("[data-control]").forEach((button) => {
      const command = button.dataset.control;
      button.disabled = !sessionId || (command === "pause" ? !["running", "degraded"].includes(status.state) : command === "manual-override" ? status.state !== "running" : command === "start" ? !readiness.ready || status.state === "running" : command === "resume" ? !readiness.ready || !["paused", "degraded"].includes(status.state) : false);
    });
    $("practice-badge").textContent = status.room && status.room.practice ? "PRACTICE" : "LIVE";
    $("practice-badge").dataset.practice = String(Boolean(status.room && status.room.practice));
    $("journal").textContent = JSON.stringify(status.journal || [], null, 2);
  }
  async function refresh() { try { const response = await fetch("/api/status", { cache: "no-store" }); update(await response.json()); } catch (error) { showError(error.message); } }
  async function loadSetup() {
    try {
      const response = await fetch("/api/setup", { cache: "no-store" });
      const value = await response.json();
      // Another surface (the ESPN overlay) may finish setup while we are editing.
      if (value.configured && editingSetup) editingSetup = false;
      setupInfo = {
        configured: Boolean(value.configured),
        team: value.team || null,
        pending_source: value.pending_source || null,
        team_options_ready: Boolean(value.team_options_ready),
        bookmarklet_seen: Boolean(value.bookmarklet_seen),
      };
      if (value.configured && value.team && !editingSetup) {
        $("setup-url-input").value = `https://fantasy.espn.com/basketball/team?leagueId=${encodeURIComponent(value.team.league_id)}&seasonId=${encodeURIComponent(value.team.season_id)}&teamId=${encodeURIComponent(value.team.team_id)}`;
        $("setup-team").value = value.team.our_team;
      }
      renderChecklist();
    } catch (error) { showError(error.message); }
  }
  $("setup-save").addEventListener("click", async () => {
    const button = $("setup-save"); button.disabled = true;
    try {
      const result = await api("/api/setup", { our_team: $("setup-team").value });
      editingSetup = false;
      setupInfo = { ...setupInfo, configured: true, team: result.team };
      await refresh(); await loadSetup();
    } catch (error) { $("setup-team-status").hidden = false; $("setup-team-status").textContent = `Could not save: ${error.message}`; }
    finally { button.disabled = false; }
  });
  $("setup-source").addEventListener("click", async () => {
    const button = $("setup-source"); button.disabled = true;
    try {
      const value = await api("/api/setup/source", { url: $("setup-url-input").value.trim() });
      $("setup-url").dataset.touched = "1";
      $("setup-url").textContent = `Linked: league ${value.league_id}, season ${value.season_id}, team ${value.team_id}.`;
      setupInfo = { ...setupInfo, pending_source: { league_id: value.league_id, season_id: value.season_id, team_id: value.team_id }, team_options_ready: false };
      $("setup-team-status").textContent = "Waiting for the ESPN team list…";
      $("setup-team").replaceChildren(new Option("Waiting for ESPN team list…", ""));
      setupOptionsKey = null;
      $("setup-save").disabled = true;
      renderChecklist();
    } catch (error) { $("setup-url").dataset.touched = "1"; $("setup-url").textContent = `Could not save that address: ${error.message}`; }
    finally { button.disabled = false; }
  });
  $("setup-edit").addEventListener("click", async () => {
    try {
      await api("/api/setup/begin", {});
      editingSetup = true;
      setupInfo = { ...setupInfo, configured: false, pending_source: null, team_options_ready: false };
      $("setup-team").replaceChildren(new Option("Waiting for ESPN team list…", ""));
      setupOptionsKey = null;
      $("setup-save").disabled = true;
      $("setup-url").dataset.touched = "1";
      $("setup-url").textContent = "Confirm the saved address or paste a different one, then press Save.";
      renderChecklist();
      await refresh();
    } catch (error) { showError(error.message); }
  });
  async function refreshSetupOptions() {
    if (!$("setup-card") || $("setup-card").hidden || (setupInfo.configured && !editingSetup)) return;
    try {
      const response = await fetch("/api/setup/options", { cache: "no-store" });
      const value = await response.json();
      if (!Array.isArray(value.team_options) || !value.team_options.length) return;
      const optionsKey = JSON.stringify([value.league_id, value.season_id, value.team_id, value.team_options]);
      if (optionsKey === setupOptionsKey) return;
      setupOptionsKey = optionsKey;
      const selected = $("setup-team").value;
      $("setup-team").replaceChildren(new Option("Select your ESPN team…", ""));
      value.team_options.forEach((name) => $("setup-team").add(new Option(name, name)));
      if (value.team_options.includes(selected)) $("setup-team").value = selected;
      $("setup-team-status").textContent = `Team list received from ESPN for league ${value.league_id}. Choose your team.`;
      setupInfo = { ...setupInfo, team_options_ready: true };
      $("setup-save").disabled = false;
      renderChecklist();
    } catch (_error) { /* setup remains available while discovery is pending */ }
  }
  $("operator-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter || $("operator-form").querySelector("button[type=submit]");
    if (submit.disabled) return;
    let requestId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    submit.disabled = true;
    let payload;
    try {
      payload = JSON.parse($("operator-payload").value);
    } catch (error) {
      $("operator-result").textContent = JSON.stringify({ operation: "parse", request_id: requestId, command: null, status: "error", error: `Invalid JSON: ${error.message}` }, null, 2);
      submit.disabled = false;
      return;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      $("operator-result").textContent = JSON.stringify({ operation: "parse", request_id: requestId, command: null, status: "error", error: "Operator payload must be a JSON object" }, null, 2);
      submit.disabled = false;
      return;
    }
    if (typeof payload.request_id === "string" && payload.request_id) requestId = payload.request_id;
    payload.request_id = requestId;
    $("operator-result").textContent = JSON.stringify({ operation: payload.operation || "unknown", request_id: requestId, command: null, status: "pending" }, null, 2);
    try {
      const response = await fetch("/api/operator", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": window.FBL_CSRF_TOKEN },
        body: JSON.stringify(payload),
      });
      const value = await response.json();
      $("operator-result").textContent = JSON.stringify(value, null, 2);
      if (!response.ok) throw new Error(value.error || response.statusText);
      await refresh();
    } catch (error) {
      $("operator-result").textContent = JSON.stringify({ operation: payload.operation || "unknown", request_id: requestId, command: null, status: "error", error: error.message, outcome: "Inspect the journal before retrying a mutating command." }, null, 2);
      showError(error.message);
    } finally {
      submit.disabled = false;
    }
  });
  $("connect").addEventListener("click", async () => {
    try { update(await api("/api/connect", { url: $("room-url").value, practice: $("practice").checked })); } catch (error) { showError(error.message); }
  });
  document.querySelectorAll("[data-control]").forEach((button) => button.addEventListener("click", async () => {
    if (!sessionId) return showError("Connect to a room first");
    try { update(await api("/api/control", { session_id: sessionId, command: button.dataset.control })); } catch (error) { showError(error.message); }
  }));
  function setupBookmarkletInstall() {
    const link = $("advisor-bookmarklet");
    const copy = $("copy-advisor-bookmarklet");
    const url = $("advisor-bookmarklet-url");
    const status = $("advisor-bookmarklet-status");
    if (!link || !copy || !url || !status) return;
    const bookmarklet = link.getAttribute("href") || "";
    // Positive identification of the server-generated bookmarklet href. This
    // does not validate or authorize a navigation URL; the click handler
    // always prevents navigation and the value is only copied to bookmarks.
    const isGeneratedAdvisorBookmarklet = (href) => href.startsWith("javascript:");
    const enabled = isGeneratedAdvisorBookmarklet(bookmarklet);
    copy.disabled = !enabled;
    link.setAttribute("aria-disabled", String(!enabled));
    const selectUrl = () => {
      url.focus();
      url.select();
      url.setSelectionRange(0, url.value.length);
    };
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (!enabled) {
        status.textContent = "Start the panel with --page-runner to enable the advisor bookmarklet.";
        return;
      }
      status.textContent = "Drag the highlighted FBL Advisor link to Chrome bookmarks, or use Copy bookmarklet URL below. Do not use the browser star on this panel.";
      selectUrl();
    });
    copy.addEventListener("click", async () => {
      if (!enabled) return;
      try {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") throw new Error("clipboard unavailable");
        await navigator.clipboard.writeText(bookmarklet);
        status.textContent = "Copied. Create a Chrome bookmark and paste this into its URL field.";
      } catch (_error) {
        selectUrl();
        status.textContent = "The browser blocked automatic copying. The URL is selected; press Ctrl+C, then paste it into a Chrome bookmark’s URL field.";
      }
    });
  }
  setupBookmarkletInstall();
  $("bookmark-done").addEventListener("click", () => {
    bookmarkMarked = true;
    try { window.localStorage.setItem(BOOKMARK_KEY, "1"); } catch (_error) { /* optional convenience */ }
    renderChecklist();
  });
  $("setup-toggle-steps").addEventListener("click", () => {
    $("setup-toggle-steps").dataset.shown = $("setup-toggle-steps").dataset.shown ? "" : "1";
    $("setup-toggle-steps").textContent = $("setup-toggle-steps").dataset.shown ? "Hide the steps" : "Show the steps again";
    renderChecklist();
  });
  [1, 2, 3, 4, 5].forEach((step) => {
    const node = $(`step-${step}`);
    node.addEventListener("click", (event) => {
      const target = event && event.target;
      if (target && target.closest && !target.closest(".step-head")) return;
      node.dataset.open = node.dataset.open === "true" ? "false" : "true";
    });
  });
  loadSetup();
  refreshSetupOptions();
  refresh();
  setInterval(() => { refresh(); loadSetup(); refreshSetupOptions(); }, 2000);
})();
