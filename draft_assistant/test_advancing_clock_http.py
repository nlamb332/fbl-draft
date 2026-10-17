"""Canonical runner + localhost coordinator against the advancing clock.

The room remains an EspnRoomSimulator DOM fixture. This test proves that the
checked-in canonical runner and real coordinator refuse a guarded click after
a fresh-enough observation is delayed until the synthetic native clock
expires. It does not claim browser or wall-clock ESPN endurance.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .contracts import LeagueConfig
from .coordinator import Coordinator
from .marginal_lineup import DEFAULT_PROMOTION_PATH
from .server import create_server
from .test_dress_rehearsal import HEADING, PRACTICE_LEAGUE, make_catalog


def test_canonical_runner_served_coordinator_blocks_expired_delayed_click() -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is required for the advancing-clock integration driver")
    root = Path(__file__).resolve().parents[1]
    (root / "runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root / "runtime", prefix="advancing-clock-") as directory:
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
            payload = {
                "base": f"http://127.0.0.1:{server.server_port}",
                "sessionId": session_id,
                "room": {"league_id": PRACTICE_LEAGUE, "season_id": "2027", "team_id": "15"},
                "catalog": make_catalog(),
                "rules": {
                    "teams": 14,
                    "rounds": 10,
                    "starting_slots": list(config.starting_slots),
                    "bench_slots": 4,
                    "ir_slots": 3,
                    "scoring": dict(config.scoring),
                },
                "seat": 13,
                "mode": "expired",
                "timerSeconds": 3,
                "opponentDelayMs": 100,
                "delayBeforeClickMs": 4_000,
                "clockSkewMs": 5_000,
            }
            (folder / "config.json").write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [node, str(root / "draft_assistant/fixtures/advancing_clock_runner_driver.js"), str(folder / "config.json"), str(folder / "report.json")],
                capture_output=True,
                text=True,
                cwd=root,
                timeout=45,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
            assert report["canonical_runner"] is True
            assert report["served_coordinator"] is True
            assert report["synthetic_room"] is True
            assert report["status"] == "stopped"
            assert report["guarded_click_blocked"] is True
            assert report["clicks"] == 0
            status = coordinator.status(session_id)
            journal = coordinator.journal(session_id, limit=10000)
            assert report["picks"] == report["unique"] == 12
            assert "expired" in (report.get("reason") or "").lower()
            assert report["scope"]["live_browser"] is False
            assert report["scope"]["real_timer_endurance"] is False

            assert not any(action.get("status") in {"submitted", "confirmed", "uncertain"} for action in status.get("actions", []))
            assert not any(event.get("event") == "action_dispatched" for event in journal)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            coordinator.close()
