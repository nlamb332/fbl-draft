import random
import unittest

from sleeper_advisor.lineup import SLOT_ACCEPTS, best_lineup

SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL")
POS = ("PG", "SG", "SF", "PF", "C")


def brute(players, slots):
    best = 0.0
    n = len(slots)

    def rec(si, used, total):
        nonlocal best
        if si == n:
            best = max(best, total)
            return
        rec(si + 1, used, total)  # leave slot empty
        acc = SLOT_ACCEPTS[slots[si]]
        for i, (_, pos, v) in enumerate(players):
            if i not in used and v > 0 and acc & set(pos):
                rec(si + 1, used | {i}, total + v)

    rec(0, frozenset(), 0.0)
    return best


class LineupTests(unittest.TestCase):
    def test_matches_brute_force(self):
        rng = random.Random(7)
        for _ in range(150):
            k = rng.randint(0, 8)
            players = []
            for i in range(k):
                pos = tuple(sorted(rng.sample(POS, rng.choice((1, 1, 2))),
                                   key=POS.index))
                players.append((f"p{i}", pos, round(rng.uniform(-5, 50), 1)))
            slots = rng.sample(SLOTS, rng.randint(1, 7))
            total, assign = best_lineup(players, slots)
            self.assertAlmostEqual(total, brute(players, slots), places=6)
            vals = {p[0]: p for p in players}
            self.assertAlmostEqual(
                total, sum(vals[pid][2] for pid in assign), places=6)
            used = list(assign.values())
            for s in set(used):
                self.assertLessEqual(used.count(s), slots.count(s))
            for pid, s in assign.items():
                self.assertTrue(SLOT_ACCEPTS[s] & set(vals[pid][1]))

    def test_c_glut_only_fills_c_and_util(self):
        players = [(f"c{i}", ("C",), 50 - i) for i in range(6)]
        total, assign = best_lineup(players, SLOTS)
        self.assertAlmostEqual(total, 50 + 49 + 48)
        self.assertEqual(sorted(assign.values()), ["C", "UTIL", "UTIL"])

    def test_non_positive_ignored_and_bench_slot_skipped(self):
        total, assign = best_lineup(
            [("a", ("PG",), 0.0), ("b", ("PG",), -3.0)], ("PG", "BN"))
        self.assertEqual((total, assign), (0.0, {}))
        total, assign = best_lineup([("a", ("PG",), 5.0)], ("BN", "PG"))
        self.assertEqual(assign, {"a": "PG"})

    def test_multi_position_reshuffle(self):
        players = [("a", ("PG", "SG"), 30), ("b", ("PG",), 29), ("c", ("SG",), 1)]
        total, _ = best_lineup(players, ("PG", "SG"))
        self.assertAlmostEqual(total, 59)

    def test_unknown_slot_accepts_any(self):
        total, assign = best_lineup([("a", ("C",), 5.0)], ("XYZ",))
        self.assertEqual(assign, {"a": "XYZ"})


if __name__ == "__main__":
    unittest.main()
