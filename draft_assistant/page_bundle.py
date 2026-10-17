"""Build the harness-agnostic in-page runner bundle from checked-in modules.

Any agent harness that can evaluate JavaScript in the visible ESPN tab (a
Codex/CUA Playwright REPL, Claude in Chrome, or an operator's DevTools console)
loads the same bundle with one snippet.  Each module body is wrapped unchanged
in its own function scope with a minimal CommonJS `require`; nothing is
rewritten, minified, or evaluated from a string.  The bundle reports the
SHA-256 of every source file it contains so the loader can refuse a panel
serving a different checkout.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MODULE_DIR = Path(__file__).resolve().parent
BUNDLE_NAME = "fbl-page-runner-v1"
# Browser modules only; Node-only helpers (live_probe, room_simulator,
# dom_fixture, rehearsal_harness) are deliberately excluded.
BUNDLE_MODULES = (
    "browser_observer.js",
    "operator_runtime.js",
    "batch_recovery.js",
    "live_projection_inputs.js",
    "native_activation.js",
    "view_switch.js",
    "canonical_runner.js",
    "heartbeat_publisher.js",
    "page_capture.js",
    "draft_universe.js",
    "page_runner.js",
    "page_controls.js",
)

# Keep this in sync with the server's page-origin gate.  These origins are the
# only pages that may receive the short-lived bootstrap message from the
# localhost bridge.
BOOKMARKLET_PAGE_ORIGINS = ("https://fantasy.espn.com", "https://www.espn.com")


@dataclass(frozen=True)
class Bundle:
    text: str
    bundle_hash: str
    source_hashes: dict[str, str]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_bundle(module_dir: Path = MODULE_DIR) -> Bundle:
    sources: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for name in BUNDLE_MODULES:
        raw = (module_dir / name).read_bytes()
        hashes[f"draft_assistant/{name}"] = _sha256(raw)
        sources[name] = raw.decode("utf-8")
    bundle_hash = _sha256(json.dumps(hashes, sort_keys=True).encode("utf-8"))
    definitions = ",\n".join(
        f"  {json.dumps('./' + name)}: function (module, exports, require) {{\n{source}\n}}"
        for name, source in sources.items()
    )
    text = f"""/* {BUNDLE_NAME}: generated from checked-in draft_assistant modules; do not edit. */
