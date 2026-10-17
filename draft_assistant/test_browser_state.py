import unittest
from pathlib import Path

from .browser_operator import (
    ActionBlocked,
    BrowserOperator,
    SessionBlocked,
    StaleExpectedTurn,
    UnknownActionOutcome,
)
from .browser_state import ObservationConflict, parse_visible_snapshot, reconcile_observations
from .contracts import LeagueConfig, RoomIdentity
from .lifecycle import DraftSession


FIXTURES = Path(__file__).with_name("fixtures")
ROOM = RoomIdentity("1000000011", "2027", "13", True)
CONFIG = LeagueConfig(league_id="1000000011", team_id="13")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class BrowserStateTests(unittest.TestCase):
    def test_parser_scopes_tables_and_keeps_unmodeled_fields_unknown(self):
        observation = parse_visible_snapshot(
            fixture("practice_round1_visible.txt"),
            session_id="practice-session",
            room=ROOM,
            config=LeagueConfig(league_id="1000000011", team_id="13"),
            revision=1,
        )
        self.assertEqual(observation.current_pick, 13)
        self.assertEqual(observation.on_clock_team, "Example Team")
        self.assertEqual(observation.seconds_remaining, 30.0)
        self.assertEqual([player.draft_rank for player in observation.available_players], [1.0, 2.0, 3.0])
        self.assertEqual(observation.available_players[0].adp, None)
        self.assertEqual(observation.available_players[0].stats["FPTS"], 67.3)
        self.assertEqual(observation.available_players[0].projected_gp, 80.0)
        self.assertTrue(set(("PF", "TF", "EJ", "DD", "TD", "QD")) <= set(observation.available_players[0].missing_stats))
        self.assertEqual(observation.rules["stat_aliases"], {"TPM": "3PM", "E": "EJ"})
        self.assertFalse(observation.available_pool_complete)
        self.assertEqual(len(observation.history), 2)
        self.assertEqual(len(observation.known_players), len(observation.available_players) + len(observation.roster))

    def test_empty_players_is_transient_and_does_not_claim_pool_complete(self):
        observation = parse_visible_snapshot(fixture("practice_round1_empty_players.txt"), session_id="s", room=ROOM, config=CONFIG)
        self.assertEqual(observation.available_players, ())
        self.assertFalse(observation.available_pool_complete)
        self.assertNotIn("scoring", observation.rules)
        self.assertIn("players_table_temporarily_empty", observation.warnings)

    def test_generic_autopick_tab_label_does_not_change_state(self):
        snapshot = fixture("practice_round1_visible.txt").replace("Autopick: Off", "Controls: Players / Autopick / Rules")
        observation = parse_visible_snapshot(snapshot, session_id="s", room=ROOM, config=CONFIG)
        self.assertIsNone(observation.autopick)

    def test_newline_per_cell_accessibility_rows_are_grouped(self):
        snapshot = """ESPN Fantasy Basketball Draft - Practice Draft for Example League
RND1of10
Clock 00:30
On the Clock: Pick13 ExampleTeam
Players
Draft
RK Player FPTS GP MIN FGM FGA FTM FTA 3PM REB AST STL BLK TO EJ PF
1
Ada Lovelace
G
67.3
80
34.1
8.2
16.0
4.0
4.8
2.1
5.8
6.4
1.4
0.6
2.2
0
0
Pick History
Pick Player Team 2026PTS PROJPTS RK
13
Ada Lovelace
Example Team
5200
67.3
1
"""
        observation = parse_visible_snapshot(snapshot, session_id="s", room=ROOM, config=CONFIG, revision=1)
        self.assertEqual([player.name for player in observation.available_players], ["Ada Lovelace"])
        self.assertEqual([event.pick_number for event in observation.history], [13])

    def test_partial_frame_does_not_drop_catalog_or_infer_drafted_from_absence(self):
        previous = parse_visible_snapshot(fixture("practice_round1_visible.txt"), session_id="s", room=ROOM, config=CONFIG, revision=1)
        current = parse_visible_snapshot(fixture("practice_round1_after_pick.txt"), session_id="s", room=ROOM, config=CONFIG, revision=2)
        result = reconcile_observations(previous, current)
        self.assertEqual(result.status, "advanced")
        self.assertEqual([player.name for player in result.observation.available_players], ["Grace Hopper", "Katherine Johnson"])
        self.assertIn("Ada Lovelace", {player.name for player in result.observation.known_players})
        self.assertEqual([event.pick_number for event in result.new_picks], [13])

    def test_reconciliation_rejects_identity_and_conflicting_history(self):
        first = parse_visible_snapshot(fixture("practice_round1_visible.txt"), session_id="s", room=ROOM, config=CONFIG, revision=1)
        other = parse_visible_snapshot(fixture("practice_round1_visible.txt"), session_id="other", room=ROOM, config=CONFIG, revision=2)
        with self.assertRaises(ObservationConflict):
            reconcile_observations(first, other)


