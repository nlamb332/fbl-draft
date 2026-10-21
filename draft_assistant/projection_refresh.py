"""Projection refresh, source provenance, and staleness evaluation.

Everything here is opt-in.  Nothing in the default advisor path imports this
module's gate; ``projection_dataset`` only calls it when asked for provenance.
The schema is documented in ``docs/PROJECTION_PROVENANCE.md`` and is the shared
contract for the ADP, news, and readiness work.

This module has no network client.  ESPN projections are captured in an
attended browser session; ``refresh_inputs`` re-reads that capture (or the
checked-in CSV as a tagged offline fallback) and optionally blends up to two
approved external source files that carry their own capture timestamp.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .campaign_data import (
    CampaignDataError,
    SeasonDataset,
    _as_float,
    _candidate_metadata,
    _is_complete_forecast,
    _new_player_record,
    _source_metadata,
    projection_dataset,
)

SCHEMA = "projection-provenance/1"
DEFAULT_MAX_AGE_HOURS = 72.0
MAX_EXTERNAL_SOURCES = 2
GAME_COUNT = 82.0

# Row-level ``gp_source`` tags.
GP_SOURCE_DIRECT = "source_direct"
GP_SOURCE_PROXY = "proxy_total_over_average"

# Freshness statuses.  ``unknown`` is treated as not ok: a gate must fail closed.
FRESH, STALE, UNKNOWN = "fresh", "stale", "unknown"

_NAME_RE = re.compile(r"[^a-z0-9]+")


def _utc(value: Any) -> datetime:
    """Parse an ISO timestamp or date to an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise CampaignDataError("timestamp is missing")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CampaignDataError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_hours(captured_at: Any, now: datetime) -> float:
    return round((now - _utc(captured_at)).total_seconds() / 3600.0, 3)


def _key(name: str) -> str:
    return _NAME_RE.sub("", name.lower())


@dataclass(frozen=True)
class SourceRows:
    """One loaded source: stamped identity plus per-player raw stats."""

    source: str
    captured_at: str
    role: str  # "base" or "external"
    weight: float
    players: tuple[Any, ...]
    gp: Mapping[str, float]
    offline_fallback: bool = False


def _raw_gp(raw: Mapping[str, Any]) -> float | None:
    for field in ("projected_gp", "gp_projected"):
        value = raw.get(field)
        if value in (None, ""):
            continue
        try:
            games = _as_float(value, field=field)
        except CampaignDataError:
            continue
        if 0.0 < games <= GAME_COUNT:
            return games
    return None


