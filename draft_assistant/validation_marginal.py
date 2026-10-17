"""Paired validation of ``marginal-lineup-v1`` against the foundation policy.

Protocol (issue #40 / #44):

* tune only on development seeds, then freeze the parameters;
* held-out: independent seeds x all 14 seats x 13 opponents, on both the
  synthetic catalog and the real ESPN catalog (synthetic schedule);
* nominal opponents plus market/custom opponent-misspecification rows;
* every delta is paired (same seed, seat, opponents' random stream, schedule
  and realized-stat noise) and bootstrapped by seed cluster, not by seat row.

The evaluator is the existing one: exact DP lineup allocation on a seeded
synthetic week, scored with a separate +-10% realized-stat copy
(``validation_runner._weekly_points``).  This module only adds the
marginal-lineup draft policy, usable-start diagnostics, and aggregation.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mock_draft.fixture import (
    TOTAL_TEAMS,
    DraftConfig,
    PlayerRecord,
    allocate_lineup,
    load_player_records,
    make_synthetic_schedule,
    synthetic_fixture,
)

from .contracts import LeagueConfig
from .marginal_lineup import (
    ALGORITHM_VERSION,
    DEFAULT_PARAMS,
    MarginalParams,
    impute_forecast_gaps,
    marginal_fingerprint,
)
from .validation_runner import _draft_one, _live, _weekly_points
from .valuation import ALGORITHM_VERSION as FOUNDATION_VERSION, strategy_fingerprint

ROOT = Path(__file__).resolve().parent.parent
REAL_CATALOG = ROOT / "mock_draft" / "data" / "players_2026-27_espn_projection.csv"
OUT_DIR = Path(__file__).resolve().parent / "validation" / "marginal-lineup-v1"
DEV_SEEDS = tuple(range(20260000, 20260024))
HELDOUT_SEEDS = tuple(range(20280000, 20280100))
CATALOGS = ("synthetic", "real")
SCENARIOS = ("nominal", "market_misspecified", "custom_misspecified")
BOOTSTRAP_SAMPLES = 4000


@lru_cache(maxsize=1)
def _real_players() -> tuple[PlayerRecord, ...]:
    return tuple(load_player_records(REAL_CATALOG))


@lru_cache(maxsize=1)
def _real_players_imputed_truth() -> tuple[PlayerRecord, ...]:
    """The real catalog with PF/DD/TD filled by the imputation model.

    Used only for the "imputed-truth" sensitivity evaluation: it asks how the
    drafted rosters score *if* the imputation model were right.  Drafting
    decisions never see this copy.
    """

    out = []
    for player in _real_players():
        _, _, filled = impute_forecast_gaps(_live(player))
        out.append(replace(player, stats={**player.stats, **filled}) if filled else player)
    return tuple(out)


def catalog_players(catalog: str, seed: int) -> list[PlayerRecord]:
    if catalog == "synthetic":
        return synthetic_fixture(player_count=195, seed=seed)
    if catalog == "real":
        return list(_real_players())
    raise ValueError(f"unknown catalog {catalog!r}")


def lineup_diagnostics(roster: Sequence[PlayerRecord], players: Sequence[PlayerRecord], *, seed: int) -> dict[str, float]:
    """Usable starts, benched games and busy-day collisions on the eval week."""

    schedule = make_synthetic_schedule(players, days=7, seed=seed)
    starts = benched = collisions = scheduled = 0
    for day in range(7):
        allocation = allocate_lineup(roster, day=day, schedule=schedule, method="dp", config=DraftConfig())
        starter_ids = set(allocation.slots.values())
        active_starters = sum(schedule.is_available(pid, day) for pid in starter_ids)
        active_bench = sum(schedule.is_available(p.player_id, day) for p in roster if p.player_id not in starter_ids)
        starts += active_starters
        benched += active_bench
        collisions += int(active_bench > 0)
        scheduled += sum(schedule.is_available(p.player_id, day) for p in roster)
    return {
        "usable_starts": float(starts),
        "games_benched": float(benched),
        "busy_collisions": float(collisions),
        "scheduled_games": float(scheduled),
    }


def variants(include_sensitivity: bool = False, params: MarginalParams = DEFAULT_PARAMS) -> dict[str, tuple[str, MarginalParams | None]]:
    base: dict[str, tuple[str, MarginalParams | None]] = {
        "foundation": ("foundation", None),
        "marginal": ("marginal-lineup-v1", params),
        "marginal_no_imputation": ("marginal-lineup-v1", replace(params, use_imputation=False)),
    }
    if include_sensitivity:
        base["marginal_pf_x0.75"] = ("marginal-lineup-v1", replace(params, pf_scale=0.75))
        base["marginal_pf_x1.25"] = ("marginal-lineup-v1", replace(params, pf_scale=1.25))
    return base


def run_task(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One (catalog, scenario, seed): 14 seats x requested variants."""

    catalog = str(task["catalog"])
    scenario = str(task["scenario"])
    seed = int(task["seed"])
    specs = {
        name: (policy, MarginalParams(**params) if params is not None else None)
        for name, (policy, params) in task["variants"].items()
    }
    players = catalog_players(catalog, seed)
    truth_players = list(_real_players_imputed_truth()) if catalog == "real" else None
    rows: list[dict[str, Any]] = []
    for seat in range(1, TOTAL_TEAMS + 1):
        eval_seed = seed + seat * 1000
        for name, (policy, params) in specs.items():
            started = time.perf_counter()
            roster, _, logs = _draft_one(players, our_seat=seat, seed=seed, opponent_mode=scenario, policy=policy, policy_params=params)
            draft_seconds = time.perf_counter() - started
            row: dict[str, Any] = {
                "catalog": catalog,
                "scenario": scenario,
                "seed": seed,
                "seat": seat,
                "variant": name,
                "weekly_points": _weekly_points(roster, players, seed=eval_seed),
                "roster": "|".join(p.player_id for p in roster),
                "c_only": sum(p.eligibility == ("C",) for p in roster),
                "center_eligible": sum("C" in p.eligibility for p in roster),
                "draft_seconds": draft_seconds,
                "fallbacks": sum(bool(item.get("fallback")) for item in logs),
                "max_pick_seconds": max((float(item.get("elapsed", 0.0)) for item in logs), default=0.0),
            }
            row.update(lineup_diagnostics(roster, players, seed=eval_seed))
            if truth_players is not None:
                by_id = {p.player_id: p for p in truth_players}
                row["imputed_truth_points"] = _weekly_points([by_id[p.player_id] for p in roster], truth_players, seed=eval_seed)
            rows.append(row)
    return rows


