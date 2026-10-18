"""Contract tests for the isolated six-team research engine."""

from __future__ import annotations

import itertools
import tempfile
import unittest
from pathlib import Path

from mock_draft.six_team import (
    LINEUP_DAYS,
    OPPONENT_PROFILES,
    PICKS_PER_DRAFT,
    SCORING_WEIGHTS,
    STARTING_SLOTS,
    STRATEGY_NAMES,
    SixTeamConfig,
    SixTeamPlayer,
    _can_assign_slots,
    allocate_lineup,
    check_roster_shape,
    default_opponent_profiles,
    generate_synthetic_players,
    load_profile,
    make_synthetic_schedule,
    realize_players,
    run_evaluation,
    score_stats,
    simulate_draft,
    _score_forecast_selected,
)


class SixTeamEngineTests(unittest.TestCase):
    def test_profile_is_loaded_and_matches_engine(self) -> None:
        profile, config, digest = load_profile()
        self.assertEqual(profile["status"], "research_only_advisory")
        self.assertFalse(profile["live_consumption"])
        self.assertEqual(config.picks, 84)
        self.assertEqual(config.roster_size, 14)
        self.assertEqual(profile["settings"]["ir_slots"], None)
        self.assertEqual(digest.__len__(), 64)
        self.assertEqual(set(profile["scoring"]), set(SCORING_WEIGHTS))

    def test_stacking_bonus_rules_are_explicit(self) -> None:
        stats = {"DD": 1, "TD": 1, "POINTS_40_PLUS": 1, "POINTS_50_PLUS": 1}
        self.assertEqual(score_stats(stats), 7.0)
        self.assertEqual(score_stats(stats, double_double_includes_triple_double=False, threshold_bonuses_stack=False), 4.0)

    def test_native_slot_matching_handles_flexible_player_conflict(self) -> None:
        # The flexible PG/SG record must be assigned to one specific slot while
        # the single-position records cover the other position.
        eligibility = (("PG", "SG"), ("PG",), ("SG",), ("SF",), ("PF",), ("C",), ("SF", "PF"), ("PG", "SG"), ("C",))
        players = [
            SixTeamPlayer(f"T-{index}", f"Test {index}", positions, {"PTS": float(index + 1)}, 1.0, float(index + 1), float(index + 1))
            for index, positions in enumerate(eligibility)
        ]
        self.assertTrue(_can_assign_slots(players, STARTING_SLOTS))
        brute_possible = any(
            all(slot == "UTIL" or _eligible(player, slot) for slot, player in zip(STARTING_SLOTS, permutation))
            for permutation in itertools.permutations(players, len(STARTING_SLOTS))
        )
        self.assertEqual(brute_possible, _can_assign_slots(players, STARTING_SLOTS))

    def test_weighted_allocator_matches_independent_brute_force(self) -> None:
        # Nine positive starter candidates plus five negative bench candidates
        # keep the independent permutation check small while exercising the
        # flexible PG/SG and SF/PF conflicts.
        starter_eligibility = (("PG", "SG"), ("PG",), ("SG",), ("SF",), ("PF",), ("SF", "PF"), ("C",), ("PF", "C"), ("PG", "SG"))
        starter_pool = [
            SixTeamPlayer(f"W-{index}", f"Weighted {index}", positions, {"PTS": float(index + 1)}, 1.0, float(index + 1), float(index + 1))
            for index, positions in enumerate(starter_eligibility)
        ]
        bench = [SixTeamPlayer(f"B-{index}", f"Bench {index}", ("PG",), {"PTS": -100.0}, 1.0, 100.0 + index, 100.0 + index) for index in range(5)]
        roster = starter_pool + bench
        schedule = make_synthetic_schedule(roster, days=1, seed=1)
        allocation = allocate_lineup(roster, day=0, schedule=schedule)
        brute_scores = []
        for permutation in itertools.permutations(starter_pool):
            if all(_eligible(player, slot) for slot, player in zip(STARTING_SLOTS, permutation)):
                brute_scores.append(sum(player.stats["PTS"] * 0.5 for player in permutation))
        self.assertTrue(brute_scores)
        self.assertEqual(allocation.score, max(brute_scores))

    def test_every_strategy_and_seat_completes_84_unique_picks(self) -> None:
        players = generate_synthetic_players(player_count=96, seed=101)
        self.assertEqual(len(OPPONENT_PROFILES), 13)
        for strategy in STRATEGY_NAMES:
            for seat in range(1, 7):
                result = simulate_draft(players, strategy=strategy, our_seat=seat, seed=500 + seat)
                self.assertEqual(len(result.picks), PICKS_PER_DRAFT)
                self.assertEqual(len({pick.player.player_id for pick in result.picks}), PICKS_PER_DRAFT)
                self.assertTrue(all(check_roster_shape(roster)[0] for roster in result.rosters.values()))
                self.assertEqual(sorted(len(roster) for roster in result.rosters.values()), [14] * 6)

    def test_opponent_sampling_rotates_across_all_thirteen_profiles(self) -> None:
        samples = [default_opponent_profiles(seed=seed) for seed in range(64)]
        self.assertTrue(all(len(sample) == 5 and len(set(sample)) == 5 for sample in samples))
        self.assertEqual({profile.name for sample in samples for profile in sample}, {profile.name for profile in OPPONENT_PROFILES})

    def test_realized_event_rates_preserve_inclusive_bonus_semantics(self) -> None:
        for seed in range(100):
            forecasts = generate_synthetic_players(seed=seed)
            for player in realize_players(forecasts, seed=seed ^ 0xA5A5A5A5):
                self.assertLessEqual(player.stats["TD"], player.stats["DD"])
                self.assertLessEqual(player.stats["POINTS_50_PLUS"], player.stats["POINTS_40_PLUS"])

    def test_forecast_selected_lineup_is_scored_with_separate_realized_world(self) -> None:
        players = generate_synthetic_players(player_count=96, seed=77)
        realized = realize_players(players, seed=78)
        schedule = make_synthetic_schedule(players, seed=79)
        draft = simulate_draft(players, strategy="lineup_aware", our_seat=1, seed=80)
        forecast_allocations = [allocate_lineup(draft.roster(), day=day, schedule=schedule) for day in range(LINEUP_DAYS)]
        forecast_ids = [allocation.slots for allocation in forecast_allocations]
        realized_by_id = {player.player_id: player for player in realized}
        realized_score = sum(_score_forecast_selected(allocation, realized_by_id, schedule, SixTeamConfig()) for allocation in forecast_allocations)
        opposite = {
            player.player_id: SixTeamPlayer(player.player_id, player.name, player.eligibility, {"PTS": -player.stats["PTS"], **{key: 0.0 for key in player.stats if key != "PTS"}}, player.projected_availability, player.market_rank, player.adp)
            for player in draft.roster()
        }
        opposite_score = sum(_score_forecast_selected(allocation, opposite, schedule, SixTeamConfig()) for allocation in forecast_allocations)
        self.assertNotEqual(realized_score, opposite_score)
        self.assertEqual(forecast_ids, [allocation.slots for allocation in [allocate_lineup(draft.roster(), day=day, schedule=schedule) for day in range(LINEUP_DAYS)]])
        self.assertTrue(any(a.stats != b.stats for a, b in zip(players, realized)))

    def test_bounded_runner_writes_complete_development_and_holdout_outputs(self) -> None:
        runtime_root = Path("runtime")
        runtime_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="six-team-test-", dir=runtime_root))
        result = run_evaluation(development_seeds=(11,), heldout_seeds=(22,), player_count=96, output_dir=temporary)
        self.assertEqual(len(result["rows"]), len(STRATEGY_NAMES) * 6 * 2)
        self.assertTrue(Path(temporary, "REPORT.md").exists())
        self.assertTrue(Path(temporary, "summary.json").exists())
        self.assertEqual(result["metadata"]["picks_per_draft"], 84)
        self.assertIn(result["selection"]["development_selected_strategy"], STRATEGY_NAMES)


def _eligible(player: SixTeamPlayer, slot: str) -> bool:
    positions = set(player.eligibility)
    if slot == "PG":
        return "PG" in positions
    if slot == "SG":
        return "SG" in positions
    if slot == "G":
        return bool(positions & {"PG", "SG", "G"})
    if slot == "SF":
        return "SF" in positions
    if slot == "PF":
        return "PF" in positions
    if slot == "F":
        return bool(positions & {"SF", "PF", "F"})
    if slot == "C":
        return "C" in positions
    return True


if __name__ == "__main__":
    unittest.main()
