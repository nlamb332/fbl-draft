import http.client
import json
import re
import tempfile
import threading
import unittest

from sleeper_advisor.cli import Poller
from sleeper_advisor.panel import (
    OVERLAY_PATH, PAGE, SLEEPER_ORIGIN, SnapshotStore, create_server, start_polling)
from sleeper_advisor.tests.fakes import DRAFT_ID, USERNAME, fixture_client


class PanelServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = SnapshotStore()
        poller = Poller(fixture_client(cls.tmp.name, "mid"), DRAFT_ID, USERNAME, top=5)
        poller.poll()
        cls.store.update(poller.snapshot, poller.error)
        cls.poller = poller
        cls.server = create_server(cls.store, "127.0.0.1", 0, poller)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def request(self, method="GET", path="/", host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", host or f"127.0.0.1:{self.port}")
            conn.putheader("Content-Length", "0")
            for key, value in (headers or {}).items():
                conn.putheader(key, value)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with self.assertRaises(ValueError):
            create_server(SnapshotStore(), "0.0.0.0", 0)
        with self.assertRaises(ValueError):
            create_server(SnapshotStore(), "192.168.1.5", 0)

    def test_index_is_self_contained(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        html = body.decode("utf-8")
        self.assertEqual(html, PAGE)
        self.assertEqual(set(re.findall(r"https?://[^\s\"'+]*", html)),
                         {"http://127.0.0.1:", "https://sleeper.com"})  # bookmarklet only
        self.assertNotRegex(html, r"(?i)<[^>]*(src|href)\s*=")  # no external markup resources
        self.assertIn("prefers-color-scheme", html)
        self.assertIn("viewport", html)
        self.assertIn("/api/state", html)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("Install the Sleeper bookmarklet", html)
        self.assertIn("/overlay.js", html)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_api_state_json(self):
        status, headers, body = self.request(path="/api/state?x=1")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ready"])
        self.assertEqual(data["our_slot"], 3)
        self.assertEqual(len(data["recommendations"]), 5)
        self.assertIn("legend", data)
        self.assertIsNone(data["error"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_localhost_host_header_allowed(self):
        self.assertEqual(self.request(host=f"localhost:{self.port}")[0], 200)

    def test_bad_host_forbidden(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}",
                     f"localhost.evil.example:{self.port}"):
            self.assertEqual(self.request(host=host)[0], 403, host)
            self.assertEqual(self.request(path="/api/state", host=host)[0], 403, host)

    def test_non_get_rejected(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
            status, headers, _ = self.request(method=method, path="/api/state")
            self.assertEqual(status, 405, method)
            self.assertEqual(headers.get("Allow"), "GET")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_unknown_path_404(self):
        self.assertEqual(self.request(path="/nope")[0], 404)

    # -- overlay + CORS ---------------------------------------------------
    def test_overlay_served_as_javascript_without_csp(self):
        status, headers, body = self.request(path="/overlay.js?v=123")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Content-Security-Policy", headers)
        self.assertEqual(body, OVERLAY_PATH.read_bytes())
        self.assertNotIn("Access-Control-Allow-Origin", headers)  # no Origin sent

    def test_cors_only_for_sleeper_origin(self):
        for path in ("/api/state", "/overlay.js"):
            _, headers, _ = self.request(path=path, headers={"Origin": SLEEPER_ORIGIN})
            self.assertEqual(headers["Access-Control-Allow-Origin"], SLEEPER_ORIGIN, path)
            self.assertEqual(headers["Vary"], "Origin")
            for origin in ("https://evil.example", "http://sleeper.com",
                           "https://sleeper.com.evil.example", "null"):
                _, headers, _ = self.request(path=path, headers={"Origin": origin})
                self.assertNotIn("Access-Control-Allow-Origin", headers, (path, origin))
        _, headers, _ = self.request(path="/", headers={"Origin": SLEEPER_ORIGIN})
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight(self):
        req = {"Origin": SLEEPER_ORIGIN, "Access-Control-Request-Method": "GET",
               "Access-Control-Request-Private-Network": "true"}
        for path in ("/api/state?draft=1", "/overlay.js"):
            status, headers, body = self.request("OPTIONS", path, headers=req)
            self.assertEqual(status, 204, path)
            self.assertEqual(body, b"")
            self.assertEqual(headers["Access-Control-Allow-Origin"], SLEEPER_ORIGIN)
            self.assertEqual(headers["Access-Control-Allow-Methods"], "GET")
            self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        status, headers, _ = self.request("OPTIONS", "/api/state", headers={"Origin": SLEEPER_ORIGIN})
        self.assertEqual(status, 204)
        self.assertNotIn("Access-Control-Allow-Private-Network", headers)

    def test_preflight_rejected_for_other_origins_and_paths(self):
        status, headers, _ = self.request("OPTIONS", "/api/state", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 405)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("OPTIONS", "/", headers={"Origin": SLEEPER_ORIGIN})[0], 405)
        self.assertEqual(self.request("POST", "/api/state", headers={"Origin": SLEEPER_ORIGIN})[0], 405)
        self.assertEqual(self.request("OPTIONS", "/api/state", host="evil.example",
                                      headers={"Origin": SLEEPER_ORIGIN})[0], 403)

    def test_invalid_draft_param_rejected(self):
        for bad in ("abc", "12a", "", "1" * 26, "-5", "1.5", "%E0%A5%A7"):
            status, _, _ = self.request(path=f"/api/state?draft={bad}")
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.poller.draft_id, DRAFT_ID)

    def test_same_draft_param_keeps_snapshot(self):
        status, _, body = self.request(path=f"/api/state?draft={DRAFT_ID}")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ready"])

    def test_state_has_identity(self):
        data = json.loads(self.request(path="/api/state")[2])
        self.assertEqual(data["identity"], "matched")
        self.assertIsNone(data["identity_banner"])


class DraftSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SnapshotStore()
        self.poller = Poller(fixture_client(self.tmp.name, "mid"), DRAFT_ID, USERNAME, slot=3, top=3)
        self.poller.poll()
        self.store.update(self.poller.snapshot, self.poller.error)
        self.server = create_server(self.store, "127.0.0.1", 0, self.poller)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_switching_draft_resets_poller_and_drops_slot(self):
        status, body = self.get("/api/state?draft=123456789")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ready"])  # old snapshot cleared
        self.assertEqual(self.poller.draft_id, "123456789")
        self.assertIsNone(self.poller.slot)
        self.assertEqual(self.poller.username, USERNAME)
        self.assertIsNone(self.poller.snapshot)

    def test_switching_back_restores_original_slot(self):
        self.get("/api/state?draft=123456789")
        self.get(f"/api/state?draft={DRAFT_ID}")
        self.assertEqual(self.poller.draft_id, DRAFT_ID)
        self.assertEqual(self.poller.slot, 3)

    def test_stale_result_from_previous_draft_is_discarded(self):
        poller = self.poller
        original = poller.client._client.fetch_json

        def switching(url):
            poller.switch_draft("555")  # user navigates while a poll is in flight
            return original(url)

        poller.client._client.fetch_json = switching
        self.assertFalse(poller.poll())
        self.assertIsNone(poller.snapshot)


class OverlayScriptTests(unittest.TestCase):
    def setUp(self):
        self.js = OVERLAY_PATH.read_text(encoding="utf-8")

    def test_no_unsafe_apis(self):
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "document.write"):
            self.assertNotIn(banned, self.js)

    def test_uses_shadow_dom_and_polls_state(self):
        self.assertIn("attachShadow", self.js)
        self.assertIn("/api/state?draft=", self.js)
        self.assertIn("POLL_MS = 3000", self.js)
        self.assertIn("__sleeperAdvisor", self.js)

    def test_only_local_default_host(self):
        self.assertEqual(set(re.findall(r"https?://[^\s\"']+", self.js)),
                         {"http://127.0.0.1:8766"})


class StoreAndPollingTests(unittest.TestCase):
    def test_not_ready_payload(self):
        store = SnapshotStore()
        store.update(None, "boom")
        data = store.payload()
        self.assertFalse(data["ready"])
        self.assertEqual(data["error"], "boom")

    def test_background_polling_publishes_and_keeps_snapshot_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = fixture_client(tmp, "mid")
            poller = Poller(client, DRAFT_ID, USERNAME, top=3)
            store = SnapshotStore()
            stop = threading.Event()
            thread = start_polling(poller, store, 0.01, stop, sleep=lambda s: None, max_iterations=3)
            thread.join(timeout=30)
            stop.set()
            self.assertFalse(thread.is_alive())
            data = store.payload()
            self.assertTrue(data["ready"])
            self.assertEqual(len(data["recommendations"]), 3)

            def failing(url):
                raise OSError("offline")

            client.fetch_json = failing
            poller.client._client.fetch_json = failing
            poller.poll()
            store.update(poller.snapshot, poller.error)
            data = store.payload()
            self.assertTrue(data["ready"])  # last good snapshot retained
            self.assertIn("offline", data["error"])

    def test_page_has_no_external_urls_or_inline_names(self):
        hosts = set(re.findall(r"https?://[^\s\"'+]*", PAGE))
        self.assertLessEqual(hosts, {"http://127.0.0.1:", "https://sleeper.com"})


if __name__ == "__main__":
    unittest.main()
