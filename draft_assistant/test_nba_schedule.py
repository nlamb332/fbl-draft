"""Real-NBA-schedule checks (issue #8): data file, light/heavy weeks, fallback, flag."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from . import nba_schedule
from .marginal_lineup import (
    MarginalParams,
    activity_vector,
    game_days,
    marginal_fingerprint,
    rank_marginal,
)
from .test_marginal_lineup import CONFIG, filler_pool, make, observation

MON = date(2026, 10, 26)


def _write(tmp_path, monkeypatch, plan: dict[str, list[int]]):
    """plan: team -> day offsets from MON (three Monday-Sunday weeks)."""

    games = [[(MON + timedelta(days=d)).isoformat(), team, "ZZZ"] for team, days in plan.items() for d in days]
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"games": games}), encoding="utf-8")
    monkeypatch.setattr(nba_schedule, "DEFAULT_SCHEDULE_PATH", path)
    return path


PLAN = {
    "AAA": [0, 1, 2, 3, 4, 7, 9, 14, 15, 17, 20],  # heavy 5, light 2, 4 with a back-to-back
    "BBB": [0, 2, 4, 7, 9, 11, 14, 16, 18],  # flat 3 games a week
}


def test_checked_in_schedule_is_complete_and_cited() -> None:
    payload = json.loads(nba_schedule.DEFAULT_SCHEDULE_PATH.read_text(encoding="utf-8"))
    assert payload["season"] == "2026-27" and payload["source"] and payload["retrieved_at"]
    schedule = nba_schedule.load_schedule()
    assert schedule is not None and len(schedule) == 30
    assert min(len(days) for days in schedule.values()) >= 78
    assert all(date(2026, 10, 20) <= d <= date(2027, 4, 15) for days in schedule.values() for d in days)


def test_light_and_heavy_weeks_come_from_the_schedule(tmp_path, monkeypatch) -> None:
    _write(tmp_path, monkeypatch, PLAN)
    games = nba_schedule.real_team_games("AAA", 3)
    assert [sum(games[i * 7 : i * 7 + 7]) for i in range(3)] == [5, 2, 4]
    assert games[0:5] == (True,) * 5 and games[7 + 2] is True
    assert [sum(nba_schedule.real_team_games("BBB", 3)[i * 7 : i * 7 + 7]) for i in range(3)] == [3, 3, 3]


def test_real_games_are_deterministic_and_aliases_resolve(tmp_path, monkeypatch) -> None:
    _write(tmp_path, monkeypatch, {"NY": [0, 2], "SA": [1], "UTAH": [3], "WSH": [4], "GS": [5], "NO": [6], "BOS": [0, 3]})
    assert nba_schedule.real_team_games("NYK", 3) == nba_schedule.real_team_games("NY", 3)
    assert nba_schedule.real_team_games("BOS", 3) == nba_schedule.real_team_games("BOS", 3)


def test_representative_weeks_skip_break_and_partial_weeks() -> None:
    schedule = nba_schedule.load_schedule()
    starts = nba_schedule.representative_weeks(schedule, 12)
    assert len(starts) == 12 and all(s.weekday() == 0 for s in starts) and starts[0] >= date(2026, 10, 26)
    assert starts == nba_schedule.representative_weeks(schedule, 12)


def test_fallback_when_flag_off_file_missing_or_team_unknown(tmp_path, monkeypatch) -> None:
    player = make("p1", ("G",), 20, team="AAA")
    synthetic = game_days(player, 3)
    _write(tmp_path, monkeypatch, PLAN)
    assert game_days(player, 3) == synthetic  # flag off ignores the schedule
    assert game_days(player, 3, True) != synthetic
    assert game_days(make("p2", ("G",), 20, team="NOPE"), 3, True) == game_days(make("p2", ("G",), 20, team="NOPE"), 3)
    assert game_days(make("p3", ("G",), 20), 3, True) == game_days(make("p3", ("G",), 20), 3)
    monkeypatch.setattr(nba_schedule, "DEFAULT_SCHEDULE_PATH", tmp_path / "missing.json")
    assert game_days(player, 3, True) == synthetic
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(nba_schedule, "DEFAULT_SCHEDULE_PATH", tmp_path / "bad.json")
    assert game_days(player, 3, True) == synthetic


def test_activity_never_exceeds_real_game_days(tmp_path, monkeypatch) -> None:
    _write(tmp_path, monkeypatch, PLAN)
    player = make("p1", ("G",), 20, team="AAA")
    games = game_days(player, 3, True)
    active = activity_vector(player, 0.8, 3, True)
    assert all(games[i] or not a for i, a in enumerate(active)) and any(active)


def test_flag_is_off_by_default_and_leaves_fingerprint_unchanged() -> None:
    assert MarginalParams().real_schedule is False
    assert "real_schedule" not in MarginalParams().as_dict()
    assert marginal_fingerprint(CONFIG, MarginalParams(real_schedule=True)) != marginal_fingerprint(CONFIG)


def test_ranking_with_real_schedule_prefers_the_heavy_schedule_team(tmp_path, monkeypatch) -> None:
    _write(tmp_path, monkeypatch, {"AAA": [0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18], "BBB": [0, 7, 14]})
    heavy = make("heavy", ("F",), 20, rank=1, team="AAA")
    light = make("light", ("F",), 20, rank=2, team="BBB")
    obs = observation([], [heavy, light, *filler_pool()], pick=4)
    params = MarginalParams(weeks=3, real_schedule=True)
    first = rank_marginal(obs, CONFIG, params=params)
    again = rank_marginal(obs, CONFIG, params=params)
    scores = {c.player_id: c.score for c in first.candidates}
    assert first.status == "ranked" and scores["heavy"] > scores["light"]
    assert [(c.player_id, c.score) for c in first.candidates] == [(c.player_id, c.score) for c in again.candidates]
