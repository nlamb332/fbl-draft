"""Render docs/DRAFT_STRATEGY_MARGINAL.md tables from the checked-in artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = Path(__file__).resolve().parent / "validation" / "marginal-lineup-v1"


def _f(value: object, digits: int = 1) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def tables() -> str:
    report = json.loads((ART / "heldout_report.json").read_text(encoding="utf-8"))
    replay = json.loads((ART / "replay_uninterrupted-1000000007.json").read_text(encoding="utf-8"))
    lines = ["| Catalog | Opponents | Mean delta (pts/wk) | 95% CI | Worst seat (mean delta, % of foundation) | C-only median (found. / policy) | Usable starts (found. / policy) | Games benched | Busy-collision days | Gate |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s in report["summaries"]:
        lines.append(
            f"| {s['catalog']} | {s['scenario']} | {_f(s['mean_delta'])} | [{_f(s['paired_bootstrap_95_lower'])}, {_f(s['paired_bootstrap_95_upper'])}] | "
            f"seat {s['worst_seat']}: {_f(s['worst_seat_mean_delta'])} ({_f(100 * s['worst_seat_loss_fraction'], 2)}%) | "
            f"{s['c_only']['foundation']['median']} / {s['c_only']['policy']['median']} | "
            f"{_f(s['usable_starts']['foundation'])} / {_f(s['usable_starts']['policy'])} | "
            f"{_f(s['games_benched']['foundation'], 2)} / {_f(s['games_benched']['policy'], 2)} | "
            f"{_f(s['busy_collisions']['foundation'], 2)} / {_f(s['busy_collisions']['policy'], 2)} | {'pass' if s['gate_pass'] else 'fail'} |"
        )
    out = ["\n".join(lines), "", "C-only count histograms (nominal opponents):", ""]
    for s in report["summaries"]:
        if s["scenario"] == "nominal":
            out.append(f"- {s['catalog']}: foundation {json.dumps(s['c_only']['foundation']['histogram'])}, policy {json.dumps(s['c_only']['policy']['histogram'])}")
    sens = ["| Real catalog comparison | Metric | Mean delta | 95% CI | Worst-seat % |", "|---|---|---|---|---|"]
    for s in report["sensitivity"]:
        sens.append(f"| {s['variant']} vs {s['baseline']} ({s['scenario']}) | {s['metric']} | {_f(s['mean_delta'])} | [{_f(s['paired_bootstrap_95_lower'])}, {_f(s['paired_bootstrap_95_upper'])}] | {_f(100 * s['worst_seat_loss_fraction'], 2)}% |")
    week = replay["opening_week"]
    rows = ["| Pick | Actual | Policy pick (full-policy path) |", "|---|---|---|"]
    rows += [f"| {p['pick']} | {p['actual_pick']} | {p['policy_pick']} |" for p in replay["policy_path"]]
    return "\n".join([
        "\n".join(out), "", "\n".join(sens), "", "\n".join(rows), "",
        f"Opening week (captured NBA schedule): actual {week['actual']['usable_starts']}/{week['actual']['scheduled_player_games']} usable starts "
        f"(C-only {week['actual']['center_only_count']}); policy path {week['policy']['usable_starts']}/{week['policy']['scheduled_player_games']} "
        f"(C-only {week['policy']['center_only_count']}).",
    ])


if __name__ == "__main__":
    print(tables())
