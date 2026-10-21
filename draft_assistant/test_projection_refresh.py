from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from draft_assistant.__main__ import main
from draft_assistant.campaign_data import PRIMITIVE_SCORING_FIELDS, CampaignDataError, SeasonDataset
from draft_assistant.contracts import RoomIdentity
from draft_assistant.coordinator import Coordinator
from draft_assistant.projection_refresh import (
    GP_SOURCE_DIRECT, GP_SOURCE_PROXY, FreshnessPolicy, evaluate_freshness, parse_external_spec,
    refresh_inputs, stamp_provenance,
)
from draft_assistant.campaign_data import projection_dataset

ROOM = RoomIdentity("1", "2027", "1")
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def _row(pid, name, pts, gp=None):
    row = {"player_id": pid, "name": name, "eligibility": ["G"], "stats": {f: 1.0 for f in PRIMITIVE_SCORING_FIELDS}}
    row["stats"]["PTS"] = pts
    if gp is not None:
        row["projected_gp"] = gp
    return row


def _write(path, rows, captured_at=None):
    payload = {"catalog": rows}
    if captured_at:
        payload["captured_at"] = captured_at
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_blend_stamps_every_row_and_uses_oldest_capture(tmp_path):
    espn = _write(tmp_path / "espn.json", [_row("a", "Alpha One", 20.0), _row("b", "Beta", 10.0)], "2026-10-21T10:00:00Z")
    ext = _write(tmp_path / "ext.json", [_row("x9", "alpha one", 30.0, gp=70), _row("zz", "Not In Base", 5.0)], "2026-10-20T12:00:00Z")
    dataset = refresh_inputs(espn_path=espn, externals=[("ext", ext, 3.0)], now=NOW)
    block = dataset.provenance["projection_provenance"]
    assert set(block["rows"]) == {"a", "b"}  # external cannot widen the pool
    assert all(r["source"] and r["captured_at"] and "age_hours" in r for r in block["rows"].values())
    players = {p.player_id: p for p in dataset.players}
    assert players["a"].stats["PTS"] == pytest.approx((20 * 1 + 30 * 3) / 4)
    assert players["b"].stats["PTS"] == 10.0
    assert block["rows"]["a"]["source"] == "espn+ext"
    assert block["rows"]["a"]["captured_at"] == "2026-10-20T12:00:00Z"
    assert block["rows"]["a"]["age_hours"] == 24.0
    assert block["rows"]["a"]["gp_source"] == GP_SOURCE_DIRECT
    assert players["a"].projected_availability == pytest.approx(70 / 82)
    assert block["rows"]["b"]["gp_source"] == GP_SOURCE_PROXY
    assert block["offline_fallback"] is False


def test_missing_or_undated_external_is_skipped_not_fatal(tmp_path):
    espn = _write(tmp_path / "espn.json", [_row("a", "Alpha", 20.0)], "2026-10-21T10:00:00Z")
    undated = _write(tmp_path / "undated.json", [_row("a", "Alpha", 99.0)])
    dataset = refresh_inputs(espn_path=espn, externals=[("gone", tmp_path / "gone.json", 1.0), ("undated", undated, 1.0)], now=NOW)
    block = dataset.provenance["projection_provenance"]
    assert {s["source"] for s in block["skipped_sources"]} == {"gone", "undated"}
    assert dataset.players[0].stats["PTS"] == 20.0
    assert [s["source"] for s in block["sources"]] == ["espn"]


def test_missing_espn_capture_falls_back_to_tagged_checked_in_csv(tmp_path):
    dataset = refresh_inputs(espn_path=tmp_path / "missing.json", now=NOW)
    block = dataset.provenance["projection_provenance"]
    assert block["offline_fallback"] is True
    assert block["sources"][0]["source"] == "checked_in_projection_csv"
    assert len(dataset.players) == 195
    assert all(r["captured_at"] == "2026-10-10T00:00:00Z" for r in block["rows"].values())
    report = evaluate_freshness(dataset.provenance, now=NOW, max_age_hours=72)
    assert report["offline_fallback"] and report["status"] == "stale" and not report["ok"]
    assert any("offline fallback" in w for w in report["warnings"])


def test_too_many_externals_rejected(tmp_path):
    with pytest.raises(CampaignDataError):
        refresh_inputs(externals=[("a", tmp_path, 1.0)] * 3, now=NOW)


def test_stale_warning_threshold_and_fail_closed():
    dataset = stamp_provenance(projection_dataset(), now=NOW)
    prov = dataset.provenance
    assert evaluate_freshness(prov, now=NOW, max_age_hours=24 * 365)["status"] == "fresh"
    stale = evaluate_freshness(prov, now=NOW, max_age_hours=72)
    assert stale["status"] == "stale" and "refresh inputs" in stale["warnings"][0]
    assert evaluate_freshness({}, now=NOW)["status"] == "unknown"
    broken = json.loads(json.dumps(prov))
    next(iter(broken["projection_provenance"]["rows"].values())).pop("captured_at")
    assert evaluate_freshness(broken, now=NOW)["ok"] is False
    future = evaluate_freshness(prov, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert future["ok"] is False


def test_parse_external_spec():
    assert parse_external_spec("hashtag=/tmp/x.csv:2.5")[2] == 2.5
    assert parse_external_spec(r"hashtag=C:\data\x.csv")[1] == Path(r"C:\data\x.csv")
    with pytest.raises(CampaignDataError):
        parse_external_spec("nopath")


def test_cli_refresh_roundtrip(tmp_path, capsys):
    espn = _write(tmp_path / "espn.json", [_row("a", "Alpha", 20.0)], "2026-10-21T10:00:00Z")
    out = tmp_path / "out.json"
    assert main(["refresh-inputs", "--espn", str(espn), "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["players"] == 1
    assert SeasonDataset.load_json(out).provenance["projection_provenance"]["schema"] == "projection-provenance/1"


def _coordinator(tmp_path, dataset_path, enforce, now):
    policy = FreshnessPolicy(dataset_path, 72.0, enforce)
    return Coordinator(":memory:", clock=lambda: now.isoformat(), recover=False, projection_policy=policy)


def test_policy_flag_off_by_default_and_gate_blocks_readiness(tmp_path):
    stale = stamp_provenance(projection_dataset(), now=NOW).save_json(tmp_path / "ds.json")
    with Coordinator(":memory:", recover=False) as plain:
        assert plain.projection_freshness() is None
    warn = _coordinator(tmp_path, stale, False, NOW)
    gate = _coordinator(tmp_path, stale, True, NOW)
    session = "s1"
    for coord in (warn, gate):
        coord.create_session(ROOM, session_id=session)
    assert warn.status(session)["projection_freshness"]["enforced"] is False
    assert "projection_fresh" not in warn.readiness(session)["checks"]
    ready = gate.readiness(session)
    assert ready["checks"]["projection_fresh"] is False and ready["ready"] is False
    assert any("stale" in r for r in ready["reasons"])
    # Same dataset becomes acceptable once the clock is inside the threshold.
    fresh_gate = _coordinator(tmp_path, stale, True, NOW)
    fresh_gate.clock = lambda: (NOW - timedelta(days=11)).isoformat()
    fresh_gate.create_session(ROOM, session_id=session)
    assert fresh_gate.readiness(session)["checks"]["projection_fresh"] is True


def test_gate_flag_requires_dataset(capsys):
    assert main(["--projection-staleness-gate", "status"]) == 2