def run_matrix(
    *,
    seeds: Sequence[int],
    catalogs: Sequence[str],
    scenarios: Sequence[str],
    variant_specs: Mapping[str, tuple[str, MarginalParams | None]],
    out_path: Path,
    workers: int = 28,
) -> list[dict[str, Any]]:
    """Run tasks in parallel, appending JSON lines so an interrupted run resumes."""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[tuple[str, str, int]] = set()
    rows: list[dict[str, Any]] = []
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows.append(row)
                done.add((row["catalog"], row["scenario"], int(row["seed"])))
    serial = {name: (policy, params.as_dict() if params is not None else None) for name, (policy, params) in variant_specs.items()}
    tasks = [
        {"catalog": catalog, "scenario": scenario, "seed": seed, "variants": serial}
        for scenario in scenarios
        for catalog in catalogs
        for seed in seeds
        if (catalog, scenario, seed) not in done
    ]
    started = time.perf_counter()
    with out_path.open("a", encoding="utf-8") as handle, ProcessPoolExecutor(max_workers=workers) as pool:
        for count, result in enumerate(pool.map(run_task, tasks, chunksize=1), start=1):
            for row in result:
                handle.write(json.dumps(row) + "\n")
            handle.flush()
            rows.extend(result)
            print(f"[{count}/{len(tasks)}] {result[0]['catalog']}/{result[0]['scenario']}/{result[0]['seed']} elapsed {time.perf_counter() - started:.0f}s", flush=True)
    return rows


# --- aggregation -------------------------------------------------------------

