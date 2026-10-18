"""Plot held-out Lock-In results, clustering correlated seats/weeks by pool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np

LABELS = {
    "custom_per_game": "Custom points per game",
    "availability": "Availability adjusted",
    "legacy_daily_lineup_coverage": "Prior coverage heuristic",
    "lock_in_value": "Forecast Lock-In value",
    "replacement_aware": "Position replacement value",
    "market_static": "Synthetic market order",
    "market_value_blend": "Market/value blend",
    "market_aware_timing": "Market-aware timing",
    "always_first": "First played game",
    "mean_threshold": "Mean threshold",
    "forecast_dp": "Forecast stopping rule",
    "nominal": "Nominal model",
    "injury_stress": "Injury stress",
    "forecast_shift": "Forecast shift",
}
BG, INK, MUTED, ACCENT = "#f6f9fc", "#182c43", "#52647b", "#137f91"


def label(key):
    return LABELS.get(key, key.replace("_", " ").title())


def save(fig, output, name):
    for extension in ("png", "svg"):
        fig.savefig(output / f"{name}.{extension}", dpi=180, facecolor=BG)
    svg = output / f"{name}.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(fig)


def render(source: Path, output: Path) -> None:
    all_rows = json.loads((source / "rows.json").read_text(encoding="utf-8"))
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    selection = json.loads((source / "selection.json").read_text(encoding="utf-8"))
    selected = selection["development_selected_strategy"]
    rows = [r for r in all_rows if r["split"] == "heldout" and r["scenario"] == "nominal" and r["policy_rule"] == "forecast_dp"]
    seeds = sorted({r["pool_seed"] for r in rows})
    strategies = sorted({r["strategy"] for r in rows})
    groups = {s: [r for r in rows if r["strategy"] == s] for s in strategies}
    mean = lambda s, metric: float(np.mean([r[metric] for r in groups[s]]))
    ordered = sorted(strategies, key=lambda s: mean(s, "weekly_realized_points"), reverse=True)
    pool_means = {
        s: np.array([np.mean([r["weekly_realized_points"] for r in groups[s] if r["pool_seed"] == seed]) for seed in seeds])
        for s in strategies
    }
    resamples = np.random.default_rng(20261017).integers(0, len(seeds), size=(4000, len(seeds)))
    comparisons = []
    for strategy in ordered:
        if strategy == selected:
            continue
        deltas = pool_means[selected] - pool_means[strategy]
        low, high = np.quantile(deltas[resamples].mean(axis=1), [.025, .975])
        comparisons.append({"baseline": strategy, "mean": float(deltas.mean()), "low": float(low), "high": float(high)})

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                         "svg.hashsalt": "six-team-lock-in-20261017"})
    output.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(17, 11), facecolor=BG)
    fig.subplots_adjust(left=.24, right=.96, top=.79, bottom=.16, wspace=.77, hspace=.72)
    fig.text(.04, .95, "Six-team Lock-In strategy outcomes", fontsize=25, weight="bold")
    fig.text(.04, .91, f"Held-out nominal model: {len(seeds)} pools × 6 seats × {metadata['weeks']} weeks · one selected game per starter per week", fontsize=12)
    fig.text(.04, .875, f"Development-selected draft policy: {label(selected)} · every draft policy uses the same forecast stopping rule", fontsize=11, color=MUTED)
    for ax in axes.flat:
        ax.set_facecolor(BG)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#ced9e5")
        ax.grid(axis="x", color="#e0e8f0", linewidth=.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)

    def bars(ax, metric, title, xlabel, percent=False):
        values = [mean(s, metric) for s in ordered]
        ax.barh(range(len(ordered)), values, height=.62,
                color=[ACCENT if s == selected else "#a2b2c6" for s in ordered])
        ax.set_yticks(range(len(ordered)), [label(s) for s in ordered])
        ax.invert_yaxis()
        ax.set_title(title, loc="left", weight="bold", fontsize=14, pad=16)
        ax.set_xlabel(xlabel, labelpad=12)
        ax.set_xlim(0, 1 if percent else max(max(values)*1.24, 1))
        if percent:
            ax.xaxis.set_major_formatter(PercentFormatter(1))
        for i, (value, strategy) in enumerate(zip(values, ordered)):
            ax.text(value + (.014 if percent else max(values)*.018), i,
                    f"{value:.1%}" if percent else f"{value:,.1f}", va="center",
                    weight="bold" if strategy == selected else "normal")

    bars(axes[0, 0], "weekly_realized_points", "Usable weekly Lock-In points", "Mean score across nine assigned starting slots")
    ax = axes[0, 1]
    for i, item in enumerate(comparisons):
        ax.errorbar(item["mean"], i,
                    xerr=[[max(0, item["mean"]-item["low"])], [max(0, item["high"]-item["mean"])]],
                    fmt="o", color=ACCENT, capsize=4)
    ax.set_yticks(range(len(comparisons)), ["vs. " + label(item["baseline"]) for item in comparisons])
    ax.invert_yaxis()
    ax.axvline(0, color=MUTED, linestyle="--", linewidth=1)
    ax.set_title("Selected policy's paired advantage", loc="left", weight="bold", fontsize=14, pad=16)
    ax.set_xlabel("Weekly point difference · 95% pool-cluster interval", labelpad=12)
    bars(axes[1, 0], "weekly_opponent_pair_win_fraction", "Weekly opponent-pair win rate", "Five opponents per league; ties count half", percent=True)
    bars(axes[1, 1], "hindsight_gap", "Gap to hindsight ceiling", "Future-best-game bound minus online score; lower is closer")
    fig.text(.04, .085, "Exploratory synthetic game worlds · fixed starters before the week · no future scores available to lock/pass decisions", fontsize=11)
    fig.text(.04, .05, "Hindsight is a report-only bound. Intervals resample pools, retaining correlated seats and weeks. No waivers, IR or live policy validation.", fontsize=10, color=MUTED)
    save(fig, output, "strategy_outcomes")

    heldout = [r for r in all_rows if r["split"] == "heldout"]
    scenarios = [s for s in ("nominal", "injury_stress", "forecast_shift") if any(r["scenario"] == s for r in heldout)]
    rules = [s for s in ("always_first", "mean_threshold", "forecast_dp") if any(r["policy_rule"] == s for r in heldout)]
    def cell(strategy, scenario, rule):
        return float(np.mean([r["weekly_realized_points"] for r in heldout if r["strategy"] == strategy and r["scenario"] == scenario and r["policy_rule"] == rule]))
    strategy_matrix = np.array([[cell(s, scenario, "forecast_dp") for scenario in scenarios] for s in ordered])
    management_matrix = np.array([[cell(selected, scenario, rule) for scenario in scenarios] for rule in rules])
    management_comparisons = []
    for rule in rules:
        if rule == "forecast_dp":
            continue
        deltas = np.array([
            np.mean([r["weekly_realized_points"] for r in heldout if r["strategy"] == selected and r["scenario"] == "nominal" and r["policy_rule"] == "forecast_dp" and r["pool_seed"] == seed])
            - np.mean([r["weekly_realized_points"] for r in heldout if r["strategy"] == selected and r["scenario"] == "nominal" and r["policy_rule"] == rule and r["pool_seed"] == seed])
            for seed in seeds
        ])
        low, high = np.quantile(deltas[resamples].mean(axis=1), [.025, .975])
        management_comparisons.append({"baseline":rule,"mean":float(deltas.mean()),"low":float(low),"high":float(high)})
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.5), facecolor=BG)
    fig.subplots_adjust(left=.22, right=.93, top=.72, bottom=.25, wspace=.78)
    fig.text(.04, .92, "Lock-In robustness and weekly decisions", fontsize=23, weight="bold")
    fig.text(.04, .86, "Held-out mean weekly points · separate tests of draft policy and lock/pass management", fontsize=12)
    for ax, matrix, names, title in (
        (axes[0], strategy_matrix, ordered, "Draft policies under the same stopping rule"),
        (axes[1], management_matrix, rules, "Lock/pass rules for the selected draft policy"),
    ):
        im = ax.imshow(matrix, cmap="YlGnBu", aspect="auto")
        midpoint = (matrix.min()+matrix.max())/2
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                ax.text(j, i, f"{matrix[i,j]:.1f}", ha="center", va="center", color="white" if matrix[i,j]>midpoint else INK, fontsize=12)
        ax.set_yticks(range(len(names)), [label(name) for name in names])
        ax.set_xticks(range(len(scenarios)), [label(s) for s in scenarios], rotation=22, ha="right")
        ax.set_title(title, loc="left", fontsize=12, weight="bold", pad=16)
        ax.tick_params(length=0)
        fig.colorbar(im, ax=ax, fraction=.05, pad=.06)
    fig.text(.04, .105, "The selected draft policy is fixed using development worlds. Stress scenarios change assumed health or forecast accuracy.", fontsize=11)
    fig.text(.04, .065, "Fixed weekly starter assignment excludes bench switching. These are model sensitivities, not real-player forecasts or calibrated win probabilities.", fontsize=10, color=MUTED)
    save(fig, output, "robustness_and_management")
    (output / "plot_statistics.json").write_text(json.dumps({
        "selected_strategy": selected, "heldout_pool_seeds": seeds, "bootstrap_seed": 20261017,
        "bootstrap_resamples": 4000, "paired_comparisons": comparisons,
        "strategy_order": ordered, "scenario_order": scenarios, "management_rule_order": rules,
        "robustness_weekly_points": strategy_matrix.tolist(), "management_weekly_points": management_matrix.tolist(),
        "management_comparisons": management_comparisons,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    render(args.source, args.output)
