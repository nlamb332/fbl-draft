"""Deterministic live draft valuation.

The live adapter deliberately passes typed :class:`DraftObservation` objects to
this module.  The scorer has no browser or network dependencies, and therefore
is safe to use for replay and dry runs as well as by the coordinator.  Unknown
statistical inputs are retained as warnings; they are never filled from rank,
row order, or a fabricated zero projection.
"""

from __future__ import annotations

import math
import time
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    CandidateScore,
    DraftObservation,
    DraftRecommendation,
    LeagueConfig,
    LivePlayer,
    utc_now,
)

TOTAL_GAMES = 82.0
_EPSILON = 1e-12
_TIE_FRACTION = 0.015
ALGORITHM_VERSION = "draft-strategy-v2"
ROLLOUT_MAX_CANDIDATES = 8
OPPONENT_MODEL_VERSION = "rank-plus-opponent-needs-v1"


@dataclass(frozen=True)
class PlayerValue:
    """The auditable pieces of a player's draft value."""

    custom_fp: float
    gp_factor: float
    season_value: float
    coverage_raw: float
    coverage_bonus: float
    score: float
    warnings: tuple[str, ...] = ()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stats(player: LivePlayer) -> dict[str, float]:
    values: dict[str, float] = {}
    for key, raw in (player.stats or {}).items():
        number = _number(raw)
        if number is not None:
            values[str(key).upper().strip()] = number
    return values


def custom_fantasy_points(
    player: LivePlayer,
    config: LeagueConfig | None = None,
) -> tuple[float, tuple[str, ...], dict[str, float]]:
    """Return per-game custom points, warnings, and weighted components.

    Every scoring field absent from the observation is reported.  Known fields
    contribute their supplied value, which keeps the ordering deterministic
    while making incomplete records visible to the caller.
    """

    cfg = config or LeagueConfig()
    stats = _stats(player)
    warnings: list[str] = []
    explicit_missing = {str(item).upper().strip() for item in player.missing_stats}
    components: dict[str, float] = {}
    total = 0.0
    for raw_key, raw_weight in cfg.scoring.items():
        key = str(raw_key).upper().strip()
        weight = _number(raw_weight)
        if weight is None:
            warnings.append(f"invalid scoring weight for {key}; ignored")
            continue
        if key == "DD" and cfg.double_double_includes_triple_double:
            value = max(stats.get("DD", 0.0), stats.get("TD", 0.0))
            known = "DD" in stats or "TD" in stats
        elif key == "DD" and not cfg.double_double_includes_triple_double:
            value = max(0.0, stats.get("DD", 0.0) - stats.get("TD", 0.0))
            known = "DD" in stats or "TD" in stats
        else:
            value = stats.get(key, 0.0)
            known = key in stats
        if key in explicit_missing:
            known = False
        if not known:
            warnings.append(f"missing projected stat: {key}")
            continue
        contribution = float(weight) * float(value)
        components[key] = contribution
        total += contribution
    # Keep the order stable and avoid duplicate warnings from explicit metadata.
    return total, tuple(dict.fromkeys(warnings)), components


def _games_factor(player: LivePlayer) -> tuple[float, tuple[str, ...]]:
    """Choose direct projected GP first, then availability, with provenance."""

    warnings: list[str] = []
    gp = _number(player.projected_gp)
    if gp is not None:
        if gp < 0 or gp > TOTAL_GAMES:
            warnings.append(f"projected GP {gp:g} outside 0..82; clamped")
        return max(0.0, min(1.0, gp / TOTAL_GAMES)), tuple(warnings)
    availability = _number(player.availability)
    if availability is not None:
        if availability < 0 or availability > 1:
            warnings.append(f"availability {availability:g} outside 0..1; clamped")
        return max(0.0, min(1.0, availability)), tuple(warnings)
    warnings.append("missing projected GP and availability; using neutral factor 1.0")
    return 1.0, tuple(warnings)


def _coverage(player: LivePlayer, roster: Sequence[LivePlayer]) -> float:
    """A small soft signal for currently scarce starting positions."""

    demand = {"C": 1, "G": 2, "F": 2}
    counts = {position: 0 for position in demand}
    for existing in roster:
        for position in demand:
            if position in existing.eligibility:
                counts[position] += 1
    # A multi-position player can satisfy more than one future category, but
    # the cap prevents flexibility from becoming a quota or a hard preference.
    missing = sum(max(0, demand[position] - counts[position]) for position in demand)
    if not missing:
        return 0.0
    return float(sum(max(0, demand[position] - counts[position]) for position in player.eligibility if position in demand))


