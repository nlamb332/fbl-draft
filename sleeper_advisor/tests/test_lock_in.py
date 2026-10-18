import json
import math
import tempfile
import unittest
from pathlib import Path

from sleeper_advisor.lock_in import (
    ForecastConfig,
    ForecastDefaults,
    ForecastError,
    GameForecast,
    estimate_player,
    forecast_stopping_value,
    load_forecasts,
    normal_expected_max,
)
from sleeper_advisor.model import SleeperPlayer


SCORING = {"pts": 1.0, "reb": 1.0, "to": -1.0, "bonus_pt_40p": 2.0}


def player(pid="p", *, stats=None, projected=True, injury=None):
    return SleeperPlayer(
        player_id=pid,
        name=f"Player {pid}",
        positions=("PG",),
        nba_team="TST",
        injury_status=injury,
        stats=dict(stats if stats is not None else {"pts": 10.0, "reb": 5.0}),
        adp=10.0,
        has_projection=projected,
    )


class LockInMathTests(unittest.TestCase):
    def test_hand_calculated_dp_and_negative_final_is_legal(self):
        games = (
            GameForecast(mean=8.0, stddev=0.0, availability_probability=1.0),
            GameForecast(mean=12.0, stddev=0.0, availability_probability=1.0),
        )
        self.assertEqual(forecast_stopping_value(games), 12.0)
        self.assertEqual(
            forecast_stopping_value(
                (GameForecast(mean=-5.0, stddev=0.0, availability_probability=1.0),)
            ),
            -5.0,
        )

    def test_explicit_dnp_mixture(self):
        self.assertEqual(
            forecast_stopping_value(
                (GameForecast(mean=20.0, stddev=0.0, availability_probability=0.5),)
            ),
            10.0,
        )
        # A DNP on the first opportunity preserves the second opportunity.
        self.assertEqual(
            forecast_stopping_value(
                (
                    GameForecast(mean=100.0, stddev=0.0, availability_probability=0.0),
                    GameForecast(mean=20.0, stddev=0.0, availability_probability=1.0),
                )
            ),
            20.0,
        )

    def test_variance_option_is_expected_max_not_hindsight(self):
        deterministic = forecast_stopping_value(
            (
                GameForecast(10.0, 0.0, 1.0),
                GameForecast(10.0, 0.0, 1.0),
            )
        )
        volatile = forecast_stopping_value(
            (
                GameForecast(10.0, 10.0, 1.0),
                GameForecast(10.0, 0.0, 1.0),
            )
        )
        self.assertEqual(deterministic, 10.0)
        self.assertAlmostEqual(volatile, 10.0 + 10.0 / math.sqrt(2.0 * math.pi))
        self.assertAlmostEqual(normal_expected_max(10.0, 0.0, 10.0), 10.0)

    def test_one_game_slot_value_is_not_games_times_fp(self):
        one = estimate_player(
            player("one", stats={"pts": 30.0}),
            SCORING,
            ForecastConfig(
                season="2026",
                scoring=SCORING,
                defaults=ForecastDefaults(scheduled_games_per_week=1, stddev_fraction=0.0, availability_probability=1.0),
            ),
        )
        four = estimate_player(
            player("four", stats={"pts": 10.0}),
            SCORING,
            ForecastConfig(
                season="2026",
                scoring=SCORING,
                defaults=ForecastDefaults(scheduled_games_per_week=4, stddev_fraction=0.0, availability_probability=1.0),
            ),
        )
        self.assertEqual(one.fp_per_game, 30.0)
        self.assertEqual(one.weekly_points, 30.0)
        self.assertEqual(four.weekly_points, 10.0)
        self.assertEqual(four.game_count, 4.0)

    def test_negative_week_and_zero_schedule(self):
        negative = estimate_player(
            player("negative", stats={"pts": -4.0}),
            {"pts": 1.0},
            ForecastConfig(
                season="2026",
                scoring={"pts": 1.0},
                defaults=ForecastDefaults(scheduled_games_per_week=1, stddev_fraction=0.0, availability_probability=1.0),
            ),
        )
        zero = estimate_player(
            player("zero", stats={"pts": 20.0}),
            {"pts": 1.0},
            ForecastConfig(
                season="2026",
                scoring={"pts": 1.0},
                defaults=ForecastDefaults(scheduled_games_per_week=0),
            ),
        )
        self.assertEqual(negative.weekly_points, -4.0)
        self.assertEqual(zero.weekly_points, 0.0)
        self.assertTrue(any("No scheduled games" in warning for warning in zero.warnings))


class ForecastFileTests(unittest.TestCase):
    def write_json(self, value):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        with handle:
            json.dump(value, handle)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def base_file(self):
        return {
            "mode": "lock_in",
            "season": "2026",
            "scoring": SCORING,
            "defaults": {
                "scheduled_games_per_week": 3,
                "stddev_fraction": 0.2,
                "availability_probability": 0.9,
            },
            "players": {
                "local": {
                    "weeks": {
                        "0": [
                            {"mean": 12.0, "stddev": 0.0, "availability_probability": 1.0},
                            {"mean": 20.0, "stddev": 0.0, "availability_probability": 1.0},
                        ]
                    }
                }
            },
        }

    def test_local_forecasts_bind_season_and_scoring(self):
        config = load_forecasts(self.write_json(self.base_file()), season="2026", scoring=SCORING)
        projection = estimate_player(player("local", stats={}, projected=False), SCORING, config)
        self.assertEqual(projection.source, "local_forecast")
        self.assertEqual(projection.fp_per_game, 16.0)
        self.assertEqual(projection.weekly_points, 20.0)
        self.assertEqual(projection.game_count, 2.0)
        self.assertTrue(projection.assumptions["scoring_bound"])
        with self.assertRaises(ForecastError):
            config.validate_for("2027", SCORING)
        with self.assertRaises(ForecastError):
            estimate_player(player("local", stats={}, projected=False), {"pts": 2.0}, config)

    def test_actual_scoring_weights_and_missing_bonus_warning(self):
        projected = estimate_player(player("score", stats={"pts": 10.0, "reb": 5.0}), SCORING)
        self.assertEqual(projected.fp_per_game, 15.0)
        self.assertTrue(any("bonus_pt_40p" in warning for warning in projected.warnings))

    def test_adp_only_is_zero_until_local_override(self):
        projected = estimate_player(player("adp", stats={}, projected=False), SCORING)
        self.assertEqual(projected.source, "adp_only")
        self.assertEqual(projected.weekly_points, 0.0)
        self.assertTrue(any("ADP only" in warning for warning in projected.warnings))

    def test_rejects_outcomes_future_scores_and_malformed_numbers(self):
        for field in ("outcomes", "future_scores"):
            raw = self.base_file()
            raw[field] = []
            with self.assertRaises(ForecastError):
                load_forecasts(self.write_json(raw), season="2026", scoring=SCORING)
        raw = self.base_file()
        raw["players"]["local"]["weeks"]["0"][0]["mean"] = "12"
        with self.assertRaises(ForecastError):
            load_forecasts(self.write_json(raw), season="2026", scoring=SCORING)
        raw = self.base_file()
        raw["mode"] = "research"
        with self.assertRaises(ForecastError):
            load_forecasts(self.write_json(raw), season="2026", scoring=SCORING)


if __name__ == "__main__":
    unittest.main()
