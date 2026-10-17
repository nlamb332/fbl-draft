"""Issue #43: offline full dress rehearsal of the canonical modules over real HTTP.

`rehearsal_harness.js` drives browser_observer.js, operator_runtime.js,
native_activation.js and batch_recovery.js against the room simulator while the
Python coordinator serves the real /api/operator and /api/control endpoints.
The sanitized export is written to a temporary directory (never into
validation/).  Assertions that depend on the #41 heartbeat or #42/#46
post-click confirmation are expected failures that document current behavior.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mock_draft.fixture import SCORING_WEIGHTS

from .contracts import LeagueConfig
from .coordinator import Coordinator
from .preflight import count_local_listeners
from .server import create_server
from .session_export import ARTIFACT_VERSION, build_sanitized_export
from .valuation import recommend

ROOT = Path(__file__).resolve().parents[1]
PRACTICE_LEAGUE = "1000000012"
HEADING = "ESPN Fantasy Basketball Draft - Practice Draft for Example League"
ELIGIBILITY = (["C"], ["G"], ["F"], ["G", "F"], ["C", "F"], ["G"], ["F"], ["C", "F"], ["G", "F"], ["G"], ["F"], ["C"])
FAULTS = {
    "missing_resume": True,
    "recommended_player_vanishes": {"own_pick_index": 2},
    "post_click_hydration_lag": {"own_pick_index": 5},
    "observation_gap": {"at_pick": 56},
    "agent_crash_handoff": {"at_pick": 84},
}
PROMOTION = ROOT / "draft_assistant" / "validation" / "marginal-lineup-v1" / "promotion_artifact.json"


def read_rehearsal_report(result: subprocess.CompletedProcess[str], report_path: Path) -> dict:
    """Validate subprocess output before attempting to consume its artifact."""
    context = f"Node rehearsal failed (exit={result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    if not report_path.is_file():
        raise AssertionError(f"{context}\nreport missing: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{context}\nreport invalid: {report_path}: {exc}") from exc
    if result.returncode != 0 or report.get("outcome") == "harness_error":
        raise AssertionError(f"{context}\nreport error: {report.get('error')}")
    return report


def make_catalog(count: int = 240) -> list[dict]:
    catalog = []
    for i in range(count):
        stats = {key: 0.0 for key in SCORING_WEIGHTS}
        stats.update({"PTS": 24.0 - i * 0.07, "FGM": 8.0, "FGA": 16.0, "FTM": 4.0, "FTA": 5.0, "3PM": 1.5, "REB": 8.0 - (i % 7), "AST": 3.0 + (i % 5), "STL": 1.0, "BLK": 0.5 + (i % 3) * 0.4, "TO": 2.0})
        catalog.append({"player_id": str(3000000 + i), "name": f"Pat{chr(65 + i % 26)} Hoop{i:03d}", "eligibility": ELIGIBILITY[i % len(ELIGIBILITY)], "stats": stats, "projected_gp": 70.0, "draft_rank": float(i + 1)})
    return catalog


class DressRehearsalTests(unittest.TestCase):
    report: dict
    export: dict
    status: dict
    export_path: Path
    elapsed: float

    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("node"):
            raise unittest.SkipTest("Node is required")
        runtime_dir = ROOT / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="dress-rehearsal-", dir=runtime_dir, ignore_cleanup_errors=True)
        cls.addClassCleanup(cls.tmp.cleanup)
        tmp = Path(cls.tmp.name)
        config = LeagueConfig()
        cls.coordinator = Coordinator(tmp / "control.sqlite3", config=config, strategy=recommend, recover=False, promotion_path=PROMOTION)
        cls.addClassCleanup(cls.coordinator.close)
        cls.server = create_server(cls.coordinator, port=0)
        cls.addClassCleanup(cls.server.server_close)
        if count_local_listeners(cls.server.server_port) is None:
            cls.server.listener_counter = lambda port: 1  # platform cannot enumerate; preflight tests cover the real counter
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(lambda: cls.thread.join(timeout=2))
        cls.addClassCleanup(cls.server.shutdown)
        session = cls.coordinator.connect(
            f"https://fantasy.espn.com/basketball/draft?leagueId={PRACTICE_LEAGUE}&seasonId=2027&teamId=15", practice=True
        )["session_id"]
        cls.coordinator.attest_practice(session, HEADING, config.league_id)
        cls.session_id = session
        rules = {"teams": 14, "rounds": 10, "starting_slots": list(config.starting_slots), "bench_slots": 4, "ir_slots": 3, "scoring": dict(config.scoring)}
        cfg = {
            "base": f"http://127.0.0.1:{cls.server.server_port}",
            "sessionId": session,
            "catalog": make_catalog(),
            "rules": rules,
            "room": {"league_id": PRACTICE_LEAGUE, "season_id": "2027", "team_id": "15"},
            "seat": 13,
            "faults": FAULTS,
        }
        (tmp / "config.json").write_text(json.dumps(cfg))
        started = time.monotonic()
        result = subprocess.run(
            ["node", str(ROOT / "draft_assistant" / "rehearsal_harness.js"), str(tmp / "config.json"), str(tmp / "report.json")],
            capture_output=True, text=True, cwd=ROOT, timeout=170,
        )
        cls.elapsed = time.monotonic() - started
        cls.report = read_rehearsal_report(result, tmp / "report.json")
        cls.status = cls.coordinator.status(session)
        cls.export = build_sanitized_export(
            cls.coordinator, session,
            latency_rows=cls.report["latency_rows"],
            foreground={"started_at": cls.report["started_at"], "ended_at": cls.report["finished_at"], "duration_seconds": round(cls.elapsed, 3), "invocation_count": len(cls.report["batches"]), "recovery_pause_resume_after_start": len(cls.report["recoveries"]) > 0, "provenance": "offline dress-rehearsal harness"},
            provenance="offline simulator; not live evidence",
            release_limits=["OFFLINE SIMULATION ONLY: no live ESPN room was used; this does not close the #9 live gate.", "Fixtures are an approximation of ESPN's DOM; selectors need live confirmation."],
            extra={"dress_rehearsal": {k: cls.report[k] for k in ("harness", "seat", "faults", "activation_attempts", "recoveries", "batches", "ownership", "source_hashes_sha256", "history_refreshes", "hydration_polls")}},
        )
        cls.export_path = tmp / "dress-rehearsal-export.json"
        cls.export_path.write_text(json.dumps(cls.export, indent=2))

        # Independent clean gate combines the live heartbeat publisher and
        # server-side promotion configuration with no injected recovery faults.
        clean_session = cls.coordinator.connect(
            f"https://fantasy.espn.com/basketball/draft?leagueId={PRACTICE_LEAGUE}&seasonId=2027&teamId=15", practice=True
        )["session_id"]
        cls.coordinator.attest_practice(clean_session, HEADING, config.league_id)
        promotion_document = json.loads(PROMOTION.read_text(encoding="utf-8"))
        clean_cfg = dict(cfg, sessionId=clean_session, faults={}, heartbeat=True, cleanMode=True, agentIdleGapMs=11_000, policy={
            "name": promotion_document["policy"],
            "config_fingerprint": promotion_document["config_fingerprint"],
            "artifact_sha256": hashlib.sha256(PROMOTION.read_bytes()).hexdigest().upper(),
        })
        (tmp / "clean-config.json").write_text(json.dumps(clean_cfg), encoding="utf-8")
        clean_started = time.monotonic()
        clean_result = subprocess.run(
            ["node", str(ROOT / "draft_assistant" / "rehearsal_harness.js"), str(tmp / "clean-config.json"), str(tmp / "clean-report.json")],
            capture_output=True, text=True, cwd=ROOT, timeout=170,
        )
        cls.clean_elapsed = time.monotonic() - clean_started
        cls.clean_report = read_rehearsal_report(clean_result, tmp / "clean-report.json")
        cls.clean_status = cls.coordinator.status(clean_session)
        clean_journal = cls.coordinator.journal(clean_session, limit=1_000_000)
        promoted_recommendations = sum(
            1 for row in clean_journal
            if row.get("event") == "recommendation" and (row.get("payload") or {}).get("status") == "promoted"
        )
        cls.clean_export = build_sanitized_export(
            cls.coordinator, clean_session,
            latency_rows=cls.clean_report["latency_rows"],
            foreground={"started_at": cls.clean_report["started_at"], "ended_at": cls.clean_report["finished_at"], "duration_seconds": round(cls.clean_elapsed, 3), "invocation_count": len(cls.clean_report["batches"]), "recovery_pause_resume_after_start": bool(cls.clean_report["recoveries"]), "provenance": "offline combined heartbeat and promotion rehearsal"},
            provenance="offline simulator; not live evidence",
            release_limits=["OFFLINE SIMULATION ONLY: no live ESPN room was used; this does not close the #9 live gate."],
            extra={"dress_rehearsal": {k: cls.clean_report[k] for k in ("harness", "seat", "faults", "activation_attempts", "recoveries", "batches", "ownership", "source_hashes_sha256", "history_refreshes", "hydration_polls", "heartbeat", "canonical_modules", "policy")}, "combined_gate": {"policy": cls.clean_report["policy"], "promoted_recommendation_count": promoted_recommendations, "heartbeat_publish_gaps": cls.clean_report["heartbeat"]["publish_gaps"], "heartbeat_stats": cls.clean_report["heartbeat"]["stats"], "source_hashes_sha256": cls.clean_report["source_hashes_sha256"]}},
        )
        cls.clean_export_path = tmp / "clean-dress-rehearsal-export.json"
        cls.clean_export_path.write_text(json.dumps(cls.clean_export, indent=2), encoding="utf-8")
        output_root = os.environ.get("FBL_REHEARSAL_OUTPUT_DIR")
        if output_root:
            output_dir = Path(output_root).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cls.clean_export_path, output_dir / "clean-dress-rehearsal-export.json")
            shutil.copyfile(tmp / "clean-report.json", output_dir / "clean-report.json")

    # -- objective: 140 picks, 10 guarded confirmations, legal roster, export, < 3 min
    def test_completes_140_picks_with_ten_guarded_confirmations_and_a_legal_roster(self) -> None:
        self.assertEqual(self.report["outcome"], "completed", self.report.get("reason"))
        self.assertLess(self.elapsed, 180)
        self.assertEqual(self.status["state"], "completed")
        final = self.export["final_observation"]
        self.assertEqual(final["history_summary"]["count"], 140)
        self.assertEqual(final["history_summary"]["unique_player_ids"], 140)
        self.assertEqual(final["roster_summary"]["players"], 10)
        self.assertTrue(final["roster_summary"]["legal_roster"])
        confirmed = [a for a in self.export["actions"] if a["status"] == "confirmed"]
        self.assertEqual(len(confirmed), 10)
        self.assertTrue(all(a["claimed"] and a["dispatched"] for a in confirmed))
        self.assertEqual(len({a["player_id"] for a in confirmed}), 10)
        self.assertEqual(self.status["unresolved_actions"], [])
        self.assertEqual(self.report["final_own_picks_confirmed"], 10)
        self.assertTrue(all(v for v in self.export["verification"].values() if isinstance(v, bool)))
        self.assertEqual(self.export["verification"]["snake_order_mismatches"], [])

    def test_clean_combined_heartbeat_promotion_gate(self) -> None:
        self.assertEqual(self.clean_report["outcome"], "completed", self.clean_report.get("reason"))
        self.assertEqual(self.clean_report["sim"]["picks"], 140)
        self.assertEqual(self.clean_report["faults"], [])
        self.assertEqual(self.clean_report["recoveries"], [])
        self.assertEqual(self.clean_report["sim"]["native_resume_clicks"], 1)
        self.assertTrue(self.clean_report["heartbeat"]["enabled"])
        self.assertGreater(self.clean_report["heartbeat"]["stats"]["published"], 4)
        self.assertGreaterEqual(self.clean_report["heartbeat"]["agent_call_gap"]["duration_ms"], 10_000)
        self.assertGreaterEqual(self.clean_report["heartbeat"]["agent_call_gap"]["publishes_during_gap"], 4)
        self.assertEqual(self.clean_report["canonical_modules"], ["browser_observer.js", "operator_runtime.js", "heartbeat_publisher.js"])
        self.assertEqual(self.clean_status["state"], "completed")
        self.assertEqual(self.clean_status["unresolved_actions"], [])
        observation = self.clean_export["final_observation"]
        self.assertEqual(observation["history_summary"]["count"], 140)
        self.assertEqual(observation["history_summary"]["unique_player_ids"], 140)
        self.assertEqual(observation["roster_summary"]["players"], 10)
        self.assertTrue(observation["roster_summary"]["legal_roster"])
        confirmed = [a for a in self.clean_export["actions"] if a["status"] == "confirmed"]
        self.assertEqual(len(confirmed), 10)
        self.assertEqual(len({a["player_id"] for a in confirmed}), 10)
        self.assertTrue(all(a["claimed"] and a["dispatched"] for a in confirmed))
        self.assertTrue(all(v for v in self.clean_export["verification"].values() if isinstance(v, bool)))
        self.assertEqual(len(self.clean_export["latency"]["rows"]), 10)
        self.assertEqual((self.clean_status.get("recommendation") or {}).get("lookahead_status"), "promoted")
        self.assertEqual(self.clean_report["policy"]["name"], "marginal-lineup-v1")
        self.assertEqual(self.clean_report["policy"]["config_fingerprint"], json.loads(PROMOTION.read_text())["config_fingerprint"])
        gate = self.clean_export["combined_gate"]
        self.assertEqual(gate["promoted_recommendation_count"], 10)
        self.assertLessEqual(float(gate["heartbeat_publish_gaps"]["max_seconds"] or 0.0), 10.0)
        self.assertEqual(gate["heartbeat_publish_gaps"]["over_freshness_limit"], 0)
        self.assertTrue(gate["policy"]["artifact_sha256"])
        self.assertEqual(gate["heartbeat_publish_gaps"], self.clean_status["publish_gaps"])
        self.assertTrue(self.clean_report["source_hashes_sha256"]["draft_assistant/heartbeat_publisher.js"])
        self.assertEqual(self.clean_export["latency"]["provenance"], "offline simulator; not live evidence")

    def test_subprocess_report_errors_preserve_exit_and_streams(self) -> None:
        result = subprocess.CompletedProcess(["node"], 7, "captured out", "captured err")
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(AssertionError, "captured err"):
                read_rehearsal_report(result, missing)
            malformed = Path(directory) / "bad.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "report invalid"):
                read_rehearsal_report(result, malformed)

    def test_export_has_the_live_validation_shape_and_no_secrets(self) -> None:
        live = ROOT / "draft_assistant" / "validation" / "browser-recovery-2026-10-11" / "uninterrupted-1000000007.json"
        live_keys = set(json.loads(live.read_text()))
        self.assertEqual(self.export["artifact_version"], ARTIFACT_VERSION)
        self.assertTrue(live_keys <= set(self.export), live_keys - set(self.export))
        on_disk = self.export_path.read_text()
        for secret in ("claim_token", "owner_token", "known_players", "available_players"):
            self.assertNotIn(f'"{secret}":', on_disk)
        self.assertNotIn(str(ROOT / "draft_assistant" / "validation"), str(self.export_path))
        # with the #42 history-then-Players confirmation, the lag-fault pick is
        # confirmed directly, so all ten owned picks have a click-to-confirm row
        self.assertEqual(len(self.export["latency"]["rows"]), 10)

    # -- injected failure modes
    def test_missing_resume_refuses_then_a_single_native_click_activates(self) -> None:
        first, second = self.report["activation_attempts"][:2]
        self.assertEqual(first["status"], "refused")
        self.assertFalse(first["native_resume_clicked"])
        self.assertEqual(second["status"], "activated")
        self.assertLess(second["elapsed_ms"], 10_000)
        self.assertEqual(self.report["sim"]["native_resume_clicks"], 1)

    def test_virtualized_feed_hydration_and_abbreviated_roster_are_survived(self) -> None:
        names = {f["fault"] for f in self.report["faults"]}
        self.assertTrue({"virtualized_feed", "late_history_hydration", "abbreviated_roster_names"} <= names)
        self.assertGreater(self.report["history_refreshes"], 0)
        self.assertGreater(self.report["hydration_polls"], 0)
        self.assertEqual(self.export["final_observation"]["history_summary"]["count"], 140)

    def test_vanished_recommendation_is_never_clicked_and_resolved_not_attempted(self) -> None:
        fault = next(f for f in self.report["faults"] if f["fault"] == "recommended_player_vanishes")
        clicked = {c["player_id"] for c in self.report["sim"]["draft_clicks"]}
        self.assertNotIn(fault["player_id"], clicked)
        failed = [a for a in self.export["actions"] if a["status"] == "failed"]
        self.assertEqual([a["player_id"] for a in failed], [fault["player_id"]])
        own_players = {a["player_id"] for a in self.export["actions"] if a["status"] == "confirmed"}
        self.assertNotIn(fault["player_id"], own_players)

    def test_observation_gap_stops_the_batch_and_recovers_by_explicit_resume(self) -> None:
        reasons = [r["stop_reason"] for r in self.report["recoveries"]]
        self.assertTrue(any("ms old" in (reason or "") for reason in reasons), reasons)
        self.assertTrue(all(r["recovered"] for r in self.report["recoveries"]))
        self.assertGreaterEqual(self.export["journal"]["event_counts"].get("resume", 0), len(reasons))

    def test_agent_crash_hands_over_by_session_and_revision_and_old_owner_is_rejected(self) -> None:
        transfer = self.report["ownership"]["transfer"]
        self.assertTrue(transfer["old_owner_write_rejected"])
        self.assertEqual(transfer["to_epoch"], 2)
        self.assertEqual(self.status["owner"]["epoch"], 2)
        self.assertEqual(self.export["journal"]["event_counts"].get("ownership_transferred"), 1)

    def test_every_published_frame_is_schema_clean(self) -> None:
        # A `_live_filter` field would have been rejected as a DraftObservation
        # at publish time and surfaced as a recovery failure.
        self.assertTrue(all(r["recovered"] for r in self.report["recoveries"]))
        self.assertNotIn("_live_filter", json.dumps(self.export["final_observation"]))

    # -- dependent confirmation behavior
    def test_post_click_lag_is_confirmed_without_an_uncertain_action_once_42_lands(self) -> None:
        """#42 / PR #46 (post-click confirmation): a lagging read-back no longer leaves the action uncertain."""
        self.assertEqual(self.export["journal"]["event_counts"].get("action_uncertain", 0), 0)


if __name__ == "__main__":
    unittest.main()
