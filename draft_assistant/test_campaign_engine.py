from __future__ import annotations

import unittest
from datetime import date, timedelta
from itertools import product
from types import SimpleNamespace

from mock_draft.fixture import PlayerRecord, load_player_records

from .campaign_engine import _daily_allocation, _opponent_rank, _player_ppg, _world_outcome, evaluate_roster, generate_world, rank_policy, simulate_draft
from .contracts import PickEvent


def _players() -> tuple[PlayerRecord, ...]:
    specs = (
        ("C1", ("C",), 20),
        ("C2", ("C",), 10),
        ("G1", ("G",), 20),
        ("G2", ("G",), 19),
        ("G3", ("G",), 1),
        ("F1", ("F",), 20),
        ("F2", ("F",), 19),
        ("F3", ("F",), 1),
        ("GF1", ("G", "F"), 18),
        ("GF2", ("G", "F"), 17),
    )
    return tuple(
        PlayerRecord(
            player_id=player_id,
            name=player_id,
            eligibility=eligibility,
            stats={"PTS": float(points), "REB": 0.0, "AST": 0.0},
            projected_availability=1.0,
            market_rank=index,
            adp=index,
            schedule_group="TEAM-A",
            source_label="test",
        )
        for index, (player_id, eligibility, points) in enumerate(specs, start=1)
    )


