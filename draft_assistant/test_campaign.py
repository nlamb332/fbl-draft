"""Statistical guardrails for the strategy-quality research runner."""
from .campaign import compare_rows, cluster_interval, make_tasks, markdown_report, summarize, write_json
from pathlib import Path
from unittest.mock import patch


def rows_for_worlds(count=8):
    rows = []
    for seed in range(count):
        for seat in (1, 2, 3):
            for policy, points in (("marginal", 110 + seed), ("foundation", 100 + seed)):
                rows.append(dict(dataset="forecast", environment="moderate", opponent="nominal", seed=seed, seat=seat, policy=policy, points=points, cluster_id=str(seed)))
    return rows


def test_pairing_and_clustering_do_not_count_seats_as_independent():
    result = compare_rows(rows_for_worlds())[0]
    assert result["pairs"] == 24
    assert result["independent_clusters"] == 8
    assert result["mean_delta"] == 10
    assert result["ci95"] == [10, 10]
    assert result["classification"] == "advantage supported"


def test_one_world_is_inconclusive_even_with_many_seats():
    result = compare_rows(rows_for_worlds(1))[0]
    assert result["ci95"] == [None, None]
    assert result["classification"] == "inconclusive"


def test_inexact_cells_are_never_paired():
    rows = rows_for_worlds(1)
    for row in rows:
        if row["policy"] == "foundation":
            row["environment"] = "injury"
    assert compare_rows(rows) == []


def test_identical_historical_worlds_do_not_become_independent_from_opponent_seeds():
    rows = rows_for_worlds()
    for row in rows:
        row["cluster_id"] = "historical-fixed-seasons"
    assert compare_rows(rows)[0]["independent_clusters"] == 1


def test_interval_handles_empty_and_singleton():
    assert cluster_interval([]) == (None, None)
    assert cluster_interval([10]) == (None, None)


def test_resume_keys_include_dataset_contents_and_code(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("one")
    arguments = dict(datasets=[("a", path)], experiment="monte_carlo", seeds=1, output=tmp_path, code_fingerprint="code", deadline=10, seats=[1])
    before = make_tasks(**arguments)
    assert len(before) == 6
    path.write_text("two")
    after = make_tasks(**arguments)
    assert before[0]["job_id"] != after[0]["job_id"]


def test_report_does_not_claim_a_result_without_rows():
    report = markdown_report({"completed_jobs": 0, "target_jobs": 6, "limitations": ["No independent worlds completed."]})
    assert "0 / 6" in report
    assert "No independent worlds completed." in report


def test_partial_scenario_world_is_not_used_for_pooled_campaign_claims():
    rows = rows_for_worlds(2)
    for row in rows:
        row["experiment"] = "monte_carlo"
    summary = summarize(rows)
    assert summary["comparisons"] == []
    assert summary["rows_excluded_from_pooled_estimates"] == len(rows)
    assert summary["breakdowns"]["environment"]["moderate"]


def test_transient_windows_checkpoint_lock_does_not_lose_completed_results(tmp_path):
    target = tmp_path / "checkpoint.json"
    target.write_text('{"status":"old"}')
    real_replace = Path.replace
    attempts = []
    def temporarily_locked(source, destination):
        attempts.append(source)
        if len(attempts) <= 2:
            raise PermissionError("external reader temporarily withholds delete sharing")
        return real_replace(source, destination)
    with patch.object(Path, "replace", temporarily_locked):
        write_json(target, {"status": "complete", "rows": [1, 2, 3]})
    assert len(attempts) == 3
    assert '"complete"' in target.read_text()
    assert not target.with_suffix(".json.tmp").exists()
