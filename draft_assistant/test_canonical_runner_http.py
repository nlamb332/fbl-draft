"""Run the reusable attended factory end to end over real local HTTP, offline."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .contracts import LeagueConfig
from .coordinator import Coordinator
from .marginal_lineup import DEFAULT_PROMOTION_PATH
from .server import create_server
from .test_dress_rehearsal import HEADING, PRACTICE_LEAGUE, make_catalog, read_rehearsal_report


def test_canonical_factory_completes_guarded_practice_over_http():
    root = Path(__file__).resolve().parents[1]
    (root / "runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root / "runtime") as directory:
        folder = Path(directory)
        config = LeagueConfig()
        coordinator = Coordinator(folder / "control.sqlite3", recover=False, promotion_path=DEFAULT_PROMOTION_PATH)
        server = create_server(coordinator, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            session_id = coordinator.connect(
                f"https://fantasy.espn.com/basketball/draft?leagueId={PRACTICE_LEAGUE}&seasonId=2027&teamId=15",
                practice=True,
            )["session_id"]
            coordinator.attest_practice(session_id, HEADING, config.league_id)
            payload = {"base": f"http://127.0.0.1:{server.server_port}", "sessionId": session_id,
                       "room": {"league_id": PRACTICE_LEAGUE, "season_id": "2027", "team_id": "15"},
                       "catalog": make_catalog(), "rules": {
                           "teams": 14, "rounds": 10, "starting_slots": list(config.starting_slots),
                           "bench_slots": 4, "ir_slots": 3, "scoring": dict(config.scoring)}}
            (folder / "config.json").write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([shutil.which("node"), str(root / "draft_assistant/fixtures/canonical_runner_driver.js"),
                                     str(folder / "config.json"), str(folder / "report.json")],
                                    capture_output=True, text=True, cwd=root, timeout=90)
            report = read_rehearsal_report(result, folder / "report.json")
            assert report["status"] == "completed", (report.get("reason"), report["evidence"]["extra"]["batches"])
            assert report["picks"] == report["unique"] == 140
            assert report["clicks"] == 10
            assert report["native_resume_clicks"] == 1
            assert len(report["evidence"]["latency_rows"]) == 10
            status = coordinator.status(session_id)
            assert status["state"] == "completed"
            assert not status["unresolved_actions"]
            assert len(status["actions"]) == 10
            assert all(a["status"] == "confirmed" and a["claimed"] and a["dispatched"] for a in status["actions"])
            journal = coordinator.journal(session_id, limit=10000)
            assert sum(e["event"] == "recommendation" and e["payload"].get("status") == "promoted" for e in journal) == 10
            assert not any(e["event"] in {"pause", "stale_pause", "stale_degraded", "action_uncertain"} for e in journal)
            evidence = report["evidence"]
            evidence["provenance"] = "offline canonical factory over real localhost HTTP; simulated ESPN DOM and opponents"
            evidence["release_limits"] = ["Offline only; live browser selectors and connectivity remain unverified."]
            (folder / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            exported = subprocess.run([sys.executable, str(root / "scripts/export_sanitized_session.py"),
                                       "--db", str(folder / "control.sqlite3"), "--session-id", session_id,
                                       "--evidence", str(folder / "evidence.json"), "--output", str(folder / "sanitized.json")],
                                      capture_output=True, text=True, cwd=root, timeout=15)
            assert exported.returncode == 0, exported.stdout + exported.stderr
            sanitized = (folder / "sanitized.json").read_text(encoding="utf-8")
            assert json.loads(sanitized)["final_observation"]["history_summary"]["count"] == 140
            assert '"owner_token":' not in sanitized and '"known_players":' not in sanitized
            output_dir = os.environ.get("FBL_CANONICAL_OUTPUT_DIR")
            if output_dir:
                destination = Path(output_dir)
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(folder / "sanitized.json", destination / "sanitized-session.json")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            coordinator.close()
