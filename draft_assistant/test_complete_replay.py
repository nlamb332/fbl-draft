"""Full ten-round coordinator rehearsals at every snake seat (no UI writes)."""
from pathlib import Path
import tempfile
import unittest

from .bridge import BrowserBridge
from .contracts import DraftObservation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity, utc_now
from .coordinator import Coordinator


class CompleteReplayTests(unittest.TestCase):
    def test_every_seat_confirms_ten_actions_in_a_140_pick_room(self):
        # Flexible synthetic players isolate orchestration from strategy quality;
        # strategy validation uses mixed eligibility and modeled opponents.
        from .valuation import recommend
        Path("runtime").mkdir(exist_ok=True)
        for seat in range(14):
            with self.subTest(seat=seat + 1), tempfile.TemporaryDirectory(dir="runtime") as directory:
                config = LeagueConfig()
                room = RoomIdentity(config.league_id, config.season_id, config.team_id)
                order = tuple(config.our_team if i == seat else f"Opponent {i}" for i in range(14))
                catalog = tuple(LivePlayer(f"p{i:03}", f"Synthetic Player {i}", ("C", "G", "F"),
                                          stats={key: 0.0 for key in config.scoring} | {"PTS": 200 - i},
                                          projected_gp=82, draft_rank=i + 1) for i in range(144))
                available = list(catalog)
                history, roster = [], []
                revision = 0
                with Coordinator(Path(directory) / "replay.sqlite3", config=config, strategy=recommend, recover=False) as coordinator:
                    sid = coordinator.create_session(room)["session_id"]

                    def frame(pick, phase="draft"):
                        nonlocal revision
                        revision += 1
                        return DraftObservation(
                            sid, room, utc_now(), revision=revision, phase=phase,
                            current_pick=pick, on_clock_team=coordinator._expected_team_for_pick(pick, order),
                            seconds_remaining=(90, 30, 3)[seat % 3], pick_order=order,
                            history=tuple(history), roster=tuple(roster), available_players=tuple(available),
                            known_players=catalog, autopick=False, complete=True, history_complete=True,
                            available_pool_complete=True,
                            rules={"teams": 14, "rounds": 10, "starting_slots": config.starting_slots,
                                   "bench_slots": 4, "ir_slots": 3, "scoring": config.scoring})

                    coordinator.ingest_observation(frame(1))
                    coordinator.start(sid)
                    for pick in range(1, 141):
                        if pick > 1:
                            coordinator.ingest_observation(frame(pick))
                        team = coordinator._expected_team_for_pick(pick, order)
                        action = None
                        if team == config.our_team:
                            coordinator.recommend(sid)
                            action = coordinator.next_action(sid)
                            self.assertIsNotNone(action)
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
                            coordinator.ack_action(action.action_id, "confirmed", observation=frame(pick + 1, "complete" if pick == 140 else "draft"))
                    if coordinator.status(sid)["state"] != "completed":
                        coordinator.ingest_observation(frame(141, "complete"))
                    status = coordinator.status(sid)
                    self.assertEqual(status["state"], "completed")
                    self.assertEqual(len(history), 140)
                    self.assertEqual(len({p.player_id for p in history}), 140)
                    self.assertEqual(len(status["actions"]), 10)
                    self.assertTrue(all(action["status"] == "confirmed" for action in status["actions"]))
                    self.assertEqual(status["unresolved_actions"], [])
