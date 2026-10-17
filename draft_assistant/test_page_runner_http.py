"""Harness-agnostic in-page runner: served bundle, page-origin gate, full offline run."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from .contracts import LeagueConfig
from .coordinator import Coordinator
from .marginal_lineup import DEFAULT_PROMOTION_PATH
from .page_bundle import BUNDLE_MODULES, build_bundle, loader_snippet
from .server import create_server
from .test_dress_rehearsal import HEADING, PRACTICE_LEAGUE, make_catalog, read_rehearsal_report

ROOT = Path(__file__).resolve().parents[1]
ROOM_URL = f"https://fantasy.espn.com/basketball/draft?leagueId={PRACTICE_LEAGUE}&seasonId=2027&teamId=15"
PAGE_ORIGIN = "https://fantasy.espn.com"


class PageRunnerServerCase(unittest.TestCase):
    page_runner = True

    def setUp(self) -> None:
        (ROOT / "runtime").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.folder = Path(self.tmp.name)
        self.config = LeagueConfig()
        self.coordinator = Coordinator(self.folder / "control.sqlite3", recover=False, promotion_path=DEFAULT_PROMOTION_PATH)
        self.server = create_server(self.coordinator, port=0, page_runner_enabled=self.page_runner)
        self.server.listener_counter = lambda port: 1
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.session_id = self.coordinator.connect(ROOM_URL, practice=True)["session_id"]
        self.coordinator.attest_practice(self.session_id, HEADING, self.config.league_id)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.coordinator.close()
        self.tmp.cleanup()

    def page_post(self, payload: dict, *, token: str | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.base}/api/operator",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": PAGE_ORIGIN, "X-CSRF-Token": token or self.coordinator.csrf_token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()


class PageOriginGateTests(PageRunnerServerCase):
    def test_unowned_session_is_refused_even_for_status(self) -> None:
        code, body = self.page_post({"operation": "status", "session_id": self.session_id})
        self.assertEqual(code, 409)
        self.assertIn("already has an owner", body["error"])

    def test_owned_session_requires_the_owner_token(self) -> None:
        token = self.coordinator.ownership.claim(self.session_id, label="test")["owner_token"]
        code, _ = self.page_post({"operation": "status", "session_id": self.session_id, "owner_token": "wrong"})
        self.assertEqual(code, 409)
        code, body = self.page_post({"operation": "status", "session_id": self.session_id, "owner_token": token})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["session_id"], self.session_id)

    def test_ownership_and_queue_operations_stay_off_the_page(self) -> None:
        self.coordinator.ownership.claim(self.session_id, label="test")
        for operation in ("claim-owner", "transfer-owner", "queue-prepare"):
            code, body = self.page_post({"operation": operation, "session_id": self.session_id})
            self.assertEqual(code, 403, operation)
            self.assertIn("may only publish heartbeats", body["error"])

    def test_bundle_is_served_with_its_source_hashes(self) -> None:
        code, text = self.get("/page-runner.js")
        self.assertEqual(code, 200)
        bundle = build_bundle()
        self.assertEqual(text.decode("utf-8"), bundle.text)
        self.assertEqual(set(bundle.source_hashes), {f"draft_assistant/{name}" for name in BUNDLE_MODULES})
        self.assertNotIn(self.coordinator.csrf_token, bundle.text)

    def test_loader_snippet_pins_the_bundle_hash_and_reads_the_token(self) -> None:
        snippet = loader_snippet(self.base)
        self.assertIn(build_bundle().bundle_hash, snippet)
        self.assertIn(self.coordinator.csrf_token, snippet)
        self.assertIn("/page-runner.js?h=", snippet)


class PageRunnerDisabledTests(PageRunnerServerCase):
    page_runner = False

    def test_default_server_keeps_the_page_to_heartbeats(self) -> None:
        self.coordinator.ownership.claim(self.session_id, label="test")
        code, _ = self.page_post({"operation": "status", "session_id": self.session_id})
        self.assertEqual(code, 403)
        self.assertEqual(self.get("/page-runner.js")[0], 404)


@unittest.skipUnless(shutil.which("node"), "Node is required")
class PageRunnerEndToEndTests(PageRunnerServerCase):
    def test_served_bundle_completes_a_guarded_practice_from_the_page_origin(self) -> None:
        owner = self.coordinator.ownership.claim(self.session_id, label="page-runner-test")["owner_token"]
        bundle = build_bundle()
        (self.folder / "page-runner.js").write_text(self.get("/page-runner.js")[1].decode("utf-8"), encoding="utf-8")
        payload = {
            "base": self.base, "sessionId": self.session_id, "ownerToken": owner, "csrfToken": self.coordinator.csrf_token,
            "bundlePath": str(self.folder / "page-runner.js"), "bundleHash": bundle.bundle_hash,
            "room": {"league_id": PRACTICE_LEAGUE, "season_id": "2027", "team_id": "15"},
            "catalog": make_catalog(),
            "rules": {"teams": 14, "rounds": 10, "starting_slots": list(self.config.starting_slots), "bench_slots": 4, "ir_slots": 3, "scoring": dict(self.config.scoring)},
        }
        (self.folder / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run([shutil.which("node"), str(ROOT / "draft_assistant/fixtures/page_runner_driver.js"),
                                 str(self.folder / "config.json"), str(self.folder / "report.json")],
                                capture_output=True, text=True, cwd=ROOT, timeout=120)
        report = read_rehearsal_report(result, self.folder / "report.json")
        self.assertEqual(report["preflight"]["read_back"]["native_timer_seconds"], 30)
        self.assertEqual(report["summary"]["batch"]["status"], "completed", report["summary"])
        self.assertEqual(report["picks"], 140)
        self.assertEqual(report["unique"], 140)
        self.assertEqual(report["clicks"], 10)
        self.assertEqual(report["native_resume_clicks"], 1)
        self.assertEqual(len(report["evidence"]["latency_rows"]), 10)
        status = self.coordinator.status(self.session_id)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["pick_timer_seconds"], 30.0)
        self.assertFalse(status["unresolved_actions"])
        self.assertTrue(all(a["status"] == "confirmed" and a["claimed"] and a["dispatched"] for a in status["actions"]))
        journal = self.coordinator.journal(self.session_id, limit=10000)
        self.assertFalse(any(e["event"] in {"pause", "stale_pause", "stale_degraded", "action_uncertain"} for e in journal))


if __name__ == "__main__":
    unittest.main()
