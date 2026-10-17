"""Issue #43: session ownership transfer, practice preflight, `_live_filter` contract."""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
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
from .preflight import count_local_listeners
from .server import create_server

PRACTICE_LEAGUE = "1000000012"
HEADING = "ESPN Fantasy Basketball Draft - Practice Draft for Example League"
OWN = "Example Team"
ORDER = (OWN,) + tuple(f"Team {i}" for i in range(1, 14))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir="runtime")

        def strategy(obs, config, **kwargs):
            player = obs.available_players[0]
            return DraftRecommendation(obs.session_id, obs.revision, _now(), (CandidateScore(player.player_id, player.name, 1.0),))

        self.coordinator = Coordinator(f"{self.tmp.name}/control.sqlite3", recover=False, strategy=strategy)
        self.server = create_server(self.coordinator, port=0)
        self.server.listener_counter = lambda port: 1
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
        request = Request(self.base + "/api/operator", json.dumps(payload).encode(), {"Content-Type": "application/json", "Origin": self.base})
        try:
            with urlopen(request) as response:
                return json.load(response)
        except HTTPError as error:
            return json.load(error)

    def observation(self, session_id: str, *, practice: bool, revision: int = 1, autopick: bool = False, order=ORDER) -> dict:
        league = PRACTICE_LEAGUE if practice else "1000000000"
        players = tuple(LivePlayer(f"p{i}", f"Player {i}", ("G", "F"), stats={"PTS": 20}, projected_gp=70) for i in range(1, 15))
        return DraftObservation(
            session_id=session_id,
            room=RoomIdentity(league, "2027", "15", practice=practice),
            observed_at=_now(),
            revision=revision,
            phase="active",
            current_pick=1,
            on_clock_team=order[0],
            seconds_remaining=90,
            pick_order=order,
            available_players=players,
            autopick=autopick,
            rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
            complete=True,
            history_complete=True,
        ).to_dict()

    def practice_session(self) -> str:
        sid = self.coordinator.connect(
            f"https://fantasy.espn.com/basketball/draft?leagueId={PRACTICE_LEAGUE}&seasonId=2027&teamId=15", practice=True
        )["session_id"]
        self.coordinator.attest_practice(sid, HEADING, "1000000000")
        return sid

    def live_session(self, sid: str = "owned-session") -> str:
        self.coordinator.connect("https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15", session_id=sid)
        return sid


