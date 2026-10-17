"""Focused checks for the local ESPN advisor bookmarklet bootstrap."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from .coordinator import Coordinator
from .page_bundle import (
    advisor_bookmarklet_href,
    build_bundle,
    recommendation_bookmarklet_runtime,
)
from .server import create_server


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "draft_assistant" / "static"


class BookmarkletUnitTests(unittest.TestCase):
    def test_install_card_explains_dragging_the_link_and_manual_url_fallback(self) -> None:
        panel = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("Drag THIS link", panel)
        self.assertIn("Do not bookmark this panel page", panel)
        self.assertIn('id="copy-advisor-bookmarklet"', panel)
        self.assertIn('id="advisor-bookmarklet-url"', panel)
        self.assertIn("Right-click the Chrome bookmarks bar", panel)
        self.assertIn("event.preventDefault()", app)
        self.assertIn("navigator.clipboard.writeText", app)
        self.assertIn("press Ctrl+C", app)

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_panel_app_is_valid_javascript(self) -> None:
        completed = subprocess.run(
            [shutil.which("node"), "--check", str(STATIC_DIR / "app.js")],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_bookmarklet_is_a_stable_bootstrap_without_token_or_hash(self) -> None:
        href = advisor_bookmarklet_href("http://127.0.0.1:8765")
        self.assertTrue(href.startswith("javascript:"))
        self.assertIn("/bookmarklet-bridge?", href)
        self.assertIn("/advisor-bookmarklet.js", href)
        self.assertNotIn("test-csrf", href)
        self.assertNotIn(build_bundle().bundle_hash, href)

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_dynamic_runtime_is_valid_javascript_and_has_no_embedded_bootstrap_values(self) -> None:
        runtime = recommendation_bookmarklet_runtime()
        completed = subprocess.run(
            [shutil.which("node"), "--check"],
            input=runtime,
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("window.__FBL_ADVISOR_CONFIG__", runtime)
        self.assertNotIn("__FBL_BOOKMARKLET_", runtime)
        self.assertNotIn(build_bundle().bundle_hash, runtime)

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_bookmarklet_source_is_valid_javascript(self) -> None:
        href = advisor_bookmarklet_href()
        source = href[len("javascript:"):]
        completed = subprocess.run(
            [shutil.which("node"), "--check"],
            input=source,
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_bookmarklet_bootstrap_accepts_only_matching_popup_message(self) -> None:
        href = advisor_bookmarklet_href()
        source = href[len("javascript:"):]
        script = f"""
const vm = require("vm");
const source = {json.dumps(source)};
const alerts = [];
const popup = {{closed: false, close() {{ this.closed = true; }}}};
let messageHandler = null;
const context = {{
  location: {{origin: "https://fantasy.espn.com", protocol: "https:", pathname: "/basketball/draft"}},
  crypto: {{randomUUID: () => "nonce-test"}},
  URL,
  console: {{error() {{}}, info() {{}}}},
  alert(value) {{ alerts.push(value); }},
  open(url) {{ popup.url = url; return popup; }},
  addEventListener(_name, handler) {{ messageHandler = handler; }},
  removeEventListener(_name, handler) {{ if (messageHandler === handler) messageHandler = null; }},
  setTimeout(handler) {{ popup.timer = handler; return 1; }},
  clearTimeout() {{}},
  document: {{
    createElement() {{ return {{}}; }},
    head: {{appendChild(node) {{
      if (node.src !== "http://127.0.0.1:8765/advisor-bookmarklet.js") throw new Error("unexpected runtime URL");
      const config = context.__FBL_ADVISOR_CONFIG__;
      context[config.callbackKey](null, {{status: "connected"}});
    }}}}
  }}
}};
context.window = context;
vm.createContext(context);
vm.runInContext(source, context);
const nonce = new URL(popup.url).searchParams.get("nonce");
messageHandler({{source: popup, origin: "http://127.0.0.1:8765", data: {{type: "fbl-advisor-bootstrap", nonce, csrfToken: "fresh-token", bundleHash: "fresh-hash"}}}});
if (alerts.length || context.__FBL_ADVISOR_BUSY__ || context.__FBL_ADVISOR_CONFIG__ || !popup.closed) throw new Error("bootstrap did not cleanly complete");
console.log(JSON.stringify({{nonce, alerts}}));
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"nonce":"nonce-test"', completed.stdout)


class BookmarkletHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "runtime").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.coordinator = Coordinator(Path(self.tmp.name) / "control.sqlite3", recover=False)
        self.server = create_server(self.coordinator, port=0, page_runner_enabled=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.coordinator.close()
        self.tmp.cleanup()

    def get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_panel_offers_drag_install_link_without_secrets(self) -> None:
        status, body = self.get("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn('id="advisor-bookmarklet"', text)
        self.assertIn("javascript:", text)
        self.assertNotIn("__BOOKMARKLET_HREF__", text)
        anchor_start = text.index('id="advisor-bookmarklet"')
        anchor_end = text.index("</a>", anchor_start)
        self.assertNotIn(self.coordinator.csrf_token, text[anchor_start:anchor_end])

    def test_bridge_uses_current_token_and_bundle_hash_for_allowed_origin(self) -> None:
        status, body = self.get("/bookmarklet-bridge?targetOrigin=https%3A%2F%2Ffantasy.espn.com&nonce=nonce_123")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn(self.coordinator.csrf_token, text)
        self.assertIn(build_bundle().bundle_hash, text)
        self.assertIn('"targetOrigin":"https://fantasy.espn.com"', text)
        self.assertIn('"nonce":"nonce_123"', text)
        self.assertNotIn("Access-Control-Allow-Origin", text)

    def test_bridge_rejects_non_espn_target_and_runtime_is_secret_free(self) -> None:
        status, body = self.get("/bookmarklet-bridge?targetOrigin=https%3A%2F%2Fevil.example&nonce=nonce_123")
        self.assertEqual(status, 400)
        self.assertIn("allowed ESPN page", body.decode("utf-8"))
        status, body = self.get("/advisor-bookmarklet.js")
        self.assertEqual(status, 200)
        runtime = body.decode("utf-8")
        self.assertNotIn(self.coordinator.csrf_token, runtime)
        self.assertNotIn(build_bundle().bundle_hash, runtime)


class BookmarkletDisabledHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "runtime").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.coordinator = Coordinator(Path(self.tmp.name) / "control.sqlite3", recover=False)
        self.server = create_server(self.coordinator, port=0, page_runner_enabled=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.coordinator.close()
        self.tmp.cleanup()

    def get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_disabled_server_does_not_serve_bookmarklet_runtime_or_bridge(self) -> None:
        self.assertEqual(self.get("/advisor-bookmarklet.js")[0], 404)
        self.assertEqual(self.get("/bookmarklet-bridge?targetOrigin=https%3A%2F%2Ffantasy.espn.com&nonce=nonce_123")[0], 404)
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Start the panel with --page-runner", body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
