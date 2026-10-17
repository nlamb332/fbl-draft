"""Focused control-plane checks; browser/network integration stays outside this unit suite."""

from __future__ import annotations

import os
import shutil
import json
import subprocess
import sys
from dataclasses import replace
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path

from mock_draft.fixture import SCORING_WEIGHTS

from .bridge import BrowserBridge
from .contracts import CandidateScore, DraftObservation, DraftRecommendation, LivePlayer, PickEvent, RoomIdentity
from .coordinator import ActionConflict, Coordinator, FreshnessError, IdentityError, ReadinessError
from .server import create_server


def now(delta: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()


class ControlTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        self.tmp = Path("runtime") / f"test-control-{os.getpid()}-{id(self)}.sqlite3"
        self.db = str(self.tmp)
        self.instances = []
        self.player = LivePlayer("p1", "Player One", ("G",), stats={"PTS": 20})
        self.room = RoomIdentity("1000000000", "2027", "15")

    def tearDown(self) -> None:
        for instance in self.instances:
            instance.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                (Path(f"{self.tmp}{suffix}")).unlink()
            except FileNotFoundError:
                pass

    def observation(self, *, session_id: str, revision: int = 1, observed_at: str | None = None, pick: int = 1, team: str = "Example Team", available: bool = True, history: tuple[PickEvent, ...] = ()) -> DraftObservation:
        players = (self.player,) if available else ()
        return DraftObservation(
            session_id=session_id,
            room=self.room,
            observed_at=observed_at or now(),
            revision=revision,
            phase="active",
            current_pick=pick,
            on_clock_team=team,
            seconds_remaining=30,
            pick_order=("Example Team",) + tuple(f"Team {i}" for i in range(1, 14)),
            history=history,
            roster=players if history else (),
            available_players=players,
            autopick=False,
            rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
            complete=True,
            history_complete=True,
            available_pool_complete=False,
        )

    def ready_running(self, strategy=None) -> tuple[Coordinator, str]:
        coordinator = Coordinator(self.db, strategy=strategy)
        self.instances.append(coordinator)
        connected = coordinator.connect(self.room.safe_url())
        sid = connected["session_id"]
        coordinator.ingest_observation(self.observation(session_id=sid))
        self.assertTrue(coordinator.readiness(sid)["ready"])
        coordinator.start(sid)
        return coordinator, sid

    def test_clock_expiry_accounts_for_observation_age(self) -> None:
        coordinator, sid = self.ready_running()
        expired = replace(self.observation(session_id=sid, revision=2, observed_at=now(-5)), seconds_remaining=1)
        coordinator.ingest_observation(expired)
        with self.assertRaises(FreshnessError):
            coordinator.next_action(sid)

    def test_stale_and_future_data_never_drives_actions(self) -> None:
        coordinator = Coordinator(self.db)
        self.instances.append(coordinator)
        sid = coordinator.connect(self.room.safe_url())["session_id"]
        with self.assertRaises(FreshnessError):
            coordinator.ingest_observation(self.observation(session_id=sid, observed_at=now(11)))
        coordinator.ingest_observation(self.observation(session_id=sid, observed_at=now(-11)))
        self.assertEqual(coordinator.status(sid)["state"], "observing")

    def test_identity_and_double_claim(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        with self.assertRaises(ActionConflict):
            coordinator.claim_action(action.action_id)
        coordinator.pause(sid)
        with self.assertRaises(ActionConflict):
            coordinator.final_guard(action.action_id)

    def test_marginal_lineup_fallback_is_journaled(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        from . import marginal_lineup
        from .contracts import LeagueConfig
        from .marginal_lineup import MarginalParams, marginal_fingerprint
        from .valuation import recommend

        config = LeagueConfig()
        payload = {"policy": "marginal-lineup-v1", "status": "advisory", "algorithm_version": "marginal-lineup-v1",
                   "config_fingerprint": marginal_fingerprint(config), "params": MarginalParams().as_dict()}
        ticks = iter(range(0, 1_000_000, 10))

        def strategy(obs, cfg, **kwargs):
            return recommend(obs, cfg, promotion_path=payload)

        coordinator, sid = self.ready_running(strategy)
        with mock.patch.object(marginal_lineup, "time", SimpleNamespace(perf_counter=lambda: float(next(ticks)))):
            rec = coordinator.recommend(sid)
        self.assertEqual(rec.lookahead_status, "fallback")
        self.assertEqual([c.player_id for c in rec.candidates], ["p1"])
        entry = next(item for item in coordinator.journal(sid) if item["event"] == "recommendation")
        detail = entry["payload"] if isinstance(entry["payload"], dict) else json.loads(entry["payload"])
        self.assertEqual(detail["status"], "fallback")
        self.assertIn("fallback to foundation", detail["explanation"])

    def test_unknown_outcome_and_authoritative_confirmation(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        result = BrowserBridge(coordinator).execute(action.action_id, lambda command: {"status": "uncertain"})
        self.assertEqual(result.status, "uncertain")
        with self.assertRaises(ActionConflict):
            coordinator.next_action(sid)
        confirmed_obs = self.observation(session_id=sid, revision=2, pick=2, history=(PickEvent(1, "Example Team", "p1", "Player One"),))
        confirmed = coordinator.ack_action(action.action_id, "confirmed", observation=confirmed_obs)
        self.assertEqual(confirmed.status, "confirmed")

    def test_submitted_ack_observation_refreshes_after_status_poll_race_without_confirming(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        # The visible command has passed its one-time final guard. A stale
        # status poll can pause the session, but must preserve that in-flight
        # action because it was already dispatched.
        coordinator.final_guard(action.action_id)
        stale = self.observation(session_id=sid, revision=2, observed_at=now(-11))
        coordinator.ingest_observation(stale)
        self.assertEqual(coordinator.status(sid)["state"], "paused")

        fresh = self.observation(session_id=sid, revision=3)
        submitted = coordinator.ack_action(action.action_id, "submitted", observation=fresh)
        self.assertEqual(submitted.status, "submitted")
        status = coordinator.status(sid)
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["latest_revision"], 3)
        self.assertEqual(status["actions"][0]["status"], "submitted")
        self.assertNotEqual(status["actions"][0]["status"], "confirmed")

    def test_same_revision_exact_retry_is_idempotent_but_changed_frame_is_rejected(self) -> None:
        coordinator, sid = self.ready_running()
        baseline = DraftObservation.from_dict(coordinator.status(sid)["observation"])
        retry = coordinator.ingest_or_match_observation(baseline, session_id=sid)
        self.assertEqual(retry["latest_revision"], 1)
        changed = replace(baseline, seconds_remaining=29)
        with self.assertRaises(IdentityError):
            coordinator.ingest_or_match_observation(changed, session_id=sid)

    def test_pause_after_final_guard_preserves_inflight_dispatch(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        # command_for is the bridge's final guard and records dispatch before
        # a potentially slow visible browser callback starts.
        BrowserBridge(coordinator).command_for(action.action_id)
        coordinator.pause(sid)
        current = coordinator.status(sid)["actions"][0]
        self.assertEqual(current["status"], "pending")
        coordinator.resume(sid)
        self.assertEqual(coordinator.next_action(sid).action_id, action.action_id)
        with self.assertRaises(ActionConflict):
            BrowserBridge(coordinator).command_for(action.action_id)

    def test_cli_status_and_observation_share_running_service_state(self) -> None:
        coordinator, sid = self.ready_running()
        executable = sys.executable
        status = subprocess.run([executable, "-m", "draft_assistant", "--db", self.db, "status", sid], cwd=Path.cwd(), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(status.stdout)["state"], "running")
        fresh = self.observation(session_id=sid, revision=2)
        published = subprocess.run([executable, "-m", "draft_assistant", "--db", self.db, "publish-observation"], cwd=Path.cwd(), input=json.dumps(fresh.to_dict()), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(published.stdout)["latest_revision"], 2)

    def test_cli_dispatch_command_consumes_guard_once(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        result = subprocess.run([sys.executable, "-m", "draft_assistant", "--db", self.db, "dispatch-command", action.action_id], cwd=Path.cwd(), capture_output=True, text=True, check=True)
        command = json.loads(result.stdout)
        self.assertEqual(command["action_id"], action.action_id)
        with self.assertRaises(ActionConflict):
            coordinator.final_guard(action.action_id)

    def test_server_uses_requested_database_and_local_bind(self) -> None:
        coordinator = Coordinator(self.db)
        self.instances.append(coordinator)
        server = create_server(coordinator, port=0)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertEqual(server.coordinator.db_path, self.db)
        server.server_close()

    def test_dispatched_but_unclicked_action_is_released_as_failed(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        coordinator.claim_action(action.action_id)
        coordinator.final_guard(action.action_id)
        with self.assertRaises(ActionConflict):
            coordinator.ack_action(action.action_id, "cancelled")
        released = coordinator.ack_action(action.action_id, "failed", detail="not clicked: player row disappeared")
        self.assertEqual(released.status, "failed")
        self.assertEqual(coordinator.status(sid)["unresolved_actions"], [])
        coordinator.ingest_observation(self.observation(session_id=sid, revision=2))
        coordinator.recommend(sid)
        retry = coordinator.next_action(sid)
        self.assertIsNotNone(retry)
        self.assertNotEqual(retry.action_id, action.action_id)

    def test_configured_promotion_artifact_is_the_server_side_default(self) -> None:
        seen = []

        def strategy(obs, config, **kwargs):
            seen.append(kwargs.get("promotion_path"))
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.promotion_path = "configured-artifact.json"
        coordinator.recommend(sid)
        coordinator.recommend(sid, promotion_path="cli-override.json")
        self.assertEqual(seen, ["configured-artifact.json", "cli-override.json"])

    def test_http_callers_cannot_supply_a_promotion_artifact(self) -> None:
        import http.client
        import threading

        seen = []

        def strategy(obs, config, **kwargs):
            seen.append(kwargs.get("promotion_path"))
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.promotion_path = "configured-artifact.json"
        server = create_server(coordinator, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            forged = {"policy": "marginal-lineup-v1", "status": "promotable"}
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("POST", "/api/recommend", json.dumps({"session_id": sid, "promotion_path": forged}),
                               {"Content-Type": "application/json", "X-CSRF-Token": coordinator.csrf_token})
            self.assertEqual(connection.getresponse().status, 200)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(seen, ["configured-artifact.json"])

    def test_cli_defaults_to_the_accepted_marginal_promotion(self) -> None:
        from .__main__ import _promotion, build_parser
        from .contracts import LeagueConfig
        from .marginal_lineup import DEFAULT_PROMOTION_PATH, load_promotion_payload, promotion_valid

        args = build_parser().parse_args(["serve"])
        self.assertEqual(_promotion(args), str(DEFAULT_PROMOTION_PATH))
        self.assertTrue(promotion_valid(load_promotion_payload(DEFAULT_PROMOTION_PATH), LeagueConfig()))
        self.assertIsNone(_promotion(build_parser().parse_args(["--foundation-only", "serve"])))

    def test_newer_incomplete_frame_cannot_claim(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        malformed = self.observation(session_id=sid, revision=2)
        malformed = replace(malformed, complete=False)
        coordinator.ingest_observation(malformed)
        with self.assertRaises(ActionConflict):
            coordinator.next_action(sid)

    def test_roster_must_match_our_history_before_action(self) -> None:
        coordinator, sid = self.ready_running()
        obs = self.observation(session_id=sid, revision=2, pick=2, team="Team 1", history=(PickEvent(1, "Example Team", "p1", "Player One"),))
        coordinator.ingest_observation(replace(obs, roster=()))
        readiness = coordinator.readiness(sid)
        self.assertFalse(readiness["ready"])
        self.assertIn("roster", readiness["checks"])
        self.assertIn("roster", " ".join(readiness["reasons"]))

    def test_snake_history_and_already_picked_player_are_rejected(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        bad_history = self.observation(session_id=sid, revision=2, pick=2, history=(PickEvent(1, "Team 1", "p2", "Other"),))
        coordinator.ingest_observation(bad_history)
        coordinator.recommend(sid)
        with self.assertRaises(ActionConflict):
            coordinator.next_action(sid)

        order = ("Example Team",) + tuple(f"Team {i}" for i in range(1, 14))
        valid_history = self.observation(session_id=sid, revision=3, pick=28, team="Example Team", history=tuple(PickEvent(pick, order[((pick - 1) % 14) if ((pick - 1) // 14) % 2 == 0 else 13 - ((pick - 1) % 14)], f"p{pick}", f"Player {pick}") for pick in range(1, 28)))
        coordinator.ingest_observation(valid_history)
        coordinator.resume(sid)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        with self.assertRaises(IdentityError):
            coordinator.claim_action(action.action_id)

    def test_authoritative_complete_closes_session_and_reconciles(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        BrowserBridge(coordinator).execute(action.action_id, lambda command: {"status": "submitted"})
        order = ("Example Team",) + tuple(f"Team {i}" for i in range(1, 14))
        history = tuple(PickEvent(pick, order[((pick - 1) % 14) if ((pick - 1) // 14) % 2 == 0 else 13 - ((pick - 1) % 14)], f"p{pick}", f"Player {pick}") for pick in range(1, 141))
        own_history_ids = [event.player_id for event in history if event.team == "Example Team"]
        legal_positions = (("C",), ("G",), ("G",), ("F",), ("F",), ("C",), ("G",), ("F",), ("C",), ("G",))
        roster = tuple(LivePlayer(player_id, f"Player {player_id[1:]}", eligibility) for player_id, eligibility in zip(own_history_ids, legal_positions))
        final = DraftObservation(session_id=sid, room=self.room, observed_at=now(), revision=2, phase="complete", current_pick=140, on_clock_team=None, seconds_remaining=0, pick_order=("Example Team",) + tuple(f"Team {i}" for i in range(1, 14)), history=history, roster=roster, available_players=(self.player,), autopick=False, rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)}, complete=True, history_complete=True, available_pool_complete=True)
        self.assertEqual(coordinator.ingest_observation(final)["state"], "completed")
        self.assertEqual(coordinator.reconcile(sid)[0].status, "confirmed")

    def test_final_guard_rejects_newer_revision(self) -> None:
        def strategy(obs, config, **kwargs):
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore("p1", "Player One", 1.0),))

        coordinator, sid = self.ready_running(strategy)
        coordinator.recommend(sid)
        action = coordinator.next_action(sid)
        assert action
        coordinator.claim_action(action.action_id)
        coordinator.ingest_observation(self.observation(session_id=sid, revision=2))
        with self.assertRaises(IdentityError):
            coordinator.final_guard(action.action_id)

    def test_restart_pauses_and_preserves_unresolved_action(self) -> None:
        coordinator, sid = self.ready_running()
        # Insert an action through the normal recommendation path with no
        # strategy provider by manually staging a manual override candidate.
        coordinator.pause(sid, manual_override=True)
        staged = coordinator.manual_pick(sid, "p1")
        coordinator.close()
        restarted = Coordinator(self.db)
        self.instances.append(restarted)
        status = restarted.status(sid)
        self.assertEqual(status["state"], "paused")
        self.assertTrue(any(item["action_id"] == staged.action_id for item in status["unresolved_actions"]))

    def test_wrong_turn_and_manual_takeover(self) -> None:
        coordinator, sid = self.ready_running()
        coordinator.pause(sid, manual_override=True)
        staged = coordinator.manual_pick(sid, "p1")
        self.assertEqual(coordinator.status(sid)["state"], "paused")
        with self.assertRaises(ActionConflict):
            BrowserBridge(coordinator).command_for(staged.action_id)
        coordinator.resume(sid)  # explicit resume releases the unsubmitted manual stage
        self.assertEqual(coordinator.status(sid)["state"], "running")


if __name__ == "__main__":
    unittest.main()

