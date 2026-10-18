"""Production Lock-In forecast valuation for the Sleeper draft advisor.

This module contains the small, executable part of the Lock-In model.  It
does not simulate games or inspect realized outcomes.  A forecast is a
conditional normal score for a game, mixed with an explicit probability that
the player is unavailable (a DNP).  The resulting value is the expected score
that can be locked into one weekly slot.

The public Sleeper projection feed supplies season-level means, but it does
not supply calibrated game variance, a weekly schedule, or health
probabilities.  The default game model therefore labels those inputs as
assumptions.  A local JSON file can replace the defaults with per-player,
per-week game forecasts; its season and scoring map are bound to the league
before those forecasts are used.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .scoring import fantasy_points


DEFAULT_SCHEDULED_GAMES_PER_WEEK = 3
DEFAULT_STDDEV_FRACTION = 0.20
DEFAULT_AVAILABILITY_PROBABILITY = 0.90

_ROOT_FIELDS = frozenset({"mode", "season", "scoring", "defaults", "players", "source"})
_DEFAULT_FIELDS = frozenset(
    {"scheduled_games_per_week", "stddev_fraction", "availability_probability"}
)
_PLAYER_FIELDS = frozenset({"weeks"})
_GAME_FIELDS = frozenset({"mean", "stddev", "availability_probability"})


class ForecastError(ValueError):
    """Raised when a forecast file or league binding is invalid."""


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ForecastError(f"{field_name} must be a finite number")
    return number


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ForecastError(f"{field_name} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: frozenset[str], *, field_name: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ForecastError(f"{field_name} contains unknown field(s): {', '.join(unknown)}")


def _normalise_scoring(scoring: Mapping[str, Any], *, field_name: str = "scoring") -> dict[str, float]:
    if not isinstance(scoring, Mapping):
        raise ForecastError(f"{field_name} must be an object")
    result: dict[str, float] = {}
    for key, raw_value in scoring.items():
        if not isinstance(key, str) or not key.strip():
            raise ForecastError(f"{field_name} keys must be non-empty strings")
        result[key] = _finite_number(raw_value, field_name=f"{field_name}.{key}")
    return result


@dataclass(frozen=True)
class ForecastDefaults:
    """Assumptions used when no per-player game forecast is available."""

    scheduled_games_per_week: int = DEFAULT_SCHEDULED_GAMES_PER_WEEK
    stddev_fraction: float = DEFAULT_STDDEV_FRACTION
    availability_probability: float = DEFAULT_AVAILABILITY_PROBABILITY

    def __post_init__(self) -> None:
        games = self.scheduled_games_per_week
        if isinstance(games, bool) or not isinstance(games, int) or games < 0:
            raise ForecastError("scheduled_games_per_week must be a non-negative integer")
        fraction = _finite_number(self.stddev_fraction, field_name="stddev_fraction")
        if fraction < 0:
            raise ForecastError("stddev_fraction must be non-negative")
        availability = _finite_number(
            self.availability_probability, field_name="availability_probability"
        )
        if not 0.0 <= availability <= 1.0:
            raise ForecastError("availability_probability must be between 0 and 1")
        object.__setattr__(self, "stddev_fraction", fraction)
        object.__setattr__(self, "availability_probability", availability)


@dataclass(frozen=True)
class GameForecast:
    """Conditional forecast for one scheduled game, in fantasy points."""

    mean: float
    stddev: float
    availability_probability: float

    def __post_init__(self) -> None:
        mean = _finite_number(self.mean, field_name="mean")
        stddev = _finite_number(self.stddev, field_name="stddev")
        availability = _finite_number(
            self.availability_probability, field_name="availability_probability"
        )
        if stddev < 0:
            raise ForecastError("stddev must be non-negative")
        if not 0.0 <= availability <= 1.0:
            raise ForecastError("availability_probability must be between 0 and 1")
        object.__setattr__(self, "mean", mean)
        object.__setattr__(self, "stddev", stddev)
        object.__setattr__(self, "availability_probability", availability)


@dataclass(frozen=True)
class ForecastConfig:
    """Validated, season/scoring-bound local Lock-In forecasts."""

    season: str
    scoring: Mapping[str, float]
    defaults: ForecastDefaults = field(default_factory=ForecastDefaults)
    player_weeks: Mapping[str, Mapping[int, tuple[GameForecast, ...]]] = field(
        default_factory=dict
    )
    source: str = "assumed_public_projection"
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.season, str) or not self.season.strip():
            raise ForecastError("season must be a non-empty string")
        scoring = _normalise_scoring(self.scoring)
        if not isinstance(self.defaults, ForecastDefaults):
            raise ForecastError("defaults must be a ForecastDefaults instance")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ForecastError("source must be a non-empty string")
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(item, str) and item for item in self.warnings
        ):
            raise ForecastError("warnings must be a tuple of non-empty strings")
        normalised_players: dict[str, dict[int, tuple[GameForecast, ...]]] = {}
        if not isinstance(self.player_weeks, Mapping):
            raise ForecastError("player_weeks must be an object")
        for player_id, weeks in self.player_weeks.items():
            if not isinstance(player_id, str) or not player_id:
                raise ForecastError("player_weeks player IDs must be non-empty strings")
            if not isinstance(weeks, Mapping):
                raise ForecastError(f"player_weeks[{player_id!r}] must be an object")
            normalised_weeks: dict[int, tuple[GameForecast, ...]] = {}
            for week, games in weeks.items():
                if isinstance(week, bool) or not isinstance(week, int) or week < 0:
                    raise ForecastError(f"week for player {player_id!r} must be a non-negative integer")
                if not isinstance(games, (tuple, list)):
                    raise ForecastError(f"games for player {player_id!r}, week {week} must be a list")
                converted: list[GameForecast] = []
                for game in games:
                    if not isinstance(game, GameForecast):
                        raise ForecastError(
                            f"games for player {player_id!r}, week {week} must contain GameForecast values"
                        )
                    converted.append(game)
                normalised_weeks[week] = tuple(converted)
            normalised_players[player_id] = normalised_weeks
        object.__setattr__(self, "scoring", scoring)
        object.__setattr__(self, "player_weeks", normalised_players)

    def validate_for(self, season: str, scoring: Mapping[str, Any]) -> None:
        """Raise if this file cannot be used with the requested league."""

        if not isinstance(season, str) or season != self.season:
            raise ForecastError(
                f"forecast season {self.season!r} does not match league season {season!r}"
            )
        requested = _normalise_scoring(scoring)
        if dict(self.scoring) != requested:
            raise ForecastError("forecast scoring does not match the league scoring settings")

    def weeks_for(self, player_id: str) -> Mapping[int, tuple[GameForecast, ...]] | None:
        return self.player_weeks.get(str(player_id))


@dataclass(frozen=True)
class LockInProjection:
    """A player's expected one-slot weekly Lock-In value."""

    player_id: str
    fp_per_game: float
    weekly_points: float
    game_count: float
    source: str
    warnings: tuple[str, ...]
    assumptions: Mapping[str, Any]


