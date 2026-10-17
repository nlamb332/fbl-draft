"""Practice-room identity must be observed and bound to one coordinator session."""

from __future__ import annotations

import unittest
import os
from pathlib import Path

from mock_draft.fixture import SCORING_WEIGHTS

from .contracts import DraftObservation, LivePlayer, RoomIdentity, utc_now
from .coordinator import Coordinator, IdentityError, ReadinessError


PRACTICE_HEADING = "ESPN Fantasy Basketball Draft - Practice Draft for Example League"
LIVE_LEAGUE_ID = "1000000000"
PRACTICE_LEAGUE_ID = "1000000006"


class PracticeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        self.db_path = Path("runtime") / f"test-practice-{os.getpid()}-{id(self)}.sqlite3"
        self.db = str(self.db_path)
        self.coordinator = Coordinator(self.db)
        self.room = RoomIdentity(PRACTICE_LEAGUE_ID, "2027", "15", True)
        self.player = LivePlayer("p1", "Visible Player", ("G",), stats={"PTS": 20})

    def tearDown(self) -> None:
        self.coordinator.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.db_path}{suffix}").unlink()
            except FileNotFoundError:
                pass

    def ready_observation(self, session_id: str) -> DraftObservation:
        return DraftObservation(
            session_id=session_id,
            room=self.room,
            observed_at=utc_now(),
            revision=1,
            phase="draft",
            current_pick=1,
            on_clock_team="Example Team",
            seconds_remaining=30,
            pick_order=("Example Team",) + tuple(f"Team {i}" for i in range(1, 14)),
            available_players=(self.player,),
            autopick=False,
            rules={
                "teams": 14,
                "rounds": 10,
                "starting_slots": ["C", "G", "G", "F", "F", "UTIL"],
                "bench_slots": 4,
                "ir_slots": 3,
                "scoring": dict(SCORING_WEIGHTS),
            },
            complete=True,
            history_complete=True,
        )

    def test_dynamic_practice_room_is_accepted_after_explicit_attestation(self) -> None:
        session_id = self.coordinator.create_session(self.room)["session_id"]
        self.coordinator.ingest_observation(self.ready_observation(session_id))
        before = self.coordinator.readiness(session_id)
        self.assertFalse(before["checks"]["practice_attestation"])
        self.assertEqual(before["checks"]["room"], True)

        status = self.coordinator.attest_practice(
            session_id,
            heading=PRACTICE_HEADING,
            source_league_id=LIVE_LEAGUE_ID,
        )
        self.assertEqual(status["practice_attestation"]["source_league_id"], LIVE_LEAGUE_ID)
        self.assertEqual(status["practice_attestation"]["room"]["league_id"], PRACTICE_LEAGUE_ID)
        self.assertTrue(any(item["event"] == "practice_attested" for item in status["journal"]))
        self.assertTrue(self.coordinator.readiness(session_id)["checks"]["practice_attestation"])
        self.assertEqual(self.coordinator.start(session_id)["state"], "running")

    def test_unverified_practice_room_cannot_start(self) -> None:
        session_id = self.coordinator.create_session(self.room)["session_id"]
        self.coordinator.ingest_observation(self.ready_observation(session_id))
        readiness = self.coordinator.readiness(session_id)
        self.assertFalse(readiness["ready"])
        self.assertTrue(all(value for key, value in readiness["checks"].items() if key != "practice_attestation"), readiness)
        with self.assertRaises(ReadinessError) as rejected:
            self.coordinator.start(session_id)
        self.assertIn("attestation", str(rejected.exception))

    def test_wrong_source_or_heading_is_rejected(self) -> None:
        session_id = self.coordinator.create_session(self.room)["session_id"]
        with self.assertRaises(IdentityError):
            self.coordinator.attest_practice(session_id, heading=PRACTICE_HEADING, source_league_id="999")
        with self.assertRaises(IdentityError):
            self.coordinator.attest_practice(
                session_id,
                heading="ESPN Fantasy Basketball Draft - Practice Draft for Another Team",
                source_league_id=LIVE_LEAGUE_ID,
            )

    def test_attestation_persists_across_coordinator_restart(self) -> None:
        session_id = self.coordinator.create_session(self.room)["session_id"]
        self.coordinator.attest_practice(session_id, heading=PRACTICE_HEADING, source_league_id=LIVE_LEAGUE_ID)
        self.coordinator.close()
        self.coordinator = Coordinator(self.db)
        self.assertTrue(self.coordinator.readiness(session_id)["checks"]["practice_attestation"])

    def test_configured_real_league_cannot_disguise_itself_as_practice(self) -> None:
        live_room = RoomIdentity(LIVE_LEAGUE_ID, "2027", "15", True)
        with self.assertRaises(IdentityError):
            self.coordinator.create_session(live_room)

    def test_attestation_is_not_reusable_across_sessions(self) -> None:
        first = self.coordinator.create_session(self.room)["session_id"]
        second = self.coordinator.create_session(self.room)["session_id"]
        self.coordinator.attest_practice(first, heading=PRACTICE_HEADING, source_league_id=LIVE_LEAGUE_ID)
        second_status = self.coordinator.status(second)
        self.assertIsNone(second_status["practice_attestation"])
        self.assertFalse(self.coordinator.readiness(second)["checks"]["practice_attestation"])


if __name__ == "__main__":
    unittest.main()