def bootstrap_ci(values: Sequence[float], *, seed: int = 90210, samples: int = BOOTSTRAP_SAMPLES) -> tuple[float, float]:
    """Percentile 95% interval of the mean, resampling seed-cluster values."""

    if not values:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    clusters = list(values)
    means = sorted(statistics.fmean(clusters[rng.randrange(len(clusters))] for _ in clusters) for _ in range(samples))
    return means[int(0.025 * samples)], means[min(samples - 1, int(0.975 * samples))]


def _index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, int, int, str], Mapping[str, Any]]:
    return {(r["catalog"], r["scenario"], int(r["seed"]), int(r["seat"]), r["variant"]): r for r in rows}


def summarize(
    rows: Sequence[Mapping[str, Any]],
    *,
    catalog: str,
    scenario: str,
    variant: str,
    baseline: str = "foundation",
    metric: str = "weekly_points",
    seed_offset: int = 0,
) -> dict[str, Any]:
    index = _index(r for r in rows if r["catalog"] == catalog and r["scenario"] == scenario)
    seeds = sorted({int(r["seed"]) for r in rows if r["catalog"] == catalog and r["scenario"] == scenario})
    by_seed: dict[int, list[float]] = {}
    by_seat: dict[int, list[float]] = {seat: [] for seat in range(1, TOTAL_TEAMS + 1)}
    base_by_seat: dict[int, list[float]] = {seat: [] for seat in range(1, TOTAL_TEAMS + 1)}
    for seed in seeds:
        for seat in range(1, TOTAL_TEAMS + 1):
            a = index.get((catalog, scenario, seed, seat, variant))
            b = index.get((catalog, scenario, seed, seat, baseline))
            if a is None or b is None or metric not in a or metric not in b:
                continue
            delta = float(a[metric]) - float(b[metric])
            by_seed.setdefault(seed, []).append(delta)
            by_seat[seat].append(delta)
            base_by_seat[seat].append(float(b[metric]))
    clusters = [statistics.fmean(v) for v in by_seed.values()]
    lower, upper = bootstrap_ci(clusters, seed=90210 + seed_offset)
    seat_rows = []
    for seat in range(1, TOTAL_TEAMS + 1):
        if not by_seat[seat]:
            continue
        mean_delta = statistics.fmean(by_seat[seat])
        base = statistics.fmean(base_by_seat[seat])
        seat_rows.append({"seat": seat, "mean_delta": mean_delta, "baseline_mean": base, "mean_delta_fraction": mean_delta / base if base else 0.0})
    worst = min(seat_rows, key=lambda r: r["mean_delta"]) if seat_rows else None

    def diag(name: str, key: str) -> float:
        values = [float(r[key]) for r in rows if r["catalog"] == catalog and r["scenario"] == scenario and r["variant"] == name and key in r]
        return statistics.fmean(values) if values else float("nan")

    def c_only_distribution(name: str) -> dict[str, Any]:
        values = [int(r["c_only"]) for r in rows if r["catalog"] == catalog and r["scenario"] == scenario and r["variant"] == name]
        counts = Counter(values)
        return {
            "histogram": {str(k): counts[k] for k in sorted(counts)},
            "median": statistics.median(values) if values else None,
            "mean": statistics.fmean(values) if values else None,
        }

    return {
        "catalog": catalog,
        "scenario": scenario,
        "variant": variant,
        "baseline": baseline,
        "metric": metric,
        "independent_seed_clusters": len(clusters),
        "seat_rows": sum(len(v) for v in by_seed.values()),
        "mean_delta": statistics.fmean(clusters) if clusters else float("nan"),
        "paired_bootstrap_95_lower": lower,
        "paired_bootstrap_95_upper": upper,
        "baseline_mean": statistics.fmean(statistics.fmean(v) for v in base_by_seat.values() if v) if seat_rows else float("nan"),
        "worst_seat": worst["seat"] if worst else None,
        "worst_seat_mean_delta": worst["mean_delta"] if worst else float("nan"),
        "worst_seat_loss_fraction": worst["mean_delta_fraction"] if worst else float("nan"),
        "by_seat": seat_rows,
        "c_only": {"foundation": c_only_distribution(baseline), "policy": c_only_distribution(variant)},
        "center_eligible_mean": {"foundation": diag(baseline, "center_eligible"), "policy": diag(variant, "center_eligible")},
        "usable_starts": {"foundation": diag(baseline, "usable_starts"), "policy": diag(variant, "usable_starts")},
        "games_benched": {"foundation": diag(baseline, "games_benched"), "policy": diag(variant, "games_benched")},
        "busy_collisions": {"foundation": diag(baseline, "busy_collisions"), "policy": diag(variant, "busy_collisions")},
        "fallbacks": sum(int(r.get("fallbacks", 0)) for r in rows if r["catalog"] == catalog and r["scenario"] == scenario and r["variant"] == variant),
        "gate_pass": bool(clusters) and lower > 0 and bool(worst) and worst["mean_delta_fraction"] >= -0.05,
    }


