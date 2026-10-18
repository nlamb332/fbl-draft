import copy
import tempfile
import unittest
from pathlib import Path

from sleeper_advisor.api import SleeperClient
from sleeper_advisor.model import (
    DEFAULT_SCORING,
    build_players,
    build_state,
    parse_league,
    pick_slot,
    roster_pick_numbers,
    slot_pick_numbers,
)
from sleeper_advisor.tests.fakes import (
    DRAFT_ID,
    USER_ID,
    USERNAME,
    fake_fetch,
    fixture_client,
    fixture_routes,
    load_fixture,
)


class PickMathTests(unittest.TestCase):
    def test_snake(self):
        slots = [pick_slot(n, 6, "snake") for n in range(1, 19)]
        self.assertEqual(slots, [1, 2, 3, 4, 5, 6, 6, 5, 4, 3, 2, 1, 1, 2, 3, 4, 5, 6])

    def test_linear(self):
        slots = [pick_slot(n, 4, "linear") for n in range(1, 13)]
        self.assertEqual(slots, [1, 2, 3, 4] * 3)

    def test_third_round_reversal(self):
        slots = [pick_slot(n, 4, "snake", reversal_round=3) for n in range(1, 21)]
        self.assertEqual(
            slots,
            [1, 2, 3, 4, 4, 3, 2, 1, 4, 3, 2, 1, 1, 2, 3, 4, 4, 3, 2, 1],
        )

    def test_slot_pick_numbers(self):
        self.assertEqual(slot_pick_numbers(3, 6, 14, "snake")[:5], (3, 10, 15, 22, 27))
        self.assertEqual(slot_pick_numbers(1, 6, 4, "snake"), (1, 12, 13, 24))
        self.assertEqual(slot_pick_numbers(2, 4, 3, "linear"), (2, 6, 10))
        self.assertEqual(slot_pick_numbers(1, 4, 5, "snake", reversal_round=3), (1, 8, 12, 13, 20))
        every = sorted(n for s in range(1, 7) for n in slot_pick_numbers(s, 6, 14, "snake"))
        self.assertEqual(every, list(range(1, 85)))

    def test_traded_picks_reassign_ownership(self):
        s2r = {str(s): s for s in range(1, 7)}
        traded = load_fixture("traded_picks")
        mine = roster_pick_numbers(3, 6, 14, "snake", None, s2r, traded)
        self.assertNotIn(27, mine)  # round 5 slot 3 traded to roster 5
        self.assertIn(32, mine)  # round 6 slot 5 acquired
        self.assertEqual(len(mine), 14)
        theirs = roster_pick_numbers(5, 6, 14, "snake", None, s2r, traded)
        self.assertIn(27, theirs)
        self.assertNotIn(32, theirs)

    def test_traded_picks_use_slot_to_roster_mapping(self):
        s2r = {"1": 10, "2": 20}
        traded = [{"round": 1, "roster_id": 10, "owner_id": 20}]
        self.assertEqual(roster_pick_numbers(20, 2, 2, "linear", None, s2r, traded), (1, 2, 4))
        self.assertEqual(roster_pick_numbers(10, 2, 2, "linear", None, s2r, traded), (3,))


class LeagueTests(unittest.TestCase):
    def test_parse_pre_draft_fixture(self):
        league = parse_league(load_fixture("draft_pre"), load_fixture("league"))
        self.assertEqual(league.draft_id, "900000000000000001")
        self.assertEqual(league.league_id, "800000000000000001")  # from metadata
        self.assertEqual(league.starting_slots, ("PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL"))
        self.assertEqual((league.teams, league.rounds, league.bench_slots), (6, 14, 5))
        self.assertEqual(league.draft_type, "snake")
        self.assertEqual(league.mock_type, "league_mock")
        self.assertEqual(league.status, "pre_draft")
        self.assertIsNone(league.reversal_round)
        self.assertEqual(league.scoring["pts"], 0.5)
        self.assertEqual(league.scoring["tpm"], 0.5)
        self.assertEqual(league.season, "2026")

    def test_default_scoring_without_league(self):
        league = parse_league(load_fixture("draft_pre"), None)
        self.assertEqual(league.scoring, DEFAULT_SCORING)
        self.assertIsNot(league.scoring, DEFAULT_SCORING)

    def test_reversal_round_and_linear(self):
        draft = load_fixture("draft_pre")
        draft["settings"]["reversal_round"] = 3
        draft["type"] = "linear"
        league = parse_league(draft, None)
        self.assertEqual(league.reversal_round, 3)
        self.assertEqual(league.draft_type, "linear")
        draft["settings"]["reversal_round"] = 0
        self.assertIsNone(parse_league(draft, None).reversal_round)

    def test_missing_fields_do_not_crash(self):
        league = parse_league({}, None)
        self.assertEqual((league.teams, league.rounds, league.starting_slots), (0, 0, ()))
        league = parse_league({"draft_id": 5, "settings": None, "metadata": None}, load_fixture("league"))
        self.assertEqual(league.teams, 6)
        self.assertEqual(len(league.starting_slots), 9)
        self.assertEqual(league.bench_slots, 5)


class PlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.players = build_players(load_fixture("players"), load_fixture("projections"))

    def test_counts_and_dropped(self):
        self.assertEqual(len(self.players), 60)  # two players without eligibility dropped
        self.assertNotIn("9061", self.players)

    def test_positions_ordered_and_multi(self):
        self.assertEqual(self.players["9001"].positions, ("PF", "C"))
        self.assertEqual(self.players["9003"].positions, ("SG", "SF"))
        seen = {p for pl in self.players.values() for p in pl.positions}
        self.assertEqual(seen, {"PG", "SG", "SF", "PF", "C"})

    def test_projection_and_adp_only(self):
        top = self.players["9001"]
        self.assertTrue(top.has_projection)
        self.assertGreater(top.stats["pts"], 20)
        self.assertNotIn("adp_std", top.stats)
        self.assertIsNotNone(top.adp)
        adp_only = self.players["9040"]
        self.assertFalse(adp_only.has_projection)
        self.assertEqual(adp_only.stats, {})
        self.assertIsNotNone(adp_only.adp)
        nothing = self.players["9058"]
        self.assertFalse(nothing.has_projection)
        self.assertIsNone(nothing.adp)

    def test_missing_dd_td_stay_missing(self):
        self.assertIn("td", self.players["9001"].stats)
        self.assertNotIn("td", self.players["9030"].stats)
        self.assertNotIn("dd", self.players["9030"].stats)

    def test_injury_merge(self):
        self.assertEqual(self.players["9005"].injury_status, "Out")
        self.assertEqual(self.players["9014"].injury_status, "Questionable")  # projection only
        self.assertEqual(self.players["9022"].injury_status, "Doubtful")  # players file only
        self.assertIsNone(self.players["9002"].injury_status)

    def test_unicode_name_and_team(self):
        self.assertEqual(self.players["9007"].name, "Marko Đurić")
        self.assertTrue(self.players["9007"].nba_team)

    def test_projection_only_player_and_bad_rows(self):
        rows = [
            {"player_id": "1", "stats": {"pts": "12.5", "adp_std": 3, "reb": None},
             "player": {"fantasy_positions": ["PG"], "first_name": "A", "last_name": "B", "team": "DEN"}},
            {"player_id": None},
            {"player_id": "2", "stats": None, "player": None},
        ]
        built = build_players({}, rows)
        self.assertEqual(list(built), ["1"])
        self.assertEqual(built["1"].stats, {"pts": 12.5})
        self.assertEqual(built["1"].adp, 3.0)
        self.assertEqual(build_players(None, None), {})


class BuildStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_mid_draft(self):
        state = build_state(fixture_client(self.tmp, "mid"), DRAFT_ID, username=USERNAME)
        self.assertEqual(state.our_slot, 3)
        self.assertEqual(state.current_pick, 15)
        self.assertEqual(state.on_clock_slot, 3)
        self.assertEqual(len(state.picks), 14)
        self.assertEqual([p["pick_no"] for p in state.picks], list(range(1, 15)))
        self.assertEqual([p.player_id for p in state.our_roster], ["9003", "9009"])
        self.assertEqual(len(state.drafted_ids), 14)
        self.assertEqual(state.our_next_picks[:3], (15, 22, 27))
        self.assertEqual(state.league.status, "drafting")
        avail = {p.player_id for p in state.available}
        self.assertFalse(avail & state.drafted_ids)
        self.assertEqual(len(avail), 56 - 14)
        self.assertNotIn("9058", avail)  # no projection and no ADP
        self.assertEqual(state.warnings, ())

    def test_cpu_picks_present_and_not_ours(self):
        state = build_state(fixture_client(self.tmp, "mid"), DRAFT_ID, username=USERNAME)
        cpu = [p for p in state.picks if p["picked_by"] == ""]
        self.assertTrue(cpu)
        self.assertTrue(all(p["draft_slot"] in (4, 5, 6) for p in cpu))
        self.assertTrue(all(p["draft_slot"] == 3 for p in state.picks if p["picked_by"] == USER_ID))

    def test_traded_picks(self):
        state = build_state(fixture_client(self.tmp, "traded"), DRAFT_ID, username=USERNAME)
        self.assertNotIn(27, state.our_next_picks)
        self.assertIn(32, state.our_next_picks)
        self.assertEqual(len(state.our_next_picks), 12 - 0)  # 14 - 2 made

    def test_traded_pick_on_clock_uses_owner_slot(self):
        routes = fixture_routes("mid")
        picks = load_fixture("picks_mid")[:11]  # next pick 12 -> round 2 slot 1
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks"] = picks
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/traded_picks"] = [
            {"round": 2, "roster_id": 1, "owner_id": 4}
        ]
        client = SleeperClient(fake_fetch(routes), cache_dir=self.tmp)
        state = build_state(client, DRAFT_ID, username=USERNAME)
        self.assertEqual(state.current_pick, 12)
        self.assertEqual(state.on_clock_slot, 4)

    def test_pre_draft_slot_unknown_then_manual_slot(self):
        state = build_state(fixture_client(self.tmp, "pre"), DRAFT_ID, username=USERNAME)
        self.assertIsNone(state.our_slot)
        self.assertEqual(state.our_next_picks, ())
        self.assertEqual(state.current_pick, 1)
        self.assertEqual(state.on_clock_slot, 1)
        self.assertEqual(len(state.available), 56)
        self.assertEqual(state.our_roster, ())
        self.assertTrue(any("slot is unknown" in w for w in state.warnings))
        state = build_state(fixture_client(self.tmp, "pre"), DRAFT_ID, username=USERNAME, slot=2)
        self.assertEqual(state.our_slot, 2)
        self.assertEqual(state.our_next_picks[:3], (2, 11, 14))

    def test_no_username_no_slot(self):
        state = build_state(fixture_client(self.tmp, "mid"), DRAFT_ID)
        self.assertIsNone(state.our_slot)
        self.assertEqual(state.our_roster, ())
        self.assertEqual(len(state.available), 56 - 14)

    def test_slot_without_user_uses_slot_picks(self):
        state = build_state(fixture_client(self.tmp, "mid"), DRAFT_ID, slot=3)
        self.assertEqual([p.player_id for p in state.our_roster], ["9003", "9009"])

    def test_slot_inferred_from_picks_when_draft_order_null(self):
        routes = fixture_routes("mid")
        draft = load_fixture("draft_drafting")
        draft["draft_order"] = None
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}"] = draft
        state = build_state(SleeperClient(fake_fetch(routes), cache_dir=self.tmp), DRAFT_ID, username=USERNAME)
        self.assertEqual(state.our_slot, 3)
        self.assertEqual(len(state.our_roster), 2)

    def test_draft_order_wins_over_conflicting_slot(self):
        state = build_state(fixture_client(self.tmp, "mid"), DRAFT_ID, username=USERNAME, slot=5)
        self.assertEqual(state.our_slot, 3)
        self.assertTrue(any("--slot" in w for w in state.warnings))

    def test_user_lookup_failure_is_a_warning(self):
        routes = fixture_routes("mid")
        routes["https://api.sleeper.app/v1/user/user_a"] = OSError("404")
        state = build_state(SleeperClient(fake_fetch(routes), cache_dir=self.tmp), DRAFT_ID, username=USERNAME, slot=3)
        self.assertEqual(state.our_slot, 3)
        self.assertTrue(any("look up" in w for w in state.warnings))
        self.assertEqual(len(state.our_roster), 2)

    def test_missing_league_uses_default_scoring(self):
        routes = fixture_routes("mid")
        draft = load_fixture("draft_drafting")
        draft["metadata"].pop("league_id")
        draft["league_id"] = None
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}"] = draft
        state = build_state(SleeperClient(fake_fetch(routes), cache_dir=self.tmp), DRAFT_ID, username=USERNAME)
        self.assertIsNone(state.league.league_id)
        self.assertEqual(state.league.scoring, DEFAULT_SCORING)
        self.assertTrue(any("default Sleeper scoring" in w for w in state.warnings))

    def test_complete_draft_has_no_current_pick(self):
        routes = fixture_routes("mid")
        draft = load_fixture("draft_drafting")
        draft["status"] = "complete"
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}"] = draft
        state = build_state(SleeperClient(fake_fetch(routes), cache_dir=self.tmp), DRAFT_ID, username=USERNAME)
        self.assertIsNone(state.current_pick)
        self.assertIsNone(state.on_clock_slot)

    def test_unknown_drafted_player_falls_back_to_pick_metadata(self):
        routes = fixture_routes("mid")
        picks = copy.deepcopy(load_fixture("picks_mid"))
        picks[2]["player_id"] = "424242"
        picks[2]["metadata"] = {"first_name": "Zed", "last_name": "Unknown", "position": "C", "team": "DEN"}
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks"] = picks
        state = build_state(SleeperClient(fake_fetch(routes), cache_dir=self.tmp), DRAFT_ID, username=USERNAME)
        self.assertEqual(state.our_roster[0].name, "Zed Unknown")
        self.assertEqual(state.our_roster[0].positions, ("C",))
        self.assertIn("424242", state.drafted_ids)

    def test_picks_sorted_and_robust_to_bad_rows(self):
        routes = fixture_routes("mid")
        picks = list(reversed(load_fixture("picks_mid")))
        picks.append({"pick_no": None})
        picks.append("junk")
        routes[f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks"] = picks
        state = build_state(SleeperClient(fake_fetch(routes), cache_dir=self.tmp), DRAFT_ID, username=USERNAME)
        self.assertEqual([p["pick_no"] for p in state.picks], list(range(1, 15)))


if __name__ == "__main__":
    unittest.main()
