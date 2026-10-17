(() => {
  let sessionId = null;
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
  function update(data) {
    const status = data.sessions ? (data.sessions.find((item) => item.session_id === sessionId) || data.sessions[0]) : data;
    if (!status) return;
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
  refresh();
  setInterval(refresh, 2000);
})();
