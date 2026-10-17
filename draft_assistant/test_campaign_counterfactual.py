"""Small contract tests for the campaign counterfactual workstream."""

from __future__ import annotations

import gzip
import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from . import campaign_counterfactual as counterfactual
from .campaign_counterfactual import (
    build_decision_states,
    run_counterfactuals,
    select_strongest_alternative,
)


@dataclass
class Dataset:
    name: str
    players: tuple[dict[str, object], ...]
    dates: tuple[str, ...] = ()
    scheduled: dict[str, tuple[str, ...]] | None = None
    outcomes: dict[str, dict[str, dict[str, float]]] | None = None


def _players(count: int = 48) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "player_id": f"p{index}",
            "name": f"Player {index}",
            "eligibility": ("C", "F") if index % 3 == 0 else ("G",),
            "stats": {"PTS": float(index)},
            "value": float(index),
        }
        for index in range(1, count + 1)
    )


def _base_history() -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for pick in range(1, 17):
        events.append(
            {
                "pick_number": pick,
                "seat": (pick if pick <= 14 else 15 - (pick - 14)),
                "player_id": f"p{pick}",
            }
        )
    # Seat 13 owns picks 13 and 16 in a 14-team snake.
    events[12]["seat"] = 13
    events[15]["seat"] = 13
    return events


class FakeCampaignEngine:
    def generate_world(self, base_dataset, seed, environment, calibration=None):
        return base_dataset

    def rank_policy(self, players, roster, history, our_seat, policy):
        # Each policy has a deterministic first choice.  Marginal is ordered
        # by exact fixture value, making its top five easy to inspect.
        rows = list(players)
        if policy == "market":
            rows.sort(key=lambda p: int(str(p["player_id"])[1:]))
        elif policy == "foundation":
            rows.sort(key=lambda p: -float(p["value"]))
        elif policy == "custom_ppg":
            rows.sort(key=lambda p: -float(p["value"]) + (0 if p["player_id"] == "p20" else 1000))
        elif policy == "custom_total":
            rows.sort(key=lambda p: -float(p["value"]) + (0 if p["player_id"] == "p19" else 1000))
        elif policy == "positional_vor":
            rows.sort(key=lambda p: -float(p["value"]) + (0 if p["player_id"] == "p18" else 1000))
        else:
            rows.sort(key=lambda p: -float(p["value"]))
        return [{"player_id": p["player_id"], "score": p["value"], "components": {"fixture": p["value"]}} for p in rows]

    def simulate_draft(
        self,
        players,
        our_seat,
        seed,
        opponent_mode,
        policy,
        *,
        prefix=(),
        forced=None,
        deadline=None,
    ):
        forced = dict(forced or {})
        by_id = {p["player_id"]: p for p in players}
        used = [raw["player_id"] if isinstance(raw, dict) else raw for raw in prefix]
        if any(pid not in by_id or used.count(pid) for pid in forced.values()):
            return {"status": "unavailable", "roster": [], "trace": []}
        # Deliberately exercise pair-plan fallback: p48 is stolen at the
        # second pick under every opponent mode.
        if 16 in forced and forced[16] == "p48":
            return {"status": "unavailable", "roster": [], "trace": []}
        history = [
            {"pick_number": index, "seat": 13 if index in (13, 16) else 1, "player_id": pid}
            for index, pid in enumerate(used, 1)
        ]
        next_id = 1
        for pick in range(len(used) + 1, 17):
            if pick in forced:
                chosen = forced[pick]
            else:
                while f"p{next_id}" in used:
                    next_id += 1
                chosen = f"p{next_id}"
            if chosen not in by_id or chosen in used:
                return {"status": "unavailable", "roster": [], "trace": []}
            used.append(chosen)
            history.append({"pick_number": pick, "seat": 13 if pick in (13, 16) else 1, "player_id": chosen})
        roster = [by_id[pid] for event, pid in zip(history, used) if event["seat"] == 13]
        return {"status": "complete", "roster": roster, "history": history, "trace": [{"seed": seed, "mode": opponent_mode}]}

    def evaluate_roster(self, dataset, roster):
        points = sum(float(player["value"]) for player in roster)
        return {"points": points, "weekly_points": points, "usable_starts": points, "benched_points": 0.0, "hindsight_points": points}


