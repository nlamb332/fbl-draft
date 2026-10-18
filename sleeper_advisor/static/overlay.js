/* Sleeper Advisor in-page overlay. Injected by the bookmarklet on
 * the sleeper.com draft room. Read-only: polls the local advisor panel
 * and renders its answer. All text is set through textContent (no markup
 * from data is ever parsed). */
(function () {
  "use strict";
  if (window.__sleeperAdvisor) { window.__sleeperAdvisor.toggle(); return; }

  var script = document.currentScript;
  var BASE = "http://127.0.0.1:8766";
  try { if (script && script.src) { BASE = new URL(script.src).origin; } } catch (e) { /* keep default */ }
  var POLL_MS = 3000;
  var TOP_N = 8;

  function draftId() {
    var m = /^\/draft\/[A-Za-z]+\/(\d+)/.exec(location.pathname);
    return m ? m[1] : null;
  }

  var CSS = [
    ":host{all:initial}",
    ".box{position:fixed;top:72px;right:16px;width:360px;max-height:80vh;display:flex;flex-direction:column;",
    "z-index:2147483647;background:#14181f;color:#e8ebf0;border:1px solid #3a4352;border-radius:10px;",
    "box-shadow:0 6px 24px rgba(0,0,0,.45);font:13px/1.4 system-ui,-apple-system,'Segoe UI',sans-serif}",
    ".box.wide{width:min(720px,94vw)}",
    ".bar{display:flex;align-items:center;gap:6px;padding:6px 8px;cursor:move;user-select:none;",
    "background:#1f2530;border-radius:10px 10px 0 0;font-weight:700}",
    ".bar .t{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    "button{font:inherit;color:#e8ebf0;background:#2b3342;border:1px solid #3a4352;border-radius:6px;",
    "padding:1px 8px;cursor:pointer}",
    "button:hover{background:#38425a}",
    ".body{padding:8px;overflow:auto}",
    ".id{background:#b3261e;color:#fff;font-weight:700;border-radius:8px;padding:8px;margin-bottom:8px}",
    ".clock{border-radius:8px;padding:6px 8px;margin-bottom:8px;font-weight:700;background:#4a3d0b;border:1px solid #e6b450}",
    ".clock.now{background:#12392a;border-color:#52c28a;color:#52c28a}",
    ".sub{color:#97a1ae;font-size:12px;margin:4px 0}",
    "h4{margin:8px 0 4px;font-size:12px;text-transform:uppercase;color:#97a1ae}",
    "table{border-collapse:collapse;width:100%}",
    "th,td{text-align:left;padding:2px 5px;border-bottom:1px solid #2b313d;vertical-align:top}",
    "th{color:#97a1ae;font-weight:600;font-size:11px}",
    ".n{text-align:right;font-variant-numeric:tabular-nums}",
    ".pos{color:#52c28a}.neg{color:#ff8a80}.err{color:#ff8a80;font-weight:600}",
    ".warn{color:#e6b450;font-size:12px}",
    "[hidden]{display:none!important}"
  ].join("");

  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    if (cls) { n.className = cls; }
    return n;
  }
  function fmt(v, d) { return (v === null || v === undefined) ? "-" : Number(v).toFixed(d); }
  function table(headers, rows, numCols) {
    var t = el("table"), head = el("tr");
    headers.forEach(function (h, i) { head.appendChild(el("th", h, numCols.indexOf(i) >= 0 ? "n" : "")); });
    t.appendChild(head);
    rows.forEach(function (r) {
      var tr = el("tr");
      r.forEach(function (c, i) {
        var cls = numCols.indexOf(i) >= 0 ? "n" : "";
        if (c && typeof c === "object") { tr.appendChild(el("td", c.text, (cls + " " + c.cls).trim())); }
        else { tr.appendChild(el("td", c, cls)); }
      });
      t.appendChild(tr);
    });
    return t;
  }

  // -- mount ----------------------------------------------------------------
  var host = document.createElement("div");
  host.id = "sleeper-advisor-overlay";
  var root = host.attachShadow({ mode: "open" });
  var style = document.createElement("style");
  style.textContent = CSS;
  root.appendChild(style);

  var box = el("div", null, "box");
  var bar = el("div", null, "bar");
  var title = el("span", "Sleeper Advisor", "t");
  var expandBtn = el("button", "Expand");
  var foldBtn = el("button", "–");
  var closeBtn = el("button", "×");
  expandBtn.title = "Show the full tables";
  foldBtn.title = "Collapse";
  closeBtn.title = "Close and stop updating";
  [title, expandBtn, foldBtn, closeBtn].forEach(function (n) { bar.appendChild(n); });
  var body = el("div", null, "body");
  body.appendChild(el("div", "Loading...", "sub"));
  box.appendChild(bar);
  box.appendChild(body);
  root.appendChild(box);
  document.documentElement.appendChild(host);

  var state = { collapsed: false, expanded: false, last: null, timer: null, closed: false, error: "" };

  var api = {};

  // -- drag -------------------------------------------------------------------
  (function () {
    var drag = null;
    bar.addEventListener("mousedown", function (e) {
      if (e.target !== bar && e.target !== title) { return; }
      var r = box.getBoundingClientRect();
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    function move(e) {
      if (!drag) { return; }
      var x = Math.max(0, Math.min(window.innerWidth - 60, e.clientX - drag.dx));
      var y = Math.max(0, Math.min(window.innerHeight - 30, e.clientY - drag.dy));
      box.style.left = x + "px"; box.style.top = y + "px"; box.style.right = "auto";
    }
    function up() { drag = null; }
    window.addEventListener("mousemove", move, true);
    window.addEventListener("mouseup", up, true);
    api.cleanupDrag = function () {
      window.removeEventListener("mousemove", move, true);
      window.removeEventListener("mouseup", up, true);
    };
  })();

  // -- render -----------------------------------------------------------------
  function openSlots(s) {
    return (s.lineup || []).filter(function (r) { return r.name === null; }).map(function (r) { return r.slot; });
  }

  function render() {
    var s = state.last;
    body.textContent = "";
    body.hidden = state.collapsed;
    expandBtn.hidden = state.collapsed;
    box.classList.toggle("wide", state.expanded && !state.collapsed);
    expandBtn.textContent = state.expanded ? "Compact" : "Expand";
    if (state.collapsed) { return; }
    if (state.error) { body.appendChild(el("div", state.error, "err")); }
    if (!s) { body.appendChild(el("div", "Waiting for the advisor...", "sub")); return; }
    if (!s.ready) { body.appendChild(el("div", "Waiting for the first update...", "sub")); return; }

    if (s.identity_banner) { body.appendChild(el("div", s.identity_banner, "id")); }

    var clock;
    if (s.current_pick === null) { clock = el("div", "Draft complete", "clock"); }
    else if (s.on_clock) { clock = el("div", ">>> YOU ARE ON THE CLOCK <<< (pick " + s.current_pick + ")", "clock now"); }
    else if (s.picks_away !== null && s.picks_away > 0) {
      clock = el("div", "Your pick in " + s.picks_away + " (pick " + s.current_pick + " of the draft is on the clock)", "clock");
    } else { clock = el("div", "Pick " + s.current_pick + " on the clock (slot " + s.on_clock_slot + ")", "clock"); }
    body.appendChild(clock);

    var recs = s.recommendations || [];
    var shown = state.expanded ? recs : recs.slice(0, TOP_N);
    body.appendChild(el("h4", "Recommended picks"));
    if (!shown.length) { body.appendChild(el("div", "No recommendations available.", "sub")); }
    else if (state.expanded) {
      body.appendChild(table(["#", "Player", "Pos", "Lock/wk", "ADP", "vs wait", "Why"], shown.map(function (r) {
        return [r.rank, r.name, r.positions.join("/"), fmt(r.components && r.components.lock_in_weekly_points, 1),
          fmt(r.adp, 1), { text: (r.score >= 0 ? "+" : "") + r.score.toFixed(1), cls: r.score >= 0 ? "pos" : "neg" }, r.explanation];
      }), [0, 3, 4, 5]));
    } else {
      body.appendChild(table(["Player", "Pos", "vs wait", "ADP"], shown.map(function (r) {
        return [r.name, r.positions.join("/"),
          { text: (r.score >= 0 ? "+" : "") + r.score.toFixed(1), cls: r.score >= 0 ? "pos" : "neg" }, fmt(r.adp, 1)];
      }), [2, 3]));
    }

    var open = openSlots(s);
    body.appendChild(el("h4", "Open lineup slots"));
    body.appendChild(el("div", open.length ? open.join(", ") : "All starting slots filled.", "sub"));

    if (state.expanded) {
      body.appendChild(el("h4", "Your lineup"));
      body.appendChild(table(["Slot", "Player", "Pos", "Lock/wk"], (s.lineup || []).map(function (r) {
        return [r.slot, r.name === null ? "-" : r.name, r.positions.join("/"), fmt(r.lock_in_weekly_points, 1)];
      }), [3]));
      var bench = s.bench || [];
      body.appendChild(el("div", bench.length ? "Bench: " + bench.map(function (r) {
        return r.name + " (" + r.positions.join("/") + ")"; }).join(", ") : "Bench: empty", "sub"));
      (s.warnings || []).forEach(function (w) { body.appendChild(el("div", w, "warn")); });
    }
    if (s.error) { body.appendChild(el("div", "Fetch error: " + s.error, "err")); }
  }

  // -- polling ----------------------------------------------------------------
  function tick() {
    if (state.closed) { return; }
    var id = draftId();
    if (!id) {
      state.error = "Open a draft room (/draft/nba/<id>) to see advice.";
      render();
      return;
    }
    fetch(BASE + "/api/state?draft=" + encodeURIComponent(id), { cache: "no-store" })
      .then(function (r) { if (!r.ok) { throw new Error("HTTP " + r.status); } return r.json(); })
      .then(function (data) {
        if (state.closed) { return; }
        state.error = "";
        state.last = data;
        render();
      })
      .catch(function (e) {
        if (state.closed) { return; }
        state.error = "Advisor panel unreachable (" + e.message + "). Is it still running?";
        render();
      });
  }

  function start() { if (!state.timer) { state.timer = setInterval(tick, POLL_MS); } tick(); }
  function stop() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  Object.assign(api, {
    toggle: function () {
      if (state.closed) { return; }
      host.hidden = !host.hidden;
      if (host.hidden) { stop(); } else { start(); }
    },
    close: function () {
      state.closed = true;
      stop();
      if (api.cleanupDrag) { api.cleanupDrag(); }
      if (host.parentNode) { host.parentNode.removeChild(host); }
      try { delete window.__sleeperAdvisor; } catch (e) { window.__sleeperAdvisor = undefined; }
    }
  });
  window.__sleeperAdvisor = api;

  foldBtn.addEventListener("click", function () {
    state.collapsed = !state.collapsed;
    foldBtn.textContent = state.collapsed ? "+" : "–";
    render();
  });
  expandBtn.addEventListener("click", function () { state.expanded = !state.expanded; render(); });
  closeBtn.addEventListener("click", api.close);

  start();
})();
