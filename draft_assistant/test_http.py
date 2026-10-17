"""Exercise the real localhost HTTP boundary, without browser or ESPN writes."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .coordinator import Coordinator
from .server import create_server


class HTTPIntegrationTests(unittest.TestCase):
    def test_panel_connect_and_control_remain_observing_without_verified_state(self):
        Path("runtime").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="runtime") as directory:
            coordinator = Coordinator(directory + "/control.sqlite3", recover=False)
            server = create_server(coordinator, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base) as response:
                    panel = response.read().decode()
                self.assertIn("FBL Draft Control", panel)
                self.assertNotIn("__CSRF_TOKEN__", panel)
                with urlopen(base + "/api/runtime-info") as response:
                    metadata = json.load(response)
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
                self.assertEqual(metadata["service"], "fbl-draft-control")
                self.assertEqual(metadata["policy"]["name"], "foundation")
                self.assertTrue(metadata["startup_source"]["source_hashes_sha256"])
                self.assertNotIn(coordinator.csrf_token, json.dumps(metadata))
                body = json.dumps({"url": "https://fantasy.espn.com/basketball/draft?leagueId=1000000011&seasonId=2027&teamId=15", "practice": True}).encode()
                request = Request(base + "/api/connect", body, {"Content-Type": "application/json", "Origin": base})
                with urlopen(request) as response:
                    status = json.load(response)
                self.assertEqual(status["state"], "observing")
                self.assertFalse(status["readiness"]["ready"])
                body = json.dumps({"session_id": status["session_id"], "command": "start"}).encode()
                request = Request(base + "/api/control", body, {"Content-Type": "application/json", "Origin": base})
                with self.assertRaises(HTTPError) as blocked:
                    urlopen(request)
                self.assertEqual(blocked.exception.code, 409)
                request = Request(base + "/api/control", body, {"Content-Type": "application/json", "Origin": "https://example.com"})
                with self.assertRaises(HTTPError) as blocked:
                    urlopen(request)
                self.assertEqual(blocked.exception.code, 403)
                with urlopen(base + "/api/status") as response:
                    self.assertEqual(json.load(response)["sessions"][0]["state"], "observing")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                coordinator.close()
