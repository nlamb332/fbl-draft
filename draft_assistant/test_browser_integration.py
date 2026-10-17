"""Cross-module checks for the visible parser and durable control plane."""

from __future__ import annotations

import os
from pathlib import Path
import unittest

from .browser_operator import BrowserOperator
from .browser_state import parse_visible_snapshot
from .contracts import CandidateScore, DraftObservation, DraftRecommendation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity, utc_now
from .coordinator import Coordinator, ReadinessError
from .lifecycle import DraftSession


class BrowserCoordinatorIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        self.db_path = Path("runtime") / f"test-browser-integration-{os.getpid()}-{id(self)}.sqlite3"
        self.room = RoomIdentity("1000000006", "2027", "13", True)
        self.config = LeagueConfig(team_id="13")
        self.coordinator: Coordinator | None = None

    def tearDown(self) -> None:
        if self.coordinator is not None:
            self.coordinator.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.db_path}{suffix}").unlink()
            except FileNotFoundError:
                pass

    def _structured_frame(self, *, session_id: str, revision: int = 1, pick: int = 13, available: bool = True, after_pick: bool = False) -> dict:
        team_order = tuple(f"Team {index}" for index in range(1, 13)) + ("Example Team", "Team 14")
        history = tuple(
            PickEvent(index, team_order[(index - 1) % len(team_order)], f"drafted-{index}", f"Drafted {index}")
            for index in range(1, pick if not after_pick else 13)
        )
        if after_pick:
            history = history + (PickEvent(13, "Example Team", "p1", "Candidate One"),)
            current_pick = 14
            on_clock = "Team 14"
            roster = (LivePlayer("p1", "Candidate One", ("G",)),)
        else:
            current_pick = pick
            on_clock = "Example Team"
            roster = ()
        players = (
            LivePlayer("p1", "Candidate One", ("G",), stats={"FPTS": 68.0, "PTS": 24.0}, projected_gp=80, draft_rank=1),
            LivePlayer("p2", "Candidate Two", ("C",), stats={"FPTS": 64.0, "PTS": 21.0}, projected_gp=78, draft_rank=2),
        ) if available and not after_pick else (LivePlayer("p2", "Candidate Two", ("C",), stats={"FPTS": 64.0, "PTS": 21.0}, projected_gp=78, draft_rank=2),)
        return {
            "session_id": session_id,
            "observed_at": utc_now(),
            "revision": revision,
            "phase": "draft",
            "current_pick": current_pick,
            "on_clock_team": on_clock,
            "seconds_remaining": 30,
            "pick_order": team_order,
            "history": history,
            "roster": roster,
            "available_players": players,
            "known_players": tuple((*players, *roster)),
            "autopick": False,
            "rules": {
                "teams": 14,
                "rounds": 10,
                "starting_slots": list(self.config.starting_slots),
                "bench_slots": 4,
                "ir_slots": 3,
                "scoring": dict(self.config.scoring),
            },
            "complete": True,
            "history_complete": True,
            "available_pool_complete": False,
        }

    def test_parser_observation_reaches_readiness_and_guarded_readback_confirmation(self) -> None:
        self.coordinator = Coordinator(self.db_path, config=self.config)
        session_id = self.coordinator.create_session(self.room)["session_id"]
        self.coordinator.attest_practice(
            session_id,
            heading="ESPN Fantasy Basketball Draft - Practice Draft for Example League",
            source_league_id=self.config.league_id,
        )
        initial = parse_visible_snapshot(
            self._structured_frame(session_id=session_id),
            session_id=session_id,
            room=self.room,
            config=self.config,
        )
        self.assertEqual(initial.rules["bench_slots"], 4)
        self.assertEqual(initial.rules["ir_slots"], 3)
        self.assertEqual(initial.rules["starting_slots"], list(self.config.starting_slots))
        self.assertEqual(len(initial.pick_order), 14)
        self.assertEqual(initial.pick_order[12], self.config.our_team)
        self.assertEqual([event.pick_number for event in initial.history], list(range(1, 13)))
        self.assertEqual(initial.roster, ())

        self.coordinator.ingest_observation(initial)
        readiness = self.coordinator.readiness(session_id)
        self.assertTrue(readiness["ready"], readiness)
        self.coordinator.start(session_id)

        def strategy(observation: DraftObservation, config: LeagueConfig, **_: object) -> DraftRecommendation:
            return DraftRecommendation(observation.session_id, observation.revision, utc_now(), (CandidateScore("p1", "Candidate One", 1.0),))

        self.coordinator.strategy = strategy
        self.coordinator.recommend(session_id)
        action = self.coordinator.next_action(session_id)
        self.assertIsNotNone(action)
        assert action is not None
        self.coordinator.claim_action(action.action_id)

        operator_session = DraftSession(session_id, state="running")
        operator = BrowserOperator(
            session=operator_session,
            room=self.room,
            config=self.config,
            observation=initial,
            coordinator_guard=lambda planned, _: self.coordinator.final_guard(planned.action_id),
        )
        submitted = operator.submit(action, confirm=True, submitter=lambda _: {"status": "submitted"})
        self.assertEqual(submitted.status, "submitted")

        after = parse_visible_snapshot(
            self._structured_frame(session_id=session_id, revision=2, after_pick=True),
            session_id=session_id,
            room=self.room,
            config=self.config,
        )
        confirmed = self.coordinator.ack_action(action.action_id, "confirmed", observation=after)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(self.coordinator.status(session_id)["actions"][0]["status"], "confirmed")

    def test_transient_partial_frame_cannot_start(self) -> None:
        self.coordinator = Coordinator(self.db_path, config=self.config)
        session_id = self.coordinator.create_session(self.room)["session_id"]
        partial = parse_visible_snapshot(
            """ESPN Fantasy Basketball Draft - Practice Draft for Example League
RND1of10
Clock 00:30
On the Clock: Pick13 ExampleTeam
Players
Draft
RK Player FPTS GP MIN FGM FGA FTM FTA 3PM REB AST STL BLK TO EJ PF
No Players Available
""",
            session_id=session_id,
            room=self.room,
            config=self.config,
        )
        self.assertFalse(partial.available_pool_complete)
        self.assertEqual(partial.available_players, ())
        self.coordinator.ingest_observation(partial)
        readiness = self.coordinator.readiness(session_id)
        self.assertFalse(readiness["ready"])
        with self.assertRaises(ReadinessError):
            self.coordinator.start(session_id)


if __name__ == "__main__":
    unittest.main()
