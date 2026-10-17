from __future__ import annotations

import json
import time

from draft_assistant.campaign_data import CampaignDataError, PRIMITIVE_SCORING_FIELDS, PlayerRecord, SeasonDataset
from draft_assistant.campaign_history import (
    audit_historical_dataset,
    build_prior_season_forecasts,
    build_actual_schedule,
    historical_datasets,
    historical_status,
    load_stats_file,
    normalize_stats_rows,
)
import draft_assistant.campaign_history as campaign_history_module


def _player(player_id: str, team: str) -> PlayerRecord:
    return PlayerRecord(
        player_id=player_id,
        name=player_id,
        eligibility=("G",),
        stats={field: 1.0 for field in PRIMITIVE_SCORING_FIELDS},
        projected_availability=1.0,
        market_rank=1,
        adp=1,
        schedule_group=team,
    )


def test_schedule_uses_team_games_and_keeps_dnp_zero_candidate():
    players = (_player("p1", "AAA"), _player("p2", "AAA"))
    schedule = build_actual_schedule(
        players,
        [{"date": "2023-01-01", "team": "AAA", "game_id": "g1"}],
    )
    assert schedule == {"2023-01-01": ("p1", "p2")}
    outcomes = normalize_stats_rows([{"date": "2023-01-01", "player_id": "p1", "PTS": 11}])
    assert "p2" not in outcomes["2023-01-01"]
    dataset = SeasonDataset(
        name="dnp",
        players=players,
        dates=("2023-01-01",),
        scheduled=schedule,
        outcomes=outcomes,
        provenance={},
    )
    assert dataset.actual_stat("2023-01-01", "p2", "PTS") == 0.0


def test_schedule_does_not_union_all_teams_across_a_trade():
    player = _player("p1", "AAA")
    team_games = [
        {"date": "2023-01-01", "team": "AAA"},
        {"date": "2023-01-10", "team": "AAA"},
        {"date": "2023-01-10", "team": "BBB"},
        {"date": "2023-01-20", "team": "BBB"},
    ]
    rows = [
        {"date": "2023-01-01", "player_id": "p1", "team": "AAA"},
        {"date": "2023-01-10", "player_id": "p1", "team": "BBB"},
    ]
    schedule = build_actual_schedule((player,), team_games, rows)
    assert schedule["2023-01-01"] == ("p1",)
    assert schedule["2023-01-20"] == ("p1",)
    assert schedule["2023-01-10"] == ("p1",)


def test_stats_loader_preserves_missing_optional_fields(tmp_path):
    path = tmp_path / "box_scores.json"
    path.write_text(
        json.dumps(
            [
                {"date": "2023-01-01", "player_id": "p1", "PTS": 10, "REB": 2},
                {"date": "2023-01-02", "player_id": "p3", "PTS": 10, "REB": 10, "AST": 10, "STL": 1, "BLK": 1},
                {"date": "2023-01-01", "player_id": "p2", "PTS": "--"},
            ]
        )
    )
    rows = load_stats_file(path)
    assert rows[0]["stats"] == {"PTS": 10.0, "REB": 2.0}
    assert next(row for row in rows if row["player_id"] == "p2")["stats"] == {}
    derived = normalize_stats_rows(rows)
    assert derived["2023-01-02"]["p3"]["DD"] == 1.0
    assert derived["2023-01-02"]["p3"]["TD"] == 1.0
    assert "TF" not in derived["2023-01-02"]["p3"]


def test_stats_loader_accepts_official_resultsets_and_cdn_schedule_shapes(tmp_path):
    stats_path = tmp_path / "nba_resultset.json"
    stats_path.write_text(json.dumps({
        "resultSets": [{
            "headers": ["GAME_DATE", "PLAYER_ID", "PLAYER_NAME", "PTS", "REB", "AST", "STL", "BLK"],
            "rowSet": [["2022-10-19T00:00:00", "p1", "P1", 10, 10, 10, 0, 0]],
        }],
    }))
    rows = load_stats_file(stats_path)
    assert rows[0]["date"] == "2022-10-19"
    assert rows[0]["player_id"] == "p1"
    assert rows[0]["stats"]["TD"] == 1.0

    schedule_path = tmp_path / "schedule.json"
    schedule_path.write_text(json.dumps({
        "leagueSchedule": {
            "gameDates": [{
                "gameDate": "10/19/2022 00:00:00",
                "games": [{
                    "gameId": "g1",
                    "homeTeam": {"teamTricode": "AAA"},
                    "awayTeam": {"teamTricode": "BBB"},
                }],
            }],
        },
    }))
    schedule_rows = load_stats_file(schedule_path)
    assert {row["team"] for row in schedule_rows} == {"AAA", "BBB"}


