"""Conservative historical-data adapters for strategy-quality backtests.

The campaign must never turn a missing box-score row into a made-up forecast
or select a pool using future games played.  This module therefore favors
small, explicit import interfaces and reports a data gap when the cache does
not contain both a pre-season forecast and post-season outcomes.  It uses only
the Python standard library so cached files can be prepared offline.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from .campaign_data import (
        COMMON_SCORING_FIELDS,
        OPTIONAL_SCORING_FIELDS,
        PRIMITIVE_SCORING_FIELDS,
        CampaignDataError,
        PlayerRecord,
        SeasonDataset,
        load_projection_capture,
        projection_dataset,
        _as_float,
        _normalise_date,
        _normalise_positions,
        _engine_positions,
        _normalise_stats,
        _new_player_record,
        _is_complete_forecast,
    )
except ImportError:  # pragma: no cover - direct script/import compatibility.
    from campaign_data import (  # type: ignore[no-redef]
        COMMON_SCORING_FIELDS,
        OPTIONAL_SCORING_FIELDS,
        PRIMITIVE_SCORING_FIELDS,
        CampaignDataError,
        PlayerRecord,
        SeasonDataset,
        load_projection_capture,
        projection_dataset,
        _as_float,
        _normalise_date,
        _normalise_positions,
        _engine_positions,
        _normalise_stats,
        _new_player_record,
        _is_complete_forecast,
    )


TARGET_SEASONS: tuple[str, ...] = ("2022-23", "2023-24", "2024-25")
DEVELOPMENT_SEASON: str = "2021-22"
VETERAN_COHORT_RULE = "preseason-supported positions; no future GP or realized-season pool filtering"
OFFICIAL_PRIMARY_SOURCES: tuple[str, ...] = (
    "https://www.nba.com/stats/players/boxscores",
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
)
DEFAULT_CACHE_DIR = Path("runtime") / "strategy-quality-data"

LAST_HISTORICAL_STATUS: dict[str, Any] = {}


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    # Callers commonly pass an absolute monotonic deadline.  Supporting a
    # duration-like value would make a zero/near-zero deadline ambiguous, so
    # keep this function intentionally strict and documented in status.
    return deadline - time.monotonic()


def _deadline_expired(deadline: float | None) -> bool:
    remaining = _remaining(deadline)
    return remaining is not None and remaining <= 0


def _date_from_row(row: Mapping[str, Any]) -> str | None:
    for key in (
        "date", "game_date", "gameDate", "event_date", "eventDate",
        "GAME_DATE", "GAME_DATE_EST", "DATE",
    ):
        if row.get(key):
            try:
                return _normalise_date(row[key], field="game date")
            except CampaignDataError:
                text = str(row[key]).strip()
                for fmt in ("%m/%d/%Y", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(text[:10], fmt).date().isoformat()
                    except ValueError:
                        continue
                return None
    return None


def _player_id(row: Mapping[str, Any]) -> str:
    for key in (
        "player_id", "playerId", "athlete_id", "athleteId", "id",
        "PLAYER_ID", "ATHLETE_ID", "ID",
    ):
        if row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return ""


def _team_id(row: Mapping[str, Any]) -> str:
    for key in (
        "team", "team_id", "teamId", "team_abbreviation", "teamAbbreviation", "nba_team",
        "TEAM_ID", "TEAM_ABBREVIATION", "TEAM_ABBREVIATION_HOME", "TEAM_ABBREVIATION_AWAY",
    ):
        if row.get(key) not in (None, ""):
            return str(row[key]).strip().upper()
    return ""


def _extract_stats(row: Mapping[str, Any]) -> dict[str, float]:
    value = row.get("stats", row.get("statistics", row.get("box_score")))
    if value is not None:
        parsed = _normalise_stats(value)
    else:
        parsed = _normalise_stats({key: raw for key, raw in row.items() if str(key).upper() in COMMON_SCORING_FIELDS})
    # Preserve only scoring/stat fields that can be compared; source-specific
    # labels and fantasy totals are not outcomes.
    result = {key: parsed[key] for key in parsed if key in COMMON_SCORING_FIELDS}
    # DD/TD/QD are valid actual-game observations derivable from a complete
    # box score.  This is deliberately separate from TF/EJ: those fields are
    # never inferred or filled when the public source does not provide them.
    categories = ("PTS", "REB", "AST", "STL", "BLK")
    if all(field in result for field in categories):
        tens = sum(result[field] >= 10 for field in categories)
        result.setdefault("DD", float(tens >= 2))
        result.setdefault("TD", float(tens >= 3))
        result.setdefault("QD", float(tens >= 4))
    return result


def _iter_rows(payload: Any) -> Iterable[Mapping[str, Any]]:
    """Yield rows from common public stats/schedule JSON shapes."""
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, Mapping):
                yield row
        return
    if not isinstance(payload, Mapping):
        return
    # nba_api/Stats endpoints return ``resultSets`` objects with one header
    # array and a parallel ``rowSet`` array.  Keep the column names intact so
    # the normalizers can consume both these rows and hand-written fixtures.
    result_sets = payload.get("resultSets", payload.get("resultSet"))
    if result_sets is not None:
        sets = result_sets if isinstance(result_sets, list) else [result_sets]
        emitted = False
        for result_set in sets:
            if not isinstance(result_set, Mapping):
                continue
            headers = result_set.get("headers", result_set.get("Headers"))
            rows = result_set.get("rowSet", result_set.get("rowset", result_set.get("rows", ())))
            if isinstance(headers, Sequence) and not isinstance(headers, (str, bytes)) and isinstance(rows, list):
                for values in rows:
                    if isinstance(values, Mapping):
                        yield values
                    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                        emitted = True
                        yield dict(zip((str(header) for header in headers), values))
            elif isinstance(rows, list):
                for values in rows:
                    if isinstance(values, Mapping):
                        emitted = True
                        yield values
        if emitted:
            return
    # The official CDN schedule wraps game dates and each date's game list in
    # ``leagueSchedule``.  Emit one team-game row per home/away side so DNPs
    # can be scheduled from team games rather than outcome appearances.
    league_schedule = payload.get("leagueSchedule")
    if isinstance(league_schedule, Mapping):
        game_dates = league_schedule.get("gameDates", league_schedule.get("game_dates", ()))
        emitted = False
        if isinstance(game_dates, list):
            for day_entry in game_dates:
                if not isinstance(day_entry, Mapping):
                    continue
                day = day_entry.get("gameDate", day_entry.get("date"))
                games = day_entry.get("games", ())
                if not isinstance(games, list):
                    continue
                for game in games:
                    if not isinstance(game, Mapping):
                        continue
                    game_id = game.get("gameId", game.get("game_id"))
                    for side_key in ("homeTeam", "awayTeam"):
                        team = game.get(side_key)
                        if isinstance(team, Mapping):
                            team_id = team.get("teamTricode", team.get("teamAbbreviation", team.get("teamId")))
                        else:
                            team_id = team
                        if team_id not in (None, ""):
                            emitted = True
                            yield {"date": day, "team": team_id, "game_id": game_id}
        if emitted:
            return
    for key in ("rows", "data", "results", "players", "outcomes", "box_scores", "games", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, Mapping):
                    yield row
            return
        if isinstance(value, Mapping):
            # A date keyed outcome map is expanded without losing the date.
            for date_key, date_rows in value.items():
                if isinstance(date_rows, Mapping):
                    for pid, stats in date_rows.items():
                        if isinstance(stats, Mapping):
                            yield {"date": date_key, "player_id": pid, "stats": stats}
            return
    # A single row is useful for line-delimited imports.
    if _player_id(payload) or _date_from_row(payload):
        yield payload


def load_stats_file(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Load JSON/CSV stats rows while preserving unavailable fields.

    A blank, ``--``, or missing stat remains absent.  It is never converted to
    a zero; zero is applied only by a consumer for a player scheduled but not
    represented in the outcome rows.
    """
    source = Path(path)
    if source.suffix.lower() in {".json", ".jsonl"}:
        if source.suffix.lower() == ".jsonl":
            payload: Any = []
            for line in source.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    payload.append(json.loads(line))
        else:
            with source.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        rows: list[dict[str, Any]] = []
        for raw in _iter_rows(payload):
            row = dict(raw)
            row["player_id"] = _player_id(row)
            row["date"] = _date_from_row(row)
            row["team"] = _team_id(row)
            row["stats"] = _extract_stats(row)
            rows.append(row)
        return tuple(rows)
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            source_rows = csv.DictReader(handle)
            rows = []
            for raw in source_rows:
                row = dict(raw)
                row["player_id"] = _player_id(row)
                row["date"] = _date_from_row(row)
                row["team"] = _team_id(row)
                row["stats"] = _extract_stats(row)
                rows.append(row)
            return tuple(rows)
    raise CampaignDataError(f"unsupported historical input format: {source.suffix!r}")


