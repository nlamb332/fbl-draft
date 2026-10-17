"""HTTP operator bridge checks for visible browser handoff."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mock_draft.fixture import SCORING_WEIGHTS

from .contracts import CandidateScore, DraftObservation, DraftRecommendation, LivePlayer, RoomIdentity
from .coordinator import Coordinator
from .server import create_server


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperatorHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir="runtime")
        self.coordinator = Coordinator(f"{self.tmp.name}/control.sqlite3", recover=False)
        self.server = create_server(self.coordinator, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.coordinator.close()
        self.tmp.cleanup()

    def post(self, payload: dict) -> dict:
        request = Request(
            self.base + "/api/operator",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", "Origin": self.base},
        )
        try:
            with urlopen(request) as response:
                return json.load(response)
        except HTTPError as error:
            return json.load(error)

    def observation(self, session_id: str, revision: int = 1) -> dict:
        room = RoomIdentity("1000000000", "2027", "15")
        player = LivePlayer("p1", "Player One", ("G",), stats={"PTS": 20})
        return DraftObservation(
            session_id=session_id,
            room=room,
            observed_at=_now(),
            revision=revision,
            phase="active",
            current_pick=1,
            on_clock_team="Example Team",
            seconds_remaining=30,
            pick_order=("Example Team",) + tuple(f"Team {i}" for i in range(1, 14)),
            roster=(),
            available_players=(player,),
            autopick=False,
            rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
            complete=True,
            history_complete=True,
        ).to_dict()

    def test_unready_prepare_and_start_are_rejected(self) -> None:
        connected = self.coordinator.connect("https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15")
        sid = connected["session_id"]
        prepare = self.post({"operation": "prepare", "session_id": sid})
        self.assertEqual(prepare["operation"], "prepare")
        self.assertEqual(prepare["status"], "error")
        start = self.post({"operation": "start", "session_id": sid})
        self.assertEqual(start["operation"], "start")
        self.assertEqual(start["status"], "error")
        self.assertEqual(self.coordinator.status(sid)["state"], "observing")

    def test_prepare_returns_one_guarded_command_and_consumes_it_once(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, _now(), (CandidateScore("p1", "Player One", 1.0),))

        self.coordinator.strategy = strategy
        observation = self.observation("operator-session")
        started = self.post({"operation": "start", "session_id": "operator-session", "observation": observation})
        self.assertEqual(started["status"], "running")
        prepared = self.post({"operation": "prepare", "session_id": "operator-session", "request_id": "pick-1"})
        self.assertEqual(prepared["operation"], "prepare")
        self.assertEqual(prepared["status"], "command_ready")
        self.assertEqual(prepared["command"]["type"], "select_player")
        self.assertEqual(prepared["request_id"], "pick-1")
        repeated = self.post({"operation": "prepare", "session_id": "operator-session"})
        self.assertEqual(repeated["status"], "error")
        self.assertIn("claimed", repeated["error"])

    def test_observation_cannot_be_published_to_a_different_control_session(self) -> None:
        response = self.post({"operation": "start", "session_id": "other-session", "observation": self.observation("operator-session")})
        self.assertEqual(response["status"], "error")
        self.assertIn("observation session", response["error"])
        self.assertEqual(self.coordinator.status()["sessions"], [])

    def test_invalid_operator_json_is_visible(self) -> None:
        request = Request(self.base + "/api/operator", b'{"operation":"status","request_id":"bad-json"', {"Content-Type": "application/json", "Origin": self.base})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        body = json.load(caught.exception)
        self.assertEqual(body["status"], "error")
        self.assertIsNone(body["request_id"])


if __name__ == "__main__":
    unittest.main()
