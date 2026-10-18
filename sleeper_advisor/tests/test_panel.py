import http.client
import json
import re
import tempfile
import threading
import unittest

from sleeper_advisor.cli import Poller
from sleeper_advisor.panel import PAGE, SnapshotStore, create_server, start_polling
from sleeper_advisor.tests.fakes import DRAFT_ID, USERNAME, fixture_client


class PanelServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = SnapshotStore()
        poller = Poller(fixture_client(cls.tmp.name, "mid"), DRAFT_ID, USERNAME, top=5)
        poller.poll()
        cls.store.update(poller.snapshot, poller.error)
        cls.server = create_server(cls.store, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def request(self, method="GET", path="/", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", host or f"127.0.0.1:{self.port}")
            conn.putheader("Content-Length", "0")
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
        self.assertNotRegex(html, r"https?://")
        self.assertNotRegex(html, r"(?i)(src|href)\s*=")
        self.assertIn("prefers-color-scheme", html)
        self.assertIn("viewport", html)
        self.assertIn("/api/state", html)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)
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
        self.assertIsNone(re.search(r"https?://", PAGE))


if __name__ == "__main__":
    unittest.main()
