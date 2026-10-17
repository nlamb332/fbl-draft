"""Contract tests for the synthetic mock-draft fixture and experiment runner."""

from __future__ import annotations

import unittest
from itertools import permutations

from mock_draft.fixture import (
    PICKS_PER_DRAFT,
    STARTING_SLOTS,
    STRATEGY_NAMES,
    TOTAL_TEAMS,
    DraftConfig,
    MockDraft,
    PlayerRecord,
    allocate_lineup,
    make_synthetic_schedule,
    score_stats,
    synthetic_fixture,
)


class FixtureContractTests(unittest.TestCase):
    def test_config_encodes_exact_league_shape(self) -> None:
        config = DraftConfig()
        self.assertEqual(config.total_teams, 14)
        self.assertEqual(config.rounds, 10)
        self.assertEqual(config.picks, 140)
        self.assertEqual(config.starting_slots, ("C", "G", "G", "F", "F", "UTIL"))
        self.assertEqual(config.bench_slots, 4)
        self.assertEqual(config.ir_slots, 3)

    def test_single_draft_has_140_unique_picks_and_14_legal_rosters(self) -> None:
        players = synthetic_fixture(player_count=150, seed=44)
        result = MockDraft(players, strategy="custom_value", seat=7, seed=99).run()
        self.assertEqual(len(result.picks), PICKS_PER_DRAFT)
        self.assertEqual(len({pick.player.player_id for pick in result.picks}), PICKS_PER_DRAFT)
        self.assertEqual(len(result.rosters), TOTAL_TEAMS)
        self.assertEqual(sorted(result.seat_by_team.values()), list(range(1, TOTAL_TEAMS + 1)))
        self.assertTrue(all(len(roster) == 10 for roster in result.rosters.values()))
        round_one = [pick.seat for pick in result.picks[:TOTAL_TEAMS]]
        round_two = [pick.seat for pick in result.picks[TOTAL_TEAMS : 2 * TOTAL_TEAMS]]
        self.assertEqual(round_one, list(range(1, TOTAL_TEAMS + 1)))
        self.assertEqual(round_two, list(range(TOTAL_TEAMS, 0, -1)))

    def test_draft_is_reproducible_for_same_seed(self) -> None:
        players = synthetic_fixture(player_count=145, seed=45)
        first = MockDraft(players, strategy="upside", seat=4, seed=777).run()
        second = MockDraft(players, strategy="upside", seat=4, seed=777).run()
        self.assertEqual([pick.player.player_id for pick in first.picks], [pick.player.player_id for pick in second.picks])

    def test_every_strategy_and_every_seat_can_run(self) -> None:
        # Keep unit tests focused: one seat per strategy plus all seats for the
        # baseline checks API coverage without repeating the full experiment.
        players = synthetic_fixture(player_count=140, seed=55)
        strategies = STRATEGY_NAMES
        for strategy in strategies:
            result = MockDraft(players, strategy=strategy, seat=1, seed=123).run()
            self.assertEqual(len(result.picks), PICKS_PER_DRAFT)
        for seat in range(1, TOTAL_TEAMS + 1):
            result = MockDraft(players, strategy="baseline_market", seat=seat, seed=123).run()
            self.assertEqual(len(result.picks), PICKS_PER_DRAFT)

    def test_exact_lineup_assignment_is_legal_and_schedule_is_seeded(self) -> None:
        players = synthetic_fixture(player_count=150, seed=77)
        result = MockDraft(players, strategy="availability", seat=1, seed=88).run()
        schedule_a = make_synthetic_schedule(players, days=7, seed=314)
        schedule_b = make_synthetic_schedule(players, days=7, seed=314)
        self.assertEqual(schedule_a.availability, schedule_b.availability)
        allocation = allocate_lineup(result.roster(), day=0, schedule=schedule_a, method="dp")
        self.assertEqual(len(allocation.slots), len(STARTING_SLOTS))
        self.assertLessEqual(len(allocation.inactive_starters), len(STARTING_SLOTS))
        self.assertEqual(len(set(allocation.slots.values())), len(STARTING_SLOTS))

    def test_exact_dp_matches_brute_force_on_adversarial_ten_player_roster(self) -> None:
        eligibilities = (("C",), ("C", "F"), ("G",), ("G", "F"), ("G", "F"), ("F",), ("F",), ("C", "G", "F"), ("C", "F"), ("G",))
        roster = [
            PlayerRecord(
                player_id=f"TEST-{index}",
                name=f"Test {index}",
                eligibility=eligibility,
                stats={"PTS": float(index + 1)},
                projected_availability=1.0,
                market_rank=float(index + 1),
                adp=float(index + 1),
                source_label="unit_test",
            )
            for index, eligibility in enumerate(eligibilities)
        ]
        slots = ("C", "G", "G", "F", "F", "UTIL")
        values = [score_stats(player.stats) for player in roster]
        brute_scores = []
        for chosen in permutations(range(len(roster)), len(slots)):
            if all(slot == "UTIL" or slot in roster[index].eligibility for slot, index in zip(slots, chosen)):
                brute_scores.append(sum(values[index] for index in chosen))
        allocation = allocate_lineup(roster, method="dp")
        self.assertTrue(brute_scores)
        self.assertEqual(allocation.score, max(brute_scores))
        self.assertEqual(len(set(allocation.slots.values())), len(slots))

    def test_dd_td_overlap_is_explicit(self) -> None:
        stats = {"PTS": 10, "DD": 1, "TD": 1}
        self.assertEqual(score_stats(stats, double_double_includes_triple_double=True) - score_stats(stats, double_double_includes_triple_double=False), 3)

    def test_config_rejects_wrong_team_count(self) -> None:
        with self.assertRaises(ValueError):
            DraftConfig(total_teams=12)
        with self.assertRaises(ValueError):
            DraftConfig(total_teams=13)
        with self.assertRaises(ValueError):
            DraftConfig(total_teams=15)


if __name__ == "__main__":
    unittest.main()

