"""Normalize the rendered ESPN projection snapshot into simulation input.

The input is intentionally the text captured from the signed-in, rendered ESPN
projection table.  This script does no enrichment: it preserves the displayed
per-game primitives and only derives a stable id, fantasy-slot eligibility,
the displayed-row market proxy, and an *implied* games value from total/average.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "espn_ui_raw_2026-10-10.txt"
OUT = ROOT / "players_2026-27_espn_projection.csv"

STAT_NAMES = (
    "MIN",
    "FGM",
    "FGA",
    "FTM",
    "FTA",
    "3PM",
    "REB",
    "AST",
    "STL",
    "BLK",
    "TO",
    "PTS",
)

OUT_COLUMNS = [
    "player_id",
    "name",
    "team",
    "injury_status",
    "espn_positions",
    "eligibility",
    "source_row_rank",
    "market_rank",
    "adp",
    "market_rank_source",
    "adp_source",
    "mpg",
    "fgm",
    "fga",
    "ftm",
    "fta",
    "3pm",
    "reb",
    "ast",
    "stl",
    "blk",
    "to",
    "pts",
    "projected_total",
    "displayed_fantasy_average",
    "gp_projected",
    "gp_implied_from_total_avg",
    "pf",
    "tf",
    "ej",
    "dd",
    "td",
    "qd",
    "upside",
    "projected_availability",
    "availability_source",
    "schedule_group",
    "source_label",
    "stats",
]


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "player"


def _parse_row(raw: str) -> tuple[str, str, str, str, list[str]] | None:
    """Return name, status, team, positions, numeric fields.

    ESPN rows have either ``name|team|positions`` or
    ``name|status|team|positions`` before the semicolon.  The two FA rows have
    ``--`` placeholders and are intentionally excluded by the caller.
    """

    left, *values = raw.rstrip("\n").split(";")
    if len(values) != 14 or any(value.strip() == "--" for value in values):
        return None
    fields = left.split("|")
    if len(fields) == 3:
        name, second, positions = fields
        if second.strip().upper() in {"DTD", "O", "OUT", "INJ"}:
            status, team = second.strip().upper(), ""
        else:
            status, team = "", second.strip()
    elif len(fields) == 4:
        name, status, team, positions = fields
        status = status.strip().upper()
    else:
        raise ValueError(f"unexpected row prefix: {left!r}")
    return name.strip(), status, team.strip(), positions.strip(), values


def _eligibility(positions: str) -> str:
    """Map ESPN PG/SG/SF/PF/C tags to engine G/F/C slots."""

    mapped = set()
    for position in positions.replace(" ", "").split(","):
        if position in {"PG", "SG"}:
            mapped.add("G")
        elif position in {"SF", "PF"}:
            mapped.add("F")
        elif position == "C":
            mapped.add("C")
    # Stable canonical ordering helps comparisons and keeps C/F/G explicit.
    return "|".join(position for position in ("C", "F", "G") if position in mapped)


def audit_normalized() -> dict[str, float | int]:
    """Check raw-to-CSV identity and known-component score consistency.

    The ESPN displayed average is one decimal, while each input primitive is
    also rounded to one decimal. A 0.25 tolerance catches transcription or
    formula errors without treating ordinary propagated rounding as a failure.
    Missing PF/TF/EJ/DD/TD/QD fields are deliberately excluded from this
    known-component check.
    """

    raw_rows = []
    with RAW.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parsed = _parse_row(line)
            if parsed is not None:
                name, _status, _team, _positions, values = parsed
                raw_rows.append((name, values))
    with OUT.open("r", encoding="utf-8", newline="") as handle:
        normalized = list(csv.DictReader(handle))
    if len(raw_rows) != len(normalized):
        raise RuntimeError(f"raw/normalized count mismatch: {len(raw_rows)} != {len(normalized)}")
    primitive_fields = ("pts", "fgm", "fga", "ftm", "fta", "3pm", "reb", "ast", "stl", "blk", "to")
    identity_mismatches = 0
    drifts = []
    for (raw_name, raw_values), row in zip(raw_rows, normalized):
        if row["name"] != raw_name or row["player_id"] != _slug(raw_name):
            identity_mismatches += 1
        for field, raw_value in zip(("mpg", "fgm", "fga", "ftm", "fta", "3pm", "reb", "ast", "stl", "blk", "to", "pts"), raw_values[:12]):
            if abs(float(row[field]) - float(raw_value)) > 1e-9:
                identity_mismatches += 1
        s = {key: float(row[key]) for key in primitive_fields}
        known_score = (
            s["pts"] + 2 * s["fgm"] - s["fga"] + s["ftm"] - s["fta"] + s["3pm"]
            + 2 * s["reb"] + 2 * s["ast"] + 4 * s["stl"] + 4 * s["blk"] - 2 * s["to"]
        )
        drifts.append(known_score - float(row["displayed_fantasy_average"]))
    max_abs_drift = max((abs(value) for value in drifts), default=0.0)
    if identity_mismatches:
        raise RuntimeError(f"raw-to-normalized identity mismatches: {identity_mismatches}")
    if max_abs_drift > 0.5:
        raise RuntimeError(f"severe known-component score drift: {max_abs_drift:.3f}")
    return {
        "rows": len(normalized),
        "identity_mismatches": identity_mismatches,
        "max_abs_known_score_drift": round(max_abs_drift, 3),
        "known_score_within_tolerance": sum(abs(value) <= 0.25 for value in drifts),
    }


def build() -> int:
    records = []
    used_ids: set[str] = set()
    with RAW.open("r", encoding="utf-8") as handle:
        source_rank = 0
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            source_rank += 1
            parsed = _parse_row(line)
            if parsed is None:
                continue
            name, status, team, positions, values = parsed
            values_float = [float(value) for value in values]
            stats = dict(zip(STAT_NAMES, values_float[: len(STAT_NAMES)]))
            # ESPN exposes a rounded season total and average, but no GP.
            # Retain the exact display values and expose the quotient only as
            # a clearly-labelled derived proxy.
            projected_total = values_float[12]
            average = values_float[13]
            implied_gp = projected_total / average if average else None
            player_id = _slug(name)
            if player_id in used_ids:
                suffix = 2
                while f"{player_id}-{suffix}" in used_ids:
                    suffix += 1
                player_id = f"{player_id}-{suffix}"
            used_ids.add(player_id)
            # The UI does not provide a health probability.  Use the only
            # displayed season-length signal as a rough availability proxy;
            # retain the current injury label separately for auditability.
            availability = min(max((implied_gp or 0.0) / 82.0, 0.0), 1.0)
            record = {
                "player_id": player_id,
                "name": name,
                "team": team,
                "injury_status": status,
                "espn_positions": positions.replace(", ", ","),
                "eligibility": _eligibility(positions),
                "source_row_rank": source_rank,
                # The ESPN page order is a visible roster-order proxy, not ADP.
                "market_rank": source_rank,
                "adp": source_rank,
                "market_rank_source": "ESPN rendered UI row order (% rostered proxy; not ADP)",
                "adp_source": "unavailable; duplicated roster-order proxy for engine compatibility",
                "mpg": values_float[0],
                "fgm": values_float[1],
                "fga": values_float[2],
                "ftm": values_float[3],
                "fta": values_float[4],
                "3pm": values_float[5],
                "reb": values_float[6],
                "ast": values_float[7],
                "stl": values_float[8],
                "blk": values_float[9],
                "to": values_float[10],
                "pts": values_float[11],
                "projected_total": projected_total,
                "displayed_fantasy_average": average,
                "gp_projected": "",
                "gp_implied_from_total_avg": round(implied_gp, 3) if implied_gp is not None else "",
                # ESPN rendered 0.0 for these fields; blank means unmodeled,
                # rather than asserting a real zero forecast.
                "pf": "",
                "tf": "",
                "ej": "",
                "dd": "",
                "td": "",
                "qd": "",
                "upside": 0.0,
                "projected_availability": availability,
                "availability_source": "rough derived proxy: (displayed total / displayed average) / 82; not a calibrated health probability",
                "schedule_group": team,
                "source_label": "ESPN rendered projections UI snapshot 2026-10-10",
                "stats": json.dumps(stats, separators=(",", ":"), sort_keys=True),
            }
            records.append(record)

    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    print(f"wrote {len(records)} complete player rows to {OUT}")
    print(f"audit: {audit_normalized()}")
    return len(records)


if __name__ == "__main__":
    build()
