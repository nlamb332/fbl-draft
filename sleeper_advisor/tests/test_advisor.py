import dataclasses
import random
import time
import unittest

from sleeper_advisor.advisor import POLICY_ID, Recommendation, _survival, recommend
from sleeper_advisor.lock_in import ForecastConfig, GameForecast, estimate_player
from sleeper_advisor.model import DraftState, SleeperLeague, SleeperPlayer

SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL")
SCORING = {"pts": 1.0, "reb": 1.0}
POS = ("PG", "SG", "SF", "PF", "C")


def league(slots=SLOTS, bench=5, teams=6, rounds=14, scoring=None):
    return SleeperLeague(
        draft_id="1", league_id=None, season="2026", draft_type="snake",
        mock_type=None, status="drafting", teams=teams, rounds=rounds,
        starting_slots=tuple(slots), bench_slots=bench, reversal_round=None,
        scoring=dict(scoring or SCORING))


def player(pid, pos, fp, adp=None, projected=True, injury=None):
    return SleeperPlayer(
        player_id=pid, name=f"Player {pid}", positions=tuple(pos),
        nba_team="XXX", injury_status=injury,
        stats={"pts": float(fp)} if projected else {},
        adp=adp, has_projection=projected)


def state(available, roster=(), lg=None, cur=1, our_slot=1, next_picks=None):
    lg = lg or league()
    if next_picks is None:
        next_picks = tuple(range(cur, cur + 3 * lg.teams, lg.teams))
    return DraftState(
        league=lg, our_slot=our_slot, current_pick=cur,
        on_clock_slot=our_slot, picks=(), drafted_ids=frozenset(),
        our_roster=tuple(roster), available=tuple(available),
        our_next_picks=tuple(next_picks), warnings=())


