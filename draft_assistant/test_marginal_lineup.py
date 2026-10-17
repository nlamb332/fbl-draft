"""Unit, wiring and performance checks for ``marginal-lineup-v1``."""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mock_draft.fixture import synthetic_fixture

from . import marginal_lineup
from .contracts import DraftObservation, LeagueConfig, LivePlayer, RoomIdentity, utc_now
from .marginal_lineup import (
    ALGORITHM_VERSION,
    MarginalParams,
    double_triple_probabilities,
    impute_forecast_gaps,
    lineup_points,
    marginal_fingerprint,
    promotion_valid,
    rank_marginal,
    replacement_levels,
    starter_demand,
)
from .validation_runner import _live
from .valuation import rank_players, recommend, strategy_fingerprint

CONFIG = LeagueConfig()
ROOM = RoomIdentity("1000000000", "2027", "15", True)
ORDER = tuple(CONFIG.our_team if seat == 4 else str(seat) for seat in range(1, 15))
FULL_STATS = {"PTS": 18, "REB": 5, "AST": 3, "FGM": 6, "FGA": 12, "FTM": 3, "FTA": 4, "3PM": 1, "STL": 1, "BLK": 0.5, "TO": 2,
              "PF": 2, "TF": 0, "EJ": 0, "DD": 0, "TD": 0, "QD": 0}


def make(pid: str, positions: tuple[str, ...], pts: float, *, rank: float | None = None, team: str | None = None) -> LivePlayer:
    stats = dict(FULL_STATS)
    stats["PTS"] = pts
    return LivePlayer(pid, pid, positions, stats, projected_gp=82, draft_rank=rank, nba_team=team, source="test")


def observation(roster, available, *, pick: int = 40, history=()) -> DraftObservation:
    return DraftObservation(
        session_id="s", room=ROOM, observed_at=utc_now(), revision=7, phase="draft", current_pick=pick,
        on_clock_team=CONFIG.our_team, pick_order=ORDER, history=tuple(history), roster=tuple(roster),
        available_players=tuple(available), available_pool_complete=True, history_complete=True,
    )


def filler_pool() -> list[LivePlayer]:
    pool = []
    for index in range(30):
        pool.append(make(f"fg{index}", ("G",), 8 + index * 0.1, rank=100 + index))
        pool.append(make(f"ff{index}", ("F",), 8 + index * 0.1, rank=140 + index))
        pool.append(make(f"fc{index}", ("C", "F"), 8 + index * 0.1, rank=180 + index))
    return pool


def test_two_c_only_starters_make_a_third_c_only_worth_less_than_equal_gf() -> None:
    roster = [
        make("c1", ("C",), 22), make("c2", ("C",), 21),
        make("g1", ("G",), 20), make("g2", ("G",), 19),
        make("f1", ("F",), 20), make("f2", ("F",), 19),
    ]
    c_only = make("cand-c", ("C",), 24, rank=50)
    wing = make("cand-gf", ("G", "F"), 24, rank=51)
    result = rank_marginal(observation(roster, [c_only, wing, *filler_pool()], pick=60), CONFIG)
    assert result.status == "ranked"
    scores = {item.player_id: item for item in result.candidates}
    assert scores["cand-c"].components["raw_weekly_value"] == pytest.approx(scores["cand-gf"].components["raw_weekly_value"])
    assert scores["cand-gf"].components["marginal_lineup_weekly"] > scores["cand-c"].components["marginal_lineup_weekly"]
    assert scores["cand-c"].components["c_only"] == 1.0 and scores["cand-gf"].components["c_only"] == 0.0
    assert [item.player_id for item in result.candidates].index("cand-gf") < [item.player_id for item in result.candidates].index("cand-c")


def test_empty_roster_follows_raw_value() -> None:
    pool = [make("g-high", ("G",), 30, rank=5), make("g-low", ("G",), 22, rank=6), make("f-mid", ("F",), 26, rank=7),
            make("c-top", ("C", "F"), 31, rank=8), *filler_pool()]
    result = rank_marginal(observation([], pool, pick=4), CONFIG)
    order = [item.player_id for item in result.candidates]
    assert order.index("g-high") < order.index("g-low")
    top_raw = sorted(result.candidates[:4], key=lambda item: -item.components["raw_weekly_value"])
    assert {item.player_id for item in result.candidates[:3]} <= {"g-high", "c-top", "f-mid", "g-low"}
    assert top_raw[0].components["raw_weekly_value"] >= top_raw[-1].components["raw_weekly_value"]


def test_lineup_is_exact_optimum_for_known_cases() -> None:
    # C=1, G=2, F=4.  Three C-only players can fill only C and UTIL.
    assert lineup_points([(50.0, 1), (40.0, 1), (30.0, 1), (20.0, 2)]) == pytest.approx(110.0)
    # C/F players cannot fill the two G slots: only C, F, F and UTIL are usable.
    assert lineup_points([(50.0, 5), (40.0, 5), (30.0, 5), (20.0, 5), (10.0, 5), (5.0, 5)]) == pytest.approx(140.0)
    assert lineup_points([]) == 0.0