class CampaignEngineTests(unittest.TestCase):
    def test_exact_forecast_allocation_and_realized_hindsight(self) -> None:
        players = _players()
        roster = players
        iso_date = "2026-10-20"
        realized = {
            item.player_id: {"PTS": float(item.stats["PTS"])} for item in players
        }
        realized.update({"C2": {"PTS": 80.0}, "G3": {"PTS": 100.0}, "F3": {"PTS": 90.0}})
        dataset = SimpleNamespace(
            dates=(iso_date,),
            scheduled={iso_date: tuple(item.player_id for item in players)},
            outcomes={iso_date: realized},
            missing_scoring_fields=(),
        )
        result = evaluate_roster(dataset, roster)
        self.assertGreater(result["hindsight_points"], result["points"])
        self.assertGreater(result["benched_points"], 0.0)
        self.assertEqual(result["weekly_points"], {"2026-W43": result["points"]})

    def test_rank_policy_exposes_numeric_components(self) -> None:
        players = _players()
        for policy in ("foundation", "custom_ppg", "custom_total", "positional_vor", "market"):
            ranked = rank_policy(players, (), (), 1, policy)
            self.assertTrue(ranked)
            self.assertTrue(all(isinstance(row["score"], float) for row in ranked))
            self.assertTrue(all(isinstance(row["components"], dict) for row in ranked))

    def test_world_schedule_is_correlated_and_seed_stable(self) -> None:
        players = _players()
        base = SimpleNamespace(
            name="fixture",
            players=players,
            dates=(),
            outcomes={"future": {"C1": {"PTS": 9999.0}}},
            provenance={"source": "test"},
            missing_scoring_fields=(),
        )
        first = generate_world(base, 101, "nominal")
        second_base = SimpleNamespace(**{**base.__dict__, "outcomes": {"other": {"C1": {"PTS": -1.0}}}})
        second = generate_world(second_base, 101, "nominal")
        self.assertEqual(first.scheduled, second.scheduled)
        self.assertEqual(first.outcomes, second.outcomes)
        self.assertEqual(len(first.dates), 182)
        self.assertEqual(sum("C1" in ids for ids in first.scheduled.values()), 82)
        self.assertEqual(first.missing_scoring_fields, ("PF", "TF", "EJ"))

    def test_world_environment_and_calibration_are_explicit(self) -> None:
        players = tuple(
            PlayerRecord(
                player_id=item.player_id,
                name=item.name,
                eligibility=item.eligibility,
                stats=dict(item.stats),
                projected_availability=0.70,
                market_rank=item.market_rank,
                adp=item.adp,
                schedule_group=item.schedule_group,
            )
            for item in _players()
        )
        base = SimpleNamespace(name="fixture", players=players, dates=(), outcomes={}, provenance={}, missing_scoring_fields=())
        moderate = generate_world(base, 5, "moderate")
        injury = generate_world(base, 5, "injury", calibration={"player_bias_sd": 0.25})
        self.assertEqual(injury.provenance["environment"], "injury")
        self.assertIn("assumed", moderate.provenance["calibration"])
        self.assertEqual(len(moderate.dates), 182)
        self.assertNotEqual(moderate.outcomes, injury.outcomes)
        with self.assertRaises(ValueError):
            generate_world(base, 5, "injury", calibration={"unknown": 1.0})

    def test_moderate_world_gp_tracks_projected_availability(self) -> None:
        observed_means = {}
        for availability in (0.0, 0.1, 0.5, 0.9, 1.0):
            player = PlayerRecord(
                player_id="availability-test",
                name="availability-test",
                eligibility=("G",),
                stats={"PTS": 20.0, "STL": 0.3},
                projected_availability=availability,
                market_rank=1,
                adp=1,
                schedule_group="TEAM-A",
            )
            base = SimpleNamespace(name="availability", players=(player,), dates=(), outcomes={}, provenance={}, missing_scoring_fields=())
            games = []
            for seed in range(100):
                world = generate_world(base, seed, "moderate")
                games.append(sum(player.player_id in rows for rows in world.outcomes.values()))
            observed_means[availability] = sum(games) / len(games)
        for availability, mean_games in observed_means.items():
            self.assertLessEqual(abs(mean_games - 82.0 * availability), 2.0)

    def test_opponent_modes_use_normalized_contradictory_signals(self) -> None:
        players = list(_players())
        # Contradictory market and production order, with enough shared
        # eligibility to keep the comparison about the opponent model.
        for index, item in enumerate(players):
            players[index] = PlayerRecord(
                player_id=item.player_id,
                name=item.name,
                eligibility=("G", "F") if index >= 2 else ("C",),
                stats={"PTS": float(100 - index * 10)},
                projected_availability=1.0,
                market_rank=float(10 - index),
                adp=float(10 - index),
            )
        rosters = {1: []}
        market_first = _opponent_rank(players, 1, 1, rosters, "market", 3)[0][1].player_id
        custom_first = _opponent_rank(players, 1, 1, rosters, "custom", 3)[0][1].player_id
        self.assertEqual(market_first, "GF2")
        self.assertEqual(custom_first, "C1")

    def test_custom_early_rank_preserves_elite_production(self) -> None:
        players = load_player_records("mock_draft/data/players_2026-27_espn_projection.csv")
        rosters = {seat: [] for seat in range(1, 15)}
        jokic = next(item.player_id for item in players if item.name == "Nikola Jokic")
        for seat in range(1, 15):
            ranked = _opponent_rank(players, seat, 1, rosters, "custom", 2026101400 + seat)
            self.assertLess([item.player_id for _, item in ranked].index(jokic), 3)

    def test_opponent_fit_bonus_waits_for_roster_foundation(self) -> None:
        players = tuple(
            PlayerRecord(
                player_id=player_id,
                name=player_id,
                eligibility=eligibility,
                stats={"PTS": 50.0},
                projected_availability=1.0,
                market_rank=index,
                adp=index,
            )
            for index, (player_id, eligibility) in enumerate(
                (("C1", ("C",)), ("G1", ("G",)), ("G2", ("G",)), ("F1", ("F",))),
                start=1,
            )
        )
        early = dict((item.player_id, score) for score, item in _opponent_rank(players, 1, 3, {1: []}, "custom", 44))
        late = dict((item.player_id, score) for score, item in _opponent_rank(players, 1, 3, {1: [players[1], players[2]]}, "custom", 44))
        self.assertGreater(late["C1"] - early["C1"], 0.005)
        self.assertGreater(late["F1"] - early["F1"], 0.01)
        self.assertGreater((late["F1"] - early["F1"]) - (late["C1"] - early["C1"]), 0.006)

    def test_daily_dp_matches_exhaustive_assignments_with_empty_slots(self) -> None:
        roster = _players()
        active = {"C1", "G1", "F1", "GF1"}
        chosen, _, score = _daily_allocation(roster, active, _player_ppg)
        by_index = {item.player_id: index for index, item in enumerate(roster)}
        options = [by_index[player_id] for player_id in sorted(active)] + [-1]
        slots = ("C", "G", "G", "F", "F", "UTIL")
        best = float("-inf")
        for assignment in product(options, repeat=len(slots)):
            selected = [index for index in assignment if index >= 0]
            if len(selected) != len(set(selected)):
                continue
            if any(slot != "UTIL" and slot not in roster[index].eligibility for slot, index in zip(slots, assignment) if index >= 0):
                continue
            best = max(best, sum(_player_ppg(roster[index]) for index in selected))
        self.assertAlmostEqual(score, best)
        self.assertLessEqual(len(chosen), 6)

    def test_evaluation_is_invariant_to_draft_roster_order(self) -> None:
        players = tuple(
            PlayerRecord(
                player_id=item.player_id,
                name=item.name,
                eligibility=item.eligibility,
                stats={"PTS": 10.0},
                projected_availability=1.0,
                market_rank=item.market_rank,
                adp=item.adp,
            )
            for item in _players()
        )
        iso_date = "2026-10-20"
        outcomes = {iso_date: {item.player_id: {"PTS": float(index * 10)} for index, item in enumerate(players, start=1)}}
        dataset = SimpleNamespace(
            dates=(iso_date,),
            scheduled={iso_date: tuple(item.player_id for item in players)},
            outcomes=outcomes,
            missing_scoring_fields=(),
        )
        forward = evaluate_roster(dataset, players)
        reverse = evaluate_roster(dataset, tuple(reversed(players)))
        self.assertEqual(forward["points"], reverse["points"])
        self.assertEqual(forward["hindsight_points"], reverse["hindsight_points"])
        self.assertEqual(forward["allocations"], reverse["allocations"])

    def test_low_count_rounding_preserves_stochastic_mean(self) -> None:
        player = PlayerRecord(
            player_id="LOW-STL",
            name="LOW-STL",
            eligibility=("G",),
            stats={"PTS": 10.0, "STL": 0.3},
            projected_availability=1.0,
            market_rank=1,
            adp=1,
        )
        observed = []
        for day_offset in range(1000):
            iso_date = (date(2026, 1, 1) + timedelta(days=day_offset)).isoformat()
            outcome = _world_outcome(
                player,
                91,
                iso_date,
                "TEST",
                "moderate",
                day_offset,
                {"player_bias_sd": 0.0, "daily_sd": 0.0, "team_shock_sd": 0.0},
            )
            observed.append(outcome["STL"])
        self.assertAlmostEqual(sum(observed) / len(observed), 0.3, delta=0.05)

    def test_prefix_replay_and_unavailable_forced_pick(self) -> None:
        # A small but legal 140-player pool exercises prefix validation without
        # relying on any random state from a prior simulation.
        source = list(_players())
        while len(source) < 140:
            index = len(source) + 1
            pattern = ("G",) if index % 3 == 0 else (("F",) if index % 3 == 1 else ("C", "F"))
            source.append(
                PlayerRecord(
                    player_id=f"P{index}",
                    name=f"P{index}",
                    eligibility=pattern,
                    stats={"PTS": 5.0, "REB": 2.0, "AST": 1.0},
                    projected_availability=1.0,
                    market_rank=index,
                    adp=index,
                    schedule_group=f"T{index % 14}",
                )
            )
        first = simulate_draft(tuple(source), 1, 7, "nominal", "custom_ppg", forced={1: "C1"})
        self.assertEqual(first["status"], "complete")
        event = first["history"][0]
        replay = simulate_draft(tuple(source), 1, 7, "nominal", "custom_ppg", prefix=(event,), forced={1: "C1"})
        self.assertEqual(replay["status"], "complete")
        unavailable = simulate_draft(tuple(source), 1, 7, "nominal", "custom_ppg", forced={1: "C1", 2: "C1"})
        self.assertEqual(unavailable["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