def test_historical_loader_reports_gap_and_deadline(tmp_path):
    datasets = historical_datasets(tmp_path, deadline=time.monotonic() - 1)
    assert datasets == []
    status = historical_status()
    assert status["status"] == "deadline_expired"
    assert status["available"] is False


def test_cached_official_probe_is_not_retried(tmp_path, monkeypatch):
    (tmp_path / "official_acquisition_attempt.json").write_text(
        json.dumps([{"name": "leaguegamelog", "status": "error", "error_type": "TimeoutError"}])
    )
    monkeypatch.setattr(campaign_history_module, "_probe_official_sources", lambda deadline: (_ for _ in ()).throw(AssertionError("retry")))
    assert historical_datasets(tmp_path, time.monotonic() + 1) == []
    assert historical_status(tmp_path)["network_fetch"].startswith("cached_bounded_attempt")


def test_audit_flags_forecast_cutoff_after_outcomes():
    dataset = SeasonDataset(
        name="leak",
        players=(_player("p1", "AAA"),),
        dates=("2023-01-01",),
        scheduled={"2023-01-01": ("p1",)},
        outcomes={"2023-01-01": {"p1": {"PTS": 1}}},
        provenance={"forecast_cutoff": "2023-02-01", "outcome_start": "2023-01-01", "positions_source": "preseason"},
    )
    assert audit_historical_dataset(dataset)["forecast_leakage"] is True


def test_cached_historical_dataset_requires_verified_cutoff_and_positions(tmp_path):
    dataset = SeasonDataset(
        name="cached-2022-23",
        players=(_player("p1", "AAA"),),
        dates=("2023-01-01",),
        scheduled={"2023-01-01": ("p1",)},
        outcomes={"2023-01-01": {"p1": {"PTS": 1}}},
        provenance={
            "forecast_cutoff": "2022-10-01",
            "forecast_cutoff_verified": True,
            "outcome_start": "2023-01-01",
            "positions_source": "preseason-supported positions",
        },
    )
    path = tmp_path / "cached-2022-23.json"
    dataset.save_json(path)
    loaded = campaign_history_module._load_cached_dataset(path)
    assert loaded.name == dataset.name

    missing_positions = json.loads(path.read_text())
    del missing_positions["provenance"]["positions_source"]
    path.write_text(json.dumps(missing_positions))
    try:
        campaign_history_module._load_cached_dataset(path)
    except CampaignDataError as exc:
        assert "positions_source" in str(exc)
    else:
        raise AssertionError("historical cache without position provenance must be rejected")


def test_cached_historical_dataset_rejects_forecast_leakage(tmp_path):
    dataset = SeasonDataset(
        name="leaked-2022-23",
        players=(_player("p1", "AAA"),),
        dates=("2023-01-01",),
        scheduled={"2023-01-01": ("p1",)},
        outcomes={"2023-01-01": {"p1": {"PTS": 1}}},
        provenance={
            "forecast_cutoff": "2023-02-01",
            "forecast_cutoff_verified": True,
            "outcome_start": "2023-01-01",
            "positions_source": "preseason-supported positions",
        },
    )
    path = tmp_path / "leaked.json"
    dataset.save_json(path)
    try:
        campaign_history_module._load_cached_dataset(path)
    except CampaignDataError as exc:
        assert "cutoff" in str(exc)
    else:
        raise AssertionError("forecast leakage must be rejected before drafting")


def test_prior_season_forecast_builder_uses_positions_and_no_future_gp():
    row = {"date": "2022-04-01", "player_id": "p1", "name": "P1", "team": "AAA"}
    row.update({field: 1 for field in PRIMITIVE_SCORING_FIELDS})
    forecasts = build_prior_season_forecasts([row, {**row, "date": "2022-04-02"}], {"p1": ("PG",)})
    assert len(forecasts) == 1
    assert forecasts[0].eligibility == ("G",)
    assert forecasts[0].projected_availability == 2 / 82
    assert "2024" not in forecasts[0].source_label
