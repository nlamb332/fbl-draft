import json
from types import SimpleNamespace
import unittest

from .campaign_espn import attach_ranks
from .campaign_engine import clear_policy_cache, rank_policy
from .test_campaign_engine import _players


class EspnOnlyTests(unittest.TestCase):
    def test_rank_ignores_custom_production_availability_and_market(self):
        players = _players()
        capture = {"catalog": [{"name": p.name, "player_id": str(i), "draft_rank": 100-i} for i, p in enumerate(players)]}
        joined, audit = attach_ranks(players, capture)
        ranked = rank_policy(joined, (), (), 1, "espn_rank")
        self.assertEqual(ranked[0]["player_id"], players[-1].player_id)
        altered = tuple(SimpleNamespace(**{**p.__dict__, "stats": {"PTS": 99999 if i == 0 else 0}, "projected_availability": 0 if i == 9 else 1,
                                          "market_rank": 9999-i, "adp": i+1}) for i, p in enumerate(joined))
        self.assertEqual(ranked, rank_policy(altered, (), (), 1, "espn_rank"))
        self.assertEqual(len(audit), len(players))
        # The attached metadata must survive the complete draft trace writer.
        serialized = json.loads(json.dumps({"roster": joined}, default=vars, allow_nan=False))
        self.assertEqual(serialized["roster"][0]["espn_draft_rank"], joined[0].espn_draft_rank)

    def test_requires_complete_explicit_rank_coverage(self):
        players = _players()
        for rank in (None, 0, float("nan"), True):
            with self.assertRaises(ValueError):
                attach_ranks(players[:1], {"catalog": [{"name": players[0].name, "player_id": "1", "draft_rank": rank}]})
        with self.assertRaises(ValueError):
            attach_ranks(players, {"catalog": []})

    def test_rank_changes_invalidate_cache_and_ties_use_id(self):
        clear_policy_cache()
        players = _players()[:2]
        capture = {"catalog": [{"name": p.name, "player_id": p.player_id, "draft_rank": 1} for p in players]}
        joined, _ = attach_ranks(players, capture)
        self.assertEqual([r["player_id"] for r in rank_policy(joined, (), (), 1, "espn_rank")], ["C1", "C2"])
        joined[0].espn_draft_rank = 3
        self.assertEqual(rank_policy(joined, (), (), 1, "espn_rank")[0]["player_id"], "C2")
        self.assertEqual(rank_policy(joined, (joined[1],), (), 1, "espn_rank")[0]["player_id"], "C1")


if __name__ == "__main__":
    unittest.main()