class MiniSnakeEngine:
    """Exact two-team snake fixture: picks 1,2,2,1 over five players."""

    _marginal_order = ("a", "b", "e", "c", "d")

    def generate_world(self, base_dataset, seed, environment, calibration=None):
        # Static worlds are intentional here: the fixture tests exact branch
        # enumeration, while production worlds are independent by seed.
        return base_dataset

    def rank_policy(self, players, roster, history, our_seat, policy):
        order = list(self._marginal_order)
        if policy == "foundation":
            order = ["b", "a", "d", "e", "c"]
        elif policy == "custom_ppg":
            order = ["e", "b", "a", "d", "c"]
        elif policy == "custom_total":
            order = ["b", "e", "d", "a", "c"]
        elif policy == "positional_vor":
            order = ["b", "a", "e", "d", "c"]
        elif policy == "market":
            order = ["c", "d", "e", "a", "b"]
        by_id = {str(player["player_id"]): player for player in players}
        return [
            {"player_id": pid, "score": float(sum(by_id[pid]["daily"])), "components": {"mini": 1.0}}
            for pid in order
            if pid in by_id
        ]

    def simulate_draft(self, players, our_seat, seed, opponent_mode, policy, *, prefix=(), forced=None, deadline=None):
        forced = dict(forced or {})
        by_id = {str(player["player_id"]): player for player in players}
        used = [str(raw.get("player_id")) if isinstance(raw, dict) else str(raw) for raw in prefix]
        if len(used) > 4 or len(set(used)) != len(used) or any(pid not in by_id for pid in used):
            return {"status": "unavailable", "roster": [], "history": [], "trace": []}
        history = []
        for pick, pid in enumerate(used, 1):
            history.append({"pick_number": pick, "seat": 1 if pick in (1, 4) else 2, "player_id": pid})
        for pick in range(len(used) + 1, 5):
            if pick in forced:
                chosen = str(forced[pick])
            elif pick in (1, 4):
                ranked = self.rank_policy(players, [by_id[pid] for pid in used if pid in by_id], history, our_seat, policy)
                chosen = next((row["player_id"] for row in ranked if row["player_id"] not in used), None)
            else:
                available = [player for pid, player in by_id.items() if pid not in used]
                chosen = max(available, key=lambda player: (sum(player["daily"]), str(player["player_id"]))) ["player_id"] if available else None
            if chosen is None or chosen not in by_id or chosen in used:
                return {"status": "unavailable", "roster": [], "history": history, "trace": []}
            used.append(chosen)
            history.append({"pick_number": pick, "seat": 1 if pick in (1, 4) else 2, "player_id": chosen})
        roster = [by_id[event["player_id"]] for event in history if event["seat"] == 1]
        return {"status": "complete", "roster": roster, "history": history, "trace": [{"mini": True}]}

    def evaluate_roster(self, dataset, roster):
        # One starter slot per day makes the shared schedule collision explicit.
        daily = [max((float(player["daily"][index]) for player in roster), default=0.0) for index in range(2)]
        points = sum(daily)
        return {"points": points, "weekly_points": {"mini": points}, "usable_starts": 2, "benched_points": 0.0, "hindsight_points": points}


class RetryOnceEngine(FakeCampaignEngine):
    def __init__(self, *, pair: bool = False):
        self.fail_once = True
        self.pair = pair
        self.simulate_calls = 0

    def simulate_draft(self, players, our_seat, seed, opponent_mode, policy, *, prefix=(), forced=None, deadline=None):
        self.simulate_calls += 1
        forced = dict(forced or {})
        should_fail = bool(forced) and ((len(forced) >= 2) if self.pair else (len(forced) == 1))
        if self.fail_once and should_fail:
            self.fail_once = False
            return {"status": "incomplete", "roster": [], "history": [], "trace": []}
        return super().simulate_draft(
            players,
            our_seat,
            seed,
            opponent_mode,
            policy,
            prefix=prefix,
            forced=forced,
            deadline=deadline,
        )


class CampaignCounterfactualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine_patch = patch.object(counterfactual, "_engine", return_value=FakeCampaignEngine())
        cls.engine_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.engine_patch.stop()

    def test_candidate_union_and_decision_state_inputs(self):
        players = _players()
        states = build_decision_states({"history": _base_history()}, players, our_seat=13)
        self.assertEqual([state["pick_number"] for state in states], [13, 16])
        state = states[0]
        self.assertEqual(state["prefix"], [f"p{index}" for index in range(1, 13)])
        self.assertEqual(state["remaining_pool"], state["remaining_player_ids"])
        self.assertIn(state["marginal_choice"], state["candidate_union"])
        self.assertLessEqual(len(state["candidate_union"]), 10)

    def test_selection_uses_discovery_and_allows_negative_regret(self):
        branches = {
            "marginal": {"discovery_summary": {"complete_group": True, "mean_regret": 0.0}},
            "better": {
                "discovery_summary": {"complete_group": True, "mean_regret": 2.0},
                "evaluation_summary": {"complete_group": True, "mean_regret": -1.0},
            },
            "worse": {"discovery_summary": {"complete_group": True, "mean_regret": 1.0}},
        }
        self.assertEqual(select_strongest_alternative(branches, "marginal"), "better")
        self.assertEqual(branches["better"]["evaluation_summary"]["mean_regret"], -1.0)

    def test_seed_partition_is_balanced_and_tiny_exhaustive_fixture_is_truthful(self):
        schedule = counterfactual._continuation_schedule(91, 32)
        discovery = [row for row in schedule if row["split"] == "discovery"]
        evaluation = [row for row in schedule if row["split"] == "evaluation"]
        self.assertEqual((len(discovery), len(evaluation)), (16, 16))
        self.assertEqual({row["seed"] for row in discovery} & {row["seed"] for row in evaluation}, set())
        self.assertLessEqual(max(sum(row["opponent_mode"] == mode for row in schedule) for mode in ("nominal", "market", "custom")) - min(sum(row["opponent_mode"] == mode for row in schedule) for mode in ("nominal", "market", "custom")), 1)

        # Exhaustively enumerate a real two-team snake (1,2,2,1).  The daily
        # score has one starter slot, so shared schedules create collisions;
        # the opponent reacts by taking the highest remaining total each
        # time.  We score every legal first/final roster independently and
        # feed those exact regrets to the discovery selector.
        mini_players = tuple(
            {"player_id": pid, "name": pid, "eligibility": ("G",), "daily": daily, "value": sum(daily)}
            for pid, daily in (
                ("a", (10.0, 0.0)),
                ("b", (14.0, 14.0)),
                ("c", (8.0, 1.0)),
                ("d", (7.0, 7.0)),
                ("e", (1.0, 12.0)),
            )
        )
        mini = MiniSnakeEngine()
        by_id = {player["player_id"]: player for player in mini_players}

        def exact_points(ids):
            return sum(max(by_id[pid]["daily"][day] for pid in ids) for day in range(2))

        def reactive_final_rosters(first_id):
            used = [first_id]
            for _ in (2, 3):
                available = [player for pid, player in by_id.items() if pid not in used]
                used.append(max(available, key=lambda player: (sum(player["daily"]), player["player_id"]))["player_id"])
            return [(first_id, pid) for pid in by_id if pid not in used]

        exact_values = {
            first_id: max(exact_points(roster) for roster in reactive_final_rosters(first_id))
            for first_id in by_id
        }
        baseline = exact_values["a"]
        branch_regrets = {pid: value - baseline for pid, value in exact_values.items()}
        branches = {
            candidate: {"discovery_summary": {"complete_group": True, "mean_regret": regret}}
            for candidate, regret in branch_regrets.items()
        }
        self.assertEqual(select_strongest_alternative(branches, "a"), "b")
        self.assertEqual(exact_values["a"], 22.0)
        self.assertEqual(exact_values["b"], 28.0)

        # Run the same fixture through the resumable counterfactual pipeline;
        # the static world is labeled by the fixture engine and is never used
        # as evidence for production uncertainty claims.
        dataset = Dataset(
            "mini-snake",
            mini_players,
            dates=("2026-10-20", "2026-10-21"),
            scheduled={"2026-10-20": tuple(by_id), "2026-10-21": tuple(by_id)},
            outcomes={
                "2026-10-20": {pid: {"PTS": player["daily"][0]} for pid, player in by_id.items()},
                "2026-10-21": {pid: {"PTS": player["daily"][1]} for pid, player in by_id.items()},
            },
        )
        base = {
            "history": [
                {"pick_number": 1, "seat": 1, "player_id": "a"},
                {"pick_number": 2, "seat": 2, "player_id": "b"},
                {"pick_number": 3, "seat": 2, "player_id": "d"},
                {"pick_number": 4, "seat": 1, "player_id": "e"},
            ]
        }
        with patch.object(counterfactual, "_engine", return_value=mini):
            output = run_counterfactuals(dataset, base, seed=91, continuations=2, our_seat=1)
        self.assertEqual([state["pick_number"] for state in output["states"]], [1, 4])
        first_turn = output["own_turns"][0]
        self.assertEqual(first_turn["discovery_selection"], "b")
        self.assertAlmostEqual(first_turn["branches"]["b"]["discovery_summary"]["mean_regret"], 6.0)
        self.assertTrue(output["outcome_worlds"]["world_fixed"])
        self.assertEqual(output["outcome_worlds"]["conditional_if_fixed"], True)

    def test_run_writes_resumable_gzip_and_pair_availability(self):
        players = _players()
        dataset = Dataset("tiny-exhaustive", players)
        path = Path(__file__).with_name(".counterfactual-test.json.gz")
        try:
            output = run_counterfactuals(
                dataset,
                {"history": _base_history()},
                seed=7,
                continuations=2,
                checkpoint_path=path,
            )
            self.assertTrue(path.exists())
            self.assertTrue(Path(output["trace_store"]).exists())
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["schema_version"], output["schema_version"])
            self.assertFalse(output["reconstruction"]["exact_reconstruction"])
            self.assertTrue(output["outcome_worlds"]["world_fixed"])
            self.assertTrue(output["outcome_worlds"]["conditional_if_fixed"])
            self.assertIn(output["joint13_16"]["status"], {"complete", "incomplete"})
            plans = output["joint13_16"]["plans"]
            self.assertTrue(plans)
            self.assertTrue(any(plan["summary"]["fallback_rows"] >= 0 for plan in plans))
            # The selected branch is discovery-driven; evaluation is reported
            # separately even when it favors the alternative (negative regret).
            turn = output["own_turns"][0]
            self.assertIn("evaluation_selected_branch", turn)
        finally:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".traces.jsonl.gz").unlink(missing_ok=True)

    def test_pair_rows_retain_baseline_status_and_balanced_partial_values(self):
        dataset = Dataset("pair-metadata", _players())
        path = Path(__file__).with_name(".postrun-pair-metadata.json.gz")
        try:
            output = run_counterfactuals(dataset, {"history": _base_history()}, seed=13, continuations=2, checkpoint_path=path)
        finally:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".traces.jsonl.gz").unlink(missing_ok=True)
        plans = output["joint13_16"]["plans"]
        self.assertTrue(plans)
        self.assertTrue(all("baseline_status" in row for plan in plans for row in plan["rows"]))
        self.assertTrue(all(plan["summary"]["complete_group"] for plan in plans))
        self.assertTrue(all(plan["summary"]["actual_target_used"] == 2 for plan in plans))

    def test_resume_retries_incomplete_single_and_archives_attempt(self):
        dataset = Dataset("retry-single", _players())
        engine = RetryOnceEngine()
        path = Path(__file__).with_name(".postrun-retry-single.json.gz")
        try:
            with patch.object(counterfactual, "_engine", return_value=engine):
                first = run_counterfactuals(dataset, {"history": _base_history()}, seed=17, continuations=2, checkpoint_path=path)
                incomplete = [
                    branch
                    for turn in first["own_turns"]
                    for branch in turn["branches"].values()
                    if any(row["status"] == "incomplete" for row in branch["discovery_rows"] + branch["evaluation_rows"])
                ]
                self.assertTrue(incomplete)
                second = run_counterfactuals(dataset, {"history": _base_history()}, seed=17, continuations=2, checkpoint_path=path)
        finally:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".traces.jsonl.gz").unlink(missing_ok=True)
        retried = [branch for turn in second["own_turns"] for branch in turn["branches"].values() if branch.get("retry_attempts")]
        self.assertTrue(retried)
        self.assertTrue(all(row["status"] == "complete" for branch in retried for row in branch["discovery_rows"] + branch["evaluation_rows"]))
        self.assertTrue(any(branch["discovery_summary"]["complete_group"] for branch in retried))

    def test_joint_resume_reuses_valid_rows_and_retries_only_incomplete_pair(self):
        dataset = Dataset("retry-pair", _players())
        engine = RetryOnceEngine(pair=True)
        path = Path(__file__).with_name(".postrun-retry-pair.json.gz")
        try:
            with patch.object(counterfactual, "_engine", return_value=engine):
                first = run_counterfactuals(dataset, {"history": _base_history()}, seed=19, continuations=2, checkpoint_path=path)
                first_calls = engine.simulate_calls
                second = run_counterfactuals(dataset, {"history": _base_history()}, seed=19, continuations=2, checkpoint_path=path)
                retry_calls = engine.simulate_calls - first_calls
        finally:
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".traces.jsonl.gz").unlink(missing_ok=True)
        self.assertGreater(first_calls, 0)
        self.assertGreater(retry_calls, 0)
        self.assertTrue(any(plan.get("retry_attempts") for plan in second["joint13_16"]["plans"]))
        self.assertTrue(all("baseline_status" in row for plan in second["joint13_16"]["plans"] for row in plan["rows"]))


if __name__ == "__main__":
    unittest.main()
