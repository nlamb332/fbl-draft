"""Budgeted, resumable research campaign; never changes the live draft policy.

Run ``python -m draft_assistant.campaign --help``. Detailed datasets and traces
belong in ignored runtime storage; the summary is intentionally portable.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import Any

POLICIES = ("marginal", "foundation", "custom_ppg", "custom_total", "positional_vor", "market")
OPPONENTS = ("nominal", "market", "custom")
ENVIRONMENTS = ("moderate", "injury")
SEED_START = 2026101400


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_value(value), indent=2, allow_nan=False), encoding="utf-8")
    # Windows status readers/antivirus may temporarily withhold delete sharing.
    # Keep the complete temporary checkpoint and retry its atomic replacement.
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05 * (attempt + 1))


def fingerprint() -> str:
    """Include research code and frozen production inputs in resume identity."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for pattern in ("campaign*.py", "marginal_lineup.py", "valuation.py", "contracts.py", "validation_runner.py"):
        for path in sorted(root.glob(pattern)):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    fixture = root.parent / "mock_draft" / "fixture.py"
    digest.update(fixture.read_bytes())
    return digest.hexdigest()


def snapshot_sources(output: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    paths = set((root / "draft_assistant").glob("campaign*.py"))
    paths.update(root / "draft_assistant" / name for name in ("marginal_lineup.py", "valuation.py", "contracts.py", "validation_runner.py"))
    paths.add(root / "mock_draft" / "fixture.py")
    paths.update((root / "draft_assistant" / "validation" / "marginal-lineup-v1").glob("*artifact.json"))
    paths.update((root / "draft_assistant" / "validation" / "marginal-lineup-v1").glob("*params.json"))
    hashes = {}
    for path in sorted(paths):
        relative = path.relative_to(root)
        target = output / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = path.read_bytes()
        target.write_bytes(payload)
        hashes[str(relative)] = hashlib.sha256(payload).hexdigest()
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return {"repository_revision": revision, "source_sha256": hashes}


def cluster_interval(values: list[float], *, samples: int = 4000) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    rng = random.Random(9021014)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples))
    return means[int(samples * .025)], means[min(samples - 1, int(samples * .975))]