(function () {{
  "use strict";
  const BUNDLE_HASH = {json.dumps(bundle_hash)};
  if (window.FBL && window.FBL.bundle_hash === BUNDLE_HASH) return;
  const definitions = {{
{definitions}
  }};
  const cache = {{}};
  function require(name) {{
    const key = name.startsWith("./") ? name : "./" + name;
    if (cache[key]) return cache[key].exports;
    const define = definitions[key];
    if (!define) throw new Error("module not in the FBL page bundle: " + name);
    const module = {{ exports: {{}} }};
    cache[key] = module;
    define(module, module.exports, require);
    return module.exports;
  }}
  const modules = {{}};
  for (const key of Object.keys(definitions)) modules[key.slice(2)] = require(key);
  window.FBL = modules["page_runner.js"].createPageApi({{
    bundle: {json.dumps(BUNDLE_NAME)},
    bundleHash: BUNDLE_HASH,
    sourceHashes: {json.dumps(hashes, sort_keys=True)},
    modules,
  }});
}})();
"""
    return Bundle(text=text, bundle_hash=bundle_hash, source_hashes=hashes)


def loader_snippet(base: str = "http://127.0.0.1:8765", *, csrf_token: str | None = None, module_dir: Path = MODULE_DIR) -> str:
    """Return the one-shot loader to evaluate in the visible ESPN tab.

    The expected bundle hash comes from this checkout, so a panel serving other
    code is refused.  The CSRF token is read from the local panel here and
    handed to the page in memory only.
    """
    bundle = build_bundle(module_dir)
    if csrf_token is None:
        with urllib.request.urlopen(f"{base}/api/csrf", timeout=5) as response:  # noqa: S310 - loopback panel only
            csrf_token = json.loads(response.read().decode("utf-8"))["csrf_token"]
    return f"""await (async () => {{
  const expected = {json.dumps(bundle.bundle_hash)};
  if (!window.FBL || window.FBL.bundle_hash !== expected) {{
    await new Promise((resolve, reject) => {{
      const script = document.createElement("script");
      script.src = {json.dumps(base + "/page-runner.js?h=")} + expected;
      script.onload = resolve;
      script.onerror = () => reject(new Error("FBL page bundle failed to load; is the panel running with -PageRunner?"));
      document.head.appendChild(script);
    }});
  }}
  if (!window.FBL || window.FBL.bundle_hash !== expected) throw new Error("the panel served a bundle that does not match this checkout");
  window.FBL.configure({{ baseUrl: {json.dumps(base)}, csrfToken: {json.dumps(csrf_token)} }});
  return {{ bundle: window.FBL.bundle, bundle_hash: window.FBL.bundle_hash, modules: Object.keys(window.FBL.source_hashes) }};
}})()"""


def recommendation_loader_snippet(
    base: str = "http://127.0.0.1:8765",
    *,
    csrf_token: str | None = None,
    module_dir: Path = MODULE_DIR,
) -> str:
    """Return a room-agnostic, recommendation-only ESPN page loader.

    The page supplies the current room URL, visible draft heading, and the
    explicit practice/real classification.  The local server attests practice
    rooms or verifies the configured live league for real rooms before
    returning an observer-only session token.  The snippet deliberately sends
    only the three numeric room IDs in ``url`` (never ESPN's member ID), sends
    the full visible roster order so the server can derive the configured
    seat, and never invokes Resume, Draft, or any actor operation.
    """
    bundle = build_bundle(module_dir)
    if csrf_token is None:
        with urllib.request.urlopen(f"{base}/api/csrf", timeout=5) as response:  # noqa: S310 - loopback panel only
            csrf_token = json.loads(response.read().decode("utf-8"))["csrf_token"]
    template = r'''await (async () => {
  const baseUrl = __BASE_URL__;
  const csrfToken = __CSRF_TOKEN__;
  const expected = __BUNDLE_HASH__;
  const pageWindow = window;
  const pageDocument = document;
  const pageLocation = location;
  const room = new URL(pageLocation.href);
  if (room.protocol !== "https:" || room.hostname !== "fantasy.espn.com" || room.pathname.replace(/\/+$/, "") !== "/basketball/draft") {
    throw new Error("This page is not an ESPN basketball draft room");
  }
  const numericRoomId = (key) => {
    const values = room.searchParams.getAll(key);
    if (values.length !== 1 || !/^\d+$/.test(values[0])) throw new Error(`The ESPN room URL has no unique numeric ${key}`);
    return values[0];
  };
  const roomIds = { leagueId: numericRoomId("leagueId"), seasonId: numericRoomId("seasonId"), teamId: numericRoomId("teamId") };
  const safeRoom = new URL("https://fantasy.espn.com/basketball/draft");
  Object.entries(roomIds).forEach(([key, value]) => safeRoom.searchParams.set(key, value));

  const isVisible = (node) => {
    if (!node || node.getAttribute?.("aria-hidden") === "true") return false;
    const style = pageWindow.getComputedStyle ? pageWindow.getComputedStyle(node) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    return typeof node.getClientRects !== "function" || node.getClientRects().length > 0 || node.offsetParent !== null;
  };
  const nodeText = (node) => String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim();
  const headings = Array.from(pageDocument.querySelectorAll("h1,h2,h3,[role='heading']"))
    .filter(isVisible).map(nodeText).filter(Boolean);
  const visibleHeading = headings.find(value => /ESPN\s+Fantasy\s+Basketball\s+Draft/i.test(value));
  if (!visibleHeading) throw new Error("The visible ESPN Fantasy Basketball draft heading is missing");
  const practice = /practice\s+draft/i.test(visibleHeading);

  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const readVisiblePickOrder = () => {
    // ESPN renders the roster as a native select in the draft room. Read every
    // option rather than the current selection: the selected option can be an
    // opponent while the server still owns a different configured seat.
    const controls = Array.from(pageDocument.querySelectorAll("select,[role='combobox']"))
      .filter(isVisible);
    const orders = [];
    for (const control of controls) {
      const options = Array.from(control.options || []);
      if (options.length !== 14) continue;
      const labels = options.map(nodeText).map(value => value.trim());
      if (labels.every(Boolean) && new Set(labels.map(normalize)).size === 14) orders.push(labels);
    }
    const pickOrder = orders[0];
    if (!pickOrder) throw new Error("The visible roster list does not expose the full 14-team pick order");
    return pickOrder;
  };

  const loadBundle = async () => {
    if (!pageWindow.FBL || pageWindow.FBL.bundle_hash !== expected) {
      await new Promise((resolve, reject) => {
        const script = pageDocument.createElement("script");
        script.src = `${baseUrl}/page-runner.js?h=${expected}`;
        script.onload = resolve;
        script.onerror = () => reject(new Error("FBL page bundle failed to load; is the panel running with -PageRunner?"));
        pageDocument.head.appendChild(script);
      });
    }
    if (!pageWindow.FBL || pageWindow.FBL.bundle_hash !== expected) throw new Error("The panel served a bundle that does not match this checkout");
    pageWindow.FBL.configure({ baseUrl, csrfToken });
  };
  const disposeControls = () => {
    if (pageWindow.FBLAdvisorControls && typeof pageWindow.FBLAdvisorControls.dispose === "function") pageWindow.FBLAdvisorControls.dispose();
    else pageDocument.getElementById("fbl-attended-controls")?.remove();
  };
  const mountAdvisor = (runner) => {
    disposeControls();
    const controls = pageWindow.FBL.modules["page_controls.js"].mountPageControls({
      document: pageDocument,
      runner,
      bundleHash: pageWindow.FBL.bundle_hash,
    });
    pageWindow.FBLAdvisorControls = controls;
    // Keep the first mount compact and away from the Resume/Draft controls.
    // The user can expand, drag, or choose another dock edge at any time.
    controls.dock?.("left");
    controls.collapse?.();
    return controls;
  };
  const sameRoom = (status) => {
    const connected = status && status.room;
    return Boolean(connected && connected.practice === practice
      && String(connected.league_id) === roomIds.leagueId
      && String(connected.season_id) === roomIds.seasonId
      && String(connected.team_id) === roomIds.teamId);
  };

  // Inspect the currently attached runner before loading a different bundle;
  // page-runner.js replaces window.FBL, so checking afterward would orphan an
  // active poller and could hide a running actor batch.
  const originalFBL = pageWindow.FBL;
  let existingRunner = null;
  let existingSummary = null;
  try {
    existingRunner = originalFBL && typeof originalFBL.runner === "function" ? originalFBL.runner() : null;
    existingSummary = existingRunner && typeof existingRunner.summary === "function" ? existingRunner.summary() : null;
  } catch (_error) {
    // An unattached or older bundle may report no runner; the fresh attach path
    // below remains available.
    existingRunner = null;
    existingSummary = null;
  }
  if (existingSummary && existingSummary.batch?.running) throw new Error("Stop the current batch before replacing the advisor");
  if (existingRunner && existingSummary?.mode === "recommendation"
      && originalFBL?.bundle_hash !== expected
      && typeof existingRunner.stopRecommendations === "function") {
    await existingRunner.stopRecommendations();
    existingRunner = null;
    existingSummary = null;
  }
  await loadBundle();
  if (!existingRunner) {
    try {
      existingRunner = pageWindow.FBL.runner ? pageWindow.FBL.runner() : null;
      existingSummary = existingRunner && typeof existingRunner.summary === "function" ? existingRunner.summary() : null;
    } catch (_error) {
      // A freshly loaded bundle has no attachment yet; that is the normal path.
      existingRunner = null;
      existingSummary = null;
    }
  }
  if (existingRunner && existingSummary?.mode === "recommendation" && existingSummary.inputs && typeof existingRunner.coordinatorStatus === "function") {
    let status = null;
    try {
      status = await existingRunner.coordinatorStatus();
    } catch (_error) {
      // A stale attachment is replaced below; no native action is attempted.
    }
    if (sameRoom(status)) {
      mountAdvisor(existingRunner);
      return { status: "reused", session_id: existingSummary.session_id, bundle_hash: pageWindow.FBL.bundle_hash, mode: "recommendation" };
    }
  }
  if (existingRunner && existingSummary?.mode === "recommendation" && typeof existingRunner.stopRecommendations === "function") existingRunner.stopRecommendations();

  const pickOrder = readVisiblePickOrder();
  const response = await pageWindow.fetch(`${baseUrl}/api/operator`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify({ operation: "connect-advisor", url: safeRoom.toString(), practice, visible_heading: visibleHeading, pick_order: pickOrder }),
  });
  const connection = await response.json();
  if (!response.ok || connection?.error) throw new Error(connection?.error || "the advisor connection was refused");
  const credentials = connection?.result && typeof connection.result === "object" ? connection.result : connection;
  if (credentials.setup_discovery === true) {
    const message = "FBL read the visible ESPN team list. Return to the local panel and choose your team to finish setup.";
    if (typeof window.alert === "function") window.alert(message);
    return { status: "setup-discovered", team_options: credentials.team_options || [], league_id: credentials.league_id, season_id: credentials.season_id, team_id: credentials.team_id };
  }
  const sessionId = credentials.session_id || credentials.sessionId;
  const ownerToken = credentials.observer_token || credentials.observer_only_token || credentials.owner_token || credentials.ownerToken || credentials.token;
  const ownTeam = credentials.own_team || credentials.ownTeam;
  if (!sessionId || !ownerToken || !ownTeam) throw new Error("advisor connection did not return observer credentials and own team");
  const expectedSeat = Number(credentials.expected_seat ?? credentials.expectedSeat);
  if (!Number.isInteger(expectedSeat) || expectedSeat < 1 || expectedSeat > 14) throw new Error("the visible roster seat is invalid");
  pageWindow.FBL.attach({ sessionId, ownerToken, ownTeam, expectedSeat, sourceLeagueId: credentials.source_league_id, mode: "recommendation" });
  mountAdvisor(pageWindow.FBL.runner());
  return { status: "connected", session_id: sessionId, own_team: ownTeam, expected_seat: expectedSeat, bundle_hash: pageWindow.FBL.bundle_hash, mode: "recommendation" };
})()'''
    return (
        template.replace("__BASE_URL__", json.dumps(base))
        .replace("__CSRF_TOKEN__", json.dumps(csrf_token))
        .replace("__BUNDLE_HASH__", json.dumps(bundle.bundle_hash))
    )


def recommendation_bookmarklet_runtime(module_dir: Path = MODULE_DIR) -> str:
    """Return the dynamic runtime loaded by the installed bookmarklet.

    The runtime contains the same recommendation-only loader as the CLI
    snippet, but reads ``baseUrl``, ``csrfToken`` and ``bundleHash`` from a
    one-shot in-page bootstrap object.  Consequently this response contains
    no credential or checkout hash of its own; both values are supplied by the
    localhost bridge for the current invocation.
    """
    snippet = recommendation_loader_snippet(
        "__FBL_BOOKMARKLET_BASE__",
        csrf_token="__FBL_BOOKMARKLET_TOKEN__",
        module_dir=module_dir,
    )
    expression = snippet[len("await "):] if snippet.startswith("await ") else snippet
    expression = (
        expression
        .replace(json.dumps("__FBL_BOOKMARKLET_BASE__"), "config.baseUrl")
        .replace(json.dumps("__FBL_BOOKMARKLET_TOKEN__"), "config.csrfToken")
        .replace(json.dumps(build_bundle(module_dir).bundle_hash), "config.bundleHash")
    )
    # The callback is keyed by the bookmarklet nonce and is removed by the
    # bookmarklet after completion.  It lets a classic script report async
    # loader failures without putting token/hash values in a URL or console
    # command.
    return f"""(async () => {{
  const config = window.__FBL_ADVISOR_CONFIG__;
  const finish = config && typeof window[config.callbackKey] === "function" ? window[config.callbackKey] : null;
  try {{
    if (!config || !config.baseUrl || !config.csrfToken || !config.bundleHash || !finish) throw new Error("the bookmarklet bootstrap is missing or expired");
    const result = await {expression};
    finish(null, result);
  }} catch (error) {{
    if (finish) finish(error, null);
    else throw error;
  }}
}})().catch((error) => {{
  console.error("FBL advisor bookmarklet failed:", error);
}});
"""


def advisor_bookmarklet_href(base: str = "http://127.0.0.1:8765") -> str:
    """Return a stable bookmarklet bootstrap with no embedded credentials.

    The generated URL is intentionally small.  It validates the current tab,
    opens the same-origin localhost bridge, waits for the nonce-bound message,
    and loads the current runtime.  The bridge supplies the current CSRF token
    and bundle hash only after the bookmarklet is invoked.
    """
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("bookmarklet base must be an http(s) localhost URL")
    base = base.rstrip("/")
    allowed = json.dumps(list(BOOKMARKLET_PAGE_ORIGINS), separators=(",", ":"))
    base_json = json.dumps(base, separators=(",", ":"))
    return (
        "javascript:(()=>{"
        f"const b={base_json},a={allowed},o=location.origin;"
        "const fail=m=>{console.error('FBL advisor: '+m);if(typeof window.alert==='function')window.alert('FBL advisor: '+m)};"
        "if(!a.includes(o)||location.protocol!=='https:'||location.pathname.replace(/\\/+$/,'')!=='/basketball/draft'){fail('open an ESPN Fantasy Basketball draft room before using the bookmarklet');return}"
        "if(window.__FBL_ADVISOR_BUSY__){fail('another advisor bootstrap is already running; wait for it to finish');return}window.__FBL_ADVISOR_BUSY__=true;"
        "const n=(window.crypto&&typeof window.crypto.randomUUID==='function'?window.crypto.randomUUID():Date.now()+'-'+Math.random().toString(36).slice(2));"
        "const u=b+'/bookmarklet-bridge?targetOrigin='+encodeURIComponent(o)+'&nonce='+encodeURIComponent(n);"
        "let p=null,t=null;"
        "const done=(m)=>{window.removeEventListener('message',on);if(t)window.clearTimeout(t);if(p&&!p.closed)p.close();delete window['__FBL_ADVISOR_'+n];delete window.__FBL_ADVISOR_CONFIG__;window.__FBL_ADVISOR_BUSY__=false;if(m)fail(m)};"
        "const on=e=>{if(e.source!==p||e.origin!==new URL(b).origin||!e.data||e.data.type!=='fbl-advisor-bootstrap'||e.data.nonce!==n)return;"
        "window.__FBL_ADVISOR_CONFIG__={baseUrl:b,csrfToken:e.data.csrfToken,bundleHash:e.data.bundleHash,callbackKey:'__FBL_ADVISOR_'+n};"
        "const s=document.createElement('script');s.src=b+'/advisor-bookmarklet.js';s.onload=()=>{};s.onerror=()=>done('the local advisor runtime could not load; start the panel with --page-runner');document.head.appendChild(s)};"
        "window['__FBL_ADVISOR_'+n]=(error,result)=>{if(error)done(error&&error.message||String(error));else{done();console.info('FBL advisor connected',result)}};"
        "window.addEventListener('message',on);p=window.open(u,'fbl-advisor-bridge','popup,width=420,height=260');"
        "if(!p){done('the browser blocked the local bootstrap popup; allow popups for this bookmarklet and try again');return}"
        "t=window.setTimeout(()=>done('the local panel did not answer; keep the panel running and try again'),10000);"
        "})()"
    )


def bookmarklet_bridge_page(*, csrf_token: str, bundle_hash: str, target_origin: str, nonce: str) -> str:
    """Return the no-store localhost page that hands bootstrap data to ESPN."""
    if target_origin not in BOOKMARKLET_PAGE_ORIGINS:
        raise ValueError("bookmarklet bridge target must be an allowed ESPN origin")
    payload = json.dumps(
        {"type": "fbl-advisor-bootstrap", "csrfToken": csrf_token, "bundleHash": bundle_hash, "targetOrigin": target_origin, "nonce": nonce},
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
<title>FBL advisor bootstrap</title>
<p id="status">Connecting the FBL advisor to the ESPN tab…</p>
<script>
(() => {{
  const payload = {payload};
  const status = document.getElementById("status");
  if (!window.opener || window.opener.closed) {{
    status.textContent = "Open this page from the FBL advisor bookmarklet.";
    return;
  }}
  try {{
    window.opener.postMessage(payload, payload.targetOrigin);
    status.textContent = "Connected. This window can be closed.";
    window.setTimeout(() => window.close(), 120);
  }} catch (error) {{
    status.textContent = "The ESPN tab could not receive the advisor bootstrap.";
  }}
}})();
</script>"""


# Short alias for callers that describe the page flow as an advisor loader.
advisor_loader_snippet = recommendation_loader_snippet
