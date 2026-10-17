import unittest

from .contracts import DraftObservation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity, utc_now
from .lookahead import apply_lookahead
from .replay import replay
from .valuation import custom_fantasy_points, rank_players, recommend, strategy_fingerprint, value_player


def player(player_id: str, *, pts: float = 10, gp: float | None = 82, availability=None, positions=("G",), rank=None, stats=None):
    return LivePlayer(
        player_id=player_id,
        name=player_id,
        eligibility=tuple(positions),
        stats=stats or {"PTS": pts, "REB": 1, "AST": 1, "FGM": 1, "FGA": 2, "FTM": 1, "FTA": 1, "3PM": 1, "STL": 0, "BLK": 0, "TO": 0, "PF": 0, "TF": 0, "EJ": 0, "DD": 0, "TD": 0, "QD": 0},
        projected_gp=gp,
        availability=availability,
        draft_rank=rank,
        source="test",
    )


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.config = LeagueConfig()
        self.room = RoomIdentity("1", "2027", "15", True)
        self.available = (
            player("high", pts=20, gp=82, positions=("G",), rank=1),
            player("available", pts=19, gp=None, availability=0.8, positions=("F",), rank=2),
            player("gp-wins", pts=18, gp=70, availability=0.1, positions=("C",), rank=3),
            player("dup", pts=17, gp=82, positions=("G", "F"), rank=4),
        )

    def observation(self, **changes):
        data = dict(
            session_id="s",
            room=self.room,
            observed_at=utc_now(),
            revision=4,
            current_pick=1,
            on_clock_team="15",
            available_players=self.available,
            available_pool_complete=False,
        )
        data.update(changes)
        return DraftObservation(**data)

    def test_custom_points_and_gp_precedence(self):
        fp, warnings, components = custom_fantasy_points(self.available[0], self.config)
        self.assertAlmostEqual(fp, 20 + 2 - 2 + 1 - 1 + 1 + 2 + 2)
        self.assertIn("PTS", components)
        scored = value_player(self.available[2], self.config)
        self.assertAlmostEqual(scored.gp_factor, 70 / 82)
        self.assertEqual(scored.warnings, ())

    def test_missing_stats_are_visible(self):
        missing = LivePlayer("x", "X", ("G",), {"PTS": 5}, None, None, None, None, source="test")
        fp, warnings, _ = custom_fantasy_points(missing, self.config)
        self.assertEqual(fp, 5)
        self.assertTrue(any("missing projected stat" in warning for warning in warnings))
        self.assertTrue(any("projected GP" in warning for warning in value_player(missing, self.config).warnings))

    def test_rank_filters_history_and_is_deterministic(self):
        obs = self.observation(history=(PickEvent(1, "other", "dup", "dup"),))
        first = rank_players(obs, self.config)
        second = rank_players(obs, self.config)
        self.assertEqual(first, second)
        self.assertNotIn("dup", {item.player_id for item in first})
        self.assertEqual(first[0].player_id, "high")

    def test_stale_promotion_cannot_change_order(self):
        obs = self.observation()
        stale = {"status": "valid", "session_id": "s", "revision": 3, "current_pick": 1}
        direct = recommend(obs, self.config)
        promoted = recommend(obs, self.config, lookahead_enabled=True, promotion_path=stale)
        self.assertEqual(promoted.lookahead_status, "advisory")
        self.assertEqual(promoted.candidates, direct.candidates)

    def test_valid_promotion_and_timeout_are_bounded(self):
        obs = self.observation()
        artifact = {
            "status": "valid", "session_id": "s", "revision": 4, "current_pick": 1,
            "algorithm_version": "draft-strategy-v1",
            "config_fingerprint": strategy_fingerprint(self.config),
            "validation": {
                "independent_seed_clusters": 100, "teams": 14, "opponents": 13,
                "picks_per_draft": 140, "seats_per_seed": 14,
                "foundation_vs_lookahead": True,
                "paired_bootstrap_95_lower": 1.0, "worst_seat_loss_fraction": -0.01,
            },
        }
        result = apply_lookahead(obs, self.config, budget_seconds=0.001, promote=False)
        self.assertIn(result.status, {"advisory", "fallback"})
        promoted = recommend(obs, self.config, lookahead_enabled=True, promotion_path=artifact)
        # The tiny incomplete pool cannot support a complete next-turn
        # horizon, so the gate remains advisory even with a valid artifact.
        self.assertEqual(promoted.lookahead_status, "advisory")

    def test_replay_keeps_malformed_fault_and_processes_valid_frame(self):
        obs = self.observation()
        result = replay([obs.to_dict(), {"invalid": True}], self.config)
        self.assertEqual(result.processed, 1)
        self.assertEqual(len(result.faults), 1)

if __name__ == "__main__":
    unittest.main()