def test_replacement_levels_monotone_in_demand_and_recompute_as_pool_depletes() -> None:
    values = {f"p{i}": 60.0 - i for i in range(60)}
    eligibility = {pid: ("G",) for pid in values}
    shallow = replacement_levels(values, eligibility, {"G": 10}, picks_made=0)["G"][0]
    deep = replacement_levels(values, eligibility, {"G": 30}, picks_made=0)["G"][0]
    assert deep < shallow
    assert replacement_levels(values, eligibility, {"G": 30})["G"][1] == "p29"
    depleted = {pid: value for pid, value in values.items() if int(pid[1:]) >= 5}
    after = replacement_levels(depleted, eligibility, {"G": 30}, picks_made=0)["G"][0]
    assert after < deep
    # Demand consumed by picks shrinks the depth, raising the level.
    assert replacement_levels(values, eligibility, {"G": 30}, picks_made=15)["G"][0] > deep
    demand = starter_demand(CONFIG)
    assert demand["C"] == pytest.approx(16.8) and demand["G"] == pytest.approx(33.6) and sum(demand.values()) == pytest.approx(84.0)
    bigger = starter_demand(CONFIG, MarginalParams(util_share=2.0))
    assert all(bigger[key] > demand[key] for key in demand)


def test_imputation_is_deterministic_tagged_and_never_overwrites_supplied_stats() -> None:
    gap = LivePlayer("x", "X", ("C",), {"PTS": 20.0, "REB": 11.0, "AST": 3.0, "STL": 1.0, "BLK": 1.5, "MIN": 30.0},
                     projected_gp=70, missing_stats=("EJ", "PF", "TF", "DD", "TD", "QD"), source="test")
    first = impute_forecast_gaps(gap)
    second = impute_forecast_gaps(gap)
    assert first == second
    player, tags, filled = first
    assert set(filled) == {"PF", "DD", "TD"}
    assert player.stats["PF"] == pytest.approx(3.4 * 30.0 / 36.0, abs=1e-3)
    assert any(tag.startswith("imputed PF=") for tag in tags) and any("imputed DD=" in tag for tag in tags)
    assert "PF" not in player.missing_stats and "EJ" in player.missing_stats
    assert gap.stats == {"PTS": 20.0, "REB": 11.0, "AST": 3.0, "STL": 1.0, "BLK": 1.5, "MIN": 30.0}  # input untouched
    supplied = replace(gap, stats={**gap.stats, "PF": 0.0, "DD": 0.0, "TD": 0.0}, missing_stats=())
    kept, notes, nothing = impute_forecast_gaps(supplied)
    assert kept is supplied and notes == () and nothing == {}
    partial = replace(gap, stats={**gap.stats, "PF": 1.25})
    assert impute_forecast_gaps(partial)[0].stats["PF"] == 1.25
    estimated = LivePlayer("y", "Y", ("G",), {"PTS": 12.0, "REB": 3.0, "AST": 4.0}, source="test")
    assert impute_forecast_gaps(estimated)[2]["PF"] > 0
    double, triple = double_triple_probabilities({"PTS": 28.0, "REB": 12.7, "AST": 10.1})
    assert 0.5 < double < 1.0 and 0.0 < triple < double
    assert double_triple_probabilities({"PTS": 6.0, "REB": 2.0, "AST": 1.0}) == (pytest.approx(0.0, abs=1e-3), pytest.approx(0.0, abs=1e-6))


def synthetic_observation(pick: int, picked: int) -> DraftObservation:
    live = [_live(player) for player in synthetic_fixture(player_count=195, seed=20260001)]
    roster = live[:picked]
    return observation(roster, live[picked + 14 :], pick=pick)


def test_budget_overrun_returns_foundation_and_reports_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    obs = synthetic_observation(pick=32, picked=2)
    ticks = iter(range(0, 10_000, 10))
    monkeypatch.setattr(marginal_lineup, "time", SimpleNamespace(perf_counter=lambda: float(next(ticks))))
    result = rank_marginal(obs, CONFIG)
    assert result.status == "fallback" and result.fallback_reason
    assert [c.player_id for c in result.candidates] == [c.player_id for c in rank_players(obs, CONFIG)]
    assert "fallback to foundation" in result.explanation


def artifact(config: LeagueConfig = CONFIG, *, lower: float = 5.0, loss: float = -0.01, status: str = "promotable", **overrides) -> dict:
    params = MarginalParams()
    rows = [
        {"catalog": catalog, "scenario": "nominal", "variant": "marginal", "independent_seed_clusters": 100, "seat_rows": 1400,
         "paired_bootstrap_95_lower": lower, "worst_seat_loss_fraction": loss}
        for catalog in ("synthetic", "real")
    ]
    payload = {
        "policy": ALGORITHM_VERSION, "status": status, "algorithm_version": ALGORITHM_VERSION,
        "config_fingerprint": marginal_fingerprint(config, params), "params": params.as_dict(),
        "metadata": {"teams": 14, "opponents": 13, "picks_per_draft": 140, "seats_per_seed": 14, "foundation_vs_marginal_lineup": True},
        "summaries": rows,
    }
    payload.update(overrides)
    return payload