class BrowserOperatorTests(unittest.TestCase):
    def running_operator(self, *, explicit_ids: bool = False):
        observation = parse_visible_snapshot(fixture("practice_round1_visible.txt"), session_id="s", room=ROOM, config=CONFIG, revision=1)
        session = DraftSession("s")
        operator = BrowserOperator(session=session, room=ROOM, config=LeagueConfig(league_id="1000000011", team_id="13"), observation=observation, require_explicit_player_id=explicit_ids, coordinator_guard=lambda action, observed: True)
        operator.start()
        return operator, observation

    def test_direct_submit_requires_confirmation_and_post_pick_readback(self):
        operator, observation = self.running_operator()
        action = operator.plan_pick("visible:ada-lovelace")
        with self.assertRaises(ActionBlocked):
            operator.submit(action, submitter=lambda _: None)
        calls = []
        operator.submit(action, confirm=True, submitter=lambda submitted: calls.append(submitted.action_id))
        self.assertEqual(calls, [action.action_id])
        after = parse_visible_snapshot(fixture("practice_round1_after_pick.txt"), session_id="s", room=ROOM, config=CONFIG, revision=2)
        confirmation = operator.confirm_action(action, after)
        self.assertEqual(confirmation.status, "confirmed")

    def test_pause_and_stale_revision_block_mutation(self):
        operator, observation = self.running_operator()
        action = operator.plan_pick("visible:ada-lovelace")
        operator.pause("manual takeover")
        with self.assertRaises(SessionBlocked):
            operator.submit(action, observation=observation, confirm=True, submitter=lambda _: None)
        operator, observation = self.running_operator()
        action = operator.plan_pick("visible:ada-lovelace")
        newer = parse_visible_snapshot(fixture("practice_round1_visible.txt"), session_id="s", room=ROOM, config=CONFIG, revision=2)
        with self.assertRaises(StaleExpectedTurn):
            operator.submit(action, observation=newer, confirm=True, submitter=lambda _: None)

    def test_unknown_submit_result_requires_inspection_before_retry(self):
        operator, observation = self.running_operator()
        action = operator.plan_pick("visible:ada-lovelace")
        with self.assertRaises(UnknownActionOutcome):
            operator.submit(action, confirm=True, submitter=lambda _: (_ for _ in ()).throw(RuntimeError("lost UI acknowledgement")))
        with self.assertRaises(UnknownActionOutcome):
            operator.retry(action, observation=observation, confirm=True, submitter=lambda _: None)

    def test_autopick_and_missing_clock_are_blocked(self):
        operator, observation = self.running_operator()
        autopick = parse_visible_snapshot(fixture("practice_round1_visible.txt").replace("Autopick: Off", "Autopick enabled"), session_id="s", room=ROOM, config=CONFIG, revision=1)
        with self.assertRaises(SessionBlocked):
            operator.plan_queue(["visible:ada-lovelace"], observation=autopick)


if __name__ == "__main__":
    unittest.main()
