import unittest

from sleeper_advisor.depth import Completion, absence_value

SLOTS = ("PG", "C", "UTIL")


class AbsenceValueTests(unittest.TestCase):
    def test_zero_probability_equals_lineup(self):
        roster = [("a", ("PG",), 30.0), ("b", ("C",), 20.0), ("c", ("SF",), 10.0)]
        v, l = absence_value(roster, SLOTS, 0.0)
        self.assertEqual(v, l)
        self.assertEqual(l, 60.0)

    def test_backup_reduces_absence_penalty_by_what_it_recovers(self):
        slots = ("UTIL",)
        solo, l_solo = absence_value([("a", ("PG",), 40.0)], slots, 0.1)
        pair, l_pair = absence_value([("a", ("PG",), 40.0), ("b", ("PG",), 30.0)], slots, 0.1)
        self.assertAlmostEqual(solo, 36.0)
        self.assertAlmostEqual(pair, 40.0 - 0.1 * 10.0)
        self.assertEqual(l_solo, l_pair)

    def test_ineligible_backup_is_worth_nothing(self):
        slots = ("PG", "C")
        base = [("pg", ("PG",), 40.0), ("c", ("C",), 40.0)]
        with_c = base + [("c2", ("C",), 30.0)]
        with_pg = base + [("pg2", ("PG",), 30.0)]
        v_c, _ = absence_value(with_c, slots, 0.1)
        v_pg, _ = absence_value(with_pg, slots, 0.1)
        # Equal value backups: each covers only its own slot, so equal.
        self.assertAlmostEqual(v_c, v_pg)
        # But a backup that fits no starter slot adds nothing.
        v_none, _ = absence_value(base + [("sf", ("SF",), 30.0)], slots, 0.1)
        v_base, _ = absence_value(base, slots, 0.1)
        self.assertAlmostEqual(v_none, v_base)


class CompletionTests(unittest.TestCase):
    def test_opponents_take_adp_order_and_we_take_best_value(self):
        entries = [("x", ("PG",), 50.0), ("y", ("PG",), 40.0), ("z", ("C",), 30.0)]
        adp = {"x": 1.0, "y": 2.0, "z": 3.0}
        comp = Completion(entries, adp, ("UTIL",), [], 0.0)
        # One opponent pick (takes x) then we pick the best left (y).
        v, l = comp.finish(None, 1, [])
        self.assertEqual(l, 0.0)
        v, l = comp.finish(("z", ("C",), 30.0), 0, [1])
        self.assertEqual(l, 40.0)  # opp took x; we took y over z
        comp2 = Completion(entries, adp, ("UTIL", "UTIL"), [], 0.0)
        v, l = comp2.finish(("z", ("C",), 30.0), 0, [1])
        self.assertEqual(l, 30.0 + 40.0)  # opp took x, we took y


if __name__ == "__main__":
    unittest.main()
