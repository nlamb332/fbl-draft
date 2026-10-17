"""Exercise JavaScript evidence validation with real Python operator responses."""

import json
import os
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mock_draft.fixture import SCORING_WEIGHTS
from .contracts import CandidateScore, DraftObservation, DraftRecommendation, LivePlayer, PickEvent, RoomIdentity
from .coordinator import ActionConflict, Coordinator, IdentityError
from .server import DraftRequestHandler


@unittest.skipUnless(shutil.which("node"), "Node is required for Python/browser interoperability")
class RuntimeInteropTests(unittest.TestCase):
    def test_real_operator_responses_support_checkpoint_and_terminal_evidence(self):
        def now():
            return datetime.now(timezone.utc).isoformat()

        own = "Example Team"
        order = (own,) + tuple(f"Team {i}" for i in range(1, 14))
        room = RoomIdentity("1000000000", "2027", "15")
        players = tuple(LivePlayer(f"p{i}", f"Player {i}", ("C", "G", "F"), stats={"PTS": 20}) for i in range(1, 141))
        history, roster = [], []

        def team(pick):
            round_index, offset = divmod(pick - 1, 14)
            return order[offset if round_index % 2 == 0 else 13 - offset]

        def strategy(obs, config, **kwargs):
            player = obs.available_players[0]
            return DraftRecommendation(obs.session_id, obs.revision, now(), (CandidateScore(player.player_id, player.name, 1.0),))

        with Coordinator(":memory:", recover=False, strategy=strategy) as coordinator:
            sid = coordinator.connect(room.safe_url())["session_id"]
            handler = object.__new__(DraftRequestHandler)
            handler.server = SimpleNamespace(coordinator=coordinator)

            def observation(pick, complete=False):
                return DraftObservation(
                    session_id=sid, room=room, observed_at=now(), revision=len(history) + 1,
                    phase="complete" if complete else "active", current_pick=140 if complete else pick,
                    on_clock_team=None if complete else team(pick), seconds_remaining=0 if complete else 90,
                    pick_order=order, history=tuple(history), roster=tuple(roster),
                    available_players=players[len(history):], autopick=False,
                    rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"],
                           "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
                    complete=True, history_complete=True,
                )

            initial = observation(1)
            coordinator.ingest_observation(initial)
            coordinator.pause(sid)
            paused = handler._operator({"operation": "status", "session_id": sid})
            coordinator.start(sid)
            current = initial
            for pick in range(1, 141):
                player = players[pick - 1]
                command = None
                if team(pick) == own:
                    handler._operator({"operation": "publish", "observation": current.to_dict()})
                    if pick == 1:
                        changed = {**current.to_dict(), "seconds_remaining": 89}
                        with self.assertRaises(IdentityError):
                            handler._operator({"operation": "prepare", "session_id": sid, "observation": changed})
                        self.assertEqual(coordinator.status(sid)["actions"], [])
                    prepared = handler._operator({"operation": "prepare", "session_id": sid, "observation": current.to_dict()})
                    command = prepared["command"]
                    self.assertIsNotNone(command)
                    with self.assertRaises(ActionConflict):
                        coordinator.ack_action(command["action_id"], "cancelled")
                    handler._operator({"operation": "ack", "action_id": command["action_id"], "status": "submitted"})
                history.append(PickEvent(pick, team(pick), player.player_id, player.name))
                if team(pick) == own:
                    roster.append(player)
                post = observation(pick + 1, complete=pick == 140)
                if command:
                    result = handler._operator({"operation": "reconcile", "session_id": sid, "observation": post.to_dict()})
                    action = next(a for a in result["actions"] if a["action_id"] == command["action_id"])
                    self.assertEqual(action["status"], "confirmed")
                    self.assertIs(action["claimed"], True)
                    self.assertIs(action["dispatched"], True)
                else:
                    coordinator.ingest_observation(post)
                current = post
            final = handler._operator({"operation": "status", "session_id": sid})
            self.assertEqual(final["state"], "completed")
            payload = {"paused": paused, "initial": initial.to_dict(), "final": final, "session": sid, "own": own}

        script = r"""
const fs = require('node:fs');
const assert = require('node:assert/strict');
const runtime = require('./draft_assistant/operator_runtime.js');
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
// Receipt is measured locally, as the real browser transport does.
value.paused.status_received_at = new Date().toISOString();
const checkpoint = runtime.createBatchCheckpoint(value.initial, {
  sessionId: value.session, ownTeam: value.own, coordinatorStatus: value.paused
});
assert.equal(checkpoint.own_picks_confirmed, 0);
value.final.status_received_at = new Date().toISOString();
const check = runtime.validateFinalCoordinatorEvidence(value.final, value.final.observation,
  value.final.actions, {sessionId: value.session, ownTeam: value.own});
assert.equal(value.final.actions.length, 10);
assert.equal(check.ok, true, JSON.stringify(check.reasons));
"""
        result = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps(payload), text=True,
                                capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_virtualized_feed_replay_confirms_ten_guarded_picks_without_uncertain(self):
        """Issue #42: JS runtime + post-click adapter against the real coordinator.

        The simulated room virtualizes the Players Feed to 31 rows, hydrates Pick
        History late on every visit and has a bot pick the instant ours lands.
        """
        own = "Example Team"
        teams = [f"Team {i}" for i in range(1, 15)]
        teams[6] = own
        room = RoomIdentity("1000000000", "2027", "15")

        def strategy(obs, config, **kwargs):
            player = obs.available_players[0]
            return DraftRecommendation(obs.session_id, obs.revision, datetime.now(timezone.utc).isoformat(), (CandidateScore(player.player_id, player.name, 1.0),))

        with Coordinator(":memory:", recover=False, strategy=strategy) as coordinator:
            sid = coordinator.connect(room.safe_url())["session_id"]
            handler = object.__new__(DraftRequestHandler)
            handler.server = SimpleNamespace(coordinator=coordinator)
            config = {
                "session_id": sid, "own": own, "teams": teams, "picks": 10,
                "room": {"league_id": room.league_id, "season_id": room.season_id, "team_id": room.team_id, "practice": False},
                "rules": {"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"],
                          "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
            }
            driver = Path(__file__).resolve().parent / "fixtures" / "postclick_replay_driver.js"
            process = subprocess.Popen(
                [shutil.which("node"), str(driver)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=Path(__file__).resolve().parents[1], env={**os.environ, "REPLAY_CONFIG": json.dumps(config)},
            )
            done = None
            try:
                for line in process.stdout:
                    message = json.loads(line)
                    if "request" in message:
                        try:
                            response = handler._operator(message["request"])
                        except Exception as exc:  # surfaced exactly as the HTTP layer does
                            response = DraftRequestHandler._operator_error(message["request"], exc)
                        process.stdin.write(json.dumps({"response": response}) + "\n")
                        process.stdin.flush()
                    elif "done" in message:
                        done = message["done"]
                        break
                    else:
                        self.fail(message.get("failed", line))
            finally:
                process.stdin.close()
                process.stdout.close()
                stderr = process.stderr.read()
                process.stderr.close()
                process.wait(timeout=15)
            self.assertIsNotNone(done, stderr)
            self.assertEqual(done["outcomes"], ["confirmed"] * 10)
            self.assertNotIn("uncertain", done["outcomes"])
            self.assertEqual(done["clicks"], 10, "exactly one click per guarded pick")
            self.assertEqual(done["bodyEvaluates"], 0, "no fast frame was read for confirmation")
            self.assertGreater(done["historyReads"], 20, "Pick History was polled for every pick")
            actions = coordinator.status(sid)["actions"]
            self.assertEqual(len(actions), 10)
            self.assertTrue(all(action["status"] == "confirmed" for action in actions))
