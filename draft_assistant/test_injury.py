from __future__ import annotations

from dataclasses import replace

import pytest

from .contracts import InjuryPolicy, LeagueConfig, LivePlayer
from .injury import injury_factor
from .marginal_lineup import marginal_fingerprint, rank_marginal
from .test_marginal_lineup import filler_pool, make, observation
from .valuation import _games_factor, rank_players, strategy_fingerprint, value_player

OFF = LeagueConfig()
ON = LeagueConfig(injury_policy=InjuryPolicy(enabled=True, out_factor=0.4, dtd_factor=0.9))


def obs(*players: LivePlayer):
    return observation([], [*players, *filler_pool()])


def cand(candidates, pid: str):
    return next(c for c in candidates if c.player_id == pid)


def player(status: str | None, pid: str = "p") -> LivePlayer:
    return replace(make(pid, ("F",), 20, rank=5, team="AAA"), injury_status=status)


def test_o_dtd_healthy_give_expected_factors() -> None:
    assert _games_factor(player("O"), ON.injury_policy)[0] == pytest.approx(0.4)
    assert _games_factor(player("out"), ON.injury_policy)[0] == pytest.approx(0.4)
    assert _games_factor(player("DTD"), ON.injury_policy)[0] == pytest.approx(0.9)
    assert _games_factor(player("Day-To-Day"), ON.injury_policy)[0] == pytest.approx(0.9)
    for healthy in (None, "", "ACTIVE", "healthy"):
        assert _games_factor(player(healthy), ON.injury_policy) == (1.0, ())


def test_haircut_multiplies_projected_gp_and_availability() -> None:
    half = replace(player("O"), projected_gp=41)
    assert _games_factor(half, ON.injury_policy)[0] == pytest.approx(0.5 * 0.4)
    avail = replace(player("DTD"), projected_gp=None, availability=0.8)
    assert _games_factor(avail, ON.injury_policy)[0] == pytest.approx(0.8 * 0.9)


def test_unknown_status_is_unchanged_and_warns() -> None:
    factor, warnings = injury_factor(player("QUARANTINED"), ON.injury_policy)
    assert factor == 1.0 and any("unknown injury status" in w for w in warnings)
    value = value_player(player("QUARANTINED"), ON)
    assert value.season_value == value_player(player(None), ON).season_value
    assert any("unknown injury status" in w for w in value.warnings)


def test_disabled_policy_ignores_status_and_adds_no_components_or_warnings() -> None:
    base = cand(rank_players(obs(player(None, "a")), OFF), "a")
    injured = cand(rank_players(obs(player("O", "a")), OFF), "a")
    assert injured == base
    assert "injury_adjustment" not in injured.components
    assert not injured.warnings or all("injury" not in w for w in injured.warnings)
    assert injury_factor(player("O"), OFF.injury_policy) == (1.0, ())
    assert strategy_fingerprint(OFF) == strategy_fingerprint(LeagueConfig(injury_policy=InjuryPolicy()))
    assert marginal_fingerprint(ON) != marginal_fingerprint(OFF)
    assert strategy_fingerprint(ON) != strategy_fingerprint(OFF)


def test_enabled_without_injury_data_matches_disabled_scores() -> None:
    pool = [make(f"x{i}", ("G",), 10 + i, rank=i + 1) for i in range(6)]
    off = rank_players(obs(*pool), OFF)
    on = rank_players(obs(*pool), ON)
    assert [(c.player_id, c.score) for c in on] == [(c.player_id, c.score) for c in off]
    assert all(c.components["injury_adjustment"] == 0.0 and c.components["injury_factor"] == 1.0 for c in on)


def test_foundation_candidate_exposes_injury_adjustment_line() -> None:
    healthy = cand(rank_players(obs(player(None, "a")), ON), "a")
    out = cand(rank_players(obs(player("O", "a")), ON), "a")
    custom = out.components["custom_fp"]
    assert out.components["injury_factor"] == pytest.approx(0.4)
    assert out.components["injury_adjustment"] == pytest.approx(custom * (0.4 - 1.0))
    assert out.components["season_value"] == pytest.approx(healthy.components["season_value"] * 0.4)
    assert "injury adjustment" in out.explanation


def test_injured_star_drops_below_healthy_peer_without_position_rule() -> None:
    star_out = replace(make("star", ("C",), 26, rank=1), injury_status="O")
    peer = make("peer", ("C",), 20, rank=2)
    on = [c.player_id for c in rank_players(obs(star_out, peer), ON)]
    off = [c.player_id for c in rank_players(obs(star_out, peer), OFF)]
    assert on.index("peer") < on.index("star")
    assert off.index("star") < off.index("peer")


def test_marginal_path_applies_haircut_and_reports_weekly_adjustment() -> None:
    pool = filler_pool()
    healthy = make("star", ("F",), 26, rank=1, team="AAA")
    out = replace(healthy, injury_status="O")
    on_h = rank_marginal(observation([], [healthy, *pool]), ON)
    on_o = rank_marginal(observation([], [out, *pool]), ON)
    off_o = rank_marginal(observation([], [out, *pool]), OFF)
    row = lambda result: next(c for c in result.candidates if c.player_id == "star")
    assert row(on_o).components["injury_factor"] == pytest.approx(0.4)
    assert row(on_o).components["injury_adjustment"] < 0
    assert row(on_o).components["expected_fp_per_game"] == pytest.approx(row(on_h).components["expected_fp_per_game"] * 0.4)
    assert row(on_o).score < row(on_h).score
    assert "injury_adjustment" not in row(off_o).components
    assert row(off_o).score == pytest.approx(row(on_h).score)


def test_policy_validation_rejects_out_of_range_factors() -> None:
    with pytest.raises(ValueError):
        InjuryPolicy(enabled=True, out_factor=1.5)
    with pytest.raises(ValueError):
        InjuryPolicy(dtd_factor=-0.1)
