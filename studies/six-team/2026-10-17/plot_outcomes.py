"""Render the held-out six-team results; requires matplotlib and numpy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import PercentFormatter

LABELS = {
    "custom_per_game": "Custom points per game",
    "availability": "Availability adjusted",
    "market": "Synthetic market order",
    "lineup_aware": "Lineup coverage heuristic",
    "big_preference": "Fixed big preference",
}


def render(source: Path, output: Path) -> None:
    rows = json.loads((source / "rows.json").read_text(encoding="utf-8"))
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    selection = json.loads((source / "selection.json").read_text(encoding="utf-8"))
    heldout = [row for row in rows if row["split"] == "heldout"]
    selected = selection["development_selected_strategy"]
    seeds = sorted({row["pool_seed"] for row in heldout})
    keys = metadata["strategies"]
    grouped = {key: [row for row in heldout if row["strategy"] == key] for key in keys}
    mean = lambda key, metric: float(np.mean([row[metric] for row in grouped[key]]))
    ordered = sorted(keys, key=lambda key: mean(key, "weekly_realized_points"), reverse=True)
    seed_means = {
        key: np.array([np.mean([row["weekly_realized_points"] for row in grouped[key] if row["pool_seed"] == seed]) for seed in seeds])
        for key in keys
    }
    samples = np.random.default_rng(20261017).integers(0, len(seeds), size=(4000, len(seeds)))
    comparisons = []
    for key in ordered:
        if key == selected:
            continue
        deltas = seed_means[selected] - seed_means[key]
        low, high = np.quantile(deltas[samples].mean(axis=1), [0.025, 0.975])
        comparisons.append((key, float(deltas.mean()), float(low), float(high)))

    background, ink, muted, accent = "#f6f9fc", "#182c43", "#52647b", "#137f91"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "text.color": ink,
                         "axes.labelcolor": muted, "xtick.color": muted, "ytick.color": muted})
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), facecolor=background)
    fig.subplots_adjust(left=.24, right=.96, top=.79, bottom=.15, wspace=.68, hspace=.68)
    fig.text(.04, .95, "Six-team draft strategy outcomes", fontsize=25, weight="bold")
    fig.text(.04, .91, f"Held-out sample: {len(seeds)} synthetic pools × 6 seats = {len(seeds)*6} drafts per strategy · 84 picks · 9 starters + 5 bench", fontsize=12)
    fig.text(.04, .875, f"Development-selected strategy: {LABELS[selected]} · user's scoring with both bonus pairs stacking", fontsize=11, color=muted)

    for ax in axes.flat:
        ax.set_facecolor(background)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#ced9e5")
        ax.grid(axis="x", color="#e0e8f0", linewidth=.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)

    colors = [accent if key == selected else "#a2b2c6" for key in ordered]

    def bars(ax, metric, title, xlabel, percent=False):
        values = [mean(key, metric) for key in ordered]
        ax.barh(range(len(ordered)), values, color=colors, height=.62)
        ax.set_yticks(range(len(ordered)), [LABELS[key] for key in ordered])
        ax.invert_yaxis()
        ax.set_title(title, loc="left", weight="bold", fontsize=14, pad=16)
        ax.set_xlabel(xlabel, labelpad=12)
        ax.set_xlim(0, 1 if percent else max(values)*1.22)
        if percent:
            ax.xaxis.set_major_formatter(PercentFormatter(1))
        for index, (value, key) in enumerate(zip(values, ordered)):
            ax.text(value + (.015 if percent else max(values)*.018), index,
                    f"{value:.1%}" if percent else f"{value:,.1f}", va="center",
                    weight="bold" if key == selected else "normal")

    bars(axes[0, 0], "weekly_realized_points", "Usable weekly points", "Mean points from forecast-selected legal lineups")
    ax = axes[0, 1]
    for index, (key, delta, low, high) in enumerate(comparisons):
        ax.errorbar(delta, index, xerr=[[max(0, delta-low)], [max(0, high-delta)]], fmt="o", color=accent, capsize=4)
    ax.set_yticks(range(len(comparisons)), ["vs. " + LABELS[item[0]] for item in comparisons])
    ax.invert_yaxis()
    ax.axvline(0, color=muted, linestyle="--", linewidth=1)
    ax.set_title("Selected strategy's paired advantage", loc="left", weight="bold", fontsize=14, pad=16)
    ax.set_xlabel("Weekly point difference · 95% pool-cluster interval", labelpad=12)
    bars(axes[1, 0], "weekly_opponent_pair_win_fraction", "Weekly opponent-pair win rate", "Fraction of comparisons won; ties count half", percent=True)
    bars(axes[1, 1], "weekly_benched_points", "Points left on the bench", "Mean weekly active points outside starting lineups")
    fig.text(.04, .075, "Exploratory synthetic outcomes · fixed rosters · forecasts select lineups · intervals resample pools, not correlated seats", fontsize=11)
    fig.text(.04, .045, "Weekly scores are not comparable to the old season totals. No real-player projections, calibrated outcomes or live advisor policy are validated.", fontsize=10, color=muted)
    output.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        fig.savefig(output / f"strategy_outcomes.{extension}", dpi=180, facecolor=background)
    svg = output / "strategy_outcomes.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(fig)
    (output / "plot_statistics.json").write_text(json.dumps({
        "selected_strategy": selected, "heldout_pool_seeds": seeds,
        "bootstrap_resamples": 4000, "bootstrap_seed": 20261017,
        "paired_comparisons": [{"baseline": key, "mean": delta, "low": low, "high": high} for key, delta, low, high in comparisons],
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    render(args.source, args.output)
