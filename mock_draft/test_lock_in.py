"""Contract tests for the isolated Lock-In research engine."""

from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path

from mock_draft.lock_in import (
    GameForecast,
    GameOutcome,
    LockInPolicyManager,
    LockInWorld,
    PlayerWeekGames,
    POLICY_RULES,
    STARTING_SLOTS,
    assign_weekly_starters,
    evaluate_lock_in_week,
    forecast_threshold,
    generate_lock_in_world,
    load_profile,
    run_research,
    score_stats,
    simulate_lock_in_draft,
    _assignment_for_roster,
    DraftResearchContext,
    draft_strategy_score,
    _next_own_pick,
    _sample_stats,
)
from mock_draft.six_team import PICKS_PER_DRAFT, SixTeamConfig, SixTeamPlayer, generate_synthetic_players


def _manual_games(player_id: str, scores: list[float], means: list[float], *, available: list[bool] | None = None) -> PlayerWeekGames:
    flags = available or [True] * len(scores)
    forecasts = tuple(GameForecast(player_id, 0, index, means[index], 0.0, 1.0) for index in range(len(scores)))
    outcomes = tuple(GameOutcome(player_id, 0, index, scores[index], flags[index], {}) for index in range(len(scores)))
    return PlayerWeekGames(forecasts, outcomes)


