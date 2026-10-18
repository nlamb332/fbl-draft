"""Loopback-only web panel for the Sleeper advisor (``serve`` command)."""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .api import parse_draft_id
from .cli import LEGEND, Poller, poll_loop

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sleeper Advisor</title>
<style>
:root{--bg:#f6f7f9;--fg:#15181d;--muted:#5d6672;--card:#fff;--line:#d9dde3;--accent:#1b6ef3;--good:#16794a;--bad:#b3261e;--warn:#8a5a00;--banner:#fff3c4}
@media (prefers-color-scheme:dark){:root{--bg:#10131a;--fg:#e8ebf0;--muted:#97a1ae;--card:#181c25;--line:#2b313d;--accent:#6aa3ff;--good:#52c28a;--bad:#ff8a80;--warn:#e6b450;--banner:#4a3d0b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:12px 16px 32px}
h1{font-size:1.25rem;margin:8px 0 2px}
h2{font-size:1rem;margin:0 0 8px}
.sub{color:var(--muted);font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0;overflow-x:auto}
.banner{border-radius:10px;padding:12px;margin:12px 0;font-weight:700;font-size:1.1rem;background:var(--banner);border:2px solid var(--warn)}
.banner.now{border-color:var(--good);color:var(--good)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--good)}.neg{color:var(--bad)}
.legend,.foot{color:var(--muted);font-size:.85rem;margin-top:8px}
.warn{color:var(--warn);font-size:.9rem}
.err{color:var(--bad);font-weight:600}
ul{margin:0;padding-left:18px}
[hidden]{display:none}
</style>
</head>
<body>
<main>
<h1 id="title">Sleeper Advisor</h1>
<div class="sub" id="summary">Loading...</div>
<div class="sub">Lock-In draft strategy · estimated weekly contribution · assumed game forecasts unless local forecasts are supplied</div>
<div id="banner" class="banner" hidden></div>
<div class="card"><h2>Your lineup</h2><div id="lineup"></div><div class="sub" id="bench"></div></div>
<div class="card"><h2>Recommended picks</h2><div id="recs"></div><div class="legend" id="legend"></div></div>
<div class="card" id="warnCard" hidden><h2>Warnings</h2><ul id="warnings" class="warn"></ul></div>
<div class="foot"><span id="updated"></span> <span id="error" class="err"></span></div>
</main>
<script>
"use strict";
var $ = function (id) { return document.getElementById(id); };
function el(tag, text, cls) {
  var n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function fmt(v, d) { return (v === null || v === undefined) ? "-" : Number(v).toFixed(d); }
function table(headers, rows, numCols) {
  var t = el("table"), head = el("tr");
  headers.forEach(function (h, i) { head.appendChild(el("th", h, numCols.indexOf(i) >= 0 ? "num" : "")); });
  t.appendChild(head);
  rows.forEach(function (r) {
    var tr = el("tr");
    r.forEach(function (c, i) {
      var cls = numCols.indexOf(i) >= 0 ? "num" : "";
      if (c && typeof c === "object") { tr.appendChild(el("td", c.text, (cls + " " + c.cls).trim())); }
      else { tr.appendChild(el("td", c, cls)); }
    });
    t.appendChild(tr);
  });
  return t;
}
function replace(id, node) { var h = $(id); h.textContent = ""; h.appendChild(node); }
function render(s) {
  if (!s.ready) {
    $("summary").textContent = "Waiting for the first update...";
  } else {
    var d = s.draft;
    $("title").textContent = d.name || ("Draft " + d.draft_id);
    var clock = s.current_pick === null ? "draft complete" : ("pick " + s.current_pick + ", slot " + s.on_clock_slot + " on the clock");
    var mine = s.our_slot === null ? "your slot unknown" : ("your slot " + s.our_slot);
    $("summary").textContent = (d.mock_type || "league draft") + " | " + d.type + " " + d.teams + " teams x " + d.rounds +
      " rounds | " + d.status + " | " + s.picks_made + " picks made | " + clock + " | " + mine;
    var b = $("banner");
    if (s.on_clock) { b.hidden = false; b.className = "banner now"; b.textContent = ">>> YOU ARE ON THE CLOCK <<<"; }
    else if (s.picks_away !== null && s.picks_away > 0 && s.picks_away <= 2) { b.hidden = false; b.className = "banner"; b.textContent = "Your pick in " + s.picks_away; }
    else if (s.our_next_picks.length) { b.hidden = false; b.className = "banner"; b.style.fontWeight = "400"; b.textContent = "Your next picks: " + s.our_next_picks.slice(0, 6).join(", "); }
    else { b.hidden = true; }
    replace("lineup", table(["Slot", "Player", "Pos", "Lock-In/wk"],
      s.lineup.map(function (r) { return [r.slot, r.name === null ? "-" : r.name, r.positions.join("/"), fmt(r.lock_in_weekly_points, 1)]; }), [3]));
    $("bench").textContent = s.bench.length ? ("Bench: " + s.bench.map(function (r) { return r.name + " (" + r.positions.join("/") + ")"; }).join(", ")) : "Bench: empty";
    if (s.recommendations.length) {
      replace("recs", table(["#", "Player", "Pos", "Lock-In/wk", "ADP", "Added/wk", "Why"],
        s.recommendations.map(function (r) {
          var sc = (r.score >= 0 ? "+" : "") + r.score.toFixed(1);
          return [r.rank, r.name, r.positions.join("/"), fmt(r.components.lock_in_weekly_points, 1), fmt(r.adp, 1), { text: sc, cls: r.score >= 0 ? "pos" : "neg" }, r.explanation];
        }), [0, 3, 4, 5]));
    } else { replace("recs", el("div", "No recommendations available.", "sub")); }
    $("legend").textContent = s.legend;
    $("updated").textContent = "Last updated " + (s.updated_at || "never") + ".";
  }
  var w = s.warnings || [];
  $("warnCard").hidden = !w.length;
  var ul = $("warnings"); ul.textContent = "";
  w.forEach(function (x) { ul.appendChild(el("li", x)); });
  $("error").textContent = s.error ? ("Fetch error: " + s.error) : "";
}
function tick() {
  fetch("/api/state", { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }).then(render).catch(function (e) {
    $("error").textContent = "Panel server unreachable: " + e.message;
  });
}
tick();
setInterval(tick, 3000);
</script>
</body>
</html>
"""


class SnapshotStore:
    """Thread-safe holder of the latest snapshot and fetch error."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: dict | None = None
        self._error: str | None = None

    def update(self, snapshot: dict | None, error: str | None) -> None:
        with self._lock:
            if snapshot is not None:
                self._snapshot = snapshot
            self._error = error

    def payload(self) -> dict:
        with self._lock:
            snapshot, error = self._snapshot, self._error
        if snapshot is None:
            return {"ready": False, "error": error, "warnings": [], "legend": LEGEND,
                    "updated_at": None}
        return dict(snapshot, ready=True, error=error)


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "SleeperAdvisor"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # keep the console quiet
        pass

    # -- helpers ------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; connect-src 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _text(self, status: int, message: str, extra: dict | None = None) -> None:
        self._send(status, message.encode("utf-8"), "text/plain; charset=utf-8", extra)

    def _host_allowed(self) -> bool:
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {f"{name}:{port}" for name in LOOPBACK_HOSTS}

    def _dispatch(self) -> None:
        if not self._host_allowed():
            self._text(403, "Forbidden host")
            return
        if self.command != "GET":
            self._text(405, "Method not allowed", {"Allow": "GET"})
            return
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            body = json.dumps(self.server.store.payload(), ensure_ascii=False).encode("utf-8")  # type: ignore[attr-defined]
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._text(404, "Not found")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _dispatch


def create_server(store: SnapshotStore, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind a loopback-only server. Raises ValueError for any other host."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"The panel only binds to loopback ({', '.join(LOOPBACK_HOSTS)}); refusing {host!r}")
    server = ThreadingHTTPServer(("127.0.0.1", port), PanelHandler)
    server.daemon_threads = True
    server.store = store  # type: ignore[attr-defined]
    return server


def start_polling(poller: Poller, store: SnapshotStore, interval: float,
                  stop: threading.Event, *, sleep: Callable[[float], Any] | None = None,
                  max_iterations: int | None = None) -> threading.Thread:
    """Poll in a daemon thread, publishing every tick to ``store``."""
    def publish(p: Poller, _changed: bool) -> None:
        store.update(p.snapshot, p.error)

    def run() -> None:
        poll_loop(poller, interval, sleep=sleep or stop.wait, max_iterations=max_iterations,
                  on_update=publish, should_stop=stop.is_set)

    thread = threading.Thread(target=run, name="sleeper-poller", daemon=True)
    thread.start()
    return thread


def cmd_serve(args: Any, client: Any, out: Any) -> int:
    draft_id = parse_draft_id(args.draft)
    store = SnapshotStore()
    try:
        server = create_server(store, args.host, args.port)
    except OSError as exc:
        print(f"error: could not start the panel on port {args.port} ({exc}). "
              "Is another copy already running? Try --port.", file=out)
        return 1
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    poller = Poller(client, draft_id, args.user, args.slot, args.top,
                    forecast_path=getattr(args,"lock_in_forecasts",None))
    stop = threading.Event()
    start_polling(poller, store, args.interval, stop)
    print(f"Sleeper advisor panel: {url}  (Ctrl+C to stop)", file=out, flush=True)
    if getattr(args, "open_browser", False):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    print("Stopped.", file=out)
    return 0
