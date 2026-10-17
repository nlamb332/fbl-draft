from __future__ import annotations

import json
from pathlib import Path

from draft_assistant.campaign_data import (
    PRIMITIVE_SCORING_FIELDS,
    PlayerRecord,
    SeasonDataset,
    load_projection_capture,
    projection_dataset,
    _new_player_record,
    CampaignDataError,
)


CAPTURE_FIXTURE = Path(__file__).with_name("fixtures") / "attended_capture_partial.json"


def _player(player_id: str = "p1", team: str = "AAA") -> PlayerRecord:
    stats = {field: 1.0 for field in PRIMITIVE_SCORING_FIELDS}
    return PlayerRecord(
        player_id=player_id,
        name=f"Player {player_id}",
        eligibility=("G",),
        stats=stats,
        projected_availability=1.0,
        market_rank=1,
        adp=1,
        schedule_group=team,
        source_label="offline-test",
    )


def test_season_dataset_json_roundtrip_preserves_contract(tmp_path):
    dataset = SeasonDataset(
        name="offline-2023-24",
        players=(_player(), _player("p2")),
        dates=("2024-01-02", "2024-01-01"),
        scheduled={"2024-01-01": ("p1", "p2")},
        outcomes={"2024-01-01": {"p1": {"PTS": 10, "DD": 1}}},
        provenance={"forecast_cutoff": "2023-10-20", "outcome_start": "2024-01-01"},
        missing_scoring_fields=("PF", "TF", "EJ", "PF"),
    )
    path = dataset.save_json(tmp_path / "dataset.json")
    roundtrip = SeasonDataset.load_json(path)
    assert roundtrip == dataset
    assert json.loads(path.read_text())["players"][0]["eligibility"] == ["G"]
    assert roundtrip.actual_stat("2024-01-01", "p2", "PTS") == 0.0
    assert roundtrip.dates == ("2024-01-01", "2024-01-02")


def test_projection_capture_excludes_incomplete_rows_and_fallback_has_195_rows():
    capture = load_projection_capture(CAPTURE_FIXTURE)
    assert capture
    assert all(set(PRIMITIVE_SCORING_FIELDS) <= set(player.stats) for player in capture)
    assert capture.provenance["identities_seen"] == 4
    assert capture.provenance["nonempty_projection_rows"] == 3
    assert capture.provenance["incomplete_nonempty_rows"] == 1
    dataset = projection_dataset("mock_draft/data/players_2026-27_espn_projection.csv")
    assert len(dataset.players) == 195
    assert dataset.provenance["identities_seen"] == 195
    assert dataset.provenance["excluded_incomplete_rows"] == 0
    assert dataset.provenance["exact_latest_trial_verified"] is False
    assert {"PF", "TF", "EJ", "DD", "TD", "QD"} <= set(dataset.missing_scoring_fields)


def test_projected_gp_derives_availability_only_when_explicit_value_is_absent():
    base = {"player_id": "gp", "name": "GP", "eligibility": ["G"], "projected_gp": 41, "stats": {field: 1 for field in PRIMITIVE_SCORING_FIELDS}}
    assert _new_player_record(base, source_label="test").projected_availability == 0.5
    assert _new_player_record({**base, "projected_gp": 0}, source_label="test").projected_availability == 0.0
    assert _new_player_record({**base, "projected_availability": 0.8}, source_label="test").projected_availability == 0.8
    try:
        _new_player_record({**base, "projected_availability": "invalid"}, source_label="test")
    except CampaignDataError:
        pass
    else:
        raise AssertionError("invalid explicit availability must be rejected")


def test_capture_provenance_extracts_source_timestamp_and_room():
    capture = load_projection_capture(CAPTURE_FIXTURE)
    assert capture.provenance["forecast_cutoff"] == "2026-10-13"
    assert capture.provenance["forecast_cutoff_verified"] is True
    assert capture.provenance["capture_room_ids"] == ["723024336"]
    dataset = projection_dataset()
    assert dataset.provenance["source"] == "checked_in_projection_csv"
    assert "capture_rejected" not in dataset.provenance
    assert len(dataset.players) == 195


def test_explicit_partial_capture_does_not_silently_fallback():
    try:
        projection_dataset(CAPTURE_FIXTURE)
    except CampaignDataError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("explicit partial capture must be rejected")