class AdvisorTests(unittest.TestCase):
    def test_c_glut_prefers_other_position(self):
        roster = [player(f"rc{i}", ("C",), 60 - i) for i in range(3)]
        avail = [player("c4", ("C",), 50, adp=1), player("pg", ("PG",), 40, adp=2)]
        recs = recommend(state(avail, roster, next_picks=(7,), cur=7))
        self.assertEqual(recs[0].player_id, "pg")
        by = {r.player_id: r for r in recs}
        self.assertAlmostEqual(by["c4"].components["lineup_gain"], 0.0)
        self.assertAlmostEqual(by["pg"].components["lineup_gain"],
                               estimate_player(avail[1], SCORING).weekly_points, places=4)

    def test_scarce_need_wins_when_fp_close_but_big_gap_wins(self):
        lg = league(slots=("PG", "C", "UTIL"), bench=2)
        roster = [player("a", ("PG",), 50), player("b", ("PG",), 45)]
        avail = [player("c", ("C",), 38, adp=3), player("pg", ("PG",), 40, adp=2)]
        recs = recommend(state(avail, roster, lg=lg, next_picks=(1,)))
        self.assertEqual(recs[0].player_id, "c")
        avail.append(player("star", ("PG",), 100, adp=1))
        recs = recommend(state(avail, roster, lg=lg, next_picks=(1,)))
        self.assertEqual(recs[0].player_id, "star")

    def test_survival_monotonic_in_adp(self):
        vals = [_survival(a, 20) for a in range(1, 60)]
        self.assertEqual(vals, sorted(vals))
        self.assertLess(vals[0], 0.1)
        self.assertGreater(vals[-1], 0.9)
        self.assertEqual(_survival(None, 20), 1.0)
        self.assertGreater(_survival(21, 20), 0.5)

    def test_survival_component_reported(self):
        avail = [player("early", ("PG",), 30, adp=2),
                 player("late", ("SG",), 30, adp=40)]
        recs = {r.player_id: r for r in recommend(state(avail))}
        self.assertLess(recs["early"].components["survival"],
                        recs["late"].components["survival"])
        self.assertTrue(recs["early"].explanation.startswith("Fills"))

    def test_replacement_logic_takes_player_who_will_not_last(self):
        avail = [player("goner", ("PG",), 40, adp=2),
                 player("lasts", ("PG",), 40, adp=60)] + [
                 player(f"f{i}", ("SG",), 10, adp=3 + i) for i in range(10)]
        recs = recommend(state(avail, next_picks=(1, 13, 25)))
        pgs = [r.player_id for r in recs if r.player_id in ("goner", "lasts")]
        self.assertEqual(pgs[0], "goner")

    def test_determinism(self):
        rng = random.Random(3)
        avail = [player(f"p{i}", (rng.choice(POS),), rng.uniform(5, 45),
                        adp=i + 1) for i in range(80)]
        a = recommend(state(avail), top_n=15)
        b = recommend(state(list(avail)), top_n=15)
        self.assertEqual(a, b)
        self.assertTrue(all(isinstance(r, Recommendation) for r in a))
        self.assertEqual(len(a), 15)
        scores = [r.score for r in a]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_unknown_slot_works(self):
        lg = league(slots=("PG", "FLEX", "UTIL"), bench=2)
        avail = [player("a", ("C",), 30, adp=1), player("b", ("PG",), 20, adp=2)]
        recs = recommend(state(avail, lg=lg))
        self.assertEqual(recs[0].player_id, "a")
        self.assertTrue(any("FLEX" in w for w in recs[0].warnings))

    def test_unknown_our_slot(self):
        avail = [player(f"p{i}", ("PG",), 30 - i, adp=i + 1) for i in range(10)]
        s = dataclasses.replace(state(avail, cur=4), our_slot=None,
                                on_clock_slot=None, our_next_picks=())
        recs = recommend(s)
        self.assertTrue(recs)
        self.assertTrue(any("slot is unknown" in w for w in recs[0].warnings))

    def test_empty_and_complete(self):
        avail = [player("a", ("PG",), 30, adp=1)]
        self.assertEqual(recommend(state([])), [])
        s = dataclasses.replace(state(avail), current_pick=None,
                                our_next_picks=())
        self.assertEqual(recommend(s), [])
        self.assertEqual(recommend(state(avail, next_picks=())), [])
        full = [player(f"r{i}", ("PG",), 10) for i in range(14)]
        self.assertEqual(recommend(state(avail, roster=full)), [])

    def test_unprojected_player_sorts_last_with_warning(self):
        avail = [player("proj", ("PG",), 20, adp=50),
                 player("noproj", ("SG",), 0, adp=1, projected=False)]
        recs = recommend(state(avail))
        self.assertEqual(recs[-1].player_id, "noproj")
        self.assertTrue(
            any("No stat projection" in w for w in recs[-1].warnings))

    def test_scoring_and_injury_warnings(self):
        lg = league(scoring={"pts": 1.0, "bonus_pt_40p": 2.0})
        avail = [player("a", ("PG",), 20, adp=1, injury="Questionable")]
        w = recommend(state(avail, lg=lg))[0].warnings
        self.assertTrue(any("bonus_pt_40p" in x for x in w))
        self.assertTrue(any("Questionable" in x for x in w))

    def test_runtime_with_500_players(self):
        rng = random.Random(11)
        avail = []
        for i in range(500):
            pos = tuple(sorted(rng.sample(POS, rng.choice((1, 1, 2))),
                               key=POS.index))
            avail.append(player(f"p{i}", pos, rng.uniform(1, 50), adp=i + 1))
        roster = [player(f"r{i}", ("SF",), 30 - i) for i in range(3)]
        start = time.monotonic()
        recs = recommend(state(avail, roster, cur=13,
                               next_picks=tuple(range(13, 80, 6))))
        elapsed = time.monotonic() - start
        self.assertTrue(recs)
        self.assertLess(elapsed, 2.5)


class LockInIntegrationTests(unittest.TestCase):
    def test_forecast_opportunities_change_pick_without_summing_games(self):
        lg = league(slots=("UTIL",), bench=1)
        players = [player("high_mean", ("PG",), 30), player("options", ("PG",), 25)]
        config = ForecastConfig(season="2026", scoring=SCORING, player_weeks={
            "high_mean": {1: (GameForecast(30, 0, 1),)},
            "options": {1: (GameForecast(25, 20, 1), GameForecast(25, 0, 1))}})
        recs = recommend(state(players, lg=lg), forecasts=config)
        self.assertEqual(recs[0].player_id, "options")
        comp = recs[0].components
        self.assertLess(comp["lineup_gain"], 50)
        self.assertGreater(comp["lineup_gain"], 25)
        self.assertGreater(comp["lock_in_weekly_points"], comp["lineup_gain"] - 1e-9)

    def test_wrong_season_rejected_before_recommendation(self):
        config = ForecastConfig(season="2027", scoring=SCORING)
        with self.assertRaisesRegex(ValueError, "season"):
            recommend(state([player("a", ("PG",), 20)]), forecasts=config)

    def test_last_roster_spot_preserves_required_center(self):
        lg = league(slots=("PG", "C"), bench=0)
        recs = recommend(state([player("pg", ("PG",), 100), player("c", ("C",), 20)],
                               roster=[player("r", ("PG",), 40)], lg=lg))
        self.assertEqual([r.player_id for r in recs], ["c"])

    def test_drafted_player_never_recommended(self):
        s = dataclasses.replace(state([player("a", ("PG",), 100), player("b", ("PG",), 20)]),
                                drafted_ids=frozenset({"a"}))
        self.assertEqual([r.player_id for r in recommend(s)], ["b"])


    def test_incomplete_feed_warns_about_completion(self):
        lg = league(slots=("PG", "C"), bench=0)
        recs = recommend(state([player("c", ("C",), 30)], lg=lg))
        self.assertTrue(any("cannot fill every starting slot" in w for w in recs[0].warnings))


