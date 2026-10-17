"""Normalized inputs for the historical strategy-quality campaign.

This module deliberately contains no network client and no draft policy.  It
turns frozen forecast captures and imported historical rows into small,
serializable objects that the campaign engine can consume.  A forecast record
is kept separate from a realized outcome: ``SeasonDataset.players`` are the
rows available before the season, while ``outcomes`` are observed rows from a
later season.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # The campaign uses the same player contract as the mock draft engine.
    from mock_draft.fixture import PlayerRecord
except ImportError:  # pragma: no cover - useful when copied as a single file.
    PlayerRecord = Any  # type: ignore[misc,assignment]


# These are the primitive forecast fields that must be present before a row is
# admitted to a historical candidate pool.  MIN is useful evidence but is not
# part of ESPN's point calculation; bonus/penalty fields are intentionally
# separate because public sources often do not supply them.
PRIMITIVE_SCORING_FIELDS: tuple[str, ...] = (
    "PTS",
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
)
OPTIONAL_SCORING_FIELDS: tuple[str, ...] = ("PF", "TF", "EJ", "DD", "TD", "QD")
COMMON_SCORING_FIELDS: tuple[str, ...] = PRIMITIVE_SCORING_FIELDS + OPTIONAL_SCORING_FIELDS
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CampaignDataError(ValueError):
    """Raised when an input cannot satisfy the normalized campaign contract."""


class ProjectionCapture(tuple):
    """Tuple-compatible complete capture rows with auditable row counts."""

    def __new__(cls, players: Iterable[PlayerRecord], provenance: Mapping[str, Any] | None = None):
        result = super().__new__(cls, tuple(players))
        result.provenance = dict(provenance or {})
        return result

    @property
    def players(self) -> tuple[PlayerRecord, ...]:
        return tuple(self)

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            if key == "players":
                return self.players
            if key == "provenance":
                return dict(self.provenance)
            raise KeyError(key)
        return super().__getitem__(key)


def _as_float(value: Any, *, field: str = "value") -> float:
    if isinstance(value, bool):
        return float(value)
    if value is None or value == "":
        raise CampaignDataError(f"{field} is missing")
    try:
        parsed = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise CampaignDataError(f"{field} must be numeric, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise CampaignDataError(f"{field} must be finite, got {value!r}")
    return parsed


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return _as_float(value)
    except CampaignDataError:
        return default


def _projected_availability(record: Mapping[str, Any]) -> float:
    """Resolve explicit availability before deriving it from projected GP."""
    missing = object()
    explicit = record.get("projected_availability", missing)
    if explicit is missing or explicit in (None, ""):
        explicit = record.get("availability", missing)
    if explicit is not missing and explicit not in (None, ""):
        availability = _as_float(explicit, field="projected_availability")
        if not 0.0 <= availability <= 1.0:
            raise CampaignDataError("projected_availability must be between 0 and 1")
        return availability
    projected_gp = record.get("projected_gp", record.get("gp_projected", missing))
    if projected_gp is missing or projected_gp in (None, ""):
        return 1.0
    games = _as_float(projected_gp, field="projected_gp")
    if games < 0.0:
        raise CampaignDataError("projected_gp must not be negative")
    return max(0.0, min(1.0, games / 82.0))


def _normalise_stats(value: Any) -> dict[str, float]:
    """Normalize a stats mapping without fabricating absent fields."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CampaignDataError("stats must be a JSON object") from exc
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CampaignDataError("stats must be a mapping")
    result: dict[str, float] = {}
    for key, raw in value.items():
        name = str(key).strip().upper()
        if not name or name in {"FPTS", "FANTASY_POINTS", "FANTASY AVERAGE"}:
            continue
        try:
            result[name] = _as_float(raw, field=name)
        except CampaignDataError:
            # ``--`` and nulls represent an unavailable source value, not 0.
            continue
    return result