class OwnershipTransferTests(ServerCase):
    def test_claim_is_exclusive_and_gates_operator_writes(self) -> None:
        sid = self.live_session()
        claimed = self.post({"operation": "claim-owner", "session_id": sid, "label": "agent-a"})
        self.assertEqual(claimed["status"], "owner_claimed")
        token = claimed["owner_token"]
        self.assertEqual(self.post({"operation": "claim-owner", "session_id": sid})["status"], "error")
        obs = self.observation(sid, practice=False)
        self.assertIn("ownership claim", self.post({"operation": "publish", "observation": obs})["error"])
        self.assertIn("ownership claim", self.post({"operation": "publish", "observation": obs, "owner_token": "wrong"})["error"])
        self.assertEqual(self.post({"operation": "publish", "observation": obs, "owner_token": token})["status"], "observing")
        self.assertEqual(self.post({"operation": "pause", "session_id": sid, "owner_token": "wrong"})["status"], "error")
        self.assertEqual(self.post({"operation": "pause", "session_id": sid, "owner_token": token})["state"], "paused")
        # Status reads and the human Pause stay available; the token is never exposed.
        status = self.post({"operation": "status", "session_id": sid})
        self.assertEqual(status["owner"]["epoch"], 1)
        self.assertNotIn(token, json.dumps(status))

    def test_valid_transfer_revokes_old_owner_and_is_journaled(self) -> None:
        sid = self.live_session()
        old = self.post({"operation": "claim-owner", "session_id": sid, "label": "agent-a"})["owner_token"]
        self.post({"operation": "publish", "observation": self.observation(sid, practice=False), "owner_token": old})
        revision = self.coordinator.status(sid)["latest_revision"]
        moved = self.post({"operation": "transfer-owner", "session_id": sid, "last_confirmed_revision": revision, "label": "agent-b"})
        self.assertEqual(moved["status"], "owner_transferred")
        self.assertEqual(moved["epoch"], 2)
        new = moved["owner_token"]
        self.assertNotEqual(new, old)
        # The old owner's writes are rejected after the transfer.
        stale = self.post({"operation": "publish", "observation": self.observation(sid, practice=False, revision=2), "owner_token": old})
        self.assertEqual(stale["status"], "error")
        self.assertIn("ownership claim", stale["error"])
        self.assertEqual(self.coordinator.status(sid)["latest_revision"], revision)
        accepted = self.post({"operation": "publish", "observation": self.observation(sid, practice=False, revision=2), "owner_token": new})
        self.assertEqual(accepted["status"], "observing")
        events = [entry for entry in self.coordinator.journal(sid) if entry["event"].startswith("ownership_")]
        self.assertEqual({entry["event"] for entry in events}, {"ownership_claimed", "ownership_transferred"})
        transferred = next(entry for entry in events if entry["event"] == "ownership_transferred")
        self.assertEqual(transferred["payload"]["from_epoch"], 1)
        self.assertEqual(transferred["payload"]["to_epoch"], 2)
        self.assertEqual(transferred["payload"]["revision"], revision)
        self.assertNotIn(old, json.dumps(events))
        self.assertNotIn(new, json.dumps(events))

    def test_stale_or_future_revision_is_rejected(self) -> None:
        sid = self.live_session()
        token = self.post({"operation": "claim-owner", "session_id": sid})["owner_token"]
        self.post({"operation": "publish", "observation": self.observation(sid, practice=False), "owner_token": token})
        self.post({"operation": "publish", "observation": self.observation(sid, practice=False, revision=2), "owner_token": token})
        for presented in (1, 3, None, "2", True):
            result = self.post({"operation": "transfer-owner", "session_id": sid, "last_confirmed_revision": presented})
            self.assertEqual(result["status"], "error", presented)
        # Claim survives rejected transfers.
        self.assertEqual(self.coordinator.status(sid)["owner"]["epoch"], 1)
        self.assertEqual(self.post({"operation": "publish", "observation": self.observation(sid, practice=False, revision=3), "owner_token": token})["status"], "observing")

    def test_transfer_requires_an_existing_claim(self) -> None:
        sid = self.live_session()
        self.assertEqual(self.post({"operation": "transfer-owner", "session_id": sid, "last_confirmed_revision": None})["status"], "error")

    def test_crashed_owner_cannot_orphan_a_running_room_and_old_ack_is_rejected(self) -> None:
        sid = self.live_session()
        old = self.post({"operation": "claim-owner", "session_id": sid})["owner_token"]
        self.post({"operation": "start", "session_id": sid, "observation": self.observation(sid, practice=False), "owner_token": old})
        prepared = self.post({"operation": "prepare", "session_id": sid, "owner_token": old})
        self.assertEqual(prepared["status"], "command_ready")
        action_id = prepared["command"]["action_id"]
        moved = self.post({"operation": "transfer-owner", "session_id": sid, "last_confirmed_revision": self.coordinator.status(sid)["latest_revision"]})
        self.assertTrue(moved["paused_running_session"])
        self.assertEqual(self.coordinator.status(sid)["state"], "paused")
        late = self.post({"operation": "ack", "action_id": action_id, "status": "submitted", "owner_token": old})
        self.assertIn("ownership claim", late["error"])
        # The prepared command already crossed the final guard, so the pause
        # cannot cancel it: it stays unresolved and is handed to the new owner,
        # who must resolve it by read-back (here: mark uncertain) before any
        # further command can be prepared.
        self.assertEqual([a["action_id"] for a in moved["unresolved_actions"]], [action_id])
        new = moved["owner_token"]
        resolved = self.post({"operation": "ack", "action_id": action_id, "status": "uncertain", "detail": "previous owner crashed", "owner_token": new})
        self.assertNotEqual(resolved["status"], "error", resolved)
        self.assertEqual(self.coordinator.status(sid)["unresolved_actions"][0]["status"], "uncertain")