def normalize_stats_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    """Normalize box-score rows to ``date -> player_id -> stats``."""
    result: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for raw in rows:
        row = dict(raw)
        day = row.get("date") or _date_from_row(row)
        pid = _player_id(row)
        if not day or not pid:
            continue
        try:
            day = _normalise_date(day)
        except CampaignDataError:
            continue
        result[day][pid] = _extract_stats(row)
    return {day: dict(sorted(players.items())) for day, players in sorted(result.items())}


def normalize_actual_stats(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    """Alias used by import scripts and older campaign notebooks."""
    return normalize_stats_rows(rows)


def historical_positions(rows: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    """Return source-supported positions for identity/eligibility audits."""
    result: dict[str, tuple[str, ...]] = {}
    for raw in rows:
        pid = _player_id(raw)
        if not pid:
            continue
        value = raw.get(
            "eligibility",
            raw.get("positions", raw.get("position", raw.get("POSITION", raw.get("POSITION_1", ())))),
        )
        if not value:
            value = tuple(
                raw[key]
                for key in ("POSITION_1", "POSITION_2", "POSITION_3")
                if raw.get(key) not in (None, "")
            )
        result[pid] = _engine_positions(value)
    return result


def veteran_cohort(
    players: Sequence[PlayerRecord],
    veteran_ids: Iterable[str] | None = None,
) -> tuple[PlayerRecord, ...]:
    """Keep the pre-season-supported veteran pool without future-GP leakage.

    A caller may pass a source-supported veteran identity set.  When it is not
    available, every forecast row with at least one supported position remains
    eligible and the missing-veteran-label limitation belongs in provenance.
    """
    allowed = None if veteran_ids is None else {str(value) for value in veteran_ids}
    return tuple(
        player for player in players
        if tuple(getattr(player, "eligibility", ()))
        and (allowed is None or str(player.player_id) in allowed)
    )


def build_prior_season_forecasts(
    prior_rows: Iterable[Mapping[str, Any]],
    positions: Mapping[str, Sequence[str]] | Iterable[Mapping[str, Any]],
    *,
    source_label: str = "official_prior_season_baseline",
) -> tuple[PlayerRecord, ...]:
    """Build a conservative forecast baseline from the prior season only.

    This is an import adapter for official player-game logs when an archived
    fantasy projection is unavailable.  Per-game primitive means and prior-GP
    availability are computed from rows dated before the target season.  Rows
    without all primitive fields or a prior-season-supported position are
    excluded; no target-season games played are consulted.
    """
    if isinstance(positions, Mapping):
        position_map = {str(pid): _engine_positions(value) for pid, value in positions.items()}
    else:
        position_map = historical_positions(positions)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in prior_rows:
        row = dict(raw)
        pid = _player_id(row)
        if not pid:
            continue
        stats = _extract_stats(row)
        if not stats:
            continue
        grouped[pid].append({
            "name": row.get("name", row.get("full_name", row.get("PLAYER_NAME", pid))),
            "date": _date_from_row(row) or "",
            "team": _team_id(row),
            "stats": stats,
        })
    forecasts: list[PlayerRecord] = []
    for pid, rows in sorted(grouped.items()):
        eligibility = tuple(position_map.get(pid, ()))
        if not eligibility:
            continue
        values: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            for key, value in row["stats"].items():
                values[key].append(float(value))
        stats = {key: sum(numbers) / len(numbers) for key, numbers in values.items() if numbers}
        if not all(field in stats for field in PRIMITIVE_SCORING_FIELDS):
            continue
        latest = max(rows, key=lambda row: row["date"])
        try:
            player = PlayerRecord(
                player_id=pid,
                name=str(latest["name"] or pid),
                eligibility=eligibility,
                stats=stats,
                projected_availability=min(1.0, len(rows) / 82.0),
                market_rank=9999.0,
                adp=9999.0,
                source_label=source_label,
                schedule_group=str(latest["team"] or ""),
            )
        except (CampaignDataError, ValueError):
            continue
        forecasts.append(player)
    return tuple(forecasts)


def _player_teams(players: Sequence[PlayerRecord], rows: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    teams: dict[str, set[str]] = defaultdict(set)
    for player in players:
        group = str(getattr(player, "schedule_group", "") or "").strip().upper()
        if group:
            teams[str(player.player_id)].add(group)
    for raw in rows:
        pid = _player_id(raw)
        team = _team_id(raw)
        if pid and team:
            teams[pid].add(team)
    return teams


def build_actual_schedule(
    players: Sequence[PlayerRecord],
    team_games: Iterable[Mapping[str, Any]],
    player_game_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, tuple[str, ...]]:
    """Build scheduled player IDs from team games, including DNPs.

    ``team_games`` should contain one row for each team game, even when a
    player did not appear in the box score.  Rows with a player ID are also
    accepted and are treated as explicit schedule evidence.  The function
    never creates a schedule by taking the keys of the realized outcomes.
    """
    if isinstance(team_games, Mapping):
        # Accept the convenient ``date -> teams`` shape used by hand-built
        # offline fixtures in addition to a list of rows.
        game_rows = []
        for raw_day, raw_teams in team_games.items():
            values = [raw_teams] if isinstance(raw_teams, str) else raw_teams
            if isinstance(values, Iterable):
                for team in values:
                    game_rows.append({"date": raw_day, "team": team})
    else:
        game_rows = [dict(row) for row in team_games]
    if isinstance(player_game_rows, Mapping):
        explicit_rows = []
        for raw_day, raw_rows in player_game_rows.items():
            values = [raw_rows] if isinstance(raw_rows, Mapping) else raw_rows
            if isinstance(values, Iterable):
                for row in values:
                    if isinstance(row, Mapping):
                        explicit_rows.append({"date": raw_day, **dict(row)})
    else:
        explicit_rows = [dict(row) for row in player_game_rows]
    teams = _player_teams(players, explicit_rows)
    player_assignments: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in explicit_rows:
        pid = _player_id(row)
        day = _date_from_row(row)
        team = _team_id(row)
        if pid and day and team:
            player_assignments[pid].append((day, team))
    for pid in player_assignments:
        player_assignments[pid].sort()
    for row in game_rows:
        pid = _player_id(row)
        team = _team_id(row)
        if pid and team:
            teams[pid].add(team)
    by_team: dict[str, set[str]] = defaultdict(set)
    for row in game_rows:
        day = _date_from_row(row)
        team = _team_id(row)
        if day and team:
            by_team[team].add(day)
    scheduled: dict[str, set[str]] = defaultdict(set)
    for player in players:
        pid = str(player.player_id)
        assignments = player_assignments.get(pid, [])
        if assignments:
            # Apply the team assignment effective on each date.  Taking the
            # union of all season teams would incorrectly give a traded player
            # games from both teams on every date.
            for team in teams.get(pid, set()):
                for day in by_team.get(team, set()):
                    effective = assignments[0][1]
                    for assignment_day, assignment_team in assignments:
                        if assignment_day <= day:
                            effective = assignment_team
                        else:
                            break
                    if effective == team:
                        scheduled[day].add(pid)
        else:
            for team in teams.get(pid, set()):
                for day in by_team.get(team, set()):
                    scheduled[day].add(pid)
    # Per-player schedule rows may be the only source for a player with a
    # mid-season team transition or an explicit DNP row.
    for row in explicit_rows:
        day = _date_from_row(row)
        pid = _player_id(row)
        if day and pid:
            scheduled[day].add(pid)
    return {day: tuple(sorted(player_ids)) for day, player_ids in sorted(scheduled.items())}


def actual_schedule(
    players: Sequence[PlayerRecord],
    team_games: Iterable[Mapping[str, Any]],
    player_game_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, tuple[str, ...]]:
    return build_actual_schedule(players, team_games, player_game_rows)


def _load_cached_dataset(path: Path) -> SeasonDataset:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, Mapping) and "dataset" in payload and isinstance(payload["dataset"], Mapping):
        payload = payload["dataset"]
    if not isinstance(payload, Mapping):
        raise CampaignDataError("cached dataset must be a JSON object")
    dataset = SeasonDataset.from_dict(payload)
    if not _is_historical_dataset(dataset):
        return dataset

    # A historical cache is trusted only when it carries an explicit,
    # verifiable pre-season position/cutoff audit.  This gate runs before a
    # dataset can enter the draft loop; a truthy but vague source label is not
    # enough to establish that positions were known before outcomes.
    provenance = dataset.provenance
    cutoff = provenance.get("forecast_cutoff")
    verified = provenance.get("forecast_cutoff_verified", True)
    if not cutoff or verified is False or str(verified).strip().lower() in {"false", "0", "no"}:
        raise CampaignDataError("historical dataset missing a verified forecast_cutoff")
    try:
        cutoff_day = _normalise_date(cutoff, field="forecast_cutoff")
    except CampaignDataError as exc:
        raise CampaignDataError("historical dataset has an invalid forecast_cutoff") from exc
    position_source = str(provenance.get("positions_source", "")).strip()
    if not position_source or position_source.lower() in {"unknown", "unavailable", "missing", "future"}:
        raise CampaignDataError("historical dataset missing a supported pre-season positions_source")
    outcome_start = provenance.get("outcome_start") or (dataset.dates[0] if dataset.dates else None)
    if not outcome_start:
        raise CampaignDataError("historical dataset missing an outcome_start")
    try:
        outcome_start_day = _normalise_date(outcome_start, field="outcome_start")
    except CampaignDataError as exc:
        raise CampaignDataError("historical dataset has an invalid outcome_start") from exc
    if cutoff_day >= outcome_start_day:
        raise CampaignDataError("historical dataset forecast cutoff is on/after outcomes")
    # Outcome and schedule identities must remain a subset of the forecast
    # pool.  This catches future-selected players before a campaign can use
    # the cache even when the cache omitted a separate audit report.
    forecast_ids = {str(player.player_id) for player in dataset.players}
    outside = {
        str(player_id)
        for rows in (dataset.outcomes.values(), dataset.scheduled.values())
        for row in rows
        for player_id in row
        if str(player_id) not in forecast_ids
    }
    if outside:
        raise CampaignDataError("historical dataset contains outcome/schedule identities outside forecast pool")
    return dataset


def _is_historical_dataset(dataset: SeasonDataset) -> bool:
    """Forecast-only captures are inputs, not historical evaluation seasons."""
    return bool(dataset.dates and dataset.outcomes)


def _status_path(cache_dir: Path) -> Path:
    return cache_dir / "historical_status.json"


def historical_status(cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Return the latest acquisition status without exposing source contents."""
    if cache_dir is not None:
        root = Path(cache_dir)
        path = (root if root.name == "historical_status.json" else root / "historical_status.json")
        if root.suffix and root.name != "historical_status.json":
            path = root.parent / "historical_status.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, Mapping):
                    return dict(payload)
            except (OSError, json.JSONDecodeError):
                pass
    if LAST_HISTORICAL_STATUS:
        return dict(LAST_HISTORICAL_STATUS)
    return {"available": False, "status": "not_run"}


def _write_status(cache_dir: Path, status: Mapping[str, Any]) -> None:
    global LAST_HISTORICAL_STATUS
    LAST_HISTORICAL_STATUS = dict(status)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _status_path(cache_dir).write_text(json.dumps(dict(status), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # Status remains available in memory; inability to write a cache should
        # not turn an honest data gap into a hard failure.
        pass


def _discover_cached_datasets(cache_dir: Path, deadline: float | None) -> tuple[list[SeasonDataset], list[str]]:
    datasets: list[SeasonDataset] = []
    errors: list[str] = []
    if not cache_dir.exists():
        return datasets, errors
    if cache_dir.is_file():
        try:
            dataset = _load_cached_dataset(cache_dir)
            return ([dataset] if _is_historical_dataset(dataset) else []), errors
        except (OSError, json.JSONDecodeError, CampaignDataError, TypeError, ValueError) as exc:
            return datasets, [f"{cache_dir.name}: {type(exc).__name__}"]
    for path in sorted(cache_dir.glob("*.json")):
        if path.name in {"historical_status.json", "official_acquisition_attempt.json"} or _deadline_expired(deadline):
            continue
        try:
            dataset = _load_cached_dataset(path)
        except (OSError, json.JSONDecodeError, CampaignDataError, TypeError, ValueError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        if not _is_historical_dataset(dataset):
            errors.append(f"{path.name}: forecast_only_or_empty_outcomes")
            continue
        if dataset.name not in {item.name for item in datasets}:
            datasets.append(dataset)
    return datasets, errors


def _try_import_manifest(cache_dir: Path, deadline: float | None) -> list[SeasonDataset]:
    """Import a tiny, explicit manifest when prepared by an offline fetcher."""
    manifest = cache_dir / "manifest.json"
    if not manifest.exists() or _deadline_expired(deadline):
        return []
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    paths = payload.get("datasets", ()) if isinstance(payload, Mapping) else ()
    result: list[SeasonDataset] = []
    if isinstance(paths, Sequence) and not isinstance(paths, (str, bytes)):
        for raw in paths:
            if _deadline_expired(deadline):
                break
            path = Path(str(raw))
            if not path.is_absolute():
                path = cache_dir / path
            try:
                dataset = _load_cached_dataset(path)
                if _is_historical_dataset(dataset):
                    result.append(dataset)
            except (OSError, json.JSONDecodeError, CampaignDataError, TypeError, ValueError):
                continue
    return result


def _probe_official_sources(deadline: float | None) -> list[dict[str, Any]]:
    """Make bounded, read-only feasibility probes; never synthesize datasets."""
    results: list[dict[str, Any]] = []
    endpoints = (
        "https://stats.nba.com/stats/leaguegamelog?Counter=0&DateFrom=&DateTo=&Direction=DESC&LeagueID=00&PlayerOrTeam=T&Season=2023-24&SeasonType=Regular%20Season&Sorter=DATE",
        "https://stats.nba.com/stats/commonallplayers?IsOnlyCurrentSeason=0&LeagueID=00&Season=2023-24",
        "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
    )
    for url in endpoints:
        remaining = _remaining(deadline)
        if remaining is not None and remaining <= 0.1:
            results.append({"url": url, "status": "deadline_expired"})
            continue
        started = time.monotonic()
        timeout = min(5.0, remaining) if remaining is not None else 5.0
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed official URLs
                sample = response.read(256)
                results.append({"url": url, "status": "ok", "http_status": int(response.status), "sample_bytes": len(sample), "elapsed_seconds": time.monotonic() - started})
        except urllib.error.HTTPError as exc:
            results.append({"url": url, "status": "http_error", "http_status": int(exc.code), "elapsed_seconds": time.monotonic() - started})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            results.append({"url": url, "status": "network_error", "error_type": type(exc.reason if isinstance(exc, urllib.error.URLError) and exc.reason else exc).__name__, "elapsed_seconds": time.monotonic() - started})
    return results


def _read_official_attempt(cache_dir: Path) -> list[dict[str, Any]] | None:
    """Read a previously completed bounded probe so retries stay finite."""
    path = cache_dir / "official_acquisition_attempt.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, list) and all(isinstance(item, Mapping) for item in payload):
        return [dict(item) for item in payload]
    return None


def historical_datasets(cache_dir: str | Path, deadline: float | None = None) -> list[SeasonDataset]:
    """Load prepared historical datasets and report an honest data gap.

    The deadline is an absolute ``time.monotonic()`` value.  No network fetch
    is launched here: official NBA endpoints require brittle access patterns
    and often do not carry pre-season fantasy forecasts.  A future fetcher can
    place normalized JSON files in ``cache_dir`` or provide ``manifest.json``;
    this function then loads them without changing selection rules.
    """
    target = Path(cache_dir)
    status_dir = target if target.suffix == "" or target.is_dir() else target.parent
    started = time.time()
    status: dict[str, Any] = {
        "status": "running",
        "available": False,
        "cache_dir": str(target),
        "target_seasons": list(TARGET_SEASONS),
        "development_season": DEVELOPMENT_SEASON,
        "source_policy": "official primary data preferred; no private APIs or credentials",
        "official_primary_sources": list(OFFICIAL_PRIMARY_SOURCES),
        "veteran_cohort_rule": VETERAN_COHORT_RULE,
        "calibration_rule": "development outcomes through 2021-22 only; absent cache remains an assumed-noise limitation",
        "network_fetch": "not_started; import normalized cache to avoid an unbounded campaign",
        "started_at_epoch": started,
    }
    if _deadline_expired(deadline):
        status.update({"status": "deadline_expired", "reason": "deadline elapsed before cache inspection"})
        _write_status(status_dir, status)
        return []
    datasets, errors = _discover_cached_datasets(target, deadline)
    datasets.extend(_try_import_manifest(target, deadline))
    unique: dict[str, SeasonDataset] = {dataset.name: dataset for dataset in datasets}
    datasets = [unique[name] for name in sorted(unique)]
    target_found = [dataset.name for dataset in datasets if any(season in dataset.name for season in TARGET_SEASONS)]
    if not datasets and not _deadline_expired(deadline):
        cached_probe = _read_official_attempt(target) if target.is_dir() else None
        if cached_probe is not None:
            status["official_probe"] = cached_probe
            status["network_fetch"] = "cached_bounded_attempt; no retry"
        else:
            status["official_probe"] = _probe_official_sources(deadline)
            status["network_fetch"] = "attempted_bounded; probe only, no synthetic outcomes"
        probes = status.get("official_probe", ())
        if probes and all(str(item.get("status", "")).lower() != "ok" for item in probes if isinstance(item, Mapping)):
            status["official_access"] = "blocked"
            status["official_blockers"] = [
                {
                    "url": item.get("url"),
                    "status": item.get("status"),
                    "error_type": item.get("error_type"),
                }
                for item in probes
                if isinstance(item, Mapping)
            ]
    status.update({
        "status": "ok" if datasets else "data_gap",
        "available": bool(datasets),
        "datasets_loaded": [dataset.name for dataset in datasets],
        "target_datasets_loaded": target_found,
        "errors": errors,
        "completed_at_epoch": time.time(),
        "data_gap": None if datasets else "No normalized pre-season forecasts plus later outcomes were cached; no historical claim is made.",
    })
    if datasets and len(target_found) < len(TARGET_SEASONS):
        status["data_gap"] = f"Only {len(target_found)}/{len(TARGET_SEASONS)} target outcome seasons are available."
    _write_status(target if target.is_dir() or target.suffix == "" else target.parent, status)
    return datasets


def dataset_from_rows(
    name: str,
    players: Sequence[PlayerRecord | Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
    schedule_rows: Iterable[Mapping[str, Any]],
    *,
    provenance: Mapping[str, Any] | None = None,
    missing_scoring_fields: Sequence[str] = OPTIONAL_SCORING_FIELDS,
) -> SeasonDataset:
    """Build a normalized dataset from an offline forecast/outcome import."""
    normalized_players = tuple(
        player if hasattr(player, "player_id") else _new_player_record(player, source_label="historical_forecast")
        for player in players
    )
    outcomes = normalize_stats_rows(outcome_rows)
    schedule_list = list(schedule_rows)
    scheduled = build_actual_schedule(normalized_players, schedule_list, schedule_list)
    dates = tuple(sorted(set(outcomes) | set(scheduled)))
    source_provenance = dict(provenance or {})
    source_provenance.setdefault("forecast_only_players", True)
    source_provenance.setdefault("selection_rule", "pool defined by pre-season forecast identity; no future GP filtering")
    source_provenance.setdefault("veteran_cohort_rule", VETERAN_COHORT_RULE)
    source_provenance.setdefault("schedule_source", "explicit team-game rows; includes DNPs when supplied")
    source_provenance.setdefault(
        "bonus_counts_source",
        "source DD/TD/QD when present; otherwise derived from actual PTS/REB/AST/STL/BLK thresholds",
    )
    return SeasonDataset(
        name=name,
        players=normalized_players,
        dates=dates,
        scheduled=scheduled,
        outcomes=outcomes,
        provenance=source_provenance,
        missing_scoring_fields=tuple(missing_scoring_fields),
    )


def audit_historical_dataset(dataset: SeasonDataset) -> dict[str, Any]:
    """Return identity, schedule, positions, and leakage checks for reports."""
    player_ids = {str(player.player_id) for player in dataset.players}
    outcome_ids = {pid for rows in dataset.outcomes.values() for pid in rows}
    scheduled_ids = {pid for rows in dataset.scheduled.values() for pid in rows}
    forecast_cutoff = dataset.provenance.get("forecast_cutoff")
    outcome_start = dataset.provenance.get("outcome_start") or (dataset.dates[0] if dataset.dates else None)
    leakage = False
    if forecast_cutoff and outcome_start:
        try:
            leakage = _normalise_date(forecast_cutoff) >= _normalise_date(outcome_start)
        except CampaignDataError:
            leakage = False
    return {
        "forecast_identity_count": len(player_ids),
        "outcome_identity_count": len(outcome_ids),
        "scheduled_identity_count": len(scheduled_ids),
        "outcome_ids_outside_forecast_pool": sorted(outcome_ids - player_ids),
        "scheduled_ids_outside_forecast_pool": sorted(scheduled_ids - player_ids),
        "forecast_cutoff": forecast_cutoff,
        "outcome_start": outcome_start,
        "forecast_leakage": leakage,
        "positions_supported_by_forecast": bool(dataset.provenance.get("positions_source")),
        "missing_scoring_fields": list(dataset.missing_scoring_fields),
    }


__all__ = [
    "DEFAULT_CACHE_DIR", "DEVELOPMENT_SEASON", "OFFICIAL_PRIMARY_SOURCES", "TARGET_SEASONS", "VETERAN_COHORT_RULE", "actual_schedule",
    "audit_historical_dataset", "build_actual_schedule", "dataset_from_rows",
    "historical_datasets", "historical_positions", "historical_status",
    "load_projection_capture", "projection_dataset",
    "build_prior_season_forecasts", "veteran_cohort",
    "load_stats_file", "normalize_actual_stats", "normalize_stats_rows",
]
