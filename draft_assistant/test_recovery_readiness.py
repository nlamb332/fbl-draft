"""Pure queue/recovery regressions supporting the attended-draft matrix."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mock_draft.fixture import SCORING_WEIGHTS

from .bridge import BrowserBridge
from .browser_operator import BrowserOperator
from .contracts import (
    CandidateScore,
    DraftObservation,
    DraftRecommendation,
    LeagueConfig,
    LivePlayer,
    PickEvent,
    RoomIdentity,
)
from .coordinator import ActionConflict, Coordinator, IdentityError, CoordinatorError
from .lifecycle import DraftSession


def _now(delta: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()


class RecoveryReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        self.room = RoomIdentity("1000000000", "2027", "15")
        self.player_one = LivePlayer("p1", "Player One", ("G",), stats={"PTS": 20})
        self.player_two = LivePlayer("p2", "Player Two", ("C",), stats={"PTS": 19})

    def observation(
        self,
        session_id: str,
        *,
        revision: int = 1,
        observed_at: str | None = None,
        pick: int = 1,
        on_clock: str | None = "Example Team",
        autopick: bool | None = False,
        history: tuple[PickEvent, ...] = (),
        roster: tuple[LivePlayer, ...] = (),
        available: tuple[LivePlayer, ...] | None = None,
        pick_order: tuple[str, ...] | None = None,
        phase: str = "draft",
        complete: bool = True,
        history_complete: bool = True,
    ) -> DraftObservation:
        return DraftObservation(
            session_id=session_id,
            room=self.room,
            observed_at=observed_at or _now(),
            revision=revision,
            phase=phase,
            current_pick=pick,
            on_clock_team=on_clock,
            seconds_remaining=30,
            pick_order=pick_order or (("Example Team",) + tuple(f"Team {i}" for i in range(1, 14))),
            history=history,
            roster=roster,
            available_players=available if available is not None else (self.player_one, self.player_two),
            autopick=autopick,
            rules={
                "teams": 14,
                "rounds": 10,
                "starting_slots": ["C", "G", "G", "F", "F", "UTIL"],
                "bench_slots": 4,
                "ir_slots": 3,
                "scoring": dict(SCORING_WEIGHTS),
            },
            complete=complete,
            history_complete=history_complete,
        )

    def running(self, strategy=None) -> tuple[Coordinator, str]:
        coordinator = Coordinator(":memory:", strategy=strategy, recover=False)
        sid = coordinator.connect(self.room.safe_url())["session_id"]
        coordinator.ingest_observation(self.observation(sid))
        coordinator.start(sid)
        return coordinator, sid

    def recommendation(self, observation, config, **_):
        return DraftRecommendation(
            observation.session_id,
            observation.revision,
            _now(),
            (CandidateScore("p1", "Player One", 1.0),),
        )

    def test_queue_plan_preserves_order_without_mutation(self) -> None:
        session = DraftSession("queue-session")
        observation = self.observation(session.session_id)
        operator = BrowserOperator(
            session=session,
            room=self.room,
            config=LeagueConfig(),
            observation=observation,
            coordinator_guard=lambda *_: True,
        )
        operator.start()

        plan = operator.plan_queue(["p2", "p1"], reason="queue-order regression")

        self.assertEqual([candidate.player_id for candidate in plan.candidates], ["p2", "p1"])
        self.assertTrue(all(candidate.action_type == "queue" for candidate in plan.candidates))
        self.assertEqual([candidate.status for candidate in plan.candidates], ["pending", "pending"])

    def test_pause_cancels_undispatched_action(self) -> None:
        coordinator, sid = self.running(self.recommendation)
        try:
            coordinator.recommend(sid)
            action = coordinator.next_action(sid)
            assert action is not None

            coordinator.pause(sid, reason="queue cancellation regression")

            status = coordinator.status(sid)
            self.assertEqual(status["state"], "paused")
            self.assertEqual(status["actions"][0]["status"], "cancelled")
            self.assertEqual(status["unresolved_actions"], [])
            self.assertIn("cancelled by pause", status["actions"][0]["detail"])
        finally:
            coordinator.close()

    def test_stale_observation_pauses_and_cancels_pending_action(self) -> None:
        coordinator, sid = self.running(self.recommendation)
        try:
            coordinator.recommend(sid)
            action = coordinator.next_action(sid)
            assert action is not None

            coordinator.ingest_observation(self.observation(sid, revision=2, observed_at=_now(-11)))

            status = coordinator.status(sid)
            self.assertEqual(status["state"], "paused")
            self.assertEqual(status["actions"][0]["status"], "cancelled")
            self.assertIn("observation requires recovery", status["reason"])
        finally:
            coordinator.close()

    def test_autopick_off_stays_paused_until_explicit_resume(self) -> None:
        coordinator, sid = self.running(self.recommendation)
        try:
            coordinator.ingest_observation(self.observation(sid, revision=2, autopick=True))
            self.assertEqual(coordinator.status(sid)["state"], "paused")

            coordinator.ingest_observation(self.observation(sid, revision=3, autopick=False))
            self.assertEqual(coordinator.status(sid)["state"], "paused")
            coordinator.recommend(sid)
            with self.assertRaises(ActionConflict):
                coordinator.next_action(sid)

            coordinator.resume(sid)
            coordinator.recommend(sid)
            action = coordinator.next_action(sid)
            self.assertIsNotNone(action)
        finally:
            coordinator.close()

    def _post_final_own_pick_frame(self, session_id: str, *, revision: int = 2) -> DraftObservation:
        """Build a seat-13 frame after pick 128, before the room completes."""
        order = tuple(f"Team {i}" for i in range(1, 13)) + ("Example Team", "Team 13")
        history = tuple(
            PickEvent(
                pick,
                Coordinator._expected_team_for_pick(pick, order),
                f"p{pick}",
                f"Player {pick}",
            )
            for pick in range(1, 129)
        )
        own_history = [event for event in history if event.team == "Example Team"]
        eligibilities = (("C",), ("G",), ("G",), ("F",), ("F",), ("C",), ("G",), ("F",), ("C",), ("G",))
        roster = tuple(
            LivePlayer(event.player_id, event.player_name, eligibility)
            for event, eligibility in zip(own_history, eligibilities)
        )
        return self.observation(
            session_id,
            revision=revision,
            pick=129,
            on_clock="Team 12",
            history=history,
            roster=roster,
            available=(),
            pick_order=order,
        )

    def test_empty_pool_is_allowed_only_after_final_own_pick(self) -> None:
        coordinator, sid = self.running()
        try:
            final_own = self._post_final_own_pick_frame(sid)
            status = coordinator.ingest_observation(final_own)
            self.assertEqual(status["state"], "running")
            readiness = coordinator.readiness(sid)
            self.assertTrue(readiness["ready"], readiness)
            self.assertTrue(readiness["checks"]["available_pool"])
        finally:
            coordinator.close()

    def test_empty_pool_exception_rejects_early_or_unsafe_frames(self) -> None:
        coordinator, sid = self.running()
        try:
            valid = self._post_final_own_pick_frame(sid)
            cases = {
                "own_team_on_clock": replace(valid, revision=3, on_clock_team="Example Team"),
                "future_own_turn": replace(
                    valid,
                    revision=7,
                    pick_order=("Example Team",) + tuple(f"Team {i}" for i in range(1, 14)),
                    on_clock_team="Team 11",
                ),
                "mismatched_roster": replace(
                    valid,
                    revision=8,
                    roster=valid.roster[:-1] + (LivePlayer("not-picked", "Not Picked", ("G",)),),
                ),
                "illegal_roster": replace(
                    valid,
                    revision=9,
                    roster=tuple(LivePlayer(player.player_id, player.name, ("C",)) for player in valid.roster),
                ),
            }
            # At pick 128 seat 13 is still on the clock and has only nine
            # authoritative picks in history; an empty player table is not a
            # post-final frame yet.
            early_history = valid.history[:-1]
            early_roster = valid.roster[:-1]
            cases["before_final_own_pick"] = replace(
                valid,
                revision=10,
                current_pick=128,
                on_clock_team="Example Team",
                history=early_history,
                roster=early_roster,
            )

            for label, observation in cases.items():
                with self.subTest(label=label):
                    coordinator.ingest_observation(observation)
                    readiness = coordinator.readiness(sid)
                    self.assertFalse(readiness["checks"]["available_pool"])
                    self.assertFalse(readiness["ready"])
                    # The first unsafe frame pauses the running session; an
                    # explicit resume is not part of this pure rejection test.
                    if coordinator.status(sid)["state"] == "paused":
                        coordinator.pause(sid, reason="reset unsafe empty-pool test")
        finally:
            coordinator.close()

    def test_manual_stage_requires_paused_override_and_authoritative_confirmation(self) -> None:
        coordinator, sid = self.running()
        try:
            coordinator.pause(sid, reason="manual takeover", manual_override=True)
            staged = coordinator.manual_pick(sid, "p1")
            self.assertTrue(staged.detail.startswith("manual_stage:"))
            with self.assertRaises(ActionConflict):
                BrowserBridge(coordinator).command_for(staged.action_id)

            after = self.observation(
                sid,
                revision=2,
                pick=2,
                on_clock="Team 1",
                history=(PickEvent(1, "Example Team", "p1", "Player One"),),
                roster=(self.player_one,),
                available=(self.player_two,),
            )
            confirmed = coordinator.confirm_manual_pick(staged.action_id, observation=after)
            self.assertEqual(confirmed.status, "confirmed")
            self.assertEqual(coordinator.status(sid)["unresolved_actions"], [])

            coordinator.resume(sid)
            self.assertEqual(coordinator.status(sid)["state"], "running")
        finally:
            coordinator.close()

    def test_restart_pauses_and_preserves_submitted_action(self) -> None:
        with tempfile.TemporaryDirectory(dir="runtime") as directory:
            db = str(Path(directory) / "recovery.sqlite3")
            coordinator = Coordinator(db, strategy=self.recommendation, recover=False)
            sid = coordinator.connect(self.room.safe_url())["session_id"]
            coordinator.ingest_observation(self.observation(sid))
            coordinator.start(sid)
            coordinator.recommend(sid)
            action = coordinator.next_action(sid)
            assert action is not None
            coordinator.claim_action(action.action_id)
            coordinator.final_guard(action.action_id)
            coordinator.ack_action(action.action_id, "submitted")
            coordinator.close()

            restarted = Coordinator(db, strategy=self.recommendation)
            try:
                status = restarted.status(sid)
                self.assertEqual(status["state"], "paused")
                self.assertEqual(status["unresolved_actions"][0]["status"], "submitted")

                with self.assertRaises(IdentityError):
                    restarted.ingest_observation(self.observation(sid, revision=1))

                after = self.observation(
                    sid,
                    revision=2,
                    pick=2,
                    on_clock="Team 1",
                    history=(PickEvent(1, "Example Team", "p1", "Player One"),),
                    roster=(self.player_one,),
                    available=(self.player_two,),
                )
                restarted.ingest_observation(after)
                resolved = restarted.reconcile(sid)
                self.assertEqual([item.action_id for item in resolved], [action.action_id])
                self.assertEqual(restarted.status(sid)["unresolved_actions"], [])
            finally:
                restarted.close()

    def test_authoritative_final_frame_is_terminal(self) -> None:
        coordinator, sid = self.running(self.recommendation)
        try:
            coordinator.recommend(sid)
            action = coordinator.next_action(sid)
            assert action is not None
            coordinator.claim_action(action.action_id)
            coordinator.final_guard(action.action_id)
            coordinator.ack_action(action.action_id, "submitted")

            order = ("Example Team",) + tuple(f"Team {i}" for i in range(1, 14))
            history = tuple(
                PickEvent(
                    pick,
                    order[(pick - 1) % 14 if ((pick - 1) // 14) % 2 == 0 else 13 - ((pick - 1) % 14)],
                    f"p{pick}",
                    f"Player {pick}",
                )
                for pick in range(1, 141)
            )
            roster = tuple(
                LivePlayer(f"p{pick}", f"Player {pick}", eligibility)
                for pick, eligibility in zip(
                    (1, 28, 29, 56, 57, 84, 85, 112, 113, 140),
                    (("C",), ("G",), ("G",), ("F",), ("F",), ("C",), ("G",), ("F",), ("C",), ("G",)),
                )
            )
            final = self.observation(
                sid,
                revision=2,
                pick=140,
                on_clock=None,
                autopick=False,
                history=history,
                roster=roster,
                available=(),
                phase="complete",
                complete=True,
                history_complete=True,
            )
            final = replace(final, seconds_remaining=0)

            self.assertEqual(coordinator.ingest_observation(final)["state"], "completed")
            self.assertEqual(coordinator.reconcile(sid)[0].status, "confirmed")
            status = coordinator.status(sid)
            self.assertEqual(status["unresolved_actions"], [])
            with self.assertRaises(CoordinatorError):
                coordinator.resume(sid)
        finally:
            coordinator.close()


if __name__ == "__main__":
    unittest.main()
