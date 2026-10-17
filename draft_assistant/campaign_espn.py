"""Add an explicit ESPN RK-only baseline to a frozen campaign's balanced worlds.

Run python -m draft_assistant.campaign_espn --help. Original results are immutable.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics
import time
from types import SimpleNamespace
import unicodedata

from . import campaign, campaign_engine as engine
from .campaign_data import SeasonDataset


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(name):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


def attach_ranks(players, capture):
    """Audit a one-to-one name join between slug-ID and ESPN-ID snapshots."""
    by_name = {}
    for row in capture["catalog"]:
        name = identity(row["name"])
        if name in by_name:
            raise ValueError(f"ambiguous ESPN player name {row['name']}")
        by_name[name] = row
    result, audit, seen = [], [], set()
    for player in players:
        name = identity(player.name)
        if name in seen or name not in by_name:
            raise ValueError(f"missing or ambiguous rank join for {player.name}")
        seen.add(name)
        row = by_name[name]
        rank = row.get("draft_rank")
        joined = SimpleNamespace(**asdict(player), espn_draft_rank=rank)
        engine.rank_espn_only([joined])  # Strictly require sourced RK; no fallback.
        result.append(joined)
        audit.append({"player_id": player.player_id, "name": player.name, "espn_id": row["player_id"],
                      "espn_draft_rank": rank, "source": row.get("source")})
    return tuple(result), audit


def validate_unchanged_science(source):
    frozen = ast.parse((source / "sources/draft_assistant/campaign_engine.py").read_text())
    current = ast.parse(Path(engine.__file__).read_text())
    before = {node.name: ast.dump(node) for node in frozen.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    after = {node.name: ast.dump(node) for node in current.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    changed = [name for name in before if before[name] != after.get(name) and name not in {"_catalog_fingerprint", "rank_policy"}]
    if changed:
        raise ValueError(f"frozen simulation/evaluation functions changed: {changed}")


def run_cell(task):
    output = Path(task["output"])
    target = output / "jobs" / f"{task['cell_id']}.json"
    if target.exists():
        saved = json.loads(target.read_text())
        if saved.get("status") == "complete" and saved.get("fingerprint") == task["fingerprint"]:
            return saved
    base = SeasonDataset.from_jsonable(json.loads(Path(task["dataset"]).read_text()))
    players, _ = attach_ranks(base.players, json.loads(Path(task["capture"]).read_text()))
    world = engine.generate_world(base, task["seed"], task["environment"])
    originals = task["original_rows"]
    memo = {}
    def evaluate(roster):
        key = tuple(sorted(p.player_id for p in roster))
        if key not in memo:
            memo[key] = {k: v for k, v in engine.evaluate_roster(world, roster).items() if k != "allocations"}
        return memo[key]
    rows = []
    trace = output / "traces" / f"{task['cell_id']}.jsonl.gz"
    trace.parent.mkdir(parents=True, exist_ok=True)
    by_id = {p.player_id: p for p in base.players}
    with gzip.open(trace, "wt", encoding="utf-8") as log:
        for seat in range(1, 15):
            original = next(row for row in originals if row["seat"] == seat and row["policy"] == "marginal")
            reproduced = evaluate([by_id[pid] for pid in original["roster_ids"]])
            for field in ("points", "benched_points", "hindsight_points", "usable_starts", "weekly_points"):
                if reproduced[field] != original[field]:
                    raise ValueError(f"original outcome mismatch: {task['cell_id']}, seat {seat}, {field}")
            draft = engine.simulate_draft(players, seat, task["seed"], task["opponent"], "espn_rank", deadline=task["deadline"])
            if draft["status"] != "complete":
                result = {"status": "incomplete", "fingerprint": task["fingerprint"], "rows": rows}
                campaign.write_json(target, result)
                return result
            metrics = evaluate(draft["roster"])
            wins = comparisons = 0
            for opponent_seat, roster in draft["rosters"].items():
                if int(opponent_seat) == seat:
                    continue
                opposing = evaluate(roster)["weekly_points"]
                for week, points in metrics["weekly_points"].items():
                    against = opposing.get(week, 0)
                    wins += 1 if points > against else .5 if points == against else 0
                    comparisons += 1
            rows.append({**{key: original[key] for key in ("dataset", "experiment", "environment", "opponent", "seed", "seat", "cluster_id")},
                         "policy": "espn_rank", **metrics, "weekly_win_fraction": wins / comparisons,
                         "roster_ids": [p.player_id for p in draft["roster"]], "reference_outcomes_reproduced": True})
            log.write(json.dumps(campaign.json_value({"seat": seat, "draft": draft}), default=vars, allow_nan=False) + "\n")
    result = {"status": "complete", "fingerprint": task["fingerprint"], "rows": rows}
    campaign.write_json(target, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("runtime/strategy-quality-2026-10-14-final"))
    parser.add_argument("--capture", type=Path, default=Path("studies/draft-strategy-quality/2026-10-15/data/espn-ranks.json"))
    parser.add_argument("--output", type=Path, default=Path("runtime/strategy-quality-espn-rank-2026-10-15"))
    parser.add_argument("--minutes", type=float, default=10)
    args = parser.parse_args()
    validate_unchanged_science(args.source)
    summary = json.loads((args.source / "summary.json").read_text())
    seeds = summary["experiments"]["monte_carlo"]["balanced_world_seeds"]
    dataset = args.source / "datasets/projection.json"
    base = SeasonDataset.from_jsonable(json.loads(dataset.read_text()))
    _, audit = attach_ranks(base.players, json.loads(args.capture.read_text()))
    args.output.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256((campaign.fingerprint() + digest(__file__) + digest(args.capture) + digest(dataset)).encode()).hexdigest()
    campaign.write_json(args.output / "manifest.json", {"fingerprint": fingerprint, "rank_join": audit,
        "rank_source": str(args.capture), "rank_source_sha256": digest(args.capture), "original_summary_sha256": digest(args.source / "summary.json"),
        "policy": "lowest explicit ESPN RK; player-ID tie break; same legality constraints; no custom scoring in pick ranking",
        "date_mismatch": "October 13 RK overlay on October 10 projections; not an exact reconstruction of either attended room",
        "unchanged_outcome_and_draft_functions": True, **campaign.snapshot_sources(args.output)})
    tasks = []
    start = time.monotonic()
    for path in sorted((args.source / "jobs").glob("*.json")):
        job = json.loads(path.read_text())
        if job.get("status") != "complete" or job["rows"][0]["seed"] not in seeds:
            continue
        row = job["rows"][0]
        tasks.append({"cell_id": f"{row['seed']}-{row['environment']}-{row['opponent']}",
                      **{key: row[key] for key in ("seed", "environment", "opponent")},
                      "dataset": str(dataset), "capture": str(args.capture), "output": str(args.output),
                      "original_rows": job["rows"], "fingerprint": fingerprint, "deadline": start + args.minutes*60})
    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run_cell, tasks))
    complete = [(task, result) for task, result in zip(tasks, results) if result["status"] == "complete"]
    # Require all six scenario cells in a seed, and all fourteen seats in each cell.
    complete_seeds = [seed for seed in seeds if sum(task["seed"] == seed for task, _ in complete) == 6]
    new_rows = [row for task, result in complete if task["seed"] in complete_seeds for row in result["rows"]]
    original_rows = [row for task, _ in complete if task["seed"] in complete_seeds for row in task["original_rows"]]
    rows = original_rows + new_rows
    policies = campaign.POLICIES + ("espn_rank",)
    metrics = {policy: {field: statistics.fmean(row[field] for row in rows if row["policy"] == policy)
                          for field in ("points", "usable_starts", "benched_points", "hindsight_points", "weekly_win_fraction")}
               for policy in policies} if new_rows else {}
    result = {"fingerprint": fingerprint, "elapsed_seconds": time.monotonic()-start, "complete_world_seeds": complete_seeds,
              "complete_jobs": len(complete), "target_jobs": len(tasks), "policy_metrics": metrics,
              "comparisons": campaign.compare_rows(rows, policies=policies),
              "opponent_comparisons": {mode: campaign.compare_rows([r for r in rows if r["opponent"] == mode], policies=("espn_rank",)) for mode in campaign.OPPONENTS},
              "espn_drafts": len(new_rows), "reference_rows_reproduced": len(new_rows)}
    campaign.write_json(args.output / "summary.json", result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