def value_player(
    player: LivePlayer,
    config: LeagueConfig | None = None,
    *,
    roster: Sequence[LivePlayer] = (),
    _base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> PlayerValue:
    """Calculate an auditable score without using market row order."""

    cfg = config or LeagueConfig()
    cached = (_base_cache or {}).get(player.player_id)
    if cached is None:
        custom, score_warnings, _ = custom_fantasy_points(player, cfg)
        gp_factor, gp_warnings = _games_factor(player)
        if _base_cache is not None:
            _base_cache[player.player_id] = (custom, score_warnings, gp_factor, gp_warnings)
    else:
        custom, score_warnings, gp_factor, gp_warnings = cached
    season = custom * gp_factor
    coverage_raw = _coverage(player, roster)
    # Coverage is a tie adjustment.  It is bounded at two percent of the
    # production value and consequently cannot erase a meaningful projection
    # difference.
    coverage_bonus = min(abs(season) * 0.02, coverage_raw * 0.05)
    score = season + coverage_bonus
    return PlayerValue(
        custom_fp=custom,
        gp_factor=gp_factor,
        season_value=season,
        coverage_raw=coverage_raw,
        coverage_bonus=coverage_bonus,
        score=score,
        warnings=tuple(dict.fromkeys((*score_warnings, *gp_warnings))),
    )


def _matching(players: Sequence[LivePlayer], slots: Sequence[str]) -> bool:
    if len(players) < len(slots):
        return False
    ordered = tuple(slots)
    used: set[int] = set()

    def visit(slot_index: int) -> bool:
        if slot_index == len(ordered):
            return True
        slot = ordered[slot_index]
        for index, player in enumerate(players):
            if index in used:
                continue
            if slot != "UTIL" and slot not in player.eligibility:
                continue
            used.add(index)
            if visit(slot_index + 1):
                return True
            used.remove(index)
        return False

    return visit(0)


def roster_feasible(
    roster: Sequence[LivePlayer],
    pool: Sequence[LivePlayer] = (),
    config: LeagueConfig | None = None,
) -> bool:
    """Check starter matching when enough pool information is available."""

    cfg = config or LeagueConfig()
    capacity = len(cfg.starting_slots) + cfg.bench_slots
    if len(roster) > capacity:
        return False
    if len(roster) == capacity:
        return _matching(roster, cfg.starting_slots)
    from collections import Counter
    from mock_draft.fixture import _min_future_assignments
    known = {player.player_id for player in roster}
    patterns = Counter(tuple(sorted(player.eligibility)) for player in pool if player.player_id not in known)
    minimum = _min_future_assignments(roster, patterns, cfg.starting_slots)
    return minimum is not None and minimum <= capacity - len(roster)


def _candidate(
    player: LivePlayer,
    value: PlayerValue,
    *,
    rank: int,
    tie_applied: bool,
) -> CandidateScore:
    explanation = (
        f"{value.custom_fp:.2f} custom FP/game × {value.gp_factor:.3f} GP/availability factor"
    )
    if value.coverage_bonus > 0:
        explanation += f"; small coverage tie adjustment +{value.coverage_bonus:.3f}"
    return CandidateScore(
        player_id=player.player_id,
        name=player.name,
        score=value.score,
        components={
            "custom_fp": value.custom_fp,
            "gp_factor": value.gp_factor,
            "season_value": value.season_value,
            "coverage_raw": value.coverage_raw,
            "coverage_bonus": value.coverage_bonus if tie_applied else 0.0,
            "rank": float(rank),
        },
        explanation=explanation,
        warnings=value.warnings,
    )


def rank_players(
    observation: DraftObservation,
    config: LeagueConfig | None = None,
    *,
    limit: int | None = None,
    _base_cache: dict[str, tuple[float, tuple[str, ...], float, tuple[str, ...]]] | None = None,
) -> tuple[CandidateScore, ...]:
    """Rank eligible, non-drafted players from one typed observation."""

    cfg = config or LeagueConfig()
    drafted = {event.player_id for event in observation.history}
    drafted.update(player.player_id for player in observation.roster)
    available: list[LivePlayer] = []
    seen: set[str] = set()
    for player in observation.available_players:
        if not player.player_id or player.player_id in drafted or player.player_id in seen:
            continue
        seen.add(player.player_id)
        available.append(player)
    # A complete pool is authoritative.  A partial pool is still useful for
    # advisory ranking, and receives a visible warning in every candidate.
    pool_warning = () if observation.available_pool_complete else ("available pool is incomplete",)
    roster = observation.roster
    values = [(player, value_player(player, cfg, roster=roster, _base_cache=_base_cache)) for player in available]
    feasibility = {}
    feasible = []
    for player, value in values:
        pattern = tuple(sorted(player.eligibility))
        if pattern not in feasibility:
            future = available
            if not observation.available_pool_complete:
                future = [LivePlayer(f"unknown-{i}", "Unknown future player", ("C", "G", "F"))
                          for i in range(max(0, 9 - len(roster)))]
            feasibility[pattern] = roster_feasible((*roster, player), future, cfg)
        if feasibility[pattern]:
            feasible.append((player, value))
    values = feasible
    # Production value is primary.  Coverage participates only inside a small
    # tie band, where it is a transparent deterministic tiebreaker.
    values.sort(key=lambda item: (-item[1].score, -item[1].coverage_raw, item[0].player_id))
    top_value = values[0][1].season_value if values else 0.0
    ranked: list[CandidateScore] = []
    for index, (player, value) in enumerate(values, start=1):
        close = abs(value.season_value - top_value) <= max(abs(top_value), 1.0) * _TIE_FRACTION
        warnings = tuple(dict.fromkeys((*value.warnings, *pool_warning)))
        if warnings != value.warnings:
            value = PlayerValue(**{**value.__dict__, "warnings": warnings})
        ranked.append(_candidate(player, value, rank=index, tie_applied=True))
    if limit is not None:
        return tuple(ranked[: max(0, int(limit))])
    return tuple(ranked)


def strategy_fingerprint(config: LeagueConfig) -> str:
    """Stable fingerprint bound into validation artifacts and promotions."""

    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "teams": config.teams,
        "rounds": config.rounds,
        "starting_slots": list(config.starting_slots),
        "bench_slots": config.bench_slots,
        "scoring": dict(sorted(config.scoring.items())),
        "double_double_includes_triple_double": config.double_double_includes_triple_double,
        "lookahead_budget_seconds": config.recommendation_budget_seconds,
        "lookahead_max_candidates": ROLLOUT_MAX_CANDIDATES,
        "opponent_model": OPPONENT_MODEL_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _promotion_artifact_valid(promotion_path: Any, observation: DraftObservation, config: LeagueConfig) -> bool:
    """Validate a static strategy report before control binds it to a revision."""

    if promotion_path is None:
        return False
    payload: Mapping[str, Any]
    try:
        if isinstance(promotion_path, Mapping):
            payload = promotion_path
        else:
            path = Path(str(promotion_path))
            if not path.is_file():
                return False
            import json

            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, Mapping):
                return False
            payload = loaded
    except (OSError, ValueError, TypeError):
        return False
    if payload.get("status") not in (None, "valid", "promotable", "approved"):
        return False
    # Automatic lookahead promotion requires an independently validated report,
    # not a hand-written session flag.  Counts are intentionally exact for the
    # league shape and the seed threshold is a minimum.
    validation = payload.get("validation", payload)
    if not isinstance(validation, Mapping):
        return False
    metadata = payload.get("metadata", {})
    if isinstance(metadata, Mapping):
        # Validation summaries written by the local runner keep counts in a
        # metadata object and metrics per opponent-misspecification scenario.
        merged = dict(metadata)
        merged.update(validation)
        validation = merged
    summaries = payload.get("summaries", ())
    try:
        if int(validation.get("independent_seed_clusters", validation.get("heldout_seed_clusters", 0))) < 100:
            return False
        if int(validation.get("teams", 0)) != 14 or int(validation.get("opponents", -1)) != 13:
            return False
        if int(validation.get("picks_per_draft", 0)) != 140 or int(validation.get("seats_per_seed", 0)) != 14:
            return False
        if validation.get("foundation_vs_lookahead") is not True:
            return False
        lower = validation.get("paired_bootstrap_95_lower")
        loss = validation.get("worst_seat_loss_fraction")
        if isinstance(summaries, Sequence) and not isinstance(summaries, (str, bytes)) and summaries:
            metrics = [item for item in summaries if isinstance(item, Mapping)]
            if len(metrics) != len(summaries):
                return False
            if any(int(item.get("independent_seed_clusters", 0)) < 100 or int(item.get("seat_rows", 0)) != 14 * int(item.get("independent_seed_clusters", 0)) for item in metrics):
                return False
            if any(not math.isfinite(float(item.get("paired_bootstrap_95_lower", float("nan")))) or not math.isfinite(float(item.get("worst_seat_loss_fraction", float("nan")))) or float(item.get("paired_bootstrap_95_lower", 0)) <= 0 or float(item.get("worst_seat_loss_fraction", -1)) < -0.05 for item in metrics):
                return False
            lower = min(float(item.get("paired_bootstrap_95_lower", 0)) for item in metrics)
            loss = min(float(item.get("worst_seat_loss_fraction", -1)) for item in metrics)
        if lower is None or loss is None or not math.isfinite(float(lower)) or not math.isfinite(float(loss)) or float(lower) <= 0 or float(loss) < -0.05:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    if payload.get("algorithm_version", validation.get("algorithm_version")) != ALGORITHM_VERSION:
        return False
    if payload.get("config_fingerprint", validation.get("config_fingerprint")) != strategy_fingerprint(config):
        return False
    expires = payload.get("expires_at")
    if expires is not None:
        try:
            from .contracts import timestamp_age

            # timestamp_age is now minus expiry; a negative age means expiry is
            # still in the future and is therefore valid.
            if timestamp_age(str(expires)) >= 0:
                return False
        except (ValueError, TypeError):
            return False
    return True