def normal_expected_max(mean: float, stddev: float, floor: float) -> float:
    """Return ``E[max(N(mean, stddev), floor)]`` for a normal forecast."""

    mean = _finite_number(mean, field_name="mean")
    stddev = _finite_number(stddev, field_name="stddev")
    floor = _finite_number(floor, field_name="floor")
    if stddev < 0:
        raise ForecastError("stddev must be non-negative")
    if stddev <= 1e-12:
        return max(mean, floor)
    z = (mean - floor) / stddev
    phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return floor + stddev * phi + (mean - floor) * cdf


def forecast_stopping_value(games: Sequence[GameForecast]) -> float:
    """Return expected executable Lock-In value for ordered game forecasts.

    A final played game is retained even when its expected score is negative;
    only earlier games can be passed in favor of the continuation value.  An
    unavailable game consumes its opportunity without replacing the current
    continuation, which is the explicit DNP mixture.
    """

    value = 0.0
    ordered = tuple(games)
    for index in range(len(ordered) - 1, -1, -1):
        game = ordered[index]
        played = (
            game.mean
            if index == len(ordered) - 1
            else normal_expected_max(game.mean, game.stddev, value)
        )
        value = game.availability_probability * played + (
            1.0 - game.availability_probability
        ) * value
    return float(value)


