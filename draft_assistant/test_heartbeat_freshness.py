"""State-aware freshness, degraded recovery, heartbeat touches and gap telemetry.

Ages are computed against a controllable clock so gaps of 12, 30 and 60 s are
exercised without sleeping.  Nothing here talks to ESPN or a browser.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest import mock

from mock_draft.fixture import SCORING_WEIGHTS

from . import coordinator as coordinator_module
from .bridge import BrowserBridge
from .contracts import CandidateScore, DraftObservation, DraftRecommendation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity, timestamp_age as real_timestamp_age
from .coordinator import ActionConflict, Coordinator, FreshnessError, IdentityError
from .ownership import OwnershipError

OUR_TEAM = "Example Team"
# Our seat is sixth: picks 1-5 and 7-14 of round one belong to opponents.
ORDER = tuple(f"Team {i}" for i in range(1, 6)) + (OUR_TEAM,) + tuple(f"Team {i}" for i in range(6, 14))
ROOM = RoomIdentity("1000000000", "2027", "15")
POOL = tuple(LivePlayer(f"p{i}", f"Player {i}", ("C", "G", "F"), stats={"PTS": 30 - i / 10}) for i in range(1, 41))


class FakeTime:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 18, 22, 0, 0, tzinfo=timezone.utc)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def iso(self) -> str:
        return self.now.isoformat()

    def age(self, value: str, now: datetime | None = None) -> float:
        return real_timestamp_age(value, now or self.now)


def recommend_first(observation, config, **_):
    player = observation.available_players[0]
    return DraftRecommendation(observation.session_id, observation.revision, "2026-10-18T22:00:00+00:00", (CandidateScore(player.player_id, player.name, 1.0),))


class HeartbeatFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.time = FakeTime()
        patcher = mock.patch.object(coordinator_module, "timestamp_age", self.time.age)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.coordinator = Coordinator(":memory:", strategy=recommend_first, recover=False, clock=self.time.iso)
        self.addCleanup(self.coordinator.close)
        self.sid = self.coordinator.connect(ROOM.safe_url())["session_id"]

    def team(self, pick: int) -> str:
        return self.coordinator._expected_team_for_pick(pick, ORDER)

    def frame(self, pick: int, *, revision: int = 0, history: tuple[PickEvent, ...] | None = None, **overrides) -> DraftObservation:
        """A complete frame for ``pick`` whose history is the first pick-1 pool players."""
        if history is None:
            history = tuple(PickEvent(n, self.team(n), f"p{n}", f"Player {n}") for n in range(1, pick))
        taken = {event.player_id for event in history}
        roster = tuple(p for p in POOL if p.player_id in {e.player_id for e in history if e.team == OUR_TEAM})
        values = dict(
            session_id=self.sid, room=ROOM, observed_at=self.time.iso(), revision=revision, phase="draft", current_pick=pick,
            on_clock_team=self.team(pick), seconds_remaining=60, pick_order=ORDER, history=history, roster=roster,
            available_players=tuple(p for p in POOL if p.player_id not in taken), autopick=False, complete=True, history_complete=True,
            rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
        )
        values.update(overrides)
        return DraftObservation(**values)

    def running_at(self, pick: int) -> None:
        self.coordinator.ingest_observation(self.frame(pick, revision=1))
        self.coordinator.start(self.sid)
        self.assertEqual(self.state(), "running")

    def state(self) -> str:
        return self.coordinator.status(self.sid)["state"]

    def events(self, name: str) -> list[dict]:
        return [e for e in self.coordinator.journal(self.sid, limit=500) if e["event"] == name]

    # --- running -> degraded -> running -------------------------------------

    def test_opponent_turn_gap_degrades_then_recovers_on_next_contiguous_frame(self) -> None:
        self.running_at(2)
        self.time.advance(12)
        status = self.coordinator.status(self.sid)
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(self.events("stale_degraded")[0]["payload"]["from_revision"], 1)
        self.assertEqual(self.events("stale_pause"), [])

        # Degraded never authorizes anything on the stale frame.
        with self.assertRaises(FreshnessError):
            self.coordinator.recommend(self.sid)
        with self.assertRaises(ActionConflict):
            self.coordinator.next_action(self.sid)

        self.coordinator.ingest_observation(self.frame(3, revision=2))
        self.assertEqual(self.state(), "running")
        recovered = self.events("degraded_recovered")
        self.assertEqual(len(recovered), 1)
        self.assertAlmostEqual(recovered[0]["payload"]["gap_seconds"], 12.0, places=2)
        self.assertEqual(recovered[0]["payload"]["from_revision"], 1)
        self.assertEqual(recovered[0]["payload"]["to_revision"], 2)

    def test_incomplete_or_stale_frames_leave_a_degraded_session_degraded(self) -> None:
        self.running_at(2)
        self.time.advance(30)
        self.assertEqual(self.state(), "degraded")
        self.coordinator.ingest_observation(self.frame(3, revision=2, complete=False))
        self.assertEqual(self.state(), "degraded")
        self.coordinator.ingest_observation(self.frame(3, revision=3, observed_at=(self.time.now - timedelta(seconds=11)).isoformat()))
        self.assertEqual(self.state(), "degraded")
        self.coordinator.ingest_observation(self.frame(3, revision=4))
        self.assertEqual(self.state(), "running")

    def test_operator_can_pause_a_degraded_session_and_resume_on_a_fresh_frame(self) -> None:
        self.running_at(2)
        self.time.advance(15)
        self.assertEqual(self.state(), "degraded")
        self.coordinator.pause(self.sid, reason="operator takeover")
        self.assertEqual(self.state(), "paused")
        self.coordinator.ingest_observation(self.frame(2, revision=2))
        self.assertEqual(self.state(), "paused")
        self.coordinator.resume(self.sid)
        self.assertEqual(self.state(), "running")

    def test_transfer_from_degraded_requires_successor_resume_after_heartbeat(self) -> None:
        self.running_at(2)
        old = self.coordinator.ownership.claim(self.sid, label="old owner")["owner_token"]
        self.time.advance(12)
        self.assertEqual(self.state(), "degraded")

        moved = self.coordinator.ownership.transfer(
            self.sid,
            last_confirmed_revision=self.coordinator.status(self.sid)["latest_revision"],
            label="successor",
        )
        self.assertTrue(moved["paused_running_session"])
        self.assertEqual(self.state(), "paused")

        # This complete contiguous heartbeat normally recovers a degraded
        # session, but after transfer it must leave the session paused.
        self.coordinator.heartbeat(self.frame(3, revision=2))
        self.assertEqual(self.state(), "paused")
        with self.assertRaises(OwnershipError):
            self.coordinator.ownership.require(self.sid, old)
        self.coordinator.ownership.require(self.sid, moved["owner_token"])
        self.coordinator.resume(self.sid)
        self.assertEqual(self.state(), "running")

    def test_transfer_from_running_requires_successor_resume_and_survives_restart(self) -> None:
        self.running_at(2)
        old = self.coordinator.ownership.claim(self.sid, label="old owner")["owner_token"]
        self.assertEqual(self.state(), "running")

        moved = self.coordinator.ownership.transfer(
            self.sid,
            last_confirmed_revision=self.coordinator.status(self.sid)["latest_revision"],
            label="successor",
        )
        self.assertTrue(moved["paused_running_session"])
        self.assertEqual(self.state(), "paused")

        # Fresh, complete, contiguous frames and heartbeats never resume it.
        self.coordinator.heartbeat(self.frame(2, revision=2))
        self.coordinator.ingest_observation(self.frame(3, revision=3))
        self.assertEqual(self.state(), "paused")
        # A restart keeps it paused and the old token stays revoked.
        self.coordinator._recover_after_restart()
        self.assertEqual(self.state(), "paused")
        with self.assertRaises(OwnershipError):
            self.coordinator.ownership.require(self.sid, old)
        self.coordinator.ownership.require(self.sid, moved["owner_token"])
        self.coordinator.resume(self.sid)
        self.assertEqual(self.state(), "running")

    # --- transitions that keep the explicit-resume requirement --------------

    def test_stale_during_our_turn_pauses_and_needs_explicit_resume(self) -> None:
        self.running_at(6)
        self.time.advance(12)
        self.assertEqual(self.state(), "paused")
        self.assertEqual(len(self.events("stale_pause")), 1)
        self.coordinator.ingest_observation(self.frame(6, revision=2))
        self.assertEqual(self.state(), "paused")
        self.coordinator.resume(self.sid)
        self.assertEqual(self.state(), "running")

    def test_stale_with_a_claimed_action_pauses(self) -> None:
        self.running_at(6)
        self.coordinator.recommend(self.sid)
        action = self.coordinator.next_action(self.sid)
        self.coordinator.claim_action(action.action_id)
        # The board moves on to an opponent turn but our claim is unresolved.
        history = tuple(PickEvent(n, self.team(n), f"p{n}", f"Player {n}") for n in range(1, 7))
        self.coordinator.ingest_observation(self.frame(7, revision=2, history=history))
        self.assertEqual(self.state(), "running")
        self.time.advance(12)
        self.assertEqual(self.state(), "paused")
        self.assertEqual(self.events("stale_degraded"), [])

    def test_stale_with_a_proposed_action_pauses_and_cancels_it(self) -> None:
        self.running_at(6)
        self.coordinator.recommend(self.sid)
        self.coordinator.next_action(self.sid)
        history = tuple(PickEvent(n, self.team(n), f"p{n}", f"Player {n}") for n in range(1, 7))
        self.coordinator.ingest_observation(self.frame(7, revision=2, history=history))
        self.time.advance(12)
        status = self.coordinator.status(self.sid)
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["actions"][0]["status"], "cancelled")

    def test_non_contiguous_history_after_the_gap_pauses(self) -> None:
        self.running_at(3)
        self.time.advance(20)
        self.assertEqual(self.state(), "degraded")
        history = tuple(PickEvent(n, self.team(n), f"p{n}", f"Player {n}") for n in (1, 2, 4, 5))
        self.coordinator.ingest_observation(self.frame(6, revision=2, history=history))
        status = self.coordinator.status(self.sid)
        self.assertEqual(status["state"], "paused")
        self.assertIn("degraded recovery refused", status["reason"])
        self.assertEqual(self.events("degraded_recovered"), [])

    def test_history_that_contradicts_the_journal_pauses(self) -> None:
        self.running_at(3)
        self.time.advance(20)
        history = (PickEvent(1, self.team(1), "p30", "Player 30"), PickEvent(2, self.team(2), "p2", "Player 2"))
        self.coordinator.ingest_observation(self.frame(3, revision=2, history=history))
        status = self.coordinator.status(self.sid)
        self.assertEqual(status["state"], "paused")
        self.assertIn("does not extend the last confirmed history", status["reason"])

    def test_room_identity_change_pauses_a_degraded_session(self) -> None:
        self.running_at(3)
        self.time.advance(20)
        self.assertEqual(self.state(), "degraded")
        other = replace(self.frame(3, revision=2), room=RoomIdentity("999", "2027", "15"))
        with self.assertRaises(IdentityError):
            self.coordinator.ingest_observation(other)
        self.assertEqual(self.state(), "paused")
        with self.assertRaises(IdentityError):
            self.coordinator.heartbeat(replace(self.frame(3), room=RoomIdentity("999", "2027", "15")))

    def test_restart_recovery_pauses_a_degraded_session(self) -> None:
        self.running_at(3)
        self.time.advance(20)
        self.assertEqual(self.state(), "degraded")
        self.coordinator._recover_after_restart()
        self.assertEqual(self.coordinator.status(self.sid)["state"], "paused")

    # --- stale frames never dispatch ---------------------------------------

    def test_stale_frame_can_never_pass_the_final_guard(self) -> None:
        self.running_at(6)
        self.coordinator.recommend(self.sid)
        action = self.coordinator.next_action(self.sid)
        self.coordinator.claim_action(action.action_id)
        self.time.advance(10.5)
        with self.assertRaises(ActionConflict):
            self.coordinator.final_guard(action.action_id)
        with self.assertRaises(Exception):
            BrowserBridge(self.coordinator).command_for(action.action_id)
        status = self.coordinator.status(self.sid)
        self.assertFalse(status["actions"][0]["dispatched"])
        self.assertEqual(status["state"], "paused")

    def test_degraded_session_cannot_claim_or_dispatch(self) -> None:
        self.running_at(5)
        self.time.advance(12)
        self.assertEqual(self.state(), "degraded")
        for call in (lambda: self.coordinator.recommend(self.sid), lambda: self.coordinator.next_action(self.sid)):
            with self.assertRaises(Exception):
                call()

    # --- heartbeat ----------------------------------------------------------

    def test_heartbeat_touch_keeps_the_same_revision_and_prevents_staleness(self) -> None:
        self.running_at(6)
        self.coordinator.recommend(self.sid)
        action = self.coordinator.next_action(self.sid)
        self.coordinator.claim_action(action.action_id)
        for _ in range(6):
            self.time.advance(4)
            result = self.coordinator.heartbeat(self.frame(6, seconds_remaining=45))
            self.assertEqual(result["status"], "touched")
            self.assertEqual(result["revision"], 1)
        self.assertEqual(self.state(), "running")
        guard = self.coordinator.final_guard(action.action_id)
        self.assertTrue(guard["allowed"])

    def test_without_heartbeats_the_same_wait_pauses(self) -> None:
        self.running_at(6)
        self.time.advance(24)
        self.assertEqual(self.state(), "paused")

    def test_heartbeat_with_changed_board_gets_a_coordinator_revision(self) -> None:
        self.running_at(2)
        self.time.advance(3)
        result = self.coordinator.heartbeat(self.frame(3, revision=0))
        self.assertEqual((result["status"], result["revision"], result["state"]), ("ingested", 2, "running"))
        # An agent frame the heartbeat already numbered is an idempotent retry.
        agent_frame = self.frame(3, revision=1, observed_at=self.time.iso())
        self.assertEqual(self.coordinator.ingest_or_match_observation(agent_frame, session_id=self.sid)["latest_revision"], 2)

    def test_incomplete_heartbeat_never_refreshes_freshness(self) -> None:
        self.running_at(2)
        self.time.advance(6)
        self.assertEqual(self.coordinator.heartbeat(self.frame(2, complete=False))["status"], "ignored")
        self.time.advance(6)
        self.assertEqual(self.state(), "degraded")

    def test_heartbeat_from_a_stale_frame_is_rejected(self) -> None:
        self.running_at(2)
        with self.assertRaises(FreshnessError):
            self.coordinator.heartbeat(self.frame(2, observed_at=(self.time.now - timedelta(seconds=11)).isoformat()))

    def test_heartbeat_recovery_reports_the_gap(self) -> None:
        self.running_at(2)
        self.time.advance(31)
        self.assertEqual(self.state(), "degraded")
        result = self.coordinator.heartbeat(self.frame(2))
        self.assertEqual((result["status"], result["state"]), ("touched", "running"))
        self.assertAlmostEqual(self.events("degraded_recovered")[0]["payload"]["gap_seconds"], 31.0, places=2)

    # --- telemetry ----------------------------------------------------------

    def test_gap_telemetry_reports_percentiles_and_histogram(self) -> None:
        self.running_at(2)  # first frame: no predecessor
        for gap in (1, 1, 1, 2, 4, 8, 12):
            self.time.advance(gap)
            self.coordinator.heartbeat(self.frame(2), visibility="visible")
        self.time.advance(2)
        self.coordinator.heartbeat(self.frame(2), visibility="hidden")
        telemetry = self.coordinator.gap_telemetry(self.sid)
        self.assertEqual(telemetry["frames"], 9)
        self.assertEqual(telemetry["gaps"], 8)
        self.assertEqual(telemetry["p50_seconds"], 2.0)
        self.assertEqual(telemetry["p95_seconds"], 12.0)
        self.assertEqual(telemetry["max_seconds"], 12.0)
        self.assertEqual(telemetry["over_freshness_limit"], 1)
        self.assertEqual(telemetry["hidden_tab_frames"], 1)
        self.assertEqual(telemetry["histogram"]["<=1s"], 3)
        self.assertEqual(telemetry["histogram"]["<=2s"], 2)
        self.assertEqual(telemetry["histogram"]["<=5s"], 1)
        self.assertEqual(telemetry["histogram"]["<=10s"], 1)
        self.assertEqual(telemetry["histogram"]["<=30s"], 1)
        self.assertEqual(sum(telemetry["histogram"].values()), telemetry["gaps"])

    def test_export_includes_telemetry_and_journal(self) -> None:
        self.running_at(2)
        self.time.advance(3)
        self.coordinator.heartbeat(self.frame(2))
        export = self.coordinator.export_session(self.sid)
        self.assertEqual(export["publish_gaps"], self.coordinator.gap_telemetry(self.sid))
        self.assertEqual(export["status"]["publish_gaps"]["frames"], 2)
        self.assertTrue(any(entry["event"] == "start" for entry in export["journal"]))


def synthetic_catalog(config: LeagueConfig, count: int) -> tuple[LivePlayer, ...]:
    return tuple(
        LivePlayer(f"r{i:03}", f"Synthetic {i}", ("C", "G", "F"), stats={key: 0.0 for key in config.scoring} | {"PTS": 200 - i}, projected_gp=82, draft_rank=i + 1)
        for i in range(count)
    )


class ReplayWithGapsTests(unittest.TestCase):
    """A 140-pick rehearsal with heartbeat publishing and injected gaps."""

    ORDER = tuple(OUR_TEAM if i == 3 else f"Opponent {i}" for i in range(14))

    def run_replay(self, gap_seconds: float) -> tuple[Coordinator, str, dict]:
        clock = FakeTime()
        config = LeagueConfig()
        room = RoomIdentity(config.league_id, config.season_id, config.team_id)
        catalog = synthetic_catalog(config, 144)
        counters = {"resumes": 0, "gaps": 0}
        with mock.patch.object(coordinator_module, "timestamp_age", clock.age):
            coordinator = Coordinator(":memory:", config=config, strategy=recommend_first, recover=False, clock=clock.iso)
            sid = coordinator.create_session(room)["session_id"]
            available, history, roster = list(catalog), [], []

            def frame(pick, phase="draft"):
                return DraftObservation(
                    sid, room, clock.iso(), revision=0, phase=phase, current_pick=pick,
                    on_clock_team=coordinator._expected_team_for_pick(pick, self.ORDER), seconds_remaining=90, pick_order=self.ORDER,
                    history=tuple(history), roster=tuple(roster), available_players=tuple(available), known_players=catalog,
                    autopick=False, complete=True, history_complete=True, available_pool_complete=True,
                    rules={"teams": 14, "rounds": 10, "starting_slots": config.starting_slots, "bench_slots": 4, "ir_slots": 3, "scoring": config.scoring})

            coordinator.heartbeat(frame(1))
            coordinator.start(sid)
            for pick in range(1, 141):
                team = coordinator._expected_team_for_pick(pick, self.ORDER)
                ours = team == config.our_team
                for beat in range(3):  # heartbeats through the turn; one long gap in some opponent turns
                    clock.advance(2)
                    if beat == 1 and pick % 10 == 7 and not ours:
                        clock.advance(gap_seconds)
                        counters["gaps"] += 1
                        coordinator.status(sid)  # a poll during the gap notices the stale board
                    coordinator.heartbeat(frame(pick))
                if coordinator.status(sid)["state"] != "running":
                    coordinator.resume(sid)
                    counters["resumes"] += 1
                action = None
                if ours:
                    coordinator.recommend(sid)
                    action = coordinator.next_action(sid)
                    coordinator.claim_action(action.action_id)
                    command = BrowserBridge(coordinator).command_for(action.action_id)
                    chosen = next(p for p in available if p.player_id == command.player_id)
                    coordinator.ack_action(action.action_id, "submitted")
                    roster.append(chosen)
                else:
                    chosen = available[0]
                available.remove(chosen)
                history.append(PickEvent(pick, team, chosen.player_id, chosen.name))
                if action:
                    confirmation = replace(frame(pick + 1, "complete" if pick == 140 else "draft"), revision=coordinator.status(sid)["latest_revision"] + 1)
                    coordinator.ack_action(action.action_id, "confirmed", observation=confirmation)
            if coordinator.status(sid)["state"] != "completed":
                coordinator.heartbeat(frame(141, "complete"))
            return coordinator, sid, {"status": coordinator.status(sid), "history": len(history), **counters}

    def test_opponent_turn_gaps_complete_140_picks_with_no_manual_resume(self) -> None:
        for gap in (12, 30, 60):
            with self.subTest(gap=gap):
                coordinator, sid, result = self.run_replay(gap)
                try:
                    names = [entry["event"] for entry in coordinator.journal(sid, limit=5000)]
                    self.assertEqual(result["status"]["state"], "completed")
                    self.assertEqual(result["history"], 140)
                    self.assertEqual(result["resumes"], 0)
                    self.assertGreater(result["gaps"], 5)
                    self.assertEqual(names.count("stale_degraded"), result["gaps"])
                    self.assertEqual(names.count("degraded_recovered"), result["gaps"])
                    self.assertEqual(names.count("stale_pause"), 0)
                    self.assertEqual(len(result["status"]["actions"]), 10)
                    self.assertTrue(all(a["status"] == "confirmed" for a in result["status"]["actions"]))
                    self.assertGreaterEqual(coordinator.gap_telemetry(sid)["max_seconds"], gap)
                finally:
                    coordinator.close()

    def test_same_gaps_during_our_turn_pause(self) -> None:
        for gap in (12, 30, 60):
            with self.subTest(gap=gap):
                clock = FakeTime()
                config = LeagueConfig()
                room = RoomIdentity(config.league_id, config.season_id, config.team_id)
                catalog = synthetic_catalog(config, 20)
                with mock.patch.object(coordinator_module, "timestamp_age", clock.age):
                    with Coordinator(":memory:", config=config, strategy=recommend_first, recover=False, clock=clock.iso) as coordinator:
                        sid = coordinator.create_session(room)["session_id"]
                        order = tuple(config.our_team if i == 0 else f"Opponent {i}" for i in range(14))
                        obs = DraftObservation(
                            sid, room, clock.iso(), revision=1, phase="draft", current_pick=1, on_clock_team=config.our_team, seconds_remaining=90,
                            pick_order=order, available_players=catalog, known_players=catalog, autopick=False, complete=True, history_complete=True,
                            available_pool_complete=True,
                            rules={"teams": 14, "rounds": 10, "starting_slots": config.starting_slots, "bench_slots": 4, "ir_slots": 3, "scoring": config.scoring})
                        coordinator.ingest_observation(obs)
                        coordinator.start(sid)
                        clock.advance(gap)
                        self.assertEqual(coordinator.status(sid)["state"], "paused")
                        coordinator.heartbeat(replace(obs, observed_at=clock.iso()))
                        self.assertEqual(coordinator.status(sid)["state"], "paused")


if __name__ == "__main__":
    unittest.main()