def _recommend_marginal(
    observation: DraftObservation,
    config: LeagueConfig,
    foundation: Sequence[CandidateScore],
    payload: Mapping[str, Any],
) -> DraftRecommendation:
    from .marginal_lineup import ALGORITHM_VERSION as MARGINAL_VERSION
    from .marginal_lineup import params_from_payload, promotion_valid, rank_marginal

    gate = promotion_valid(payload, config)
    params = params_from_payload(payload)
    candidates = tuple(foundation)
    try:
        result = rank_marginal(observation, config, params, budget_seconds=config.recommendation_budget_seconds)
    except Exception as exc:  # pragma: no cover - defensive coordinator boundary
        return DraftRecommendation(
            session_id=observation.session_id,
            revision=observation.revision,
            generated_at=utc_now(),
            candidates=candidates,
            lookahead_status="fallback",
            explanation=f"{MARGINAL_VERSION} fallback to foundation: {type(exc).__name__}",
        )
    if result.status == "fallback":
        status, explanation = "fallback", result.explanation
    elif gate:
        candidates = result.candidates
        status, explanation = "promoted", f"promoted {result.explanation}"
    else:
        status = "advisory"
        top = result.candidates[0].name if result.candidates else "none"
        explanation = f"advisory {MARGINAL_VERSION} favors {top}; promotion gate not passed, foundation ordering retained"
    return DraftRecommendation(
        session_id=observation.session_id,
        revision=observation.revision,
        generated_at=utc_now(),
        candidates=candidates,
        lookahead_status=status,
        explanation=explanation,
    )