def _read_rows(path: Path) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    """Return raw rows plus top-level metadata (JSON objects only)."""
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle)), {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        rows = payload.get("catalog", payload.get("players", ()))
        meta = {k: v for k, v in payload.items() if k not in {"catalog", "players"}}
    else:
        rows, meta = payload, {}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise CampaignDataError(f"{path} must contain a list of player rows")
    return [row for row in rows if isinstance(row, Mapping)], meta


def _row_captured_at(rows: Sequence[Mapping[str, Any]], meta: Mapping[str, Any], explicit: str | None) -> str:
    """Best verifiable capture time: explicit, file metadata, row field, or source label."""
    if explicit:
        return _iso(_utc(explicit))
    if meta.get("captured_at"):
        return _iso(_utc(meta["captured_at"]))
    stamps = sorted({str(row["captured_at"]) for row in rows if row.get("captured_at")})
    if stamps:
        return _iso(_utc(stamps[0]))  # oldest, conservative
    found = _source_metadata(row.get("source_label") or row.get("source") for row in rows)
    times = found.get("forecast_capture_timestamps")
    if times:
        return _iso(_utc(times[0]))
    cutoff = found.get("forecast_cutoff")
    if cutoff:
        return _iso(_utc(cutoff))
    raise CampaignDataError("no verifiable captured_at; pass one explicitly")


def load_source(
    path: str | Path,
    *,
    source: str,
    role: str,
    weight: float = 1.0,
    captured_at: str | None = None,
    offline_fallback: bool = False,
) -> SourceRows:
    """Load one CSV/JSON projection source; only complete primitive rows are kept."""
    source_path = Path(path)
    rows, meta = _read_rows(source_path)
    stamp = _row_captured_at(rows, meta, captured_at)
    players, gp = [], {}
    for raw in rows:
        try:
            player = _new_player_record(raw, source_label=source)
        except CampaignDataError:
            continue
        if not _is_complete_forecast(player):
            continue
        direct = _raw_gp(raw)
        if direct is not None:
            gp[player.player_id] = direct
            player = dataclasses.replace(player, projected_availability=direct / GAME_COUNT)
        players.append(dataclasses.replace(player, source_label=f"{source} {stamp}"))
    if not players:
        raise CampaignDataError(f"{source_path} has no complete projection rows")
    return SourceRows(source, stamp, role, weight, tuple(players), gp, offline_fallback)


def _blend(base: SourceRows, extras: Sequence[SourceRows]) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Weighted per-field mean over sources that supply the field.

    Players are matched to the base source by ``player_id`` then normalized
    name.  Players absent from the base are not added: the base defines the
    candidate pool, so an external source can never widen it.
    """
    by_id = {p.player_id: i for i, p in enumerate(base.players)}
    by_name = {_key(p.name): i for i, p in enumerate(base.players)}
    contributions: dict[int, list[tuple[SourceRows, Any]]] = {i: [(base, p)] for i, p in enumerate(base.players)}
    for extra in extras:
        for player in extra.players:
            index = by_id.get(player.player_id, by_name.get(_key(player.name)))
            if index is not None:
                contributions[index].append((extra, player))
    players, rows = [], {}
    for index, base_player in enumerate(base.players):
        parts = contributions[index]
        stats: dict[str, float] = {}
        for field in {f for _, p in parts for f in p.stats}:
            supplied = [(s.weight, p.stats[field]) for s, p in parts if field in p.stats]
            total = sum(w for w, _ in supplied)
            stats[field] = sum(w * v for w, v in supplied) / total
        gp_parts = [(s.weight, s.gp[p.player_id]) for s, p in parts if p.player_id in s.gp]
        if gp_parts:
            gp = sum(w * v for w, v in gp_parts) / sum(w for w, _ in gp_parts)
            availability, gp_source = gp / GAME_COUNT, GP_SOURCE_DIRECT
        else:
            availability, gp_source = base_player.projected_availability, GP_SOURCE_PROXY
        blended = dataclasses.replace(base_player, stats=stats, projected_availability=availability)
        players.append(blended)
        sources = sorted({s.source for s, _ in parts}, key=lambda n: (n != base.source, n))
        stamps = {s.source: s.captured_at for s, _ in parts}
        rows[blended.player_id] = {
            "source": "+".join(sources) if len(sources) > 1 else sources[0],
            # Oldest contributing capture: a blend is only as fresh as its stalest input.
            "captured_at": min(stamps.values()),
            "gp_source": gp_source,
            "components": [{"source": n, "captured_at": stamps[n]} for n in sources],
        }
    return players, rows


def parse_external_spec(spec: str) -> tuple[str, Path, float]:
    """``NAME=PATH`` or ``NAME=PATH:WEIGHT`` (a trailing ``:number`` is the weight)."""
    name, sep, rest = spec.partition("=")
    if not sep or not name.strip() or not rest.strip():
        raise CampaignDataError(f"external source must look like NAME=PATH[:WEIGHT], got {spec!r}")
    path, weight = rest, 1.0
    head, colon, tail = rest.rpartition(":")
    if colon and re.fullmatch(r"\d+(\.\d+)?", tail):
        path, weight = head, float(tail)
    if weight <= 0:
        raise CampaignDataError("external source weight must be positive")
    return name.strip(), Path(path), weight


def refresh_inputs(
    *,
    espn_path: str | Path | None = None,
    espn_captured_at: str | None = None,
    externals: Sequence[tuple[str, str | Path, float]] = (),
    now: datetime | str | None = None,
) -> SeasonDataset:
    """Build a provenance-stamped dataset.

    Falls back to the checked-in CSV, tagged ``offline_fallback``, when the ESPN
    capture is absent or unusable.  An external source that is missing,
    unreadable, or lacks a verifiable ``captured_at`` is skipped and recorded;
    it never blocks the refresh.
    """
    if len(externals) > MAX_EXTERNAL_SOURCES:
        raise CampaignDataError(f"at most {MAX_EXTERNAL_SOURCES} external sources may be blended")
    built = _utc(now) if now is not None else datetime.now(timezone.utc)
    warnings: list[str] = []
    skipped: list[dict[str, str]] = []

    base: SourceRows | None = None
    if espn_path is not None:
        try:
            base = load_source(espn_path, source="espn", role="base", captured_at=espn_captured_at)
        except (OSError, ValueError) as exc:  # CampaignDataError is a ValueError
            warnings.append(f"ESPN capture unusable ({exc}); using checked-in offline fallback")
            skipped.append({"source": "espn", "reason": str(exc)})
    if base is None:
        fallback = projection_dataset()
        csv_path = Path(fallback.provenance["capture_path"])
        base = load_source(csv_path, source="checked_in_projection_csv", role="base", offline_fallback=True)
        warnings.append("offline fallback: checked-in ESPN snapshot, not a fresh capture")

    extras: list[SourceRows] = []
    for name, path, weight in externals:
        try:
            extras.append(load_source(path, source=name, role="external", weight=weight))
        except (OSError, ValueError) as exc:
            skipped.append({"source": name, "reason": str(exc)})
            warnings.append(f"external source {name!r} skipped: {exc}")

    players, rows = _blend(base, extras)
    for row in rows.values():
        row["age_hours"] = _age_hours(row["captured_at"], built)
    used = [base, *extras]
    provenance = _candidate_metadata(
        len(players), players, source="projection_refresh",
        extra={"forecast_only": True, "forecast_cutoff_verified": False},
    )
    provenance["projection_provenance"] = {
        "schema": SCHEMA,
        "built_at": _iso(built),
        "offline_fallback": base.offline_fallback,
        "sources": [
            {"source": s.source, "role": s.role, "captured_at": s.captured_at, "weight": s.weight, "rows": len(s.players)}
            for s in used
        ],
        "skipped_sources": skipped,
        "warnings": warnings,
        "gp_direct_rows": sum(r["gp_source"] == GP_SOURCE_DIRECT for r in rows.values()),
        "gp_proxy_rows": sum(r["gp_source"] == GP_SOURCE_PROXY for r in rows.values()),
        "rows": rows,
    }
    return SeasonDataset(
        name="projection-2026-27", players=tuple(players), dates=(), scheduled={}, outcomes={},
        provenance=provenance,
        missing_scoring_fields=tuple(f for f in ("PF", "TF", "EJ", "DD", "TD", "QD") if any(f not in p.stats for p in players)),
    )


def stamp_provenance(dataset: SeasonDataset, *, now: datetime | str | None = None) -> SeasonDataset:
    """Add the schema block to a dataset from ``projection_dataset`` (offline fallback tag included)."""
    if "projection_provenance" in dataset.provenance:
        return dataset
    built = _utc(now) if now is not None else datetime.now(timezone.utc)
    meta = dataset.provenance
    stamps = meta.get("forecast_capture_timestamps") or ([meta["forecast_cutoff"]] if meta.get("forecast_cutoff") else [])
    captured = _iso(_utc(min(stamps))) if stamps else None
    fallback = meta.get("source") == "checked_in_projection_csv"
    source = "checked_in_projection_csv" if fallback else "attended_capture"
    block: dict[str, Any] = {
        "schema": SCHEMA,
        "built_at": _iso(built),
        "offline_fallback": fallback,
        "sources": [{"source": source, "role": "base", "captured_at": captured, "weight": 1.0, "rows": len(dataset.players)}],
        "skipped_sources": [],
        "warnings": ["offline fallback: checked-in ESPN snapshot, not a fresh capture"] if fallback else [],
        "gp_direct_rows": 0,
        "gp_proxy_rows": len(dataset.players),
        "rows": {
            p.player_id: {
                "source": source, "captured_at": captured, "gp_source": GP_SOURCE_PROXY, "components": [{"source": source, "captured_at": captured}],
                **({"age_hours": _age_hours(captured, built)} if captured else {}),
            }
            for p in dataset.players
        },
    }
    return dataclasses.replace(dataset, provenance={**meta, "projection_provenance": block})


def evaluate_freshness(
    provenance: Mapping[str, Any] | None,
    *,
    now: datetime | str | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    """Judge a dataset's provenance against ``max_age_hours`` at ``now``.

    ``ok`` is true only when every row has a source and a timestamp within the
    threshold.  Missing or malformed provenance is ``unknown`` and not ok.
    """
    current = _utc(now) if now is not None else datetime.now(timezone.utc)
    result: dict[str, Any] = {"status": UNKNOWN, "ok": False, "max_age_hours": max_age_hours, "age_hours": None,
                              "oldest_captured_at": None, "offline_fallback": False, "warnings": []}
    block = (provenance or {}).get("projection_provenance")
    if not isinstance(block, Mapping) or block.get("schema") != SCHEMA or not block.get("rows"):
        result["warnings"].append("projection data has no source/timestamp provenance")
        return result
    result["offline_fallback"] = bool(block.get("offline_fallback"))
    result["sources"] = [dict(s) for s in block.get("sources", ())]
    stamps, unstamped = [], 0
    for row in block["rows"].values():
        try:
            if not row.get("source"):
                raise CampaignDataError("no source")
            stamps.append(_utc(row.get("captured_at")))
        except CampaignDataError:
            unstamped += 1
    if unstamped:
        result["warnings"].append(f"{unstamped} projection rows lack source or captured_at")
        return result
    oldest = min(stamps)
    age = round((current - oldest).total_seconds() / 3600.0, 3)
    result.update(oldest_captured_at=_iso(oldest), age_hours=age)
    if age < 0:
        result["warnings"].append("projection captured_at is in the future; clock or capture time is wrong")
        return result
    stale = age > max_age_hours
    result["status"] = STALE if stale else FRESH
    result["ok"] = not stale
    if stale:
        result["warnings"].append(f"projection data is {age:.1f}h old (limit {max_age_hours:g}h); refresh inputs")
    if result["offline_fallback"]:
        result["warnings"].append("projection data is the checked-in offline fallback")
    result["warnings"].extend(block.get("warnings", ()) if not result["offline_fallback"] else ())
    return result


@dataclass(frozen=True)
class FreshnessPolicy:
    """Server-side staleness policy.  ``enforce`` False only warns."""

    dataset_path: Path
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS
    enforce: bool = False

    def evaluate(self, now: datetime | str | None = None) -> dict[str, Any]:
        try:
            provenance = SeasonDataset.load_json(self.dataset_path).provenance
        except (OSError, ValueError) as exc:
            provenance = None
            error = f"projection dataset unreadable: {exc}"
        else:
            error = ""
        result = evaluate_freshness(provenance, now=now, max_age_hours=self.max_age_hours)
        if error:
            result["warnings"].insert(0, error)
        result["enforced"] = self.enforce
        result["dataset_path"] = str(self.dataset_path)
        return result


def write_refresh(dataset: SeasonDataset, out: str | Path) -> Path:
    return dataset.save_json(out)


__all__ = [
    "DEFAULT_MAX_AGE_HOURS", "FRESH", "GP_SOURCE_DIRECT", "GP_SOURCE_PROXY", "MAX_EXTERNAL_SOURCES",
    "SCHEMA", "STALE", "UNKNOWN", "FreshnessPolicy", "evaluate_freshness", "load_source",
    "parse_external_spec", "refresh_inputs", "stamp_provenance", "write_refresh",
]