def test_promotion_gate_requires_both_catalogs_lower_bound_and_seat_loss() -> None:
    assert promotion_valid(artifact(), CONFIG)
    assert not promotion_valid(artifact(lower=-0.1), CONFIG)
    assert not promotion_valid(artifact(loss=-0.06), CONFIG)
    assert not promotion_valid(artifact(status="advisory"), CONFIG)
    assert not promotion_valid(artifact(config_fingerprint="0" * 64), CONFIG)
    one_catalog = artifact()
    one_catalog["summaries"] = one_catalog["summaries"][:1]
    assert not promotion_valid(one_catalog, CONFIG)
    short = artifact()
    short["summaries"][1]["independent_seed_clusters"] = 99
    assert not promotion_valid(short, CONFIG)
    tampered = artifact()
    tampered["params"] = {**tampered["params"], "vor_weight": 3.0}
    assert not promotion_valid(tampered, CONFIG)


def test_default_stays_foundation_and_selection_only_through_artifact() -> None:
    obs = synthetic_observation(pick=32, picked=2)
    foundation = [c.player_id for c in rank_players(obs, CONFIG)]
    plain = recommend(obs, CONFIG)
    assert [c.player_id for c in plain.candidates] == foundation and plain.lookahead_status == "advisory"
    advisory_payload = artifact(lower=-1.0)
    advisory = recommend(obs, CONFIG, promotion_path=advisory_payload)
    assert [c.player_id for c in advisory.candidates] == foundation
    assert advisory.lookahead_status == "advisory" and "promotion gate not passed" in advisory.explanation
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "promotion.json"
        path.write_text(json.dumps(artifact()), encoding="utf-8")
        promoted = recommend(obs, CONFIG, promotion_path=str(path))
        assert promoted.lookahead_status == "promoted" and promoted.explanation.startswith("promoted marginal-lineup-v1")
        top = promoted.candidates[0]
        for key in ("custom_fp", "replacement_level_fp_per_game", "marginal_lineup_weekly", "raw_weekly_value", "imputed_field_count"):
            assert key in top.components
        assert "marginal lineup" in top.explanation
        other = Path(directory) / "other.json"
        other.write_text(json.dumps({"policy": "something-else"}), encoding="utf-8")
        assert [c.player_id for c in recommend(obs, CONFIG, promotion_path=str(other)).candidates] == foundation


def test_promoted_recommendation_falls_back_to_foundation_when_budget_fails() -> None:
    obs = synthetic_observation(pick=32, picked=2)
    tiny = replace(CONFIG, recommendation_budget_seconds=1e-9)
    result = recommend(obs, tiny, promotion_path=artifact(tiny))
    # The marginal computation needs well over the 1 ms floor on a full pool.
    assert result.lookahead_status == "fallback"
    assert "fallback to foundation" in result.explanation
    assert [c.player_id for c in result.candidates] == [c.player_id for c in rank_players(obs, tiny)]


def test_foundation_fingerprint_is_unchanged() -> None:
    frozen = json.loads((Path(__file__).parent / "validation" / "heldout-v2" / "validation_summary.json").read_text(encoding="utf-8"))
    assert strategy_fingerprint(CONFIG) == frozen["metadata"]["config_fingerprint"]
    assert marginal_fingerprint(CONFIG) != strategy_fingerprint(CONFIG)
    assert marginal_fingerprint(CONFIG, MarginalParams(weeks=6)) != marginal_fingerprint(CONFIG)


def test_checked_in_promotion_artifact_matches_gate_and_frozen_params() -> None:
    directory = Path(__file__).parent / "validation" / "marginal-lineup-v1"
    payload = json.loads((directory / "promotion_artifact.json").read_text(encoding="utf-8"))
    frozen = json.loads((directory / "frozen_params.json").read_text(encoding="utf-8"))
    assert frozen["params"] == MarginalParams().as_dict() == payload["params"]
    assert promotion_valid(payload, CONFIG) == (payload["status"] == "promotable")
    rec = recommend(synthetic_observation(pick=32, picked=2), CONFIG, promotion_path=payload)
    assert rec.lookahead_status == ("promoted" if payload["status"] == "promotable" else "advisory")


@pytest.mark.parametrize(("pick", "picked"), [(13, 0), (128, 9)])
def test_p95_recommendation_time_within_budget(pick: int, picked: int) -> None:
    live = [_live(player) for player in synthetic_fixture(player_count=195, seed=20260002)]
    pool = live if pick == 13 else live[40:]
    roster = live[:picked]
    obs = observation(roster, [p for p in pool if p not in roster], pick=pick)
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        result = rank_marginal(obs, CONFIG)
        samples.append(time.perf_counter() - started)
        assert result.status == "ranked"
    samples.sort()
    assert samples[int(0.95 * len(samples)) - 1] <= CONFIG.recommendation_budget_seconds
