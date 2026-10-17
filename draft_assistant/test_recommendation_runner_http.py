"""Recommendation-only served-bundle coverage over real localhost HTTP."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

from .page_bundle import build_bundle
from .test_dress_rehearsal import HEADING, PRACTICE_LEAGUE, make_catalog, read_rehearsal_report
from .test_page_runner_http import PageRunnerServerCase


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node is required for recommendation-only coverage")
class RecommendationRunnerEndToEndTests(PageRunnerServerCase):
    def _preview_payload(self, owner: str) -> dict:
        catalog = make_catalog()
        teams = ["Example Team", *[f"Opponent {index}" for index in range(1, 14)]]
        return {
            "operation": "recommend-preview",
            "session_id": self.session_id,
            "owner_token": owner,
            "observation": {
                "session_id": self.session_id,
                "room": {"league_id": PRACTICE_LEAGUE, "season_id": "2027", "team_id": "15", "practice": True},
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "revision": 1,
                "phase": "draft",
                "current_pick": 1,
                "on_clock_team": "Example Team",
                "seconds_remaining": None,
                "pick_order": teams,
                "history": [],
                "roster": [],
                "available_players": catalog,
                "known_players": catalog,
                "autopick": True,
                "rules": {"teams": 14, "rounds": 10, "starting_slots": list(self.config.starting_slots), "bench_slots": 4, "ir_slots": 3, "scoring": dict(self.config.scoring)},
                "complete": True,
                "history_complete": True,
                "available_pool_complete": True,
                "warnings": [],
                "schema_version": 1,
            },
        }

    def test_preview_rejects_unverified_rules_and_order_without_readiness_gates(self) -> None:
        owner = self.coordinator.ownership.claim(self.session_id, label="preview-contract-test")["owner_token"]
        valid = self._preview_payload(owner)
        code, body = self.page_post(valid)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "preview")

        bad_rules = json.loads(json.dumps(valid))
        bad_rules["observation"]["rules"]["rounds"] = 9
        code, body = self.page_post(bad_rules)
        self.assertIn(code, (400, 409))
        self.assertIn("verified 14-team", body["error"])

        bad_order = json.loads(json.dumps(valid))
        bad_order["observation"]["pick_order"][1] = bad_order["observation"]["pick_order"][0]
        code, body = self.page_post(bad_order)
        self.assertIn(code, (400, 409))
        self.assertIn("fourteen unique teams", body["error"])

    def test_partial_visible_catalog_preview_allows_only_the_recognized_subset_marker(self) -> None:
        owner = self.coordinator.ownership.claim(self.session_id, label="partial-preview-contract-test")["owner_token"]
        partial = self._preview_payload(owner)
        catalog = partial["observation"]["known_players"]
        partial["observation"]["available_players"] = catalog[:12]
        partial["observation"]["available_pool_complete"] = False
        partial["observation"]["warnings"] = ["recommendation_catalog_incomplete"]
        code, body = self.page_post(partial)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "preview")
        self.assertEqual(self.coordinator.status(self.session_id).get("actions"), [])
        self.assertIsNone(self.coordinator.status(self.session_id).get("observation"))

        unknown = json.loads(json.dumps(partial))
        unknown["observation"]["warnings"].append("other_incomplete_warning")
        code, body = self.page_post(unknown)
        self.assertIn(code, (400, 409))
        self.assertIn("unresolved feed conflict or gap", body["error"])

    def test_full_manual_feed_produces_advisory_previews_without_actor_state(self) -> None:
        owner = self.coordinator.ownership.claim(self.session_id, label="recommendation-only-test")["owner_token"]
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
            "catalogComplete": True,
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
        config_path = self.folder / "config.json"
        report_path = self.folder / "report.json"
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "draft_assistant/fixtures/recommendation_runner_driver.js"), str(config_path), str(report_path)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        report = read_rehearsal_report(result, report_path)
        self.assertEqual(report["picks"], 140)
        self.assertEqual(report["unique"], 140)
        self.assertEqual(report["clicks"], 0)
        self.assertEqual(report["native_resume_clicks"], 0)
        self.assertGreaterEqual(report["recommendation_successes"], 10)
        self.assertEqual(report["observed_history_count"], 140)
        self.assertEqual(report["observed_roster_count"], 10)
        own_recommendations = report["own_recommendations"]
        self.assertEqual(len(own_recommendations), 10)
        self.assertEqual([item["pick"] for item in own_recommendations], [1, 28, 29, 56, 57, 84, 85, 112, 113, 140])
        self.assertTrue(all(item["player_id"] for item in own_recommendations))
        self.assertTrue(all(item["observed_at"] and item["policy_status"] for item in own_recommendations))
        own_turns = report["own_turns"]
        self.assertEqual(len(own_turns), 10)
        self.assertEqual([item["pick"] for item in own_turns], [1, 28, 29, 56, 57, 84, 85, 112, 113, 140])
        self.assertTrue(all(item["player_id"] and item["observed_at"] and item["policy_status"] for item in own_turns))
        self.assertEqual(report["summary"]["mode"], "recommendation")
        self.assertFalse(report["summary"]["recommendation"]["running"])
        diagnostics = report["summary"]["recommendation"]["diagnostics"]
        self.assertGreaterEqual(sum(item.get("event") == "recommendation" for item in diagnostics), 10)
        self.assertGreaterEqual(sum(item.get("event") == "feed-health" for item in diagnostics), 10)
        self.assertEqual(report["summary"]["batch"], None)

        status = self.coordinator.status(self.session_id)
        self.assertIsNone(status.get("observation"), "preview frames must never become persisted actionable observations")
        self.assertEqual(status.get("actions"), [])
        self.assertEqual(status.get("unresolved_actions"), [])
        journal = self.coordinator.journal(self.session_id, limit=10000)
        forbidden = {"observation", "recommendation", "action_claimed", "action_dispatched", "action_confirmed", "start", "resume", "preflight"}
        self.assertFalse(any(event.get("event") in forbidden for event in journal), journal)


if __name__ == "__main__":
    unittest.main()