class LookaheadBalanceTests(unittest.TestCase):
    """Balance must come from scoring only: no caps, quotas or bonuses."""

    def _glut(self):
        lg = league(slots=("G", "C", "UTIL"), bench=2, rounds=5)
        roster = [player("rc1", ("C",), 50), player("rc2", ("C",), 49),
                  player("rc3", ("C",), 48)]
        return lg, roster

    def test_policy_id_and_components(self):
        self.assertEqual(POLICY_ID, "forecast-lock-in-lookahead-v2")
        avail = [player("a", ("PG",), 30, adp=1), player("b", ("C",), 20, adp=2)]
        rec = recommend(state(avail))[0]
        for key in ("lineup_gain", "lookahead_value", "depth_value",
                    "lock_in_weekly_points", "survival"):
            self.assertIn(key, rec.components)
        self.assertAlmostEqual(rec.components["lookahead_value"], rec.score, places=3)
        self.assertTrue(any("absence probability" in w for w in rec.warnings))

    def test_c_glut_prefers_somewhat_lower_value_guard(self):
        lg, roster = self._glut()
        avail = [player("c4", ("C",), 46, adp=1), player("pg", ("PG",), 40, adp=2),
                 player("pg2", ("PG",), 10, adp=30)]
        recs = recommend(state(avail, roster, lg=lg, cur=1, next_picks=(1, 12, 13)))
        self.assertEqual(recs[0].player_id, "pg")

    def test_much_better_center_still_wins_despite_glut(self):
        lg, roster = self._glut()
        avail = [player("star_c", ("C",), 90, adp=1), player("pg", ("PG",), 40, adp=2),
                 player("pg2", ("PG",), 10, adp=30)]
        recs = recommend(state(avail, roster, lg=lg, cur=1, next_picks=(1, 12, 13)))
        self.assertEqual(recs[0].player_id, "star_c")

    def test_depth_has_value_without_a_bench_bonus(self):
        # Single UTIL slot: the backup is worth something only through the
        # absence term, which is derived from player values.
        lg = league(slots=("UTIL",), bench=1)
        roster = [player("starter", ("PG",), 40)]
        avail = [player("backup", ("PG",), 30, adp=1)]
        rec = recommend(state(avail, roster, lg=lg, next_picks=(1,)))[0]
        self.assertGreater(rec.components["depth_value"], 0.0)
        self.assertAlmostEqual(rec.components["lineup_gain"], 0.0)

    def test_absence_probability_read_from_forecast_defaults(self):
        from sleeper_advisor.lock_in import ForecastDefaults
        lg = league(slots=("UTIL",), bench=1)
        roster = [player("starter", ("PG",), 40)]
        avail = [player("backup", ("PG",), 30, adp=1)]
        low = ForecastConfig(season="2026", scoring=SCORING,
                             defaults=ForecastDefaults(weekly_absence_probability=0.0))
        high = ForecastConfig(season="2026", scoring=SCORING,
                              defaults=ForecastDefaults(weekly_absence_probability=0.5))
        d_low = recommend(state(avail, roster, lg=lg, next_picks=(1,)), forecasts=low)[0]
        d_high = recommend(state(avail, roster, lg=lg, next_picks=(1,)), forecasts=high)[0]
        self.assertAlmostEqual(d_low.components["depth_value"], 0.0)
        self.assertGreater(d_high.components["depth_value"], 1.0)

    def test_opponents_removal_makes_scarce_tier_valuable(self):
        # Two C and two PG remain; opponents will take the top-ADP players
        # between our picks, so taking the one that will not last matters.
        lg = league(slots=("PG", "C"), bench=0, rounds=2)
        avail = [player("c_gone", ("C",), 40, adp=2), player("c_late", ("C",), 38, adp=50),
                 player("pg_gone", ("PG",), 41, adp=1), player("pg_late", ("PG",), 39, adp=51)]
        recs = recommend(state(avail, lg=lg, next_picks=(1, 8)))
        self.assertIn(recs[0].player_id, ("pg_gone", "c_gone"))


if __name__ == "__main__":
    unittest.main()