class LockInTests(unittest.TestCase):
    def test_hand_calculated_stopping_threshold(self) -> None:
        games = _manual_games("p", [12.0, 19.0], [10.0, 20.0])
        policy = LockInPolicyManager("forecast_dp")
        self.assertEqual(forecast_threshold("forecast_dp", games.forecasts[1:]), 20.0)
        result = policy.run_player_week("p", "PG", games)
        self.assertEqual(result.locked_game_index, 1)
        self.assertEqual(result.score, 19.0)

        three = _manual_games("p", [12.0, 16.0, 20.0], [10.0, 15.0, 25.0])
        self.assertEqual(policy.run_player_week("p", "PG", three).locked_game_index, 2)

    def test_future_outcome_mutation_cannot_change_prefix_decision(self) -> None:
        policy = LockInPolicyManager("forecast_dp")
        # The first observed score is already above the continuation value, so
        # changing all future outcomes cannot change the first decision.
        first = _manual_games("p", [30.0, 19.0, 21.0], [10.0, 20.0, 22.0])
        second = _manual_games("p", [30.0, -90.0, -90.0], [10.0, 20.0, 22.0])
        self.assertEqual(policy.run_player_week("p", "PG", first).locked_game_index, 0)
        self.assertEqual(policy.run_player_week("p", "PG", second).locked_game_index, 0)

    def test_dnp_final_defaults_zero_but_played_negative_is_legal(self) -> None:
        policy = LockInPolicyManager("mean_threshold")
        dnp = _manual_games("p", [0.0], [-5.0], available=[False])
        result = policy.run_player_week("p", "PG", dnp)
        self.assertFalse(result.locked)
        self.assertTrue(result.zero_fallback)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(forecast_threshold("forecast_dp", dnp.forecasts), -5.0)
        negative = _manual_games("p", [-4.0], [-5.0])
        result = policy.run_player_week("p", "PG", negative)
        self.assertTrue(result.locked)
        self.assertEqual(result.score, -4.0)
        # A good earlier score is never used as a fallback after a final DNP.
        good_then_dnp = _manual_games("p", [20.0, 0.0], [30.0, 30.0], available=[True, False])
        result = LockInPolicyManager("forecast_dp").run_player_week("p", "PG", good_then_dnp)
        self.assertFalse(result.locked)
        self.assertEqual(result.score, 0.0)
        # always_first means first completed game, skipping an early DNP.
        dnp_then_played = _manual_games("p", [0.0, -3.0, 2.0], [10.0, 10.0, 10.0], available=[False, True, True])
        result = LockInPolicyManager("always_first").run_player_week("p", "PG", dnp_then_played)
        self.assertEqual(result.locked_game_index, 1)

    def test_forecast_health_mixture_and_lock_in_assignment_value(self) -> None:
        future = GameForecast("p", 0, 0, 20.0, 0.0, 0.5)
        self.assertEqual(forecast_threshold("forecast_dp", (future,)), 10.0)
        negative = GameForecast("p", 0, 0, -5.0, 0.0, 1.0)
        self.assertEqual(forecast_threshold("forecast_dp", (negative,)), -5.0)

        # A one-game 30-point player beats a four-game 10-point player under
        # the stopping-value assignment; summing games would choose B.
        position_list = [("PG",), ("PG",), ("SG",), ("SF",), ("PF",), ("C",), ("PG",), ("SG",), ("SF",), ("PF",), ("C",), ("SG",), ("SF",), ("PF",)]
        players = [SixTeamPlayer(f"p{index}", f"P{index}", positions, {"PTS": 1}, 1, index, index) for index, positions in enumerate(position_list)]
        values = {player.player_id: 20.0 for player in players}
        values["p0"] = 30.0  # A: one game at 30
        values["p1"] = 10.0  # B: four games at 10, stopping value still 10
        game_map = {(player.player_id, 0): _manual_games(player.player_id, [values[player.player_id]], [values[player.player_id]]) for player in players}
        game_map[("p1", 0)] = _manual_games("p1", [10.0]*4, [10.0]*4)
        world = LockInWorld("assignment-case", 0, "nominal", 1, game_map)
        selected = assign_weekly_starters(players, world, week=0)
        self.assertIn("p0", selected.starter_ids)
        self.assertNotIn("p1", selected.starter_ids)

    def test_market_timing_can_take_scarce_player_then_wait_for_value(self) -> None:
        config = SixTeamConfig()
        players = [SixTeamPlayer(name, name, ("PG",), {"PTS": value*2}, 1, rank, rank)
                   for name, value, rank in (("A",100,10),("B",90,1),("C",80,2),("D",1,3),("E",1,4))]
        values = {p.player_id: p.stats["PTS"]*.5 for p in players}
        context = DraftResearchContext(LockInWorld("timing-case", 0, "nominal", 1, {}), config, lock_values=values)
        available = {p.player_id: p for p in players}
        scores = {p.player_id: draft_strategy_score(p, (), "market_aware_timing", config=config,
                  context=context, available=available, pick_number=5, our_seat=5) for p in players}
        self.assertEqual(_next_own_pick(5, 5, config), 8)
        self.assertGreater(scores["B"], scores["A"])
        self.assertEqual(draft_strategy_score(players[1], (), "market_aware_timing", config=config,
                         context=context, available=available, pick_number=83, our_seat=2), 90)

    def test_native_box_score_events_and_profile_contract(self) -> None:
        import random
        for index in range(100):
            stats = _sample_stats({"PTS":45,"REB":12,"AST":11,"STL":2,"BLK":2,"TO":3,"3PM":8}, random.Random(index), injury_shift=0)
            self.assertTrue(all(value == int(value) for value in stats.values()))
            self.assertLessEqual(3*stats["3PM"], stats["PTS"])
            self.assertEqual(stats["POINTS_40_PLUS"], float(stats["PTS"]>=40))
            self.assertEqual(stats["POINTS_50_PLUS"], float(stats["PTS"]>=50))
            self.assertGreaterEqual(stats["DD"], stats["TD"])
        profile, _, _ = load_profile()
        runtime = Path("runtime")
        runtime.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as temp:
            path = Path(temp)/"bad-profile.json"
            bad = json.loads(json.dumps(profile))
            bad["mode"] = "daily"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_profile(path)

    def test_common_forecast_baselines_and_variance_option_value(self) -> None:
        import math
        player = SixTeamPlayer("p", "P", ("PG",), {"PTS":200}, 1, 1, 1)
        forecast = GameForecast("p", 0, 0, 10, 0, .5)
        world = LockInWorld("common-input-case", 1, "nominal", 1,
                            {("p",0):PlayerWeekGames((forecast,), (GameOutcome("p",0,0,999),))})
        config = SixTeamConfig()
        context = DraftResearchContext(world, config)
        self.assertEqual(draft_strategy_score(player, (), "custom_per_game", config=config, context=context), 10)
        self.assertEqual(draft_strategy_score(player, (), "availability", config=config, context=context), 5)
        deterministic = (GameForecast("p",0,0,20,0), GameForecast("p",0,1,20,0))
        volatile = (GameForecast("p",0,0,20,10), GameForecast("p",0,1,20,10))
        self.assertEqual(forecast_threshold("forecast_dp", deterministic), 20)
        self.assertAlmostEqual(forecast_threshold("forecast_dp", volatile), 20+10/math.sqrt(2*math.pi))

    def test_forecast_shift_keeps_realized_world_fixed(self) -> None:
        players = generate_synthetic_players(player_count=84, seed=92)
        nominal = generate_lock_in_world(players, pool_seed=92, weeks=1)
        shifted = generate_lock_in_world(players, pool_seed=92, weeks=1, scenario="forecast_shift", forecast_bias=.07, forecast_variance_error=.10)
        for key in nominal.games:
            self.assertEqual(nominal.games[key].outcomes, shifted.games[key].outcomes)
        self.assertNotEqual(nominal.games[(players[0].player_id,0)].forecasts, shifted.games[(players[0].player_id,0)].forecasts)

    def test_assignment_has_nine_immutable_starter_slots(self) -> None:
        players = generate_synthetic_players(player_count=96, seed=51)
        world = generate_lock_in_world(players, pool_seed=51, weeks=1)
        roster = players[:14]
        assignment = assign_weekly_starters(roster, world, week=0)
        self.assertEqual(len(assignment.slots), len(STARTING_SLOTS))
        self.assertEqual(len(set(assignment.starter_ids)), 9)
        self.assertEqual(set(assignment.starter_ids) | set(assignment.bench), {player.player_id for player in roster})
        week = evaluate_lock_in_week(roster, world, week=0, policy_manager=LockInPolicyManager())
        self.assertEqual(len(week.player_results), 9)
        self.assertEqual(len({result.slot for result in week.player_results}), 9)

    def test_derived_bonus_events_stack(self) -> None:
        stats = {"PTS": 50, "REB": 10, "AST": 10, "DD": 1, "TD": 1, "POINTS_40_PLUS": 1, "POINTS_50_PLUS": 1}
        self.assertEqual(score_stats(stats), 52.0)

    def test_new_draft_is_84_unique_picks_for_all_six_seats(self) -> None:
        players = generate_synthetic_players(player_count=90, seed=61)
        world = generate_lock_in_world(players, pool_seed=61, weeks=1)
        for seat in range(1, 7):
            result = simulate_lock_in_draft(players, strategy="lock_in_value", our_seat=seat, seed=601 + seat, context=None)
            self.assertEqual(len(result.picks), PICKS_PER_DRAFT)
            self.assertEqual(len({pick.player.player_id for pick in result.picks}), PICKS_PER_DRAFT)
            self.assertEqual(sorted(len(roster) for roster in result.rosters.values()), [14] * 6)

    def test_quick_runner_writes_reviewable_outputs(self) -> None:
        runtime = Path("runtime")
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as temp:
            result = run_research(
                development_pool_seeds=(71,), heldout_pool_seeds=(72,), player_count=90, weeks=2,
                scenarios=("nominal",), strategies=("lock_in_value",), policy_rules=POLICY_RULES,
                output_dir=temp,
            )
            self.assertTrue(Path(temp, "REPORT.md").exists())
            self.assertTrue(Path(temp, "rows.json").exists())
            self.assertEqual(len(result["rows"]), 2 * 6 * 3)
            self.assertTrue(all("weekly_opponent_pair_win_fraction" in row for row in result["rows"]))


if __name__ == "__main__":
    unittest.main()
