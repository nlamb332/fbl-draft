"""Room-agnostic observation-only advisor connection checks."""

from __future__ import annotations

import http.client
import json
import threading
import unittest

from .contracts import DraftObservation, LivePlayer, RoomIdentity, utc_now
from .coordinator import Coordinator
from .server import create_server


ESPN = "https://fantasy.espn.com"
HEADING = "ESPN Fantasy Basketball Draft - Practice Draft for Example League"


def room_url(league_id: str, season_id: str = "2027", team_id: str = "15") -> str:
    return f"https://fantasy.espn.com/basketball/draft?leagueId={league_id}&seasonId={season_id}&teamId={team_id}"


def team_url(league_id: str, season_id: str = "2027", team_id: str = "15") -> str:
    return f"https://fantasy.espn.com/basketball/team?leagueId={league_id}&teamId={team_id}&seasonId={season_id}"


class AdvisorConnectHTTPTests(unittest.TestCase):
    def test_practice_banner_accepts_a_user_league_name(self) -> None:
        heading = "ESPN Fantasy Basketball Draft - Practice Draft for Weekend Hoops League"
        status, result = self.connect(heading=heading)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["practice_attestation"]["heading"], heading)
        self.assertEqual(result["practice_attestation"]["source_league_id"], self.coordinator.config.league_id)
        self.assertTrue(self.coordinator.readiness(result["session_id"])["checks"]["practice_attestation"])

    def setUp(self) -> None:
        self.coordinator = Coordinator(":memory:", recover=False)
        self.server = create_server(self.coordinator, port=0, page_runner_enabled=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.coordinator.close()

    def post(self, body: dict, *, origin: str | None = ESPN, token: str | None = "default") -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        if token == "default":
            token = self.coordinator.csrf_token
        if token is not None:
            headers["X-CSRF-Token"] = token
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST", "/api/operator", json.dumps(body).encode(), headers)
            response = connection.getresponse()
            payload = json.loads(response.read() or b"{}")
            return response.status, payload
        finally:
            connection.close()

    def connect(
        self,
        league_id: str = "1000000012",
        *,
        practice: bool = True,
        heading: str = HEADING,
        origin: str | None = ESPN,
        token: str | None = "default",
        **extra: object,
    ) -> tuple[int, dict]:
        return self.post({
            "operation": "connect-advisor",
            "current_room_url": room_url(league_id),
            "practice": practice,
            "visible_heading": heading,
            "expected_seat": 13,
            # These fields deliberately must not control server identity.
            "own_team": "attacker supplied team",
            "source_league_id": "1000000018",
            **extra,
        }, origin=origin, token=token)

    def test_transient_practice_room_ids_are_accepted_and_bound_to_server_config(self) -> None:
        first_status, first = self.connect("1000000012")
        second_status, second = self.connect("1556211058")
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(first["status"], "advisor_connected")
        self.assertEqual(second["status"], "advisor_connected")
        self.assertNotEqual(first["session_id"], second["session_id"])
        for result, league_id in ((first, "1000000012"), (second, "1556211058")):
            self.assertTrue(result["observer_token"])
            self.assertEqual(result["own_team"], self.coordinator.config.our_team)
            self.assertEqual(result["source_league_id"], self.coordinator.config.league_id)
            self.assertEqual(result["expected_seat"], 13)
            status = self.coordinator.status(result["session_id"])
            self.assertEqual(status["state"], "observing")
            self.assertIsNone(status["owner"])
            self.assertEqual(status["room"]["league_id"], league_id)
            self.assertEqual(status["practice_attestation"]["source_league_id"], self.coordinator.config.league_id)
            self.assertEqual(status["practice_attestation"]["heading"], HEADING)

    def test_same_nonce_reuses_only_the_untouched_advisor_session(self) -> None:
        status, first = self.connect(request_nonce="reload-1")
        repeated_status, repeated = self.connect(request_nonce="reload-1")
        self.assertEqual((status, repeated_status), (200, 200))
        self.assertEqual(repeated["session_id"], first["session_id"])
        self.assertEqual(repeated["observer_token"], first["observer_token"])
        self.assertTrue(repeated["reused"])

    def test_loader_aliases_can_derive_seat_from_a_complete_visible_pick_order(self) -> None:
        own_team = self.coordinator.config.our_team
        pick_order = [f"Team {index}" for index in range(1, 14)]
        pick_order.insert(6, own_team)
        status, result = self.post({
            "operation": "connect-advisor",
            "url": room_url("1000000012"),
            "heading": HEADING,
            "pick_order": pick_order,
            "own_team": "client supplied identity must be ignored",
            "source_league_id": "client supplied source must be ignored",
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(result["own_team"], own_team)
        self.assertEqual(result["expected_seat"], 7)

    def test_configured_real_room_is_accepted_without_practice_attestation(self) -> None:
        heading = "ESPN Fantasy Basketball Draft - Example League"
        status, result = self.connect(
            self.coordinator.config.league_id,
            practice=False,
            heading=heading,
            current_room_url=team_url(self.coordinator.config.league_id),
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["room"]["practice"], False)
        self.assertIsNone(result.get("practice_attestation"))
        connected = self.coordinator.status(result["session_id"])
        self.assertEqual(connected["room"]["practice"], False)
        self.assertIsNone(connected["practice_attestation"])

    def test_loader_can_defer_seat_readback_until_after_server_returns_own_team(self) -> None:
        status, result = self.post({
            "operation": "connect-advisor",
            "url": room_url("1000000012"),
            "heading": HEADING,
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(result["own_team"], self.coordinator.config.our_team)
        self.assertIsNone(result["expected_seat"])

    def test_wrong_scope_or_live_room_or_heading_is_rejected_before_session_creation(self) -> None:
        invalid = [
            {"current_room_url": room_url("1000000012", season_id="2026")},
            {"current_room_url": room_url("1000000012", team_id="14")},
            {"current_room_url": room_url(self.coordinator.config.league_id)},
            {"current_room_url": room_url("1000000012"), "visible_heading": "ESPN Fantasy Basketball Draft - Live Draft for Example League"},
        ]
        for override in invalid:
            payload = {
                "operation": "connect-advisor",
                "current_room_url": room_url("1000000012"),
                "visible_practice_heading": HEADING,
                "expected_seat": 13,
                **override,
            }
            status, result = self.post(payload)
            self.assertEqual(status, 409, result)
            self.assertEqual(result["status"], "error")
            self.assertEqual(self.coordinator.status()["sessions"], [])

    def _preview_observation(self, session_id: str, league_id: str) -> dict:
        player = LivePlayer("p1", "Player One", ("G",), stats={"PTS": 20}, projected_gp=70, draft_rank=1)
        order = (self.coordinator.config.our_team,) + tuple(f"Team {index}" for index in range(1, 14))
        room = RoomIdentity(league_id, self.coordinator.config.season_id, self.coordinator.config.team_id, practice=True)
        return DraftObservation(
            session_id=session_id,
            room=room,
            observed_at=utc_now(),
            revision=1,
            phase="draft",
            current_pick=1,
            on_clock_team=self.coordinator.config.our_team,
            seconds_remaining=30,
            pick_order=order,
            known_players=(player,),
            available_players=(player,),
            autopick=False,
            rules={
                "teams": 14,
                "rounds": 10,
                "starting_slots": list(self.coordinator.config.starting_slots),
                "bench_slots": 4,
                "ir_slots": 3,
                "scoring": dict(self.coordinator.config.scoring),
            },
            complete=True,
            history_complete=True,
            available_pool_complete=True,
        ).to_dict()

    def test_observer_token_can_read_and_preview_but_cannot_control_or_claim(self) -> None:
        _, connected = self.connect()
        sid = connected["session_id"]
        token = connected["observer_token"]
        status, snapshot = self.post({"operation": "status", "session_id": sid, "owner_token": token})
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["session_id"], sid)
        preview, result = self.post({
            "operation": "recommend-preview",
            "session_id": sid,
            "owner_token": token,
            "observation": self._preview_observation(sid, "1000000012"),
        })
        self.assertEqual(preview, 200, result)
        self.assertEqual(result["status"], "preview")

        for operation in ("start", "resume", "pause", "prepare", "publish", "ack", "reconcile", "preflight", "claim-owner", "queue-claim"):
            body = {"operation": operation, "session_id": sid, "owner_token": token}
            if operation == "publish":
                body["observation"] = self._preview_observation(sid, "1000000012")
            denied, _ = self.post(body)
            self.assertNotEqual(denied, 200, operation)
        for operation in ("start", "claim-owner"):
            denied, _ = self.post(
                {"operation": operation, "session_id": sid, "owner_token": token},
                origin=None,
            )
            self.assertNotEqual(denied, 200, f"same-origin {operation}")
        denied, _ = self.post(
            {"operation": "start", "session_id": sid, "owner_token": token, "observer_token": ""},
            origin=None,
        )
        self.assertNotEqual(denied, 200, "same-origin observer token cannot be masked")
        self.assertEqual(self.coordinator.status(sid)["state"], "observing")
        self.assertIsNone(self.coordinator.status(sid)["owner"])

    def test_connect_requires_page_runner_flag_espn_origin_and_csrf(self) -> None:
        self.assertEqual(self.connect(token="wrong")[0], 403)
        self.assertEqual(self.connect(origin="https://evil.example")[0], 403)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(self.coordinator, port=0, page_runner_enabled=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.assertEqual(self.connect()[0], 403)


if __name__ == "__main__":
    unittest.main()
