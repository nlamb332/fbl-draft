"""Audit the published study with standard-library Python; no simulation run."""
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from draft_assistant.campaign import POLICIES, compare_rows


def read(name):
    path = HERE / "data" / name
    data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return json.loads(data)


def main():
    artifacts = json.loads((HERE / "ARTIFACTS.json").read_text())
    for name, entry in artifacts.items():
        payload = (HERE / name).read_bytes()
        if Path(name).suffix in {".md", ".json", ".py", ".svg"}:
            payload = payload.replace(b"\r\n", b"\n")
        assert len(payload) == entry["bytes"], name
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"], name
    initial, extension = read("summary.json"), read("espn_rank_summary.json")
    original_rows, espn_rows = read("initial_rows.json.gz"), read("espn_rows.json.gz")
    assert len(original_rows) == initial["completed_jobs"] * 84 == 4284
    assert len(espn_rows) == extension["espn_drafts"] == 672
    seeds = extension["complete_world_seeds"]
    assert seeds == initial["experiments"]["monte_carlo"]["balanced_world_seeds"]
    balanced = [row for row in original_rows if row["seed"] in seeds]
    rows = balanced + espn_rows
    for policy in POLICIES + ("espn_rank",):
        selected = [row for row in rows if row["policy"] == policy]
        assert len(selected) == 672
        assert len({(row["seed"], row["environment"], row["opponent"], row["seat"]) for row in selected}) == 672
        assert abs(statistics.fmean(row["points"] for row in selected) - extension["policy_metrics"][policy]["points"]) < 1e-8
    comparisons = compare_rows(rows, policies=POLICIES + ("espn_rank",))
    for actual in comparisons:
        saved = next(r for r in extension["comparisons"] if r["baseline"] == actual["baseline"])
        assert abs(actual["mean_delta"] - saved["mean_delta"]) < 1e-8
        assert abs(actual["relative_delta"] - saved["relative_delta"]) < 1e-10
    for prefix, count in (("initial", 4284), ("espn", 672)):
        with gzip.open(HERE / "data" / f"{prefix}_drafts.jsonl.gz", "rt", encoding="utf-8") as handle:
            observed = 0
            for line in handle:
                draft = json.loads(line)
                history = draft["history"]
                assert len(history) == 140
                assert len({pick["player_id"] for pick in history}) == 140
                assert len(draft["own_turns"]) == 10
                observed += 1
            assert observed == count
    cf = read("counterfactual.json.gz")
    checked = 0
    for turn in cf["own_turns"]:
        branch = turn["branches"][turn["marginal_choice"]]
        for split in ("discovery_rows", "evaluation_rows"):
            for row in branch[split]:
                if row.get("status") == "complete" and row.get("baseline_status") == "complete":
                    assert abs(row["value"] - row["baseline_value"]) < 1e-9
                    checked += 1
    assert checked == 240
    print(f"Verified {len(artifacts)} artifact hashes, 4,956 completed draft histories, seven policies across eight balanced worlds, paired means, and {checked} zero-regret references.")


if __name__ == "__main__":
    main()