def _normalise_positions(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.replace("|", ",").replace("/", ",")
        values = [part.strip() for part in text.split(",")]
    elif isinstance(value, Iterable):
        values = [str(part).strip() for part in value]
    else:
        values = [str(value).strip()]
    return tuple(dict.fromkeys(part.upper() for part in values if part))


def _engine_positions(value: Any) -> tuple[str, ...]:
    """Map source-specific NBA positions onto the fixture's C/G/F roles."""
    aliases = {
        "PG": "G", "SG": "G", "G": "G", "GUARD": "G",
        "SF": "F", "PF": "F", "F": "F", "FORWARD": "F",
        "C": "C", "CENTER": "C",
    }
    return tuple(dict.fromkeys(aliases[position] for position in _normalise_positions(value) if position in aliases))


def _normalise_date(raw: Any, *, field: str = "date") -> str:
    text = str(raw or "").strip()[:10]
    if not _DATE_RE.match(text):
        raise CampaignDataError(f"{field} must be an ISO date, got {raw!r}")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise CampaignDataError(f"{field} must be a valid ISO date, got {raw!r}") from exc
    return text


def _record_to_mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    fields = (
        "player_id", "name", "eligibility", "stats", "projected_availability",
        "market_rank", "adp", "upside", "source_label", "schedule_group",
    )
    result = {field: getattr(record, field) for field in fields if hasattr(record, field)}
    if isinstance(result.get("eligibility"), tuple):
        result["eligibility"] = list(result["eligibility"])
    if isinstance(result.get("stats"), Mapping):
        result["stats"] = dict(result["stats"])
    return result


def _new_player_record(record: Mapping[str, Any], *, source_label: str) -> PlayerRecord:
    """Build the repository's PlayerRecord while accepting capture aliases."""
    stats = _normalise_stats(record.get("stats"))
    if not stats:
        stats = _normalise_stats({key: value for key, value in record.items() if str(key).upper() in COMMON_SCORING_FIELDS})
    positions = record.get("eligibility", record.get("positions", record.get("espn_positions", ())))
    player_id = record.get("player_id", record.get("id", record.get("athlete_id", "")))
    name = record.get("name", record.get("full_name", ""))
    if not str(player_id).strip() or not str(name).strip():
        raise CampaignDataError("player_id and name are required")
    engine_positions = _engine_positions(positions)
    if not engine_positions:
        raise CampaignDataError(f"no supported C/G/F position for {name!r}")
    return PlayerRecord(
        player_id=str(player_id).strip(),
        name=str(name).strip(),
        eligibility=engine_positions,
        stats=stats,
        projected_availability=_projected_availability(record),
        market_rank=_safe_float(record.get("market_rank", record.get("draft_rank", record.get("rank", 9999))), 9999),
        adp=_safe_float(record.get("adp", record.get("ADP", record.get("market_rank", 9999))), 9999),
        upside=_safe_float(record.get("upside", record.get("ceiling", 0.0)), 0.0),
        source_label=str(record.get("source_label", record.get("source", source_label))),
        schedule_group=str(record.get("schedule_group", record.get("team", record.get("nba_team", ""))) or ""),
    )


def _canonical_scheduled(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CampaignDataError("scheduled must be a date-to-player mapping")
    result: dict[str, tuple[str, ...]] = {}
    for raw_day, raw_players in value.items():
        day = _normalise_date(raw_day, field="scheduled date")
        if isinstance(raw_players, str):
            raw_players = [raw_players]
        if raw_players is None:
            raw_players = ()
        try:
            players = tuple(sorted({str(pid).strip() for pid in raw_players if str(pid).strip()}))
        except TypeError as exc:
            raise CampaignDataError("scheduled player IDs must be iterable") from exc
        result[day] = players
    return dict(sorted(result.items()))


def _canonical_outcomes(value: Any) -> dict[str, dict[str, dict[str, float]]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CampaignDataError("outcomes must be a date-to-player-to-stats mapping")
    result: dict[str, dict[str, dict[str, float]]] = {}
    for raw_day, raw_players in value.items():
        day = _normalise_date(raw_day, field="outcome date")
        if not isinstance(raw_players, Mapping):
            raise CampaignDataError(f"outcomes[{day!r}] must be a mapping")
        day_rows: dict[str, dict[str, float]] = {}
        for raw_pid, raw_stats in raw_players.items():
            pid = str(raw_pid).strip()
            if not pid:
                continue
            day_rows[pid] = _normalise_stats(raw_stats)
        result[day] = dict(sorted(day_rows.items()))
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class SeasonDataset:
    """Frozen, JSON-compatible forecast/outcome dataset.

    ``scheduled`` must be supplied from a schedule or roster availability
    source.  It is intentionally never derived from ``outcomes``: a player
    listed on a team game but recorded with no box-score row is a scheduled
    zero.  Consumers should use :meth:`actual_stats`, which returns an empty
    mapping for that absence and therefore scores every missing stat as zero.
    """

    name: str
    players: tuple[PlayerRecord, ...]
    dates: tuple[str, ...]
    scheduled: dict[str, tuple[str, ...]]
    outcomes: dict[str, dict[str, dict[str, float]]]
    provenance: dict[str, Any]
    missing_scoring_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise CampaignDataError("dataset name is required")
        object.__setattr__(self, "name", str(self.name).strip())
        players: list[PlayerRecord] = []
        seen_ids: set[str] = set()
        for raw in self.players:
            player = raw if hasattr(raw, "player_id") else _new_player_record(raw, source_label="dataset")
            pid = str(player.player_id)
            if not pid or pid in seen_ids:
                raise CampaignDataError(f"duplicate or empty player_id in dataset: {pid!r}")
            seen_ids.add(pid)
            players.append(player)
        object.__setattr__(self, "players", tuple(players))
        parsed_dates = tuple(sorted({_normalise_date(value) for value in self.dates}))
        schedule = _canonical_scheduled(self.scheduled)
        outcomes = _canonical_outcomes(self.outcomes)
        # Dates are the explicit evaluation horizon.  Keep schedule/outcome
        # keys outside it visible for auditability, but require valid dates.
        object.__setattr__(self, "dates", parsed_dates)
        object.__setattr__(self, "scheduled", schedule)
        object.__setattr__(self, "outcomes", outcomes)
        missing = tuple(dict.fromkeys(str(field).strip().upper() for field in self.missing_scoring_fields if str(field).strip()))
        object.__setattr__(self, "missing_scoring_fields", missing)
        object.__setattr__(self, "provenance", dict(self.provenance or {}))

    def actual_stats(self, day: str, player_id: str) -> dict[str, float]:
        """Return realized stats, with scheduled absences represented by {}."""
        day = _normalise_date(day)
        row = self.outcomes.get(day, {}).get(str(player_id), {})
        return dict(row)

    def actual_stat(self, day: str, player_id: str, field: str) -> float:
        """Return one realized stat, using zero for an absence/missing field."""
        return float(self.actual_stats(day, player_id).get(str(field).upper(), 0.0))

    def scheduled_players(self, day: str) -> tuple[str, ...]:
        return self.scheduled.get(_normalise_date(day), ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "name": self.name,
            "players": [_record_to_mapping(player) for player in self.players],
            "dates": list(self.dates),
            "scheduled": {day: list(players) for day, players in self.scheduled.items()},
            "outcomes": self.outcomes,
            "provenance": self.provenance,
            "missing_scoring_fields": list(self.missing_scoring_fields),
        }

    def to_jsonable(self) -> dict[str, Any]:
        """Compatibility name used by the campaign checkpoint writer."""
        return self.to_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SeasonDataset":
        if not isinstance(payload, Mapping):
            raise CampaignDataError("dataset JSON must be an object")
        raw_players = payload.get("players", ())
        if not isinstance(raw_players, Sequence) or isinstance(raw_players, (str, bytes)):
            raise CampaignDataError("dataset players must be a list")
        players = tuple(
            item if hasattr(item, "player_id") else _new_player_record(item, source_label="dataset_json")
            for item in raw_players
        )
        return cls(
            name=str(payload.get("name", "")),
            players=players,
            dates=tuple(payload.get("dates", ())),
            scheduled=payload.get("scheduled", {}),
            outcomes=payload.get("outcomes", {}),
            provenance=dict(payload.get("provenance", {}) or {}),
            missing_scoring_fields=tuple(payload.get("missing_scoring_fields", ())),
        )

    @classmethod
    def from_jsonable(cls, payload: Mapping[str, Any]) -> "SeasonDataset":
        """Compatibility name used by the campaign checkpoint reader."""
        return cls.from_dict(payload)

    def save_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load_json(cls, path: str | Path) -> "SeasonDataset":
        source = Path(path)
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)


def save_dataset(dataset: SeasonDataset, path: str | Path) -> Path:
    return dataset.save_json(path)


def load_dataset(path: str | Path) -> SeasonDataset:
    return SeasonDataset.load_json(path)


def _is_complete_forecast(player: PlayerRecord) -> bool:
    return all(field in {str(key).upper() for key in player.stats} for field in PRIMITIVE_SCORING_FIELDS)


def _candidate_metadata(players_seen: int, players: Sequence[PlayerRecord], *, source: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    complete = [player for player in players if _is_complete_forecast(player)]
    nonempty = [player for player in players if player.stats]
    metadata: dict[str, Any] = {
        "source": source,
        "identities_seen": players_seen,
        "nonempty_projection_rows": len(nonempty),
        "candidate_rows": len(complete),
        "excluded_incomplete_rows": max(0, players_seen - len(complete)),
        "required_forecast_fields": list(PRIMITIVE_SCORING_FIELDS),
        "selection_rule": "complete primitive forecast fields only; never selected by future GP",
    }
    metadata.update(dict(extra or {}))
    return metadata


def _source_metadata(source_values: Iterable[Any]) -> dict[str, Any]:
    """Extract only verifiable timestamps and room IDs from source labels."""
    values = tuple(str(value) for value in source_values if value not in (None, ""))
    timestamps = sorted(set(match.group(1) for value in values for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\b", value)))
    dates = sorted(set(timestamp[:10] for timestamp in timestamps))
    if not dates:
        dates = sorted(set(match.group(1) for value in values for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", value)))
    rooms = sorted(set(match.group(1) for value in values for match in re.finditer(r"\broom\s+([0-9]+)\b", value, re.IGNORECASE)))
    metadata: dict[str, Any] = {}
    if timestamps:
        metadata["forecast_capture_timestamps"] = timestamps
    if dates:
        metadata["forecast_cutoff"] = dates[0]
        metadata["forecast_cutoff_source"] = "source label capture timestamp" if timestamps else "source label capture date"
        metadata["forecast_cutoff_verified"] = True
    if rooms:
        metadata["capture_room_ids"] = rooms
    return metadata


def load_projection_capture(path: str | Path) -> tuple[PlayerRecord, ...]:
    """Load complete forecast candidates from an attended capture.

    The capture format is an attended-room ``{"rules": ...,
    "catalog": [...]}`` object.  Rows with empty or incomplete primitive
    stats are excluded from the returned candidate tuple; counts are available
    to :func:`projection_dataset` through the private payload helper.
    """
    payload = _load_capture_payload(path)
    players = tuple(player for player in payload["players"] if _is_complete_forecast(player))
    return ProjectionCapture(players, _candidate_metadata(
        payload["rows_seen"], payload["players"], source="attended_capture",
        extra={
            "nonempty_projection_rows": sum(bool(player.stats) for player in payload["players"]),
            "incomplete_nonempty_rows": sum(bool(player.stats) and not _is_complete_forecast(player) for player in payload["players"]),
            "path": str(path),
            **dict(payload.get("source_metadata", {})),
        },
    ))


def _load_capture_payload(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("catalog", payload.get("players", payload)) if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise CampaignDataError("projection capture must contain a catalog list")
    players: list[PlayerRecord] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            players.append(_new_player_record(raw, source_label="projection_capture"))
        except CampaignDataError:
            continue
    source_metadata = _source_metadata(row.get("source") for row in rows if isinstance(row, Mapping))
    return {
        "players": tuple(players),
        "rows_seen": len(rows),
        "payload": payload,
        "path": str(source),
        "source_metadata": source_metadata,
    }


def load_projection_csv(path: str | Path) -> tuple[PlayerRecord, ...]:
    """Load normalized ESPN CSV rows, retaining only complete candidates."""
    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    players: list[PlayerRecord] = []
    for raw in rows:
        try:
            players.append(_new_player_record(raw, source_label="projection_csv"))
        except CampaignDataError:
            continue
    return tuple(player for player in players if _is_complete_forecast(player))


def load_forecast_players(path: str | Path) -> tuple[PlayerRecord, ...]:
    """Load a normalized CSV or a JSON list/object of forecast rows."""
    source = Path(path)
    if source.suffix.lower() == ".csv":
        return load_projection_csv(source)
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("players", payload) if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise CampaignDataError("forecast input must be a list or an object with players")
    players = []
    for row in rows:
        if isinstance(row, Mapping):
            try:
                player = _new_player_record(row, source_label="forecast_json")
            except CampaignDataError:
                continue
            if _is_complete_forecast(player):
                players.append(player)
    return tuple(players)


def _default_projection_paths() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parents[1]
    return (root / "mock_draft" / "data" / "players_2026-27_espn_projection.csv",)


def projection_dataset(preferred_path: str | Path | None = None) -> SeasonDataset:
    """Return the newest usable frozen forecast source.

    ``preferred_path`` is honored when it exists.  An explicit attended
    capture with incomplete nonempty rows fails loudly.  Without an explicit
    path, the standalone repository uses only its checked-in projection CSV;
    it never reaches into a parent checkout or private runtime directory.
    No claim is made that the latest attended trial is available unless the
    source metadata verifies it.
    """
    fallback_meta: dict[str, Any] = {}
    if preferred_path is not None:
        preferred = Path(preferred_path)
        candidates = (preferred,)
        explicit = True
    else:
        candidates = _default_projection_paths()
        explicit = False

    selected: Path | None = None
    capture: dict[str, Any] | None = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.suffix.lower() == ".json":
            candidate_capture = _load_capture_payload(candidate)
            nonempty = sum(bool(player.stats) for player in candidate_capture["players"])
            players = tuple(player for player in candidate_capture["players"] if _is_complete_forecast(player))
            incomplete_nonempty = nonempty - len(players)
            if incomplete_nonempty or not players:
                rejection = {
                    "capture_rejected": True,
                    "capture_fallback": "checked_in_projection_csv",
                    "capture_rejection_reason": (
                        "nonempty rows lack one or more required primitive forecast fields"
                        if incomplete_nonempty
                        else "capture has no complete primitive forecast candidates"
                    ),
                    "rejected_capture_path": str(candidate),
                    "capture_identities_seen": candidate_capture["rows_seen"],
                    "capture_nonempty_projection_rows": nonempty,
                    "capture_candidate_rows": len(players),
                    "capture_incomplete_nonempty_rows": incomplete_nonempty,
                }
                if explicit:
                    raise CampaignDataError(
                        f"explicit projection capture is incomplete: {candidate} "
                        f"({incomplete_nonempty} nonempty rows missing primitive fields)"
                    )
                fallback_meta = rejection
                continue
            selected = candidate
            capture = candidate_capture
            break
        selected = candidate
        break

    if selected is None:
        raise FileNotFoundError("no usable projection capture or checked-in projection CSV found")
    if selected.suffix.lower() == ".json":
        assert capture is not None
        players = tuple(player for player in capture["players"] if _is_complete_forecast(player))
        source_kind = "attended_capture"
        extra = {
            "identities_seen": capture["rows_seen"],
            "nonempty_projection_rows": sum(bool(player.stats) for player in capture["players"]),
            "capture_path": str(selected),
            "capture_source": "attended capture JSON",
            "exact_latest_trial_verified": False,
            "latest_trial_note": "Capture provenance is retained; this adapter does not infer that it is the latest trial.",
        }
        extra.update(capture.get("source_metadata", {}))
    else:
        with selected.open("r", encoding="utf-8-sig", newline="") as handle:
            raw_rows = list(csv.DictReader(handle))
            rows_seen = len(raw_rows)
        players = load_projection_csv(selected)
        source_kind = "checked_in_projection_csv"
        extra = {
            "identities_seen": rows_seen,
            "nonempty_projection_rows": len(players),
            "capture_path": str(selected),
            "capture_source": "mock_draft/data/players_2026-27_espn_projection.csv",
            "exact_latest_trial_verified": False,
            "latest_trial_note": "Checked-in fallback; exact latest attended trial is unavailable.",
        }
        extra.update(_source_metadata(row.get("source_label") for row in raw_rows))
    extra.update(fallback_meta)
    missing = tuple(field for field in OPTIONAL_SCORING_FIELDS if any(field not in player.stats for player in players))
    provenance = _candidate_metadata(int(extra.pop("identities_seen", len(players))), players, source=source_kind, extra=extra)
    provenance.update({
        "forecast_only": True,
        "positions_source": "source eligibility fields retained verbatim in PlayerRecord",
    })
    # A literal filename or source label is not a historical cutoff.  Keep the
    # field absent when no verifiable source date was available so consumers
    # cannot mistake it for leakage-safe provenance.
    provenance.setdefault("forecast_cutoff_verified", False)
    return SeasonDataset(
        name="projection-2026-27",
        players=players,
        dates=(),
        scheduled={},
        outcomes={},
        provenance=provenance,
        missing_scoring_fields=missing,
    )


__all__ = [
    "COMMON_SCORING_FIELDS", "CampaignDataError", "OPTIONAL_SCORING_FIELDS", "ProjectionCapture",
    "PRIMITIVE_SCORING_FIELDS", "PlayerRecord", "SeasonDataset", "load_dataset",
    "load_forecast_players", "load_projection_capture", "load_projection_csv", "projection_dataset", "save_dataset",
]