def compare_rows(rows: list[dict[str, Any]], *, reference: str = "marginal", policies: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Pair exact cells, then cluster all correlated seats/scenarios by world.

    Rows from incomplete jobs must be removed by the caller. Weekly matchup
    wins are against the reactive opponent rosters within each own draft, not
    against a counterfactual roster that couldn't coexist in the same league.
    """
    def key(row: dict[str, Any]) -> tuple:
        return tuple(row[k] for k in ("dataset", "environment", "opponent", "seed", "seat"))
    index = {(key(row), row["policy"]): row for row in rows}
    summaries = []
    for policy in policies or POLICIES:
        if policy == reference:
            continue
        pairs = [(row, index[(key(row), policy)]) for row in rows
                 if row["policy"] == reference and (key(row), policy) in index]
        if not pairs:
            continue
        groups: dict[str, list[float]] = {}
        for current, baseline in pairs:
            # Outcome world is shared across opponent populations and seats.
            cluster = str(current.get("cluster_id", (current["dataset"], current["seed"])))
            groups.setdefault(cluster, []).append(current["points"] - baseline["points"])
        clustered = [statistics.fmean(values) for values in groups.values()]
        lower, upper = cluster_interval(clustered)
        delta = statistics.fmean(clustered)
        baseline_groups: dict[str, list[float]] = {}
        for current, baseline in pairs:
            cluster = str(current.get("cluster_id", (current["dataset"], current["seed"])))
            baseline_groups.setdefault(cluster, []).append(baseline["points"])
        baseline_mean = statistics.fmean(statistics.fmean(group) for group in baseline_groups.values())
        classification = "inconclusive"
        if len(clustered) >= 8 and lower is not None and upper is not None:
            if lower > 0:
                classification = "advantage supported"
            elif upper < 0:
                classification = "disadvantage supported"
        differences = sorted(current["points"] - baseline["points"] for current, baseline in pairs)
        tail = differences[:max(1, math.ceil(len(differences) / 10))]
        summaries.append({
            "reference": reference, "baseline": policy, "pairs": len(pairs),
            "independent_clusters": len(clustered), "mean_delta": delta,
            "relative_delta": delta / baseline_mean if baseline_mean else None,
            "ci95": [lower, upper], "classification": classification,
            "exploratory": len(clustered) < 30,
            "practically_meaningful": bool(baseline_mean and abs(delta / baseline_mean) >= .01),
            "paired_win_fraction": statistics.fmean(1.0 if a["points"] > b["points"] else .5 if a["points"] == b["points"] else 0.0 for a, b in pairs),
            "worst_decile_mean_delta": statistics.fmean(tail),
        })
    return summaries


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pooled_rows = rows
    complete_world_seeds = []
    if rows and rows[0].get("experiment") == "monte_carlo":
        required_cells = {(environment, opponent) for environment in ENVIRONMENTS for opponent in OPPONENTS}
        seeds = {row["seed"] for row in rows}
        complete_world_seeds = sorted(seed for seed in seeds if {
            (row["environment"], row["opponent"]) for row in rows if row["seed"] == seed
        } == required_cells)
        pooled_rows = [row for row in rows if row["seed"] in complete_world_seeds]
    breakdowns: dict[str, Any] = {}
    for dimension in ("dataset", "environment", "opponent", "seat"):
        breakdowns[dimension] = {
            str(value): compare_rows([row for row in rows if row[dimension] == value])
            for value in sorted({row[dimension] for row in rows}, key=str)
        }
    policy_metrics = {}
    for policy in POLICIES:
        selected = [row for row in pooled_rows if row["policy"] == policy]
        if selected:
            policy_metrics[policy] = {"drafts": len(selected), **{
                field: statistics.fmean(float(row[field]) for row in selected if row.get(field) is not None)
                for field in ("points", "usable_starts", "benched_points", "hindsight_points", "weekly_win_fraction")
                if any(row.get(field) is not None for row in selected)
            }}
    return {"comparisons": compare_rows(pooled_rows), "breakdowns": breakdowns, "policy_metrics": policy_metrics,
            "balanced_world_seeds": complete_world_seeds,
            "rows_excluded_from_pooled_estimates": len(rows) - len(pooled_rows)}


def _load_dataset(path: str):
    from .campaign_data import SeasonDataset
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return SeasonDataset.from_jsonable(payload)


def _save_dataset(path: Path, dataset: Any) -> None:
    write_json(path, dataset.to_jsonable())


def run_job(task: dict[str, Any]) -> dict[str, Any]:
    """One balanced world/population job; only complete jobs enter estimates."""
    from .campaign_engine import evaluate_roster, generate_world, simulate_draft
    output = Path(task["output"])
    job_id = task["job_id"]
    result_path = output / "jobs" / f"{job_id}.json"
    if result_path.exists():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("status") == "complete" and previous.get("fingerprint") == task["fingerprint"]:
            return previous
    base = _load_dataset(task["dataset_path"])
    dataset = generate_world(base, seed=task["seed"], environment=task["environment"], calibration=task.get("calibration")) if task["experiment"] == "monte_carlo" else base
    result = {"job_id": job_id, "fingerprint": task["fingerprint"], "status": "incomplete", "rows": [], "diagnostics": [], "provenance": dataset.provenance}
    evaluation_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    def evaluate(roster):
        key = tuple(sorted(p.player_id for p in roster))
        if key not in evaluation_cache:
            evaluation_cache[key] = {k: v for k, v in evaluate_roster(dataset, roster).items() if k != "allocations"}
        return evaluation_cache[key]
    trace_path = output / "traces" / f"{job_id}.jsonl.gz"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(trace_path, "wt", encoding="utf-8", compresslevel=1) as traces:
        for seat in task["seats"]:
            for policy in POLICIES:
                if time.monotonic() >= task["deadline"]:
                    result["diagnostics"].append("phase deadline reached")
                    write_json(result_path, result)
                    return result
                draft = simulate_draft(dataset.players, our_seat=seat, seed=task["seed"], opponent_mode=task["opponent"], policy=policy, deadline=task["deadline"])
                traces.write(json.dumps(json_value({"seat": seat, "policy": policy, "draft": draft}), allow_nan=False) + "\n")
                if draft["status"] != "complete":
                    result["diagnostics"].append({"seat": seat, "policy": policy, "draft_status": draft["status"]})
                    write_json(result_path, result)
                    return result
                metrics = evaluate(draft["roster"])
                # Evaluate the thirteen rosters that actually coexist with ours.
                own_weekly = metrics.get("weekly_points", {})
                wins = losses = ties = 0
                for opponent_seat, roster in draft["rosters"].items():
                    if int(opponent_seat) == seat:
                        continue
                    opposing_weekly = evaluate(roster).get("weekly_points", {})
                    for week, points in own_weekly.items():
                        against = opposing_weekly.get(week, 0.0)
                        wins += points > against
                        losses += points < against
                        ties += points == against
                row = {"dataset": task["dataset"], "experiment": task["experiment"], "environment": task["environment"], "opponent": task["opponent"], "seed": task["seed"], "seat": seat, "policy": policy, "cluster_id": str(task["seed"]) if task["experiment"] == "monte_carlo" else "historical-fixed-seasons", **metrics,
                       "weekly_wins": wins, "weekly_losses": losses, "weekly_ties": ties,
                       "weekly_win_fraction": (wins + .5 * ties) / (wins + losses + ties) if wins + losses + ties else None,
                       "roster_ids": [p.player_id for p in draft["roster"]],
                       "policy_fallbacks": sum(bool(turn.get("policy_fallback")) for turn in draft["trace"] if turn.get("seat") == seat),
                       "feasibility_interventions": sum(bool(turn.get("candidates")) and turn["candidates"][0]["player_id"] != turn["player_id"] for turn in draft["trace"] if turn.get("seat") == seat)}
                result["rows"].append(row)
                write_json(result_path, result)
    result["status"] = "complete"
    write_json(result_path, result)
    return result


def run_phase(tasks: list[dict[str, Any]], *, workers: int, deadline: float) -> list[dict[str, Any]]:
    """Bound queued work as well as worker work; avoid a long shutdown backlog."""
    results: list[dict[str, Any]] = []
    pending_tasks = iter(tasks)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for _ in range(workers):
            task = next(pending_tasks, None)
            if task is not None:
                futures[executor.submit(run_job, task)] = task
        while futures:
            future = next(as_completed(futures))
            task = futures.pop(future)
            try:
                result = future.result()
            except Exception as exc:
                result = {"job_id": task["job_id"], "status": "error", "error": f"{type(exc).__name__}: {exc}", "rows": []}
                try:
                    write_json(Path(task["output"]) / "jobs" / f"{task['job_id']}.json", result)
                except OSError as checkpoint_error:
                    result["checkpoint_error"] = str(checkpoint_error)
            results.append(result)
            print(f"{task['experiment']} {task['job_id']}: {result['status']} ({len(result.get('rows', []))} rows)", flush=True)
            if time.monotonic() < deadline:
                following = next(pending_tasks, None)
                if following is not None:
                    futures[executor.submit(run_job, following)] = following
    return results


def make_tasks(datasets: list[tuple[str, Path]], *, experiment: str, seeds: int, output: Path, code_fingerprint: str, deadline: float, seats: list[int]) -> list[dict[str, Any]]:
    tasks = []
    environments = ENVIRONMENTS if experiment == "monte_carlo" else ("historical",)
    # Interleave seasons/populations before expanding independent seeds.
    for index in range(seeds):
        for environment in environments:
            for opponent in OPPONENTS:
                for dataset_name, path in datasets:
                    dataset_digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    job_id = hashlib.sha256(f"{experiment}/{dataset_digest}/{environment}/{opponent}/{SEED_START + index}/{seats}/{code_fingerprint}".encode()).hexdigest()[:24]
                    tasks.append({"job_id": job_id, "dataset": dataset_name, "dataset_path": str(path.resolve()), "experiment": experiment, "environment": environment, "opponent": opponent, "seed": SEED_START + index, "output": str(output.resolve()), "fingerprint": code_fingerprint, "deadline": deadline, "seats": seats})
    return tasks


def compact_counterfactual(counter: dict[str, Any]) -> dict[str, Any]:
    """Keep the portable report small; full branch evidence stays checkpointed."""
    result = {key: value for key, value in counter.items()
              if key not in {"own_turns", "states", "joint13_16", "checkpoint_identity"}}
    result["own_turns"] = []
    for turn in counter.get("own_turns", []):
        entry = {key: value for key, value in turn.items() if key != "branches"}
        entry["branches"] = {
            candidate: {key: value for key, value in branch.items() if not key.endswith("_rows")}
            for candidate, branch in turn.get("branches", {}).items()
        }
        result["own_turns"].append(entry)
    pair = counter.get("joint13_16", {})
    result["joint13_16"] = {key: value for key, value in pair.items() if key != "plans"}
    result["joint13_16"]["plans"] = [{key: value for key, value in plan.items() if key != "rows"} for plan in pair.get("plans", [])]
    return result


def markdown_report(summary: dict[str, Any]) -> str:
    lines = ["# Draft strategy quality campaign", "", "Research only: production strategy and promotion artifacts were not changed.", "",
             "Primary metric: season points scored by legal daily lineups selected using forecasts, not realized scores. Fixed rosters; no waivers or trades.", "",
             f"Completed balanced jobs: {summary.get('completed_jobs', 0)} / {summary.get('target_jobs', 0)}. Incomplete/error jobs are excluded from comparisons.", "",
             "| Experiment | Baseline | Paired delta | 95% clustered interval | Clusters | Finding |", "|---|---|---:|---|---:|---|"]
    for experiment, section in summary.get("experiments", {}).items():
        for row in section.get("comparisons", []):
            interval = row["ci95"]
            ci = "unavailable" if interval[0] is None else f"{interval[0]:.1f} to {interval[1]:.1f}"
            lines.append(f"| {experiment} | {row['baseline']} | {row['mean_delta']:+.1f} | {ci} | {row['independent_clusters']} | {row['classification']} |")
    lines.extend(["", "Positive deltas favor the current marginal strategy. Confidence intervals quantify only the sampled model worlds. Under thirty worlds remains exploratory.", "", "## Outcome metrics", "", "| Experiment | Policy | Drafts | Usable points | Usable starts | Benched points | Weekly opponent-pair win rate |", "|---|---|---:|---:|---:|---:|---:|"])
    for experiment, section in summary.get("experiments", {}).items():
        for policy, metrics in section.get("policy_metrics", {}).items():
            wins = metrics.get("weekly_win_fraction")
            wins_text = "unavailable" if wins is None else f"{wins:.1%}"
            lines.append(f"| {experiment} | {policy} | {metrics['drafts']} | {metrics.get('points', 0):.1f} | {metrics.get('usable_starts', 0):.1f} | {metrics.get('benched_points', 0):.1f} | {wins_text} |")
    lines.extend(["", "## Breakdown against foundation", "", "| Experiment | Dimension | Cell | Delta | Independent clusters |", "|---|---|---|---:|---:|"])
    for experiment, section in summary.get("experiments", {}).items():
        for dimension, cells in section.get("breakdowns", {}).items():
            for cell, comparisons in cells.items():
                for comparison in comparisons:
                    if comparison["baseline"] == "foundation":
                        lines.append(f"| {experiment} | {dimension} | {cell} | {comparison['mean_delta']:+.1f} | {comparison['independent_clusters']} |")
    scoring_fields = summary.get("missing_scoring_fields", [])
    lines.extend(["", "Actual scoring fields unavailable: " + (", ".join(scoring_fields) if scoring_fields else "none reported") + ". Missing fields limit conclusions to the modeled/common scoring components."])
    lines.extend(["", "## Interpretation and limitations", ""])
    for item in summary.get("limitations", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Historical data", "", "```json", json.dumps(summary.get("historical_status", {}), indent=2), "```", "", "## Decision regret", ""])
    counter = summary.get("counterfactual", {})
    names = summary.get("player_names", {})
    lines.extend(["Frozen simulated seat-13 draft; this is not an exact replay of the attended practice room.", "", "| Pick | Recommendation | Discovery-selected alternative | Held-out regret | 95% interval | Holdout samples / requested |", "|---|---|---|---:|---|---|"])
    for turn in counter.get("own_turns", []):
        selected = turn.get("evaluation_selected_branch") or {}
        details = selected.get("summary") or {}
        regret = details.get("mean_regret")
        ci = details.get("regret_ci95")
        choice = turn.get("marginal_choice")
        alternative = selected.get("candidate_id")
        samples = details.get("actual_target_used", 0)
        requested = details.get("requested_target_rows", counter.get("evaluation_target", 16))
        lines.append(f"| {turn['pick_number']} | {names.get(choice, choice)} | {names.get(alternative, alternative)} | {'unavailable' if regret is None else f'{regret:+.1f}'} | {'unavailable' if ci is None else f'{ci[0]:.1f} to {ci[1]:.1f}'} | {samples} / {requested} |")
    lines.extend(["", "Positive regret means the tested alternative outperformed the recommendation. Incomplete groups do not produce an estimate.", "", "Joint picks 13/16: " + str(counter.get("joint13_16", {}).get("status", "unavailable")) + ". Detailed plans, availability, and checkpoints are in the counterfactual artifact.", "", "## Next strategy work", "",
                  "Prioritize alternatives with positive held-out decision regret and weaknesses repeated across opponent populations. Treat limited-world, approximate-catalog, and common-component results as exploratory; collect stronger data before changing the live strategy.", ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/strategy-quality-campaign"))
    parser.add_argument("--data-dir", type=Path, default=Path("runtime/strategy-quality-data"))
    parser.add_argument("--budget-minutes", type=float, default=120)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--historical-seeds", type=int, default=8)
    parser.add_argument("--monte-carlo-seeds", type=int, default=16)
    parser.add_argument("--continuations", type=int, default=32)
    parser.add_argument("--projection-capture", type=Path)
    parser.add_argument("--seats", type=int, nargs="+", default=list(range(1, 15)), help="All fourteen by default; subsets are exploratory smoke runs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 < args.budget_minutes <= 120:
        raise SystemExit("initial campaign budget must be greater than zero and at most 120 minutes")
    if any(seat not in range(1, 15) for seat in args.seats) or len(set(args.seats)) != len(args.seats):
        raise SystemExit("seats must be distinct integers from 1 through 14")
    if min(args.historical_seeds, args.monte_carlo_seeds) < 0 or args.continuations < 2:
        raise SystemExit("seed counts must be nonnegative; continuations must be at least two")
    from .campaign_data import projection_dataset
    from .campaign_history import historical_datasets, historical_status as read_historical_status
    from .campaign_engine import generate_world, simulate_draft
    from .campaign_counterfactual import run_counterfactuals

    start = time.monotonic()
    seconds = args.budget_minutes * 60
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    code_hash = fingerprint()
    write_json(output / "manifest.json", {"version": 1, "fingerprint": code_hash, "arguments": vars(args), "policies": POLICIES, "production_policy_frozen": True, **snapshot_sources(output)})
    base = projection_dataset(args.projection_capture)
    base_path = output / "datasets" / "projection.json"
    _save_dataset(base_path, base)
    historical_status: dict[str, Any] = {}
    historical = []
    try:
        historical = historical_datasets(args.data_dir, deadline=start + seconds * 15 / 120)
        historical_status = {**read_historical_status(args.data_dir), "datasets": [d.name for d in historical], "status": "available" if historical else "data gap"}
    except Exception as exc:
        historical_status = {"status": "data gap", "reason": f"{type(exc).__name__}: {exc}"}
    historical_paths = []
    for index, dataset in enumerate(historical):
        path = output / "datasets" / f"historical-{index}.json"
        _save_dataset(path, dataset)
        historical_paths.append((dataset.name, path))
    historical_deadline = min(start + seconds * 50 / 120, time.monotonic() + seconds * 35 / 120)
    history_tasks = make_tasks(historical_paths, experiment="historical", seeds=args.historical_seeds, output=output, code_fingerprint=code_hash, deadline=historical_deadline, seats=args.seats)
    history_results = run_phase(history_tasks, workers=args.workers, deadline=historical_deadline) if history_tasks else []
    mc_deadline = min(start + seconds * 90 / 120, time.monotonic() + seconds * 40 / 120)
    mc_tasks = make_tasks([(base.name, base_path)], experiment="monte_carlo", seeds=args.monte_carlo_seeds, output=output, code_fingerprint=code_hash, deadline=mc_deadline, seats=args.seats)
    mc_results = run_phase(mc_tasks, workers=args.workers, deadline=mc_deadline) if mc_tasks else []
    counter_deadline = min(start + seconds * 115 / 120, time.monotonic() + seconds * 25 / 120)
    counter: dict[str, Any] = {}
    try:
        world = generate_world(base, seed=SEED_START + 10000, environment="moderate")
        frozen = simulate_draft(world.players, our_seat=13, seed=SEED_START + 10000, opponent_mode="nominal", policy="marginal", deadline=counter_deadline)
        if frozen["status"] == "complete":
            counter = run_counterfactuals(world, base_draft=frozen, seed=SEED_START + 20000, continuations=args.continuations, deadline=counter_deadline, checkpoint_path=output / "counterfactual.json")
        else:
            counter = {"status": "incomplete", "reason": "frozen base draft could not finish"}
    except Exception as exc:
        counter = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    all_results = history_results + mc_results
    rows = [row for result in all_results if result["status"] == "complete" for row in result["rows"]]
    summary = {
        "fingerprint": code_hash, "elapsed_seconds": time.monotonic() - start,
        "completed_jobs": sum(result["status"] == "complete" for result in all_results),
        "target_jobs": len(history_tasks) + len(mc_tasks),
        "targets": {
            "historical_seasons": ["2022-23", "2023-24", "2024-25"],
            "historical_jobs_if_data_available": 3 * len(OPPONENTS) * args.historical_seeds,
            "historical_jobs_completed": sum(result["status"] == "complete" for result in history_results),
            "monte_carlo_jobs_requested": len(mc_tasks),
            "monte_carlo_jobs_completed": sum(result["status"] == "complete" for result in mc_results),
            "seats": args.seats, "policies": list(POLICIES),
            "counterfactual_continuations_per_candidate": args.continuations,
        },
        "historical_status": historical_status, "projection_provenance": base.provenance,
        "player_names": {p.player_id: p.name for p in base.players},
        "missing_scoring_fields": sorted({field for row in rows for field in row.get("missing_scoring_fields", ())}),
        "policy_fallbacks": sum(row.get("policy_fallbacks", 0) for row in rows),
        "feasibility_interventions": sum(row.get("feasibility_interventions", 0) for row in rows),
        "counterfactual": compact_counterfactual(counter), "experiments": {
            experiment: summarize([row for row in rows if row["experiment"] == experiment])
            for experiment in ("historical", "monte_carlo")
        },
        "diagnostics": [{k: v for k, v in result.items() if k != "rows"} for result in all_results if result["status"] != "complete"],
        "limitations": [
            "The strategy was frozen; no production policy was promoted or tuned.",
            "Historical seasons are fixed observations, not independent outcome-world draws; opponent seeds do not create additional independent historical seasons. Historical intervals are therefore unavailable in the pooled report.",
            "Monte Carlo is conditional on its outcome generator and dated forecast catalog; assumed uncertainty is not empirical calibration.",
            "Missing scoring fields are disclosed per dataset; common-component scoring does not establish full-league scoring superiority.",
            "Counterfactuals use a frozen simulated draft, not an exact reconstruction of practice room 200082403.",
            "Under thirty independent Monte Carlo worlds is exploratory; fewer than eight cannot support a directional classification.",
            "Weekly win fractions compare every weekly opponent pairing, not a reconstructed league matchup/playoff schedule.",
        ],
    }
    write_json(output / "summary.json", summary)
    (output / "REPORT.md").write_text(markdown_report(summary), encoding="utf-8")
    print(f"Report: {output / 'REPORT.md'}; elapsed {summary['elapsed_seconds']:.1f}s", flush=True)
    return 1 if counter.get("status") == "error" or any(result["status"] == "error" for result in all_results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
