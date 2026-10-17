"""Served-bundle E2E coverage for the default paused-room capture path."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from .page_bundle import build_bundle
from .test_dress_rehearsal import make_catalog, read_rehearsal_report
from .test_page_runner_http import PageRunnerServerCase


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node is required")
class PageCaptureEndToEndTests(PageRunnerServerCase):
    def test_capture_inputs_uses_rules_toggle_projection_wheels_and_history_wheel(self) -> None:
        owner = self.coordinator.ownership.claim(self.session_id, label="capture-inputs-test")["owner_token"]
        bundle = build_bundle()
        bundle_path = self.folder / "page-runner.js"
        bundle_path.write_text(self.get("/page-runner.js")[1].decode("utf-8"), encoding="utf-8")
        catalog = make_catalog(240)
        config = {
            "base": self.base,
            "sessionId": self.session_id,
            "ownerToken": owner,
            "csrfToken": self.coordinator.csrf_token,
            "bundlePath": str(bundle_path),
            "bundleHash": bundle.bundle_hash,
            "room": {"league_id": "1000000012", "season_id": "2027", "team_id": "15"},
            "catalog": catalog,
            "initialPickCount": 50,
            "playersWindow": 14,
            "historyWindow": 14,
        }
        config_path = self.folder / "capture-config.json"
        report_path = self.folder / "capture-report.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run(
            [
                shutil.which("node"),
                str(ROOT / "draft_assistant" / "fixtures" / "page_capture_driver.js"),
                str(config_path),
                str(report_path),
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        report = read_rehearsal_report(result, report_path)

        self.assertEqual(report["inputs"]["catalog_players"], len(catalog))
        self.assertEqual(report["inputs"]["complete_core_stats"], len(catalog))
        self.assertEqual(report["inputs"]["skipped_without_position"], 0)
        self.assertTrue(report["inputs"]["catalog_complete"])
        self.assertEqual(report["inputs"]["termination_reason"], "end_of_grid")
        self.assertEqual(report["summary"]["inputs"]["source"], "captured")
        self.assertEqual(report["summary"]["inputs"]["catalog_players"], len(catalog))
        self.assertEqual(report["summary"]["inputs"]["rules"]["teams"], 14)
        self.assertEqual(report["summary"]["inputs"]["rules"]["rounds"], 10)
        self.assertEqual(report["summary"]["inputs"]["rules"]["starting_slots"], ["C", "G", "G", "F", "F", "UTIL"])
        self.assertEqual(report["summary"]["inputs"]["rules"]["scoring"]["3PM"], 1)
        self.assertEqual(report["view"], "players")
        self.assertTrue(report["paused"])
        self.assertFalse(report["show_drafted"])
        self.assertEqual(report["players_top"], 0)
        self.assertEqual(report["players_column_page"], "left")
        self.assertGreater(report["right_scrolls"], 0)
        self.assertGreater(report["left_scrolls"], 0)
        self.assertGreater(report["vertical_scrolls"], 0)
        self.assertGreater(report["history_scrolls"], 0)
        self.assertEqual(report["rendered_drafted_ids"], [str(3000000 + index) for index in range(50)])
        self.assertEqual(report["preflight"]["read_back"]["native_timer_seconds"], 30)
        self.assertEqual(report["preflight"]["read_back"]["history"], 50)


if __name__ == "__main__":
    unittest.main()
