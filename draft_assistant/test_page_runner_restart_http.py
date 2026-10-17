"""Served-bundle restart coverage for durable page attachment checkpoints."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from .page_bundle import build_bundle
from .test_dress_rehearsal import PRACTICE_LEAGUE, make_catalog, read_rehearsal_report
from .test_page_runner_http import PageRunnerServerCase, ROOT


class PageRunnerRestartEndToEndTests(PageRunnerServerCase):
    page_runner = True

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_fresh_attachment_restores_confirmed_actions_before_continuing(self) -> None:
        owner = self.coordinator.ownership.claim(self.session_id, label="page-runner-restart-test")["owner_token"]
        bundle = build_bundle()
        bundle_path = self.folder / "page-runner.js"
        bundle_path.write_text(self.get("/page-runner.js")[1].decode("utf-8"), encoding="utf-8")
        payload = {
            "base": self.base,
            "sessionId": self.session_id,
            "ownerToken": owner,
            "csrfToken": self.coordinator.csrf_token,
            "bundlePath": str(bundle_path),
            "bundleHash": bundle.bundle_hash,
            "room": {"league_id": PRACTICE_LEAGUE, "season_id": "2027", "team_id": "15"},
            "catalog": make_catalog(),
            "rules": {
                "teams": 14,
                "rounds": 10,
                "starting_slots": list(self.config.starting_slots),
                "bench_slots": 4,
                "ir_slots": 3,
                "scoring": dict(self.config.scoring),
            },
        }
        config_path = self.folder / "restart-config.json"
        report_path = self.folder / "restart-report.json"
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "draft_assistant/fixtures/page_runner_restart_driver.js"), str(config_path), str(report_path)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=180,
        )
        report = read_rehearsal_report(result, report_path)
        self.assertEqual(report["first_result"]["status"], "stopped", report)
        self.assertGreaterEqual(report["first_result"]["own_picks_confirmed"], 2, report)
        self.assertEqual(report["second_result"]["status"], "completed", report)
        self.assertEqual(report["second_result"]["own_picks_confirmed"], 10, report)
        self.assertEqual(report["picks"], 140)
        self.assertEqual(report["unique"], 140)
        self.assertEqual(report["clicks"], 10)
        self.assertEqual(report["native_resume_clicks"], 2)
        self.assertEqual(report["attachment"]["own_picks_confirmed"], len(report["first_status"]["confirmed_action_ids"]))
        self.assertEqual(report["attachment"]["own_picks_confirmed"], 2)
        self.assertEqual(report["first_status"]["confirmed_action_ids"], report["final_status"]["confirmed_action_ids"][:2])
        self.assertEqual(report["final_status"]["state"], "completed")
        status = self.coordinator.status(self.session_id)
        self.assertEqual(status["state"], "completed")
        self.assertFalse(status["unresolved_actions"])


if __name__ == "__main__":
    unittest.main()

