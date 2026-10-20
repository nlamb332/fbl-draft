import unittest

from sleeper_advisor.scoring import fantasy_points

OBSERVED = {
    "pts": 0.5, "reb": 1, "ast": 1, "stl": 2, "blk": 2, "to": -1, "tpm": 0.5,
    "dd": 1, "td": 2, "bonus_pt_40p": 2, "bonus_pt_50p": 2, "tf": -2, "ff": -2,
}


class ScoringTests(unittest.TestCase):
    def test_observed_settings_line(self):
        stats = {"pts": 20, "reb": 10, "ast": 5, "stl": 1, "blk": 1, "to": 2,
                 "tpm": 2, "dd": 0.5, "td": 0.1}
        total, parts, warnings = fantasy_points(stats, OBSERVED)
        self.assertAlmostEqual(total, 28.7)
        self.assertAlmostEqual(parts["pts"], 10)
        self.assertAlmostEqual(parts["to"], -2)
        self.assertAlmostEqual(parts["td"], 0.2)
        self.assertAlmostEqual(sum(parts.values()), total)
        keys = {w.split("'")[1] for w in warnings}
        self.assertEqual(keys, {"bonus_pt_40p", "bonus_pt_50p", "tf", "ff"})
        self.assertEqual(len(warnings), 4)

    def test_missing_dd_td_is_zero_without_warning(self):
        total, parts, warnings = fantasy_points(
            {"pts": 10}, {"pts": 0.5, "dd": 1, "td": 2})
        self.assertAlmostEqual(total, 5)
        self.assertEqual(warnings, ())
        self.assertNotIn("dd", parts)

    def test_extra_direct_keys(self):
        total, _, warnings = fantasy_points(
            {"fgm": 8, "fga": 16, "pf": 3}, {"fgm": 1, "fga": -0.5, "pf": -1})
        self.assertAlmostEqual(total, 8 - 8 - 3)
        self.assertEqual(warnings, ())

    def test_zero_weight_ignored_and_deterministic(self):
        a = fantasy_points({"pts": 1}, {"pts": 1, "zzz": 0, "qd": 3, "ff": -2})
        b = fantasy_points({"pts": 1}, {"ff": -2, "qd": 3, "zzz": 0, "pts": 1})
        self.assertEqual(a, b)
        self.assertEqual(len(a[2]), 2)

    def test_identity_components_derived_without_warning(self):
        total, parts, warnings = fantasy_points(
            {"reb": 10, "dreb": 7, "fga": 20, "fgm": 9, "tpa": 6, "tpm": 2},
            {"oreb": 7, "dreb": -2, "fgmi": 5, "tpmi": 6})
        self.assertAlmostEqual(parts["oreb"], 21)
        self.assertAlmostEqual(parts["fgmi"], 55)
        self.assertAlmostEqual(parts["tpmi"], 24)
        self.assertAlmostEqual(total, 21 - 14 + 55 + 24)
        self.assertEqual(warnings, ())

    def test_missing_shot_keys_are_zero_and_attempts_rebuilt(self):
        total, parts, warnings = fantasy_points(
            {"ftm": 4, "ftmi": 2}, {"fta": 1, "tpmi": 6})
        self.assertAlmostEqual(parts["fta"], 6)
        self.assertNotIn("tpmi", parts)
        self.assertEqual(warnings, ())

    def test_unprojected_personal_fouls_estimated_from_minutes(self):
        total, parts, warnings = fantasy_points({"sp": 1800}, {"pf": 5})
        self.assertAlmostEqual(parts["pf"], 30 * 0.08 * 5)
        self.assertEqual(len(warnings), 1)
        self.assertIn("estimated from minutes", warnings[0])
        _, parts, warnings = fantasy_points({"pf": 2, "sp": 1800}, {"pf": 5})
        self.assertAlmostEqual(parts["pf"], 10)
        self.assertEqual(warnings, ())


if __name__ == "__main__":
    unittest.main()