# Private aliases keep the math easy to locate for focused tests without
# exposing research-engine names or importing that engine.
_normal_expected_max = normal_expected_max
_forecast_stopping_value = forecast_stopping_value


def _default_games(mean: float, defaults: ForecastDefaults) -> tuple[GameForecast, ...]:
    deviation = abs(mean) * defaults.stddev_fraction
    return tuple(
        GameForecast(mean, deviation, defaults.availability_probability)
        for _ in range(defaults.scheduled_games_per_week)
    )


def _player_id(player: Any) -> str:
    value = getattr(player, "player_id", None)
    if value is None:
        raise ForecastError("player must provide a non-empty player_id")
    result = str(value).strip()
    if not result:
        raise ForecastError("player must provide a non-empty player_id")
    return result


def _player_mean(player: Any, scoring: Mapping[str, float]) -> tuple[float, tuple[str, ...], bool]:
    stats = getattr(player, "stats", None)
    has_projection = getattr(player, "has_projection", None)
    if has_projection is None:
        has_projection = isinstance(stats, Mapping) and bool(stats)
    if not has_projection or not isinstance(stats, Mapping):
        return 0.0, (), False
    value, _contributions, warnings = fantasy_points(stats, scoring)
    return float(value), tuple(warnings), True


def _dedupe(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def estimate_player(
    player: Any,
    scoring: Mapping[str, Any],
    forecasts: ForecastConfig | None = None,
) -> LockInProjection:
    """Estimate a player's per-game and one-slot weekly Lock-In value.

    ``forecasts`` may provide pre-scored game means only when its scoring map
    matches ``scoring``.  Without a player-specific override, the player's
    Sleeper projection is scored with the actual league weights and expanded
    into the explicitly labelled default game model.
    """

    scoring_map = _normalise_scoring(scoring)
    if forecasts is not None and not isinstance(forecasts, ForecastConfig):
        raise ForecastError("forecasts must be a ForecastConfig or None")
    if forecasts is not None and dict(forecasts.scoring) != scoring_map:
        raise ForecastError("forecast scoring does not match the league scoring settings")

    player_id = _player_id(player)
    mean, scoring_warnings, has_projection = _player_mean(player, scoring_map)
    defaults = forecasts.defaults if forecasts is not None else ForecastDefaults()
    local_weeks = forecasts.weeks_for(player_id) if forecasts is not None else None
    warnings: list[str] = list(forecasts.warnings if forecasts is not None else ())

    if local_weeks is not None:
        selected_weeks = tuple(sorted(local_weeks))
        week_values = [forecast_stopping_value(local_weeks[week]) for week in selected_weeks]
        game_counts = [len(local_weeks[week]) for week in selected_weeks]
        all_games = [game for week in selected_weeks for game in local_weeks[week]]
        fp_per_game = (
            sum(game.mean for game in all_games) / len(all_games) if all_games else 0.0
        )
        weekly_points = sum(week_values) / len(week_values) if week_values else 0.0
        game_count = sum(game_counts) / len(game_counts) if game_counts else 0.0
        source = "local_forecast"
        assumptions: dict[str, Any] = {
            "mode": "lock_in",
            "weeks_evaluated": selected_weeks,
            "mean_is_fantasy_points": True,
            "scoring_bound": True,
            "scheduled_games_per_week": game_count,
            "stddev_fraction": None,
            "availability_probability": None,
            "variance_source": "local per-game forecast",
            "availability_source": "local per-game forecast",
            "threshold_event_source": "local per-game forecast; no separate event-rate calibration",
        }
        warnings.append(
            "Local forecast means are already fantasy points under the bound league scoring."
        )
        if not all_games:
            warnings.append("No scheduled games in supplied weeks; weekly value is zero.")
    elif has_projection:
        games = _default_games(mean, defaults)
        weekly_points = forecast_stopping_value(games)
        fp_per_game = mean
        game_count = float(len(games))
        source = "assumed_public_projection"
        assumptions = {
            "mode": "lock_in",
            "weeks_evaluated": (0,),
            "mean_is_fantasy_points": True,
            "scoring_bound": True,
            "scheduled_games_per_week": defaults.scheduled_games_per_week,
            "stddev_fraction": defaults.stddev_fraction,
            "availability_probability": defaults.availability_probability,
            "variance_source": "unverified assumption; public projections lack per-game variance",
            "availability_source": "unverified assumption; injury_status is not calibrated",
            "threshold_event_source": "public projections lack calibrated 40+ and 50+ event rates",
        }
        warnings.extend(
            (
                "Default schedule, variance, and availability are unverified assumptions; public projections do not provide calibrated game-level inputs.",
                "player injury_status is not used to derive or calibrate availability_probability.",
                "Public projections do not provide calibrated 40+ or 50+ event rates; absent bonus fields count as zero.",
            )
        )
        if not games:
            warnings.append("No scheduled games under the configured default; weekly value is zero.")
        warnings.extend(scoring_warnings)
    else:
        # ADP-only players have no production estimate.  A local override is
        # the only supported way to assign a value to one of them.
        source = "adp_only"
        fp_per_game = 0.0
        weekly_points = 0.0
        game_count = 0.0
        assumptions = {
            "mode": "lock_in",
            "weeks_evaluated": (),
            "mean_is_fantasy_points": False,
            "scoring_bound": True,
            "scheduled_games_per_week": defaults.scheduled_games_per_week,
            "stddev_fraction": defaults.stddev_fraction,
            "availability_probability": defaults.availability_probability,
        }
        warnings.append("No stat projection; value counted as 0 (ADP only).")

    return LockInProjection(
        player_id=player_id,
        fp_per_game=float(fp_per_game),
        weekly_points=float(weekly_points),
        game_count=float(game_count),
        source=source,
        warnings=_dedupe(warnings),
        assumptions=assumptions,
    )


def _parse_defaults(raw: Any) -> ForecastDefaults:
    if raw is None:
        return ForecastDefaults()
    value = _require_mapping(raw, field_name="defaults")
    _reject_unknown(value, _DEFAULT_FIELDS, field_name="defaults")
    kwargs: dict[str, Any] = {}
    if "scheduled_games_per_week" in value:
        games = value["scheduled_games_per_week"]
        if isinstance(games, bool) or not isinstance(games, int):
            raise ForecastError("defaults.scheduled_games_per_week must be a non-negative integer")
        kwargs["scheduled_games_per_week"] = games
    if "stddev_fraction" in value:
        kwargs["stddev_fraction"] = _finite_number(
            value["stddev_fraction"], field_name="defaults.stddev_fraction"
        )
    if "availability_probability" in value:
        kwargs["availability_probability"] = _finite_number(
            value["availability_probability"], field_name="defaults.availability_probability"
        )
    return ForecastDefaults(**kwargs)


def _parse_game(raw: Any, *, field_name: str) -> GameForecast:
    value = _require_mapping(raw, field_name=field_name)
    _reject_unknown(value, _GAME_FIELDS, field_name=field_name)
    missing = sorted(_GAME_FIELDS - set(value))
    if missing:
        raise ForecastError(f"{field_name} is missing required field(s): {', '.join(missing)}")
    return GameForecast(
        mean=_finite_number(value["mean"], field_name=f"{field_name}.mean"),
        stddev=_finite_number(value["stddev"], field_name=f"{field_name}.stddev"),
        availability_probability=_finite_number(
            value["availability_probability"],
            field_name=f"{field_name}.availability_probability",
        ),
    )


def _parse_players(raw: Any) -> dict[str, dict[int, tuple[GameForecast, ...]]]:
    if raw is None:
        return {}
    players = _require_mapping(raw, field_name="players")
    parsed: dict[str, dict[int, tuple[GameForecast, ...]]] = {}
    for player_id, raw_player in players.items():
        if not isinstance(player_id, str) or not player_id:
            raise ForecastError("players keys must be non-empty strings")
        player = _require_mapping(raw_player, field_name=f"players[{player_id!r}]")
        _reject_unknown(player, _PLAYER_FIELDS, field_name=f"players[{player_id!r}]")
        if "weeks" not in player:
            raise ForecastError(f"players[{player_id!r}] must contain weeks")
        weeks = _require_mapping(player["weeks"], field_name=f"players[{player_id!r}].weeks")
        week_map: dict[int, tuple[GameForecast, ...]] = {}
        for raw_week, raw_games in weeks.items():
            if not isinstance(raw_week, str) or not raw_week.isdigit():
                raise ForecastError(
                    f"players[{player_id!r}].weeks keys must be non-negative integer strings"
                )
            week = int(raw_week)
            if not isinstance(raw_games, list):
                raise ForecastError(
                    f"players[{player_id!r}].weeks[{raw_week!r}] must be a list of game forecasts"
                )
            week_map[week] = tuple(
                _parse_game(game, field_name=f"players[{player_id!r}].weeks[{raw_week!r}][{index}]")
                for index, game in enumerate(raw_games)
            )
        parsed[player_id] = week_map
    return parsed


def load_forecasts(
    path: str | Path | None,
    *,
    season: str,
    scoring: Mapping[str, Any],
) -> ForecastConfig:
    """Load and validate a season/scoring-bound Lock-In forecast JSON file.

    ``path=None`` is useful to request the documented defaults without a
    local file.  A file is strict: its root ``mode`` must be ``lock_in`` and
    unknown fields (including outcomes or future scores) are rejected.
    """

    scoring_map = _normalise_scoring(scoring)
    if not isinstance(season, str) or not season.strip():
        raise ForecastError("season must be a non-empty string")
    if path is None:
        return ForecastConfig(season=season, scoring=scoring_map)

    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ForecastError(f"could not load forecast file {str(file_path)!r}: {exc}") from exc
    root = _require_mapping(raw, field_name="forecast root")
    _reject_unknown(root, _ROOT_FIELDS, field_name="forecast root")
    for required in ("mode", "season", "scoring"):
        if required not in root:
            raise ForecastError(f"forecast root is missing required field {required!r}")
    if root["mode"] != "lock_in":
        raise ForecastError("forecast mode must be 'lock_in'")
    file_season = root["season"]
    if not isinstance(file_season, str) or not file_season.strip():
        raise ForecastError("forecast season must be a non-empty string")
    if file_season != season:
        raise ForecastError(
            f"forecast season {file_season!r} does not match requested season {season!r}"
        )
    file_scoring = _normalise_scoring(root["scoring"], field_name="forecast scoring")
    if file_scoring != scoring_map:
        raise ForecastError("forecast scoring does not match the requested league scoring settings")
    source = root.get("source", "local_forecast")
    if not isinstance(source, str) or not source.strip():
        raise ForecastError("forecast source must be a non-empty string")
    return ForecastConfig(
        season=season,
        scoring=file_scoring,
        defaults=_parse_defaults(root.get("defaults")),
        player_weeks=_parse_players(root.get("players")),
        source=source,
    )


__all__ = [
    "DEFAULT_AVAILABILITY_PROBABILITY",
    "DEFAULT_SCHEDULED_GAMES_PER_WEEK",
    "DEFAULT_STDDEV_FRACTION",
    "ForecastConfig",
    "ForecastDefaults",
    "ForecastError",
    "GameForecast",
    "LockInProjection",
    "estimate_player",
    "forecast_stopping_value",
    "load_forecasts",
    "normal_expected_max",
]
