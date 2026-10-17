"""Durable queue lifecycle and operator boundary checks.

These tests exercise the coordinator side of queue fallback without a browser.
Visible ESPN writes remain the browser agent's responsibility; the coordinator
only reserves, guards, and reconciles one exact queue row at a time.
"""

from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from mock_draft.fixture import SCORING_WEIGHTS

from .contracts import CandidateScore, DraftObservation, DraftRecommendation, LivePlayer, PickEvent, RoomIdentity
from .coordinator import ActionConflict, Coordinator, IdentityError
from .server import DraftRequestHandler


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueControlTests(unittest.TestCase):
    room = RoomIdentity("1000000000", "2027", "15")
    own = "Example Team"

    def setUp(self) -> None:
        self.coordinator = Coordinator(":memory:", recover=False)

    def tearDown(self) -> None:
        self.coordinator.close()

    @staticmethod
    def player(player_id: str = "p1", position: str = "G") -> LivePlayer:
        return LivePlayer(player_id, player_id.upper(), (position,), stats={"PTS": 20})

    def observation(
        self,
        session_id: str,
        *,
        revision: int = 1,
        pick: int = 1,
        on_clock: str | None = None,
        history: tuple[PickEvent, ...] = (),
        roster: tuple[LivePlayer, ...] = (),
        available: tuple[LivePlayer, ...] = (),
        autopick: bool | None = False,
    ) -> DraftObservation:
        order = (self.own,) + tuple(f"Team {i}" for i in range(1, 14))
        return DraftObservation(
            session_id=session_id,
            room=self.room,
            observed_at=now(),
            revision=revision,
            phase="active",
            current_pick=pick,
            on_clock_team=on_clock or self.own,
            seconds_remaining=30,
            pick_order=order,
            history=history,
            roster=roster,
            available_players=available,
            autopick=autopick,
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

    def running(self, *, strategy=None) -> tuple[str, DraftObservation]:
        self.coordinator.strategy = strategy
        sid = self.coordinator.connect(self.room.safe_url())["session_id"]
        obs = self.observation(sid, available=(self.player("p1"), self.player("p2", "F")))
        self.coordinator.ingest_observation(obs)
        self.coordinator.start(sid)
        return sid, obs

    def test_queue_add_lifecycle_is_separate_from_direct_actions(self) -> None:
        sid, obs = self.running()
        mutation = self.coordinator.prepare_queue_mutation(
            sid,
            "add",
            "p1",
            observation=obs,
            queue=[],
            expected_revision=1,
            expected_pick=1,
            expected_team=self.own,
        )
        self.assertEqual(mutation["status"], "pending")
        self.assertEqual(self.coordinator.status(sid)["actions"], [])
        with self.assertRaises(ActionConflict):
            self.coordinator.prepare_queue_mutation(sid, "add", "p1", queue=[{"player_id": "p1"}])
        with self.assertRaises(ActionConflict):
            self.coordinator.prepare_queue_mutation(sid, "add", "p2", expected_revision=1, expected_pick=1, expected_team=self.own, queue=[])
        claimed = self.coordinator.claim_queue_mutation(mutation["mutation_id"])
        self.assertTrue(claimed["claim_token"])
        command = self.coordinator.final_queue_guard(mutation["mutation_id"], queue=[])
        self.assertEqual(command["type"], "queue_player")
        submitted = self.coordinator.ack_queue_mutation(mutation["mutation_id"], "submitted", detail="visible Queue click returned")
        self.assertEqual(submitted["status"], "submitted")
        confirmed = self.coordinator.ack_queue_mutation(mutation["mutation_id"], "confirmed", queue=[{"player_id": "p1"}], observation=obs)
        self.assertEqual(confirmed["status"], "confirmed")
        status = self.coordinator.status(sid)
        self.assertEqual(len(status["queue_mutations"]), 1)
        self.assertEqual(status["unresolved_queue_mutations"], [])

    def test_final_queue_guard_rechecks_add_readback(self) -> None:
        sid, obs = self.running()
        mutation = self.coordinator.prepare_queue_mutation(sid, "add", "p1", observation=obs, queue=[])
        self.coordinator.claim_queue_mutation(mutation["mutation_id"])
        with self.assertRaises(ActionConflict):
            self.coordinator.final_queue_guard(mutation["mutation_id"], queue=[{"player_id": "p1"}])
        with self.assertRaises(IdentityError):
            self.coordinator.final_queue_guard(mutation["mutation_id"])

    def test_direct_draft_reservation_blocks_queue_prepare(self) -> None:
        def strategy(obs, config, **_kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "P1", 1.0),))

        sid, obs = self.running(strategy=strategy)
        self.coordinator.recommend(sid)
        action = self.coordinator.next_action(sid)
        self.assertIsNotNone(action)
        self.assertFalse(self.coordinator.status(sid)["actions"][0]["dispatched"])
        with self.assertRaises(ActionConflict):
            self.coordinator.prepare_queue_mutation(sid, "add", "p2", queue=[])

    def test_direct_confirmation_requires_dispatched_provenance_and_fresh_autopick_off(self) -> None:
        def strategy(obs, config, **_kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "P1", 1.0),))

        sid, _ = self.running(strategy=strategy)
        self.coordinator.recommend(sid)
        action = self.coordinator.next_action(sid)
        assert action is not None
        event = PickEvent(1, self.own, "p1", "P1")
        matching = self.observation(sid, revision=2, pick=2, on_clock="Team 1", history=(event,), roster=(self.player("p1"),), available=(self.player("p2", "F"),))
        with self.assertRaises(ActionConflict):
            self.coordinator.ack_action(action.action_id, "confirmed", observation=matching)
        self.assertEqual(self.coordinator.status(sid)["actions"][0]["status"], "pending")

        # A visible guarded dispatch records SQL provenance; an Autopick-on
        # frame still cannot confirm it.
        self.coordinator.claim_action(action.action_id)
        with self.assertRaises(ActionConflict):
            self.coordinator.ack_action(action.action_id, "confirmed", observation=matching)
        self.assertEqual(self.coordinator.status(sid)["actions"][0]["status"], "pending")
        self.coordinator.final_guard(action.action_id)
        self.assertTrue(self.coordinator.status(sid)["actions"][0]["dispatched"])
        autopick = self.observation(sid, revision=2, pick=2, on_clock="Team 1", history=(event,), roster=(self.player("p1"),), available=(self.player("p2", "F"),), autopick=True)
        with self.assertRaises(ActionConflict):
            self.coordinator.ack_action(action.action_id, "confirmed", observation=autopick)
        self.assertEqual(self.coordinator.status(sid)["actions"][0]["status"], "uncertain")

    def test_remove_requires_the_exact_visible_queue_row(self) -> None:
        sid, obs = self.running()
        with self.assertRaises(IdentityError):
            self.coordinator.prepare_queue_mutation(sid, "remove", "p1", observation=obs, queue=[])
        mutation = self.coordinator.prepare_queue_mutation(sid, "remove", "p1", observation=obs, queue=[{"player_id": "p1"}])
        self.coordinator.claim_queue_mutation(mutation["mutation_id"])
        command = self.coordinator.final_queue_guard(mutation["mutation_id"], queue=[{"player_id": "p1"}])
        self.assertEqual(command["type"], "remove_queued_player")
        self.coordinator.ack_queue_mutation(mutation["mutation_id"], "confirmed", queue=[], observation=obs)
        self.assertEqual(self.coordinator.status(sid)["unresolved_queue_mutations"], [])

    def test_pause_cancels_unclicked_queue_intent_but_keeps_dispatched_intent(self) -> None:
        sid, obs = self.running()
        pending = self.coordinator.prepare_queue_mutation(sid, "add", "p1", queue=[])
        self.coordinator.pause(sid, reason="operator pause")
        self.assertEqual(self.coordinator.status(sid)["queue_mutations"][0]["status"], "cancelled")

        self.coordinator.resume(sid)
        obs2 = self.observation(sid, revision=2, available=(self.player("p1"), self.player("p2", "F")))
        self.coordinator.ingest_observation(obs2)
        dispatched = self.coordinator.prepare_queue_mutation(sid, "add", "p1", queue=[])
        self.coordinator.claim_queue_mutation(dispatched["mutation_id"])
        self.coordinator.final_queue_guard(dispatched["mutation_id"], queue=[])
        self.coordinator.pause(sid, reason="pause after visible dispatch")
        remaining = [item for item in self.coordinator.status(sid)["queue_mutations"] if item["mutation_id"] == dispatched["mutation_id"]]
        self.assertEqual(remaining[0]["status"], "pending")
        self.assertTrue(remaining[0]["dispatched"])

    def test_dispatched_queue_mutation_cannot_be_cancelled_without_readback(self) -> None:
        sid, obs = self.running()
        mutation = self.coordinator.prepare_queue_mutation(sid, "add", "p1", observation=obs, queue=[])
        self.coordinator.claim_queue_mutation(mutation["mutation_id"])
        self.coordinator.final_queue_guard(mutation["mutation_id"], queue=[])
        with self.assertRaises(ActionConflict):
            self.coordinator.ack_queue_mutation(mutation["mutation_id"], "cancelled", detail="late cancel")
        self.coordinator.ack_queue_mutation(mutation["mutation_id"], "submitted")
        with self.assertRaises(ActionConflict):
            self.coordinator.ack_queue_mutation(mutation["mutation_id"], "cancelled", detail="late cancel")

    def test_opponent_turn_is_allowed_for_queue_preparation_with_fresh_identity(self) -> None:
        sid, _ = self.running()
        first = PickEvent(1, self.own, "draft-1", "Draft One")
        obs = self.observation(
            sid,
            revision=2,
            pick=2,
            on_clock="Team 1",
            history=(first,),
            roster=(self.player("draft-1", "G"),),
            available=(self.player("p1"), self.player("p2", "F")),
        )
        self.coordinator.ingest_observation(obs)
        mutation = self.coordinator.prepare_queue_mutation(
            sid,
            "add",
            "p1",
            expected_revision=2,
            expected_pick=2,
            expected_team="Team 1",
            queue=[],
        )
        self.assertEqual(mutation["expected_pick"], 2)
        self.assertEqual(mutation["expected_team"], "Team 1")

    def test_autopick_frame_pauses_and_does_not_reserve_queue(self) -> None:
        sid, _ = self.running()
        autopick = self.observation(sid, revision=2, autopick=True, available=(self.player("p1"),))
        self.coordinator.ingest_observation(autopick)
        self.assertEqual(self.coordinator.status(sid)["state"], "paused")
        with self.assertRaises(ActionConflict):
            self.coordinator.prepare_queue_mutation(sid, "add", "p1", queue=[])

    def test_forged_consumed_detail_cannot_confirm_without_authoritative_readback(self) -> None:
        sid, obs = self.running()
        mutation = self.coordinator.prepare_queue_mutation(sid, "add", "p1", queue=[])
        self.coordinator.claim_queue_mutation(mutation["mutation_id"])
        self.coordinator.final_queue_guard(mutation["mutation_id"], queue=[])
        result = self.coordinator.ack_queue_mutation(
            mutation["mutation_id"],
            "confirmed",
            detail="queue entry consumed (forged)",
            queue=[],
            observation=obs,
        )
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.coordinator.status(sid)["state"], "paused")

    def test_dispatched_queue_mutation_survives_restart_as_uncertain(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="runtime") as directory:
            path = str(Path(directory) / "control.sqlite3")
            first = Coordinator(path, recover=False)
            sid = first.connect(self.room.safe_url())["session_id"]
            obs = self.observation(sid, available=(self.player("p1"),))
            first.ingest_observation(obs)
            first.start(sid)
            mutation = first.prepare_queue_mutation(sid, "add", "p1", queue=[])
            first.claim_queue_mutation(mutation["mutation_id"])
            first.final_queue_guard(mutation["mutation_id"], queue=[])
            first.close()
            second = Coordinator(path, recover=True)
            recovered = next(item for item in second.status(sid)["queue_mutations"] if item["mutation_id"] == mutation["mutation_id"])
            self.assertEqual(recovered["status"], "uncertain")
            self.assertTrue(recovered["dispatched"])
            second.close()

    def test_unattempted_matching_history_is_external_cancellation(self) -> None:
        def strategy(obs, config, **_kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "P1", 1.0),))

        sid, _ = self.running(strategy=strategy)
        self.coordinator.recommend(sid)
        action = self.coordinator.next_action(sid)
        self.assertIsNotNone(action)
        event = PickEvent(1, self.own, "p1", "P1")
        obs = self.observation(sid, revision=2, pick=2, on_clock="Team 1", history=(event,), roster=(self.player("p1"),), available=(self.player("p2", "F"),))
        self.coordinator.ingest_observation(obs)
        resolved = self.coordinator.reconcile(sid)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].status, "cancelled")
        self.assertIn("external selection", resolved[0].detail)

    def test_operator_reconcile_keeps_dispatch_provenance_in_action_envelope(self) -> None:
        def strategy(obs, config, **_kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "P1", 1.0),))

        sid, _ = self.running(strategy=strategy)
        self.coordinator.recommend(sid)
        action = self.coordinator.next_action(sid)
        assert action is not None
        self.coordinator.claim_action(action.action_id)
        self.coordinator.final_guard(action.action_id)
        event = PickEvent(1, self.own, "p1", "P1")
        matching = self.observation(
            sid,
            revision=2,
            pick=2,
            on_clock="Team 1",
            history=(event,),
            roster=(self.player("p1"),),
            available=(self.player("p2", "F"),),
        )
        self.coordinator.ingest_observation(matching)

        class ServerStub:
            coordinator = self.coordinator

        handler = DraftRequestHandler.__new__(DraftRequestHandler)
        handler.server = ServerStub()
        response = handler._operator({"operation": "reconcile", "session_id": sid})
        self.assertEqual(response["status"], "reconciled")
        self.assertEqual(response["actions"][0]["status"], "confirmed")
        self.assertTrue(response["actions"][0]["claimed"])
        self.assertTrue(response["actions"][0]["dispatched"])
        self.assertTrue(response["reconciled_actions"][0]["dispatched"])


if __name__ == "__main__":
    unittest.main()