class PracticePreflightTests(ServerCase):
    def start(self, sid: str, *, timer=90, seat=1, autopick=False, order=ORDER, include_preflight=True, command="start") -> dict:
        self.revision = getattr(self, "revision", 0) + 1
        body = {"operation": command, "session_id": sid, "observation": self.observation(sid, practice=True, revision=self.revision, autopick=autopick, order=order)}
        if include_preflight:
            body["preflight"] = {"native_timer_seconds": timer, "expected_seat": seat}
        return self.post(body)

    def test_valid_preflight_starts_the_practice_session(self) -> None:
        sid = self.practice_session()
        result = self.start(sid)
        self.assertEqual(result["status"], "running", result)

    def test_any_readable_timer_starts_and_is_recorded(self) -> None:
        sid = self.practice_session()
        result = self.start(sid, timer=30)
        self.assertEqual(result["status"], "running", result)
        self.assertEqual(result["pick_timer_seconds"], 30.0)
        self.assertEqual(self.coordinator.status(sid)["pick_timer_seconds"], 30.0)
        self.assertEqual(self.coordinator.journal(sid)[0]["event"], "pick_timer_recorded")

    def test_unreadable_timers_are_refused(self) -> None:
        sid = self.practice_session()
        for timer in (None, "90", 0, -30, float("nan"), float("inf"), True):
            self.assertEqual(self.start(sid, timer=timer)["status"], "error", timer)
        self.assertIsNone(self.coordinator.status(sid)["pick_timer_seconds"])

    def test_resume_requires_the_timer_recorded_at_start(self) -> None:
        sid = self.practice_session()
        self.assertEqual(self.start(sid, timer=60)["status"], "running")
        self.coordinator.pause(sid)
        changed = self.start(sid, timer=90, command="resume")
        self.assertEqual(changed["status"], "error")
        self.assertIn("started with 60 s", changed["error"])
        self.assertEqual(self.coordinator.status(sid)["state"], "paused")
        self.assertEqual(self.start(sid, timer=60, command="resume")["status"], "running")
        self.assertEqual(self.coordinator.status(sid)["pick_timer_seconds"], 60.0)

    def test_autopick_on_is_refused(self) -> None:
        sid = self.practice_session()
        result = self.start(sid, autopick=True)
        self.assertEqual(result["status"], "error")
        self.assertNotEqual(self.coordinator.status(sid)["state"], "running")

    def test_second_listener_or_unknown_listener_count_is_refused(self) -> None:
        sid = self.practice_session()
        self.server.listener_counter = lambda port: 2
        result = self.start(sid)
        self.assertEqual(result["status"], "error")
        self.assertIn("exactly one coordinator listener", result["error"])
        self.server.listener_counter = lambda port: None
        self.assertEqual(self.start(sid)["status"], "error")
        self.server.listener_counter = lambda port: 0
        self.assertEqual(self.start(sid)["status"], "error")
        self.assertNotEqual(self.coordinator.status(sid)["state"], "running")

    def test_team_and_seat_must_be_verified(self) -> None:
        sid = self.practice_session()
        self.assertEqual(self.start(sid, seat=2)["status"], "error")
        self.assertEqual(self.start(sid, seat=None)["status"], "error")
        self.assertEqual(self.start(sid, seat="1")["status"], "error")
        self.assertEqual(self.start(sid, order=("Someone Else",) + ORDER[1:])["status"], "error")
        self.assertEqual(self.start(sid, include_preflight=False)["status"], "error")

    def test_resume_is_gated_like_start(self) -> None:
        sid = self.practice_session()
        self.assertEqual(self.start(sid)["status"], "running")
        self.coordinator.pause(sid)
        refused = self.start(sid, autopick=True, command="resume")
        self.assertEqual(refused["status"], "error")
        self.assertEqual(self.coordinator.status(sid)["state"], "paused")
        self.assertEqual(self.start(sid, command="resume")["status"], "running")

    def test_preflight_operation_is_read_only_and_reports_reasons(self) -> None:
        sid = self.practice_session()
        ok = self.post({"operation": "preflight", "session_id": sid, "observation": self.observation(sid, practice=True), "preflight": {"native_timer_seconds": 90, "expected_seat": 1}})
        self.assertEqual(ok["status"], "preflight_ok")
        self.assertTrue(ok["ok"])
        self.assertNotEqual(self.coordinator.status(sid)["state"], "running")
        bad = self.post({"operation": "preflight", "session_id": sid, "observation": self.observation(sid, practice=True, revision=2), "preflight": {"native_timer_seconds": None, "expected_seat": 1}})
        self.assertEqual(bad["status"], "error")
        self.assertIsNone(self.coordinator.status(sid)["pick_timer_seconds"], "preflight alone never records the timer")

    def test_real_socket_listener_count_is_exactly_one(self) -> None:
        count = count_local_listeners(self.server.server_port)
        if count is None:
            self.skipTest("platform cannot enumerate listeners")
        self.assertEqual(count, 1)


@unittest.skipUnless(shutil.which("node"), "Node is required")
class LiveFilterContractTests(ServerCase):
    """`_live_filter` regression: diagnostics must stay outside DraftObservation."""

    def test_typed_contract_rejects_the_diagnostic_field_and_publish_leaves_state_unchanged(self) -> None:
        sid = self.live_session()
        polluted = {**self.observation(sid, practice=False), "_live_filter": {"excluded_player_ids": ["p1"]}}
        with self.assertRaises(TypeError):
            DraftObservation.from_dict(polluted)
        response = self.post({"operation": "publish", "observation": polluted})
        self.assertEqual(response["status"], "error")
        self.assertIn("_live_filter", response["error"])
        self.assertIsNone(self.coordinator.status(sid)["latest_revision"])

    def test_live_filter_output_observation_is_schema_clean(self) -> None:
        sid = self.live_session()
        observation = self.observation(sid, practice=False)
        observation["available_players"] = list(observation["available_players"]) + [{**observation["available_players"][0], "player_id": "bad", "name": "Incomplete Row", "stats": {}}]
        script = (
            "const {filterLiveAvailablePlayers}=require('./draft_assistant/live_projection_inputs.js');"
            "const input=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "process.stdout.write(JSON.stringify(filterLiveAvailablePlayers(input,{coreFields:['PTS']})));"
        )
        result = subprocess.run(["node", "-e", script], input=json.dumps(observation), text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        filtered = json.loads(result.stdout)
        self.assertIn("filter_metadata", filtered)
        clean = filtered["observation"]
        allowed = {field.name for field in dataclasses.fields(DraftObservation)}
        self.assertEqual(set(clean) - allowed, set(), "filter diagnostics leaked into the observation")
        self.assertNotIn("_live_filter", clean)
        DraftObservation.from_dict(clean)  # must construct
        published = self.post({"operation": "publish", "observation": clean})
        self.assertNotEqual(published["status"], "error", published)


if __name__ == "__main__":
    unittest.main()