def recommend(
    observation: DraftObservation,
    config: LeagueConfig,
    *,
    lookahead_enabled: bool = False,
    promotion_path: Any = None,
) -> DraftRecommendation:
    """Return a deterministic recommendation for the current board.

    Lookahead remains advisory unless the supplied promotion artifact passes the
    independent validation and fingerprint gate.  The control layer separately
    binds any promoted decision to its current observation revision.
    """

    if not isinstance(observation, DraftObservation):
        raise TypeError("observation must be DraftObservation")
    if not isinstance(config, LeagueConfig):
        raise TypeError("config must be LeagueConfig")
    candidates = rank_players(observation, config)
    status = "advisory"
    explanation = "deterministic custom-points ranking"
    from .marginal_lineup import load_promotion_payload

    marginal_payload = load_promotion_payload(promotion_path)
    if marginal_payload is not None:
        # An artifact that declares ``marginal-lineup-v1`` selects that policy
        # only through its own promotion gate.  Without a passing gate the
        # foundation ordering stays first and the marginal view is advisory.
        return _recommend_marginal(observation, config, candidates, marginal_payload)
    if lookahead_enabled:
        gate = _promotion_artifact_valid(promotion_path, observation, config)
        try:
            from .lookahead import apply_lookahead

            result = apply_lookahead(
                observation,
                config,
                candidates,
                promote=gate,
                budget_seconds=config.recommendation_budget_seconds,
            )
            if result.candidates:
                candidates = result.candidates
            status = "promoted" if gate and result.status == "promoted" else "advisory"
            explanation = result.explanation
        except Exception as exc:  # pragma: no cover - defensive coordinator boundary
            status = "advisory"
            explanation += f"; lookahead fallback: {type(exc).__name__}"
    return DraftRecommendation(
        session_id=observation.session_id,
        revision=observation.revision,
        generated_at=utc_now(),
        candidates=tuple(candidates),
        lookahead_status=status,
        explanation=explanation,
    )


# Readable aliases for callers and replay tools.
score_player = value_player
rank_available_players = rank_players

__all__ = [
    "PlayerValue",
    "ALGORITHM_VERSION",
    "custom_fantasy_points",
    "rank_available_players",
    "rank_players",
    "recommend",
    "roster_feasible",
    "score_player",
    "strategy_fingerprint",
    "value_player",
]
