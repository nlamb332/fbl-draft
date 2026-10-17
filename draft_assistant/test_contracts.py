import unittest
from .contracts import DraftObservation, LeagueConfig, LivePlayer, RoomIdentity, snake_pick_numbers, utc_now
from .lifecycle import DraftSession


class ContractTests(unittest.TestCase):
    def test_projection_contract_rejects_nonfinite_and_invalid_values(self):
        for fields in ({"stats": {"PTS": float("nan")}}, {"projected_gp": 100}, {"availability": -1}, {"draft_rank": 0}):
            with self.assertRaises(ValueError):
                LivePlayer("id", "Player", ("G",), **fields)
        with self.assertRaises(ValueError):
            RoomIdentity("1", "2027", "15", "false")

    def test_room_url_rejects_other_hosts_and_duplicate_identity(self):
        for url in ("https://example.com/basketball/draft?leagueId=1&seasonId=2027&teamId=15", "https://fantasy.espn.com/basketball/draft?leagueId=1&leagueId=2&seasonId=2027&teamId=15"):
            with self.assertRaises(ValueError):
                RoomIdentity.from_url(url)

    def test_team_url_parses_setup_identity_and_normalizes_to_draft_room(self):
        room = RoomIdentity.from_url(
            "https://fantasy.espn.com/basketball/team?leagueId=1000000000&teamId=15&seasonId=2027"
        )
        self.assertEqual((room.league_id, room.team_id, room.season_id), ("1000000000", "15", "2027"))
        self.assertEqual(
            room.safe_url(),
            "https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15",
        )

    def test_observation_roundtrip_keeps_unknowns(self):
        observation = DraftObservation("session", RoomIdentity("1", "2027", "15", True), utc_now())
        self.assertEqual(observation, DraftObservation.from_dict(observation.to_dict()))
        self.assertIsNone(observation.autopick)
        self.assertFalse(observation.available_pool_complete)

    def test_snake_turns_and_complete_league(self):
        self.assertEqual(snake_pick_numbers(1)[:3], (1, 28, 29))
        self.assertEqual(snake_pick_numbers(13)[:3], (13, 16, 41))
        self.assertEqual(sorted(p for s in range(1, 15) for p in snake_pick_numbers(s)), list(range(1, 141)))
        self.assertEqual(LeagueConfig().teams, 14)

    def test_connection_never_activates_execution(self):
        session = DraftSession("session")
        session.transition("observing")
        with self.assertRaises(ValueError):
            session.transition("running", ready=True)
        session.transition("ready", ready=True)
        session.transition("running", ready=True)
        session.transition("paused", reason="manual takeover")
        with self.assertRaises(ValueError):
            session.transition("running")


if __name__ == "__main__":
    unittest.main()