def tune(*, workers: int, out_dir: Path, seeds: Sequence[int] = DEV_SEEDS) -> dict[str, Any]:
    """Grid search on development seeds only; returns the chosen parameters."""

    grid = [
        MarginalParams(weeks=weeks, vor_weight=vor, depth_scale=depth, opponent_market_weight=market)
        for weeks in (6, 12)
        for vor in (0.0, 0.5)
        for depth in (0.75, 1.0, 1.25)
        for market in (0.25, 0.5, 0.75)
    ]
    foundation_rows = run_matrix(
        seeds=seeds,
        catalogs=CATALOGS,
        scenarios=("nominal",),
        variant_specs={"foundation": ("foundation", None)},
        out_path=out_dir / "tuning" / "dev_foundation.jsonl",
        workers=workers,
    )
    results = []
    for number, params in enumerate(grid):
        marginal_rows = run_matrix(
            seeds=seeds,
            catalogs=CATALOGS,
            scenarios=("nominal",),
            variant_specs={"marginal": ("marginal-lineup-v1", params)},
            out_path=out_dir / "tuning" / f"dev_config_{number:02d}.jsonl",
            workers=workers,
        )
        rows = [*foundation_rows, *marginal_rows]
        per_catalog = {c: summarize(rows, catalog=c, scenario="nominal", variant="marginal") for c in CATALOGS}
        results.append({
            "config": params.as_dict(),
            "mean_delta": {c: per_catalog[c]["mean_delta"] for c in CATALOGS},
            "ci_lower": {c: per_catalog[c]["paired_bootstrap_95_lower"] for c in CATALOGS},
            "worst_seat_loss_fraction": {c: per_catalog[c]["worst_seat_loss_fraction"] for c in CATALOGS},
            "fallbacks": sum(per_catalog[c]["fallbacks"] for c in CATALOGS),
        })
        print(f"config {number}: {results[-1]}", flush=True)

    def objective(item: Mapping[str, Any]) -> float:
        # The worse catalog's mean delta: selects for robustness, not for one catalog.
        return min(item["mean_delta"].values())

    feasible = [item for item in results if min(item["worst_seat_loss_fraction"].values()) >= -0.05 and item["fallbacks"] == 0]
    pool = feasible or results
    chosen = max(pool, key=objective)
    payload = {
        "protocol": "development seeds only; frozen before held-out",
        "dev_seeds": [seeds[0], seeds[-1], len(seeds)],
        "objective": "maximize the smaller of the two catalog mean paired deltas; require worst-seat loss <= 5% and no fallbacks",
        "grid": results,
        "chosen": chosen["config"],
        "chosen_met_constraints": bool(feasible),
    }
    (out_dir / "tuning_dev.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_heldout(*, params: MarginalParams, out_dir: Path, workers: int, seeds: Sequence[int] = HELDOUT_SEEDS) -> list[dict[str, Any]]:
    """Run the frozen policy on held-out seeds; resumable via JSON lines."""

    rows: list[dict[str, Any]] = []
    rows += run_matrix(
        seeds=seeds, catalogs=("synthetic",), scenarios=SCENARIOS,
        variant_specs={"foundation": ("foundation", None), "marginal": ("marginal-lineup-v1", params)},
        out_path=out_dir / "heldout_synthetic.jsonl", workers=workers,
    )
    rows += run_matrix(
        seeds=seeds, catalogs=("real",), scenarios=SCENARIOS,
        variant_specs=variants(False, params),
        out_path=out_dir / "heldout_real.jsonl", workers=workers,
    )
    rows += run_matrix(
        seeds=seeds, catalogs=("real",), scenarios=("nominal",),
        variant_specs={name: spec for name, spec in variants(True, params).items() if name.startswith("marginal_pf")},
        out_path=out_dir / "heldout_real_pf_sensitivity.jsonl", workers=workers,
    )
    return rows


def _summary_row(summary: Mapping[str, Any]) -> dict[str, Any]:
    skip = ("by_seat", "c_only", "usable_starts", "games_benched", "busy_collisions", "center_eligible_mean")
    flat = {key: value for key, value in summary.items() if key not in skip}
    flat["c_only_median_foundation"] = summary["c_only"]["foundation"]["median"]
    flat["c_only_median_policy"] = summary["c_only"]["policy"]["median"]
    flat["c_only_mean_foundation"] = summary["c_only"]["foundation"]["mean"]
    flat["c_only_mean_policy"] = summary["c_only"]["policy"]["mean"]
    flat["c_only_histogram_foundation"] = json.dumps(summary["c_only"]["foundation"]["histogram"])
    flat["c_only_histogram_policy"] = json.dumps(summary["c_only"]["policy"]["histogram"])
    for key in ("usable_starts", "games_benched", "busy_collisions", "center_eligible_mean"):
        flat[f"{key}_foundation"] = summary[key]["foundation"]
        flat[f"{key}_policy"] = summary[key]["policy"]
    return flat


def build_report(rows: Sequence[Mapping[str, Any]], *, params: MarginalParams, tuning: Mapping[str, Any] | None, out_dir: Path) -> dict[str, Any]:
    """Aggregate held-out rows into the report, CSVs and promotion artifact."""

    summaries: list[dict[str, Any]] = []
    for catalog in CATALOGS:
        for scenario in SCENARIOS:
            summaries.append(summarize(rows, catalog=catalog, scenario=scenario, variant="marginal"))
    sensitivity: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        sensitivity.append(summarize(rows, catalog="real", scenario=scenario, variant="marginal_no_imputation"))
    for scenario in SCENARIOS:
        sensitivity.append(summarize(rows, catalog="real", scenario=scenario, variant="marginal", metric="imputed_truth_points"))
        sensitivity.append(summarize(rows, catalog="real", scenario=scenario, variant="marginal_no_imputation", metric="imputed_truth_points"))
    for name in ("marginal_pf_x0.75", "marginal_pf_x1.25"):
        sensitivity.append(summarize(rows, catalog="real", scenario="nominal", variant=name))
        sensitivity.append(summarize(rows, catalog="real", scenario="nominal", variant=name, metric="imputed_truth_points"))
    # Imputation effect on the policy itself: marginal vs marginal_no_imputation.
    for metric in ("weekly_points", "imputed_truth_points"):
        sensitivity.append(summarize(rows, catalog="real", scenario="nominal", variant="marginal", baseline="marginal_no_imputation", metric=metric))
    nominal = {s["catalog"]: s for s in summaries if s["scenario"] == "nominal"}
    gate = all(nominal[c]["gate_pass"] and nominal[c]["independent_seed_clusters"] >= 100 and nominal[c]["fallbacks"] == 0 for c in CATALOGS)
    latencies = [float(r["max_pick_seconds"]) for r in rows if r["variant"] == "marginal"]
    metadata = {
        "runner": "draft_assistant.validation_marginal",
        "policy": ALGORITHM_VERSION,
        "foundation": FOUNDATION_VERSION,
        "heldout_seed_range": [HELDOUT_SEEDS[0], HELDOUT_SEEDS[-1]],
        "independent_seed_clusters": len({r["seed"] for r in rows}),
        "dev_seed_range": [DEV_SEEDS[0], DEV_SEEDS[-1]],
        "teams": 14,
        "opponents": 13,
        "rounds": 10,
        "picks_per_draft": 140,
        "seats_per_seed": 14,
        "scenarios": list(SCENARIOS),
        "catalogs": {"synthetic": "synthetic_fixture(195 players, per-seed)", "real": "mock_draft/data/players_2026-27_espn_projection.csv with seeded synthetic schedule"},
        "foundation_vs_marginal_lineup": True,
        "primary_metric": "validation_runner._weekly_points (exact DP lineup, +-10% realized-stat noise, synthetic week); known scoring components only on the real catalog",
        "sensitivity_metric": "imputed_truth_points: same drafted rosters scored with PF/DD/TD imputed into the realized stats (real catalog only)",
        "bootstrap": f"{BOOTSTRAP_SAMPLES} percentile resamples of per-seed cluster means",
        "frozen_params": params.as_dict(),
        "max_marginal_pick_seconds_in_validation": max(latencies) if latencies else None,
        "foundation_fingerprint": strategy_fingerprint(LeagueConfig()),
    }
    keys = ("mean_delta", "paired_bootstrap_95_lower", "paired_bootstrap_95_upper", "worst_seat", "worst_seat_mean_delta", "worst_seat_loss_fraction", "gate_pass", "fallbacks")
    decision = {
        "status": "promotable" if gate else "advisory",
        "rule": "promote only if the paired 95% bootstrap lower bound (by seed cluster) > 0 and no seat mean loss exceeds 5% on BOTH catalogs (nominal opponents), with at least 100 seed clusters and no budget fallback",
        "per_catalog": {c: {k: nominal[c][k] for k in keys} for c in CATALOGS},
    }
    kept = ("catalog", "scenario", "variant", "independent_seed_clusters", "seat_rows", "mean_delta", "paired_bootstrap_95_lower", "paired_bootstrap_95_upper", "worst_seat_loss_fraction", "gate_pass")
    artifact = {
        "policy": ALGORITHM_VERSION,
        "status": decision["status"],
        "algorithm_version": ALGORITHM_VERSION,
        "config_fingerprint": marginal_fingerprint(LeagueConfig(), params),
        "params": params.as_dict(),
        "metadata": metadata,
        "summaries": [{k: v for k, v in s.items() if k in kept} for s in summaries],
        "decision": decision,
    }
    report = {"metadata": metadata, "decision": decision, "summaries": summaries, "sensitivity": sensitivity}
    (out_dir / "heldout_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "promotion_artifact.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    write_csv(out_dir / "heldout_summary.csv", [_summary_row(s) for s in [*summaries, *sensitivity]])
    write_csv(out_dir / "heldout_rows.csv", list(rows))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("tune", "heldout", "smoke"))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--workers", type=int, default=28)
    parser.add_argument("--seeds", type=int, default=None)
    parser.add_argument("--params", default=None, help="JSON file with the frozen parameters")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "tune":
        payload = tune(workers=args.workers, out_dir=out_dir, seeds=DEV_SEEDS[: args.seeds] if args.seeds else DEV_SEEDS)
        print(json.dumps(payload["chosen"], indent=2))
        return 0
    params = MarginalParams(**json.loads(Path(args.params).read_text(encoding="utf-8"))["params"]) if args.params else DEFAULT_PARAMS
    if args.mode == "smoke":
        rows = run_matrix(seeds=DEV_SEEDS[: args.seeds or 2], catalogs=CATALOGS, scenarios=("nominal",), variant_specs=variants(False, params), out_path=out_dir / "smoke.jsonl", workers=args.workers)
        for catalog in CATALOGS:
            print(json.dumps({k: v for k, v in summarize(rows, catalog=catalog, scenario="nominal", variant="marginal").items() if k != "by_seat"}, indent=2))
        return 0
    tuning = json.loads((out_dir / "tuning_dev.json").read_text(encoding="utf-8")) if (out_dir / "tuning_dev.json").exists() else None
    rows = run_heldout(params=params, out_dir=out_dir, workers=args.workers, seeds=HELDOUT_SEEDS[: args.seeds] if args.seeds else HELDOUT_SEEDS)
    report = build_report(rows, params=params, tuning=tuning, out_dir=out_dir)
    print(json.dumps(report["decision"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
