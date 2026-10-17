"""Decision counterfactuals for the campaign draft simulator.

The campaign engine owns player valuation and draft mechanics.  This module
owns the experiment design around those mechanics: reconstructing decision
states, making a fixed candidate union, running matched continuations, and
selecting an alternative from discovery rows before looking at evaluation
rows.  It intentionally accepts dictionaries as well as the engine's
dataclasses.  That keeps checkpoint files stable and makes small exhaustive
fixtures useful without importing the full simulation stack.

The public entry points are :func:`build_decision_states` and
:func:`run_counterfactuals`.  ``run_counterfactuals`` never treats a timeout
as a zero-valued continuation.  A timed-out row remains in the checkpoint and
the corresponding group is excluded from aggregate estimates until it is
complete on resume.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import importlib
import json
import math
import random
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


POLICIES: tuple[str, ...] = (
    "marginal",
    "foundation",
    "custom_ppg",
    "custom_total",
    "positional_vor",
    "market",
)
BASELINE_POLICIES: tuple[str, ...] = POLICIES[1:]
DEFAULT_OUR_SEAT = 13
DEFAULT_SEED = 20261014
DISCOVERY_COUNT = 16
EVALUATION_COUNT = 16
BOOTSTRAP_REPLICATES = 4000
OWN_PICK_NUMBERS: tuple[int, ...] = (13, 16, 41, 44, 69, 72, 97, 100, 125, 128)
TRIAL_ID = "200082403"


class CounterfactualError(ValueError):
    """Malformed state or an invalid counterfactual request."""


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _player_id(player: Any) -> str:
    value = _get(player, "player_id", _get(player, "id", None))
    if value is None:
        raise CounterfactualError(f"player has no player_id: {player!r}")
    return str(value)


def _event_player_id(event: Any) -> str | None:
    value = _get(event, "player_id", _get(event, "id", None))
    if value is None:
        value = _get(event, "player", None)
        if value is not None and not isinstance(value, (str, int, float)):
            value = _player_id(value)
    return None if value is None else str(value)


def _event_pick(event: Any, fallback: int) -> int:
    value = _get(event, "pick_number", _get(event, "pick", fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _event_seat(event: Any) -> int | None:
    value = _get(event, "seat", _get(event, "team_seat", None))
    if value is None:
        team = _get(event, "team", None)
        if isinstance(team, int):
            value = team
        elif isinstance(team, str) and team.strip().isdigit():
            value = team.strip()
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _snake_seat(pick_number: int, teams: int = 14) -> int:
    """Return the one-based seat for a standard snake pick."""

    if pick_number <= 0:
        return 0
    index = (pick_number - 1) % teams
    round_number = (pick_number - 1) // teams
    return teams - index if round_number % 2 else index + 1


def _event_is_ours(event: Any, pick: int, our_seat: int) -> bool:
    seat = _event_seat(event)
    return seat == our_seat if seat is not None else _snake_seat(pick) == our_seat


def _current_rosters(
    events: Sequence[Any],
    player_map: Mapping[str, Any],
    our_seat: int,
) -> dict[int, list[Any]]:
    rosters: dict[int, list[Any]] = {seat: [] for seat in range(1, 15)}
    for index, event in enumerate(events, 1):
        pick = _event_pick(event, index)
        pid = _event_player_id(event)
        if pid is None or pid not in player_map:
            continue
        seat = _event_seat(event) or _snake_seat(pick)
        if seat in rosters:
            rosters[seat].append(player_map[pid])
    return rosters


def _candidate_is_feasible(
    engine: Any,
    candidate: Any,
    *,
    our_seat: int,
    rosters: Mapping[int, Sequence[Any]],
    available: Sequence[Any],
) -> bool:
    """Use the engine's legal-candidate guard when it is available."""

    checker = getattr(engine, "_legal_candidate", None)
    if checker is None:
        return True
    return bool(checker(candidate, our_seat, rosters, available))


def _history(draft: Any) -> list[Any]:
    raw = _get(draft, "history", ())
    if isinstance(raw, Mapping):
        raw = raw.values()
    return list(raw or ())


def _player_map(players: Sequence[Any]) -> dict[str, Any]:
    return {_player_id(player): player for player in players}


def _roster_at_prefix(
    events: Sequence[Any],
    player_map: Mapping[str, Any],
    prefix: Sequence[str],
    our_seat: int,
) -> list[Any]:
    ids = set(prefix)
    roster: list[Any] = []
    for index, event in enumerate(events, 1):
        pick = _event_pick(event, index)
        pid = _event_player_id(event)
        if pid in ids and _event_is_ours(event, pick, our_seat) and pid in player_map:
            roster.append(player_map[pid])
    return roster


def _engine() -> Any:
    """Load the campaign engine lazily so fixture tests can inject it."""

    try:
        return importlib.import_module("draft_assistant.campaign_engine")
    except ModuleNotFoundError:
        # The campaign package may be nested in draft_assistant during local
        # development.  Do not import it at module import time: the root CLI
        # can install the engine later in its process.
        try:
            return importlib.import_module("draft_assistant.campaign_engine")
        except ModuleNotFoundError as exc:
            raise CounterfactualError(
                "campaign_engine is required for counterfactual simulation"
            ) from exc


def _call_rank_policy(
    engine: Any,
    players: Sequence[Any],
    roster: Sequence[Any],
    history: Sequence[Any],
    our_seat: int,
    policy: str,
) -> list[Any]:
    fn = getattr(engine, "rank_policy", None)
    if fn is None:
        raise CounterfactualError("campaign_engine.rank_policy is unavailable")
    result = fn(players, roster, history, our_seat, policy)
    return list(result or ())


def _ranked_id(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    value = _get(item, "player_id", _get(item, "id", None))
    if value is None:
        return None
    return str(value)


def _score_item(item: Any) -> float | None:
    value = _get(item, "score", None)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rank_choice(ranked: Sequence[Any], available: set[str]) -> str | None:
    for item in ranked:
        pid = _ranked_id(item)
        if pid and pid in available:
            return pid
    return None


def _dedupe_ids(ids: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in ids:
        if value is None:
            continue
        pid = str(value)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _jsonable(value: Any) -> Any:
    """Convert engine records to deterministic JSON without losing trace data."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if dataclasses.is_dataclass(value):
        return {key: _jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items() if not str(key).startswith("_")}
    return str(value)


def _compact_evaluation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Retain report metrics while keeping daily allocation traces external."""

    fields = ("points", "weekly_points", "usable_starts", "benched_points", "hindsight_points", "missing_scoring_fields")
    return {field: _jsonable(evaluation[field]) for field in fields if field in evaluation}


def _append_trace(path: Path | None, key: str, trace: Any) -> str | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", compresslevel=1, encoding="utf-8") as handle:
        handle.write(json.dumps({"key": key, "trace": _jsonable(trace)}, separators=(",", ":")) + "\n")
    return str(path)


def _stable_int(*parts: Any) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _reconstruction_metadata() -> dict[str, Any]:
    return {
        "status": "approximate",
        "exact_reconstruction": False,
        "trial_id": TRIAL_ID,
        "own_pick_numbers": list(OWN_PICK_NUMBERS),
        "reason": (
            "The latest exact trial capture is unavailable. Counterfactuals use the "
            "frozen simulated seat-13 root and/or a dated captured snapshot."
        ),
        "limitations": [
            "The available captured input is an older October 13 snapshot.",
            "Coward and Filipowski concentration is reviewed under the supplied projections and outcomes.",
            "Results are decision counterfactuals, not an exact replay of trial 200082403.",
        ],
        "focus": {
            "players": ["Coward", "Filipowski"],
            "center_concentration_review": True,
            "full_ten_pick_regret_scope": True,
        },
    }


def _result_status(result: Any) -> str:
    status = str(_get(result, "status", "complete"))
    return status if status in {"complete", "incomplete", "unavailable"} else "incomplete"


def _result_roster(result: Any, our_seat: int) -> list[Any]:
    roster = _get(result, "roster", None)
    if roster is not None:
        return list(roster)
    rosters = _get(result, "rosters", {}) or {}
    if isinstance(rosters, Mapping):
        for key in (our_seat, str(our_seat), "our", "our-team", "our_team"):
            if key in rosters:
                return list(rosters[key] or ())
    return []


def _call_simulate(
    engine: Any,
    players: Sequence[Any],
    *,
    our_seat: int,
    seed: int,
    opponent_mode: str,
    policy: str,
    prefix: Sequence[str] = (),
    forced: Mapping[int, str] | None = None,
    deadline: float | None = None,
) -> Any:
    fn = getattr(engine, "simulate_draft", None)
    if fn is None:
        raise CounterfactualError("campaign_engine.simulate_draft is unavailable")
    by_id = _player_map(players)
    converted_prefix: list[Any] = []
    for index, raw in enumerate(prefix, 1):
        # Engine prefixes are PickEvent-compatible mappings.  Preserve a
        # caller-supplied event, and convert compact player-ID prefixes before
        # dispatch so an internal engine TypeError cannot be mistaken for a
        # compatibility signal.
        if _event_player_id(raw) is not None and _get(raw, "pick_number", None) is not None and _get(raw, "team", None) is not None:
            converted_prefix.append(raw)
            continue
        pid = _event_player_id(raw) or str(raw)
        seat = _snake_seat(index)
        converted_prefix.append(
            {
                "pick_number": index,
                "team": "Example Team" if seat == our_seat else str(seat),
                "player_id": pid,
                "player_name": str(_get(by_id.get(pid), "name", pid)),
            }
        )
    return fn(
        players,
        our_seat,
        seed,
        opponent_mode,
        policy,
        prefix=tuple(converted_prefix),
        forced=dict(forced or {}),
        deadline=deadline,
    )


def _generate_continuation_world(engine: Any, base_dataset: Any, seed: int) -> Any:
    fn = getattr(engine, "generate_world", None)
    if fn is None:
        raise CounterfactualError("campaign_engine.generate_world is unavailable")
    # Every matched branch and its marginal reference share this one world;
    # different continuation seeds receive independent generated outcomes.
    return fn(base_dataset, seed=int(seed), environment="moderate")


def _evaluate(engine: Any, dataset: Any, roster: Sequence[Any]) -> dict[str, Any]:
    fn = getattr(engine, "evaluate_roster", None)
    if fn is None:
        raise CounterfactualError("campaign_engine.evaluate_roster is unavailable")
    result = fn(dataset, roster)
    if not isinstance(result, Mapping):
        raise CounterfactualError("campaign_engine.evaluate_roster must return a mapping")
    return dict(result)


def _metric(evaluation: Mapping[str, Any]) -> float | None:
    value = evaluation.get("points")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CounterfactualError("campaign_engine.evaluate_roster returned no numeric points") from exc
    if not math.isfinite(number):
        raise CounterfactualError("campaign_engine.evaluate_roster returned non-finite points")
    return number


def build_decision_states(
    base_draft: Any,
    players: Sequence[Any],
    our_seat: int = DEFAULT_OUR_SEAT,
) -> list[dict[str, Any]]:
    """Build one JSON-compatible decision state for every own pick.

    The candidate union is frozen from the remaining pool at the decision:
    marginal's top five and the first choice of every baseline policy.  The
    marginal first choice is always retained, even if a prototype ranker emits
    duplicate or malformed rows.
    """

    player_list = list(players or ())
    by_id = _player_map(player_list)
    events = _history(base_draft)
    events.sort(key=lambda event: _event_pick(event, 0))
    if not events:
        return []
    engine = _engine()
    states: list[dict[str, Any]] = []
    picked_before: list[str] = []
    for event_index, event in enumerate(events):
        pick_number = _event_pick(event, event_index + 1)
        if not _event_is_ours(event, pick_number, our_seat):
            pid = _event_player_id(event)
            if pid:
                picked_before.append(pid)
            continue
        prefix = tuple(picked_before)
        roster = _roster_at_prefix(events[:event_index], by_id, prefix, our_seat)
        remaining = [player for player in player_list if _player_id(player) not in set(prefix)]
        available = {_player_id(player) for player in remaining}
        rosters = _current_rosters(events[:event_index], by_id, our_seat)
        feasibility_cache: dict[str, bool] = {}

        def feasible(pid: str) -> bool:
            if pid not in feasibility_cache:
                feasibility_cache[pid] = _candidate_is_feasible(
                    engine,
                    by_id[pid],
                    our_seat=our_seat,
                    rosters=rosters,
                    available=remaining,
                )
            return feasibility_cache[pid]

        policy_choices: dict[str, str | None] = {}
        ranked_rows: dict[str, list[Any]] = {}
        for policy in POLICIES:
            # Rank against the full catalog.  Foundation and marginal ranks
            # intentionally carry known players for positional context; the
            # candidate union is filtered only after ranking.
            ranked = _call_rank_policy(engine, player_list, roster, events[:event_index], our_seat, policy)
            filtered: list[Any] = []
            for row in ranked:
                pid = _ranked_id(row)
                if pid is None or pid not in available:
                    continue
                candidate = by_id.get(pid)
                if candidate is None or not feasible(pid):
                    continue
                filtered.append(row)
            ranked_rows[policy] = filtered
            policy_choices[policy] = _rank_choice(filtered, available)
        marginal_rows = ranked_rows["marginal"]
        candidate_ids = _dedupe_ids(
            [_ranked_id(row) for row in marginal_rows[:5]]
            + [policy_choices[policy] for policy in POLICIES]
        )
        # Preserve the actual pick for stale/partial captured histories, while
        # marking it as outside the available candidate union when necessary.
        actual_pid = _event_player_id(event)
        if (
            actual_pid
            and actual_pid not in candidate_ids
            and actual_pid in available
            and feasible(actual_pid)
        ):
            candidate_ids.append(actual_pid)
        state = {
            "pick_number": pick_number,
            "our_seat": our_seat,
            "prefix": list(prefix),
            "actual_prefix": list(prefix),
            "roster_ids": [_player_id(player) for player in roster],
            "roster": [_player_id(player) for player in roster],
            "remaining_player_ids": [_player_id(player) for player in remaining],
            "remaining_pool": [_player_id(player) for player in remaining],
            "candidate_ids": candidate_ids,
            "candidate_union": list(candidate_ids),
            "marginal_choice": policy_choices.get("marginal"),
            "policy_choices": policy_choices,
            "actual_pick_id": actual_pid,
            "actual_pick_available": actual_pid in available if actual_pid else False,
            "rank_components": {
                policy: [
                    {
                        "player_id": _ranked_id(row),
                        "score": _score_item(row),
                        "components": _jsonable(_get(row, "components", {})),
                    }
                    for row in ranked_rows[policy][:5]
                    if _ranked_id(row) is not None
                ]
                for policy in POLICIES
            },
        }
        states.append(state)
        if actual_pid:
            picked_before.append(actual_pid)
    return states


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "wt", compresslevel=1, encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    else:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return dict(json.load(handle))
    with path.open("r", encoding="utf-8") as handle:
        return dict(json.load(handle))


def _checkpoint(
    path: Path | None,
    output: Mapping[str, Any],
) -> None:
    if path is None:
        return
    # A checkpoint is intentionally a complete JSON document.  Replacing a
    # sibling temporary file makes interruption between rows recoverable.
    temp = path.with_name(path.name + (".tmp.gz" if path.suffix.lower() == ".gz" else ".tmp"))
    _write_json(temp, output)
    temp.replace(path)


def _continuation_schedule(seed: int, continuations: int) -> list[dict[str, Any]]:
    if continuations < 1:
        return []
    if continuations == 1:
        split_counts = [("discovery", 1)]
    else:
        # Keep discovery and evaluation balanced for smoke runs too.  For the
        # target 32 this yields sixteen seeds in each split, interleaved in
        # four-seed chunks so an early deadline leaves evidence in both.
        discovery = min(DISCOVERY_COUNT, (continuations + 1) // 2)
        evaluation = continuations - discovery
        if continuations >= 8:
            chunk = min(4, discovery)
            split_counts = []
            remaining_discovery = discovery
            remaining_evaluation = evaluation
            while remaining_discovery or remaining_evaluation:
                take_discovery = min(chunk, remaining_discovery)
                if take_discovery:
                    split_counts.append(("discovery", take_discovery))
                    remaining_discovery -= take_discovery
                take_evaluation = min(chunk, remaining_evaluation)
                if take_evaluation:
                    split_counts.append(("evaluation", take_evaluation))
                    remaining_evaluation -= take_evaluation
        else:
            split_counts = [("discovery", discovery), ("evaluation", evaluation)]
    rows: list[dict[str, Any]] = []
    index = 0
    for split, count in split_counts:
        for offset in range(count):
            rows.append(
                {
                    "continuation_index": index,
                    "split": split,
                    "seed": _stable_int(seed, "continuation", index),
                    # Names match campaign_engine's three reactive populations.
                    "opponent_mode": ("nominal", "market", "custom")[index % 3],
                }
            )
            index += 1
    return rows


def _engine_policy_fingerprint(engine: Any) -> str:
    """Fingerprint every policy source/config that can affect a checkpoint.

    The campaign engine delegates marginal valuation and roster allocation to
    the production modules.  Hashing only ``campaign_engine.py`` would allow a
    resume to combine rows generated before and after a production policy or
    promotion artifact changed.
    """

    module_path = getattr(engine, "__file__", None)
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    source_paths = [
        Path(module_path) if module_path else None,
        Path(__file__),
        root / "draft_assistant" / "marginal_lineup.py",
        root / "draft_assistant" / "valuation.py",
        root / "draft_assistant" / "contracts.py",
        root / "draft_assistant" / "validation_runner.py",
        root / "draft_assistant" / "validation" / "marginal-lineup-v1" / "promotion_artifact.json",
        root / "draft_assistant" / "validation" / "marginal-lineup-v1" / "frozen_params.json",
        root / "mock_draft" / "fixture.py",
    ]
    seen: set[str] = set()
    for source_path in source_paths:
        if source_path is None:
            continue
        source_path = source_path.resolve()
        marker = str(source_path).lower()
        if marker in seen:
            continue
        seen.add(marker)
        digest.update(b"\x00source:")
        digest.update(str(source_path).encode("utf-8"))
        try:
            digest.update(source_path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    digest.update(repr(tuple(getattr(engine, "POLICIES", POLICIES))).encode("utf-8"))
    return digest.hexdigest()


def _dataset_identity(dataset: Any) -> dict[str, Any]:
    """Hash both forecast inputs and observed outcomes for resume safety."""

    payload = {
        "name": _get(dataset, "name", "unknown"),
        "players": _jsonable(_get(dataset, "players", ())),
        "dates": _jsonable(_get(dataset, "dates", ())),
        "scheduled": _jsonable(_get(dataset, "scheduled", {})),
        "outcomes": _jsonable(_get(dataset, "outcomes", {})),
        "missing_scoring_fields": _jsonable(_get(dataset, "missing_scoring_fields", ())),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "forecast_outcome_digest": hashlib.sha256(encoded).hexdigest(),
        "forecast_player_count": len(payload["players"] or ()),
        "outcome_date_count": len(payload["outcomes"] or {}),
    }


def _checkpoint_identity(
    dataset: Any,
    base_draft: Any,
    engine: Any,
    *,
    seed: int,
    continuations: int,
    our_seat: int,
    schedule: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    history_payload = _jsonable(_history(base_draft))
    history_digest = hashlib.sha256(
        json.dumps(history_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    identity = {
        **_dataset_identity(dataset),
        "engine_policy_fingerprint": _engine_policy_fingerprint(engine),
        "base_history_digest": history_digest,
        "seed": int(seed),
        "continuations": int(continuations),
        "our_seat": int(our_seat),
        "target_seeds": [int(row["seed"]) for row in schedule],
        "target_splits": [str(row["split"]) for row in schedule],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity["digest"] = hashlib.sha256(encoded).hexdigest()
    return identity


def _row_complete(status: str) -> bool:
    return status == "complete"


def _branch_summary(rows: Sequence[Mapping[str, Any]], target_count: int) -> dict[str, Any]:
    statuses = [str(row.get("status", "incomplete")) for row in rows]
    valid_rows: list[Mapping[str, Any]] = []
    for row in rows:
        if row.get("status") != "complete" or row.get("baseline_status") != "complete":
            continue
        try:
            value = float(row["value"])
            regret = float(row["regret"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and math.isfinite(regret):
            valid_rows.append(row)
    values = [float(row["value"]) for row in valid_rows]
    regrets = [float(row["regret"]) for row in valid_rows]
    # Unavailable and baseline-incomplete rows remain diagnostics.  They are
    # never silently removed from a single-branch estimate.
    complete_group = target_count > 0 and len(rows) >= target_count and len(valid_rows) == len(rows)
    result: dict[str, Any] = {
        "rows": len(rows),
        "target_rows": target_count,
        "complete_group": complete_group,
        "incomplete_rows": statuses.count("incomplete"),
        "unavailable_rows": statuses.count("unavailable"),
        "ineligible_rows": len(rows) - len(valid_rows) - statuses.count("incomplete") - statuses.count("unavailable"),
        "available_rows": len(values),
        "independent_seed_clusters": len({str(row.get("seed")) for row in valid_rows}),
    }
    if complete_group and regrets:
        result["mean_regret"] = sum(regrets) / len(regrets)
        result["mean_improvement"] = result["mean_regret"]
        result["regret_ci95"] = _bootstrap_ci(valid_rows, field="regret")
        result["directional_claim_supported"] = result["independent_seed_clusters"] >= 8
    else:
        result["mean_regret"] = None
        result["mean_improvement"] = None
        result["regret_ci95"] = None
        result["directional_claim_supported"] = False
    if complete_group and values:
        result["mean_value"] = sum(values) / len(values)
    else:
        result["mean_value"] = None
    return result


def _single_row_valid(row: Mapping[str, Any]) -> bool:
    """Whether a single-branch row can enter a finalized stage estimate."""

    if row.get("status") != "complete" or row.get("baseline_status") != "complete":
        return False
    try:
        value = float(row["value"])
        regret = float(row["regret"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(value) and math.isfinite(regret)


def _recover_pair_baseline_status(row: dict[str, Any]) -> None:
    """Recover the omitted pair baseline status from first-pass metrics."""

    if row.get("baseline_status") is not None:
        return
    baseline_value = row.get("baseline_value")
    baseline_evaluation = row.get("baseline_evaluation")
    try:
        finite_baseline = math.isfinite(float(baseline_value))
    except (TypeError, ValueError):
        finite_baseline = False
    if finite_baseline and isinstance(baseline_evaluation, Mapping) and baseline_evaluation:
        row["baseline_status"] = "complete"
        row["baseline_status_recovered"] = True
    else:
        row["baseline_status"] = "incomplete"


def _pair_row_valid(row: Mapping[str, Any]) -> bool:
    if isinstance(row, dict):
        _recover_pair_baseline_status(row)
    if row.get("status") != "complete" or row.get("baseline_status") != "complete":
        return False
    try:
        values = [float(row[field]) for field in ("value", "baseline_value", "regret")]
    except (KeyError, TypeError, ValueError):
        return False
    return all(math.isfinite(value) for value in values)


def _balanced_pair_stage_indices(
    schedule: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    pair_target: int,
) -> dict[str, list[int]]:
    """Find a completed balanced pair stage within the explicit pair budget."""

    attempted = list(schedule[:pair_target])
    valid = {
        int(row.get("continuation_index"))
        for row in rows
        if _pair_row_valid(row)
    }
    prefixes: dict[str, list[int]] = {}
    for split in ("discovery", "evaluation"):
        prefix: list[int] = []
        for continuation in (item for item in attempted if item.get("split") == split):
            index = int(continuation["continuation_index"])
            if index not in valid:
                break
            prefix.append(index)
        prefixes[split] = prefix
    discovery = prefixes["discovery"]
    evaluation = prefixes["evaluation"]
    if not evaluation:
        stage_count = len(discovery) if not any(item.get("split") == "evaluation" for item in attempted) else 0
    else:
        stage_count = min(len(discovery), len(evaluation))
    if pair_target >= 8:
        stage_count = (stage_count // 4) * 4 if stage_count >= 4 else 0
    return {
        "discovery": discovery[:stage_count] if evaluation or any(item.get("split") == "evaluation" for item in attempted) else discovery,
        "evaluation": evaluation[:stage_count],
    }


def _balanced_stage_indices(
    schedule: Sequence[Mapping[str, Any]],
    branches: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the largest completed balanced continuation prefix.

    Rows after a gap remain raw diagnostics, but cannot enter an estimate.  A
    stage is taken only when every candidate has a valid row for each
    continuation in that stage.  The production schedule is already chunked
    discovery/evaluation in fours; smaller smoke runs use their full balanced
    count (for example one and one with two continuations).
    """

    split_rows = {
        split: [row for row in schedule if str(row.get("split")) == split]
        for split in ("discovery", "evaluation")
    }
    valid_by_branch: dict[str, dict[str, set[int]]] = {"discovery": {}, "evaluation": {}}
    for split, continuation_rows in split_rows.items():
        for candidate_id, branch in branches.items():
            row_map = {
                int(row.get("continuation_index")): row
                for row in branch.get(f"{split}_rows", ())
                if _single_row_valid(row)
            }
            valid_by_branch[split][candidate_id] = set(row_map)

    prefixes: dict[str, list[int]] = {}
    for split, continuation_rows in split_rows.items():
        prefix: list[int] = []
        for continuation in continuation_rows:
            index = int(continuation["continuation_index"])
            if not all(index in valid_by_branch[split].get(candidate_id, set()) for candidate_id in branches):
                break
            prefix.append(index)
        prefixes[split] = prefix

    discovery_prefix = prefixes["discovery"]
    evaluation_prefix = prefixes["evaluation"]
    if not evaluation_prefix:
        # A one-continuation smoke run has discovery only.  With a normal
        # two-plus run, no evaluation prefix means there is no balanced stage.
        stage_count = len(discovery_prefix) if not split_rows["evaluation"] else 0
    else:
        stage_count = min(len(discovery_prefix), len(evaluation_prefix))
    if len(schedule) >= 8:
        # Production-sized schedules are finalized only at full four-row
        # discovery/evaluation chunks.  This prevents the first evaluation
        # row from freezing a one-row selection while its other three peers
        # are still in flight.
        stage_count = (stage_count // 4) * 4 if stage_count >= 4 else 0
    stage = {
        "discovery": discovery_prefix[:stage_count] if split_rows["evaluation"] else discovery_prefix,
        "evaluation": evaluation_prefix[:stage_count],
        "actual_target_used": stage_count,
        "requested_targets": {split: len(rows) for split, rows in split_rows.items()},
    }
    if not split_rows["evaluation"]:
        stage["actual_target_used"] = len(stage["discovery"])
    return stage


def _finalize_single_stages(
    output: dict[str, Any],
    schedule: Sequence[Mapping[str, Any]],
    *,
    freeze_selection: bool = True,
) -> None:
    """Finalize the latest balanced stage without deleting in-flight rows."""

    for turn in output.get("own_turns", ()):
        branches = turn.get("branches", {}) or {}
        stage = _balanced_stage_indices(schedule, branches)
        turn["stage_finalization"] = {
            "discovery_actual_target_used": len(stage["discovery"]),
            "evaluation_actual_target_used": len(stage["evaluation"]),
            "discovery_requested_target": stage["requested_targets"]["discovery"],
            "evaluation_requested_target": stage["requested_targets"]["evaluation"],
            "balanced": bool(stage["discovery"]) and (
                not stage["evaluation"] or len(stage["discovery"]) == len(stage["evaluation"])
            ),
        }
        for candidate_id, branch in branches.items():
            for split in ("discovery", "evaluation"):
                allowed = set(stage[split])
                raw_rows = list(branch.get(f"{split}_rows", ()) or ())
                sample_rows = [row for row in raw_rows if int(row.get("continuation_index", -1)) in allowed]
                summary = _branch_summary(sample_rows, len(allowed))
                summary["requested_target_rows"] = stage["requested_targets"][split]
                summary["actual_target_used"] = len(allowed)
                summary["sample_count"] = len(sample_rows)
                summary["excluded_rows"] = len(raw_rows) - len(sample_rows)
                branch[f"{split}_summary"] = summary

        discovery_summary = {
            candidate_id: branch.get("discovery_summary", {})
            for candidate_id, branch in branches.items()
        }
        # Freeze only after a complete finalized discovery stage.  A later
        # holdout stage can update the selected branch's report, but never the
        # selected candidate itself.
        marginal_choice = turn.get("marginal_choice")
        marginal_summary = discovery_summary.get(marginal_choice, {}) or {}
        if freeze_selection and turn.get("discovery_selection") is None and marginal_summary.get("complete_group"):
            turn["discovery_selection"] = select_strongest_alternative(
                branches,
                marginal_choice,
                split="discovery",
            ) or marginal_choice
            turn["discovery_selection_stage"] = len(stage["discovery"])
        selected = turn.get("discovery_selection")
        if selected is not None and selected in branches and selected != marginal_choice:
            turn["evaluation_selected_branch"] = {
                "candidate_id": selected,
                "summary": branches[selected].get("evaluation_summary", {}),
            }
        else:
            turn["evaluation_selected_branch"] = {
                "candidate_id": None,
                "status": "inconclusive",
                "summary": {},
            }


def _bootstrap_ci(rows: Sequence[Mapping[str, Any]], *, field: str, replicates: int = BOOTSTRAP_REPLICATES) -> list[float] | None:
    # Cluster by continuation seed, averaging modes within each seed before
    # resampling.  This avoids presenting three opponent modes as independent
    # seasons.
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "complete" or row.get(field) is None:
            continue
        clusters[str(row.get("seed"))].append(float(row[field]))
    values = [sum(group) / len(group) for group in clusters.values() if group]
    if len(values) < 2:
        return None
    rng = random.Random(_stable_int(field, tuple(sorted(clusters))))
    means: list[float] = []
    for _ in range(max(1, int(replicates))):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[max(0, int(0.025 * len(means)) - 1)]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return [lo, hi]


def select_strongest_alternative(
    branches: Mapping[str, Mapping[str, Any]],
    marginal_choice: str | None,
    *,
    split: str = "discovery",
) -> str | None:
    """Select the highest discovery regret, excluding the recommended branch."""

    candidates: list[tuple[float, str]] = []
    for player_id, branch in branches.items():
        if player_id == marginal_choice:
            continue
        summary = branch.get(f"{split}_summary", branch.get(split, {}))
        if not isinstance(summary, Mapping) or not summary.get("complete_group"):
            continue
        regret = summary.get("mean_regret")
        if regret is None:
            continue
        candidates.append((float(regret), str(player_id)))
    return max(candidates, key=lambda item: (item[0], item[1]))[1] if candidates else None


def _single_branch_row(
    engine: Any,
    dataset: Any,
    players: Sequence[Any],
    state: Mapping[str, Any],
    continuation: Mapping[str, Any],
    candidate_id: str,
    *,
    our_seat: int,
    baseline_cache: dict[tuple[int, str], Any],
    deadline: float | None,
    trace_path: Path | None = None,
) -> dict[str, Any]:
    cont_seed = int(continuation["seed"])
    mode = str(continuation["opponent_mode"])
    key = (cont_seed, mode)
    if key not in baseline_cache:
        baseline_result = _call_simulate(
            engine,
            players,
            our_seat=our_seat,
            seed=cont_seed,
            opponent_mode=mode,
            policy="marginal",
            prefix=state.get("prefix", ()),
            deadline=deadline,
        )
        baseline_evaluation = {}
        baseline_value = None
        if _result_status(baseline_result) == "complete":
            baseline_evaluation = _compact_evaluation(_evaluate(engine, dataset, _result_roster(baseline_result, our_seat)))
            baseline_value = _metric(baseline_evaluation)
        baseline_cache[key] = {
            "result": baseline_result,
            "evaluation": baseline_evaluation,
            "value": baseline_value,
            "trace_ref": _append_trace(
                trace_path,
                f"baseline:{state['pick_number']}:{continuation['continuation_index']}",
                _get(baseline_result, "trace", ()),
            ),
        }
    baseline_entry = baseline_cache[key]
    baseline = baseline_entry["result"]
    forced_result = _call_simulate(
        engine,
        players,
        our_seat=our_seat,
        seed=cont_seed,
        opponent_mode=mode,
        policy="marginal",
        prefix=state.get("prefix", ()),
        forced={int(state["pick_number"]): candidate_id},
        deadline=deadline,
    )
    status = _result_status(forced_result)
    baseline_status = _result_status(baseline)
    row: dict[str, Any] = {
        "pick_number": int(state["pick_number"]),
        "candidate_id": candidate_id,
        "seed": cont_seed,
        "continuation_index": continuation["continuation_index"],
        "split": continuation["split"],
        "opponent_mode": mode,
        "status": status,
        "baseline_status": baseline_status,
        "available": status != "unavailable",
        "value": None,
        "baseline_value": None,
        "regret": None,
        "improvement": None,
        "evaluation": None,
        "baseline_evaluation": None,
        "roster_ids": [_player_id(player) for player in _result_roster(forced_result, our_seat)],
        "history": _jsonable(_get(forced_result, "history", ())),
        "trace_ref": _append_trace(
            trace_path,
            f"single:{state['pick_number']}:{candidate_id}:{continuation['continuation_index']}",
            _get(forced_result, "trace", ()),
        ),
        "baseline_trace_ref": baseline_entry.get("trace_ref"),
    }
    if status == "complete":
        evaluation = _evaluate(engine, dataset, _result_roster(forced_result, our_seat))
        row["evaluation"] = _compact_evaluation(evaluation)
        row["value"] = _metric(evaluation)
    if baseline_status == "complete":
        row["baseline_evaluation"] = baseline_entry["evaluation"]
        row["baseline_value"] = baseline_entry["value"]
        row["baseline_roster_ids"] = [_player_id(player) for player in _result_roster(baseline, our_seat)]
    if row["value"] is not None and row["baseline_value"] is not None:
        row["regret"] = float(row["value"]) - float(row["baseline_value"])
        row["improvement"] = float(row["regret"])
    return row


def _pair_result(
    engine: Any,
    dataset: Any,
    players: Sequence[Any],
    first_state: Mapping[str, Any],
    second_state: Mapping[str, Any],
    first_id: str,
    second_id: str,
    continuation: Mapping[str, Any],
    *,
    our_seat: int,
    deadline: float | None,
    baseline_cache: dict[tuple[int, str], Any],
    first_cache: dict[tuple[int, str, str], Any],
    trace_path: Path | None = None,
) -> dict[str, Any]:
    seed = int(continuation["seed"])
    mode = str(continuation["opponent_mode"])
    prefix = first_state.get("prefix", ())
    baseline_key = (seed, mode)
    if baseline_key not in baseline_cache:
        baseline = _call_simulate(
            engine,
            players,
            our_seat=our_seat,
            seed=seed,
            opponent_mode=mode,
            policy="marginal",
            prefix=prefix,
            deadline=deadline,
        )
        baseline_evaluation = {}
        baseline_value = None
        if _result_status(baseline) == "complete":
            baseline_evaluation = _compact_evaluation(_evaluate(engine, dataset, _result_roster(baseline, our_seat)))
            baseline_value = _metric(baseline_evaluation)
        baseline_cache[baseline_key] = {
            "result": baseline,
            "evaluation": baseline_evaluation,
            "value": baseline_value,
            "trace_ref": _append_trace(
                trace_path,
                f"pair-baseline:{first_state['pick_number']}:{continuation['continuation_index']}",
                _get(baseline, "trace", ()),
            ),
        }
    baseline_entry = baseline_cache[baseline_key]
    baseline = baseline_entry["result"]
    requested = _call_simulate(
        engine,
        players,
        our_seat=our_seat,
        seed=seed,
        opponent_mode=mode,
        policy="marginal",
        prefix=prefix,
        forced={int(first_state["pick_number"]): first_id, int(second_state["pick_number"]): second_id},
        deadline=deadline,
    )
    requested_status = _result_status(requested)
    fallback = False
    result = requested
    if requested_status == "unavailable":
        # The first pick remains fixed; the marginal policy fills the second
        # turn.  Keep the missed-plan row so availability is part of the
        # unconditional estimate.
        fallback = True
        first_key = (seed, mode, first_id)
        if first_key not in first_cache:
            first_cache[first_key] = _call_simulate(
                engine,
                players,
                our_seat=our_seat,
                seed=seed,
                opponent_mode=mode,
                policy="marginal",
                prefix=prefix,
                forced={int(first_state["pick_number"]): first_id},
                deadline=deadline,
            )
        result = first_cache[first_key]
    status = _result_status(result)
    baseline_status = _result_status(baseline)
    evaluation = _evaluate(engine, dataset, _result_roster(result, our_seat)) if status == "complete" else {}
    baseline_evaluation = baseline_entry["evaluation"] if baseline_status == "complete" else {}
    value = _metric(evaluation) if status == "complete" else None
    baseline_value = _metric(baseline_evaluation) if baseline_status == "complete" else None
    row: dict[str, Any] = {
        "first_id": first_id,
        "second_id": second_id,
        "seed": seed,
        "continuation_index": continuation["continuation_index"],
        "split": continuation["split"],
        "opponent_mode": mode,
        "status": status,
        "baseline_status": baseline_status,
        "requested_status": requested_status,
        "second_available": not fallback,
        "fallback": fallback,
        "value": value,
        "baseline_value": baseline_value,
        "regret": None if value is None or baseline_value is None else value - baseline_value,
        "improvement": None if value is None or baseline_value is None else value - baseline_value,
        "evaluation": _compact_evaluation(evaluation),
        "baseline_evaluation": _compact_evaluation(baseline_evaluation),
        "roster_ids": [_player_id(player) for player in _result_roster(result, our_seat)],
        "history": _jsonable(_get(result, "history", ())),
        "trace_ref": _append_trace(
            trace_path,
            f"pair:{first_id}->{second_id}:{continuation['continuation_index']}",
            _get(result, "trace", ()),
        ),
        "baseline_trace_ref": baseline_entry.get("trace_ref"),
    }
    return row


def _pair_summary(rows: Sequence[Mapping[str, Any]], target_count: int) -> dict[str, Any]:
    summary = _branch_summary(rows, target_count)
    attempts = len(rows)
    successes = sum(bool(row.get("second_available")) for row in rows if row.get("status") in {"complete", "unavailable"})
    summary["availability_rate"] = successes / attempts if attempts else None
    summary["fallback_rows"] = sum(bool(row.get("fallback")) for row in rows)
    complete_values = [
        float(row["value"])
        for row in rows
        if row.get("status") == "complete"
        and row.get("baseline_status") == "complete"
        and row.get("value") is not None
        and row.get("regret") is not None
        and math.isfinite(float(row["value"]))
    ]
    successful_values = [
        float(row["value"])
        for row in rows
        if row.get("status") == "complete"
        and row.get("second_available")
        and row.get("value") is not None
    ]
    # A pair plan's unconditional and conditional values are estimates only
    # when every requested row is complete.  Partial rows remain useful for
    # availability diagnostics but must not become survivorship averages.
    if summary.get("complete_group"):
        summary["unconditional_value"] = sum(complete_values) / len(complete_values) if complete_values else None
        summary["conditional_success_value"] = sum(successful_values) / len(successful_values) if successful_values else None
    else:
        summary["unconditional_value"] = None
        summary["conditional_success_value"] = None
    summary["missed_plans"] = summary["fallback_rows"]
    return summary


def _suppress_partial_estimate(summary: dict[str, Any]) -> None:
    """Clear value estimates after a requested group is known to be partial."""

    summary["complete_group"] = False
    for field in ("mean_regret", "mean_improvement", "regret_ci95", "mean_value", "unconditional_value", "conditional_success_value"):
        if field in summary:
            summary[field] = None
    summary["directional_claim_supported"] = False


def _initial_output(
    dataset: Any,
    *,
    seed: int,
    continuations: int,
    our_seat: int,
    states: Sequence[Mapping[str, Any]],
    identity: Mapping[str, Any],
    schedule: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    name = _get(dataset, "name", "unknown")
    discovery_target = sum(str(row.get("split")) == "discovery" for row in schedule)
    evaluation_target = sum(str(row.get("split")) == "evaluation" for row in schedule)
    return {
        "schema_version": "counterfactual-v1",
        "dataset": str(name),
        "seed": seed,
        "our_seat": our_seat,
        "continuations_requested": continuations,
        "discovery_target": discovery_target,
        "evaluation_target": evaluation_target,
        "opponent_modes": ["nominal", "market", "custom"],
        "continuation_schedule": [_jsonable(row) for row in schedule],
        "outcome_worlds": {
            "mode": "independent_per_continuation",
            "environment": "moderate",
            "world_fixed": False,
            "conditional_if_fixed": False,
            "world_seeds": [int(row["seed"]) for row in schedule],
            "generated_world_seeds": [],
        },
        "trace_store": None,
        "checkpoint_identity": _jsonable(identity),
        "discovery_selection_frozen": True,
        "reconstruction": _reconstruction_metadata(),
        "states": [_jsonable(state) for state in states],
        "player_names": {
            _player_id(player): str(_get(player, "name", _player_id(player)))
            for player in (_get(dataset, "players", ()) or ())
        },
        "own_turns": [],
        "joint13_16": {
            "target_continuations": continuations,
            "budget_cap": min(8, continuations),
            "executed_continuations": 0,
            "plans": [],
        },
        "completion": {"completed_keys": [], "incomplete_groups": [], "groups_total": 0, "groups_complete": 0},
    }


def _load_or_init(
    checkpoint_path: Path | None,
    initial: Mapping[str, Any],
) -> dict[str, Any]:
    if checkpoint_path is None or not checkpoint_path.exists():
        return dict(initial)
    loaded = _read_json(checkpoint_path)
    if loaded.get("schema_version") != initial.get("schema_version"):
        return dict(initial)
    if loaded.get("seed") != initial.get("seed") or loaded.get("dataset") != initial.get("dataset"):
        return dict(initial)
    if loaded.get("checkpoint_identity", {}).get("digest") != initial.get("checkpoint_identity", {}).get("digest"):
        fresh = dict(initial)
        fresh["checkpoint_reset_reason"] = "checkpoint identity changed; stale rows were ignored"
        return fresh
    return loaded


def _turn_entry(output: dict[str, Any], pick_number: int, states: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for entry in output["own_turns"]:
        if entry.get("pick_number") == pick_number:
            return entry
    state = next(state for state in states if int(state["pick_number"]) == pick_number)
    entry = {
        "pick_number": pick_number,
        "candidate_ids": list(state.get("candidate_ids", ())),
        "marginal_choice": state.get("marginal_choice"),
        "branches": {},
        "discovery_selection": None,
        "evaluation_selected_branch": None,
    }
    output["own_turns"].append(entry)
    output["own_turns"].sort(key=lambda value: int(value["pick_number"]))
    return entry


def _group_key(kind: str, pick: int, candidate: str, split: str, continuation_index: int) -> str:
    return f"{kind}:{pick}:{candidate}:{split}:{continuation_index}"


def _recompute_completion(output: dict[str, Any]) -> None:
    completed: list[str] = []
    incomplete: list[dict[str, Any]] = []
    total = 0
    complete_groups = 0
    for turn in output.get("own_turns", ()):
        for candidate_id, branch in (turn.get("branches", {}) or {}).items():
            for split in ("discovery", "evaluation"):
                rows = branch.get(f"{split}_rows", [])
                summary = branch.get(f"{split}_summary", {}) or {}
                requested_target = branch.get(f"{split}_target", 0)
                actual_target = int(summary.get("actual_target_used", 0) or 0)
                total += 1
                statuses = [str(row.get("status", "incomplete")) for row in rows]
                if actual_target > 0 and summary.get("complete_group"):
                    completed.append(f"single:{turn['pick_number']}:{candidate_id}:{split}")
                    complete_groups += 1
                else:
                    incomplete.append(
                        {
                            "kind": "single",
                            "pick_number": turn["pick_number"],
                            "candidate_id": candidate_id,
                            "split": split,
                            "rows": len(rows),
                            "target_rows": requested_target,
                            "actual_target_used": actual_target,
                            "incomplete_rows": statuses.count("incomplete"),
                            "unavailable_rows": statuses.count("unavailable"),
                            "excluded_rows": int(summary.get("excluded_rows", 0) or 0),
                        }
                    )
    output["completion"] = {
        "completed_keys": sorted(set(completed)),
        "incomplete_groups": incomplete,
        "groups_total": total,
        "groups_complete": complete_groups,
    }


def _run_joint(
    output: dict[str, Any],
    engine: Any,
    dataset: Any,
    players: Sequence[Any],
    states: Sequence[Mapping[str, Any]],
    schedule: Sequence[Mapping[str, Any]],
    *,
    our_seat: int,
    deadline: float | None,
    pair_target: int,
    pair_requested_target: int,
    world_for: Any,
    trace_path: Path | None = None,
) -> None:
    first = next((state for state in states if int(state["pick_number"]) == 13), None)
    second = next((state for state in states if int(state["pick_number"]) == 16), None)
    if first is None or second is None:
        output["joint13_16"]["status"] = "unavailable"
        return
    first_ids = list(first.get("candidate_ids", ()))[:10]
    second_pool = list(second.get("candidate_ids", ()))
    frozen = second.get("actual_pick_id") or second.get("marginal_choice")
    if frozen:
        second_pool.append(str(frozen))
    # A first-pick alternative can leave the original pick-13 choice alive at
    # pick 16.  Include the first-turn union in the second-turn pool, with an
    # explicit ten-slot cap and coverage metadata rather than silently
    # pretending the full Cartesian union was tested.
    second_union = _dedupe_ids(second_pool + first_ids)
    first_recommended = str(first.get("marginal_choice")) if first.get("marginal_choice") else None
    second_ids = second_union[:10]
    if first_recommended and first_recommended not in second_ids and first_recommended in second_union:
        second_ids[-1:] = [first_recommended]
        second_ids = _dedupe_ids(second_ids)
    output["joint13_16"]["second_candidate_union"] = second_union
    output["joint13_16"]["second_candidate_slot_limit"] = 10
    output["joint13_16"]["second_candidate_omitted"] = [pid for pid in second_union if pid not in second_ids]
    pair_target = min(pair_target, len(schedule))
    existing_plans = {
        f"{plan.get('first_id')}->{plan.get('second_id')}": plan
        for plan in (output.get("joint13_16", {}).get("plans", []) or [])
        if plan.get("first_id") is not None and plan.get("second_id") is not None
    }
    plans: dict[str, dict[str, Any]] = {}
    baseline_cache: dict[tuple[int, str], Any] = {}
    first_cache: dict[tuple[int, str, str], Any] = {}
    for first_id in first_ids:
        for second_id in second_ids:
            if first_id == second_id:
                # Ordered plans must name two distinct players.  The
                # marginal fallback is represented by the explicit fallback
                # row for a valid pair, never by a duplicate pick.
                continue
            plan_key = f"{first_id}->{second_id}"
            previous = existing_plans.get(plan_key, {})
            plan = plans.setdefault(
                plan_key,
                {
                    "first_id": first_id,
                    "second_id": second_id,
                    "target_continuations": pair_target,
                    "requested_target_continuations": pair_requested_target,
                    "rows": list(previous.get("rows", []) or []),
                    "retry_attempts": list(previous.get("retry_attempts", []) or []),
                },
            )
            for continuation in schedule[:pair_target]:
                if deadline is not None and time.monotonic() >= deadline:
                    plan.setdefault("deadline_reached", True)
                    break
                continuation_index = int(continuation["continuation_index"])
                existing_rows = [
                    row for row in plan["rows"]
                    if int(row.get("continuation_index", -1)) == continuation_index
                ]
                if existing_rows:
                    valid_existing = [row for row in existing_rows if _pair_row_valid(row)]
                    terminal_unavailable = all(row.get("status") == "unavailable" for row in existing_rows)
                    if valid_existing or terminal_unavailable:
                        continue
                    plan["retry_attempts"].extend(
                        {
                            "continuation_index": continuation_index,
                            "reason": "retrying incomplete or metric-invalid pair row",
                            "row": _jsonable(row),
                        }
                        for row in existing_rows
                    )
                    plan["rows"] = [
                        row for row in plan["rows"]
                        if int(row.get("continuation_index", -1)) != continuation_index
                    ]
                world = world_for(continuation)
                world_players = list(_get(world, "players", players) or players)
                plan["rows"].append(
                    _pair_result(
                        engine,
                        world,
                        world_players,
                        first,
                        second,
                        first_id,
                        second_id,
                        continuation,
                        our_seat=our_seat,
                        deadline=deadline,
                        baseline_cache=baseline_cache,
                        first_cache=first_cache,
                        trace_path=trace_path,
                    )
                )
            stage_indices = _balanced_pair_stage_indices(schedule, plan["rows"], pair_target)
            allowed_indices = set(stage_indices["discovery"]) | set(stage_indices["evaluation"])
            stage_rows = [
                row for row in plan["rows"]
                if int(row.get("continuation_index", -1)) in allowed_indices
            ]
            plan["summary"] = _pair_summary(stage_rows, len(allowed_indices))
            plan["summary"]["requested_target_rows"] = pair_requested_target
            plan["summary"]["actual_target_used"] = len(allowed_indices)
            plan["summary"]["actual_discovery_target_used"] = len(stage_indices["discovery"])
            plan["summary"]["actual_evaluation_target_used"] = len(stage_indices["evaluation"])
            plan["summary"]["sample_count"] = len(stage_rows)
            plan["summary"]["excluded_rows"] = len(plan["rows"]) - len(stage_rows)
            for split in ("discovery", "evaluation"):
                requested_split_target = sum(row.get("split") == split for row in schedule)
                allowed_split = set(stage_indices[split])
                split_rows = [
                    row for row in plan["rows"]
                    if row.get("split") == split
                    and int(row.get("continuation_index", -1)) in allowed_split
                ]
                plan[f"{split}_summary"] = _pair_summary(split_rows, len(allowed_split))
                plan[f"{split}_summary"]["requested_target_rows"] = requested_split_target
                plan[f"{split}_summary"]["actual_target_used"] = len(allowed_split)
                plan[f"{split}_summary"]["sample_count"] = len(split_rows)
                plan[f"{split}_summary"]["excluded_rows"] = sum(
                    row.get("split") == split for row in plan["rows"]
                ) - len(split_rows)
    output["joint13_16"]["plans"] = list(plans.values())
    output["joint13_16"]["executed_continuations"] = max((len(plan["rows"]) for plan in plans.values()), default=0)
    output["joint13_16"]["status"] = (
        "complete"
        if plans
        and all(
            plan["summary"].get("complete_group")
            and plan["summary"].get("actual_target_used") == pair_requested_target
            for plan in plans.values()
        )
        else "incomplete"
    )
    output["joint13_16"]["incomplete_groups"] = [
        {
            "first_id": plan["first_id"],
            "second_id": plan["second_id"],
            "rows": len(plan["rows"]),
            "target_rows": plan["requested_target_continuations"],
            "actual_target_used": plan["summary"].get("actual_target_used", 0),
            "incomplete_rows": sum(row.get("status") == "incomplete" for row in plan["rows"]),
        }
        for plan in plans.values()
        if (
            not plan["summary"].get("complete_group")
            or plan["summary"].get("actual_target_used", 0) < plan["requested_target_continuations"]
        )
    ]
    # Pair plans are a descriptive table.  The single-turn discovery rule
    # does not promote a pair using the same data, and no pair is selected
    # from held-out evaluation rows.
    descriptive = sorted(
        (
            {
                "first_id": plan["first_id"],
                "second_id": plan["second_id"],
                "discovery_mean_regret": plan.get("discovery_summary", {}).get("mean_regret"),
                "discovery_complete_group": bool(plan.get("discovery_summary", {}).get("complete_group")),
            }
            for plan in plans.values()
        ),
        key=lambda item: (
            item["discovery_mean_regret"] is None,
            -(float(item["discovery_mean_regret"]) if item["discovery_mean_regret"] is not None else 0.0),
            str(item["first_id"]),
            str(item["second_id"]),
        ),
    )
    output["joint13_16"]["discovery_selection"] = None
    output["joint13_16"]["discovery_descriptive_ranking"] = descriptive
    output["joint13_16"]["evaluation_selected_plan"] = {
        "status": "descriptive_only",
        "reason": "ordered pair plans are reported without selecting a plan on discovery or evaluation rows",
    }


def run_counterfactuals(
    dataset: Any,
    base_draft: Any = None,
    seed: int = DEFAULT_SEED,
    continuations: int = 32,
    deadline: float | None = None,
    checkpoint_path: str | Path | None = None,
    *,
    our_seat: int = DEFAULT_OUR_SEAT,
) -> dict[str, Any]:
    """Run matched discovery/evaluation counterfactuals.

    ``deadline`` is an absolute ``time.monotonic()`` deadline.  Work is
    interleaved by continuation index and then own turn so a short run gives
    every decision state balanced evidence before expanding to later seeds.
    """

    if continuations < 1:
        raise CounterfactualError("continuations must be positive")
    players = list(_get(dataset, "players", ()) or ())
    if not players:
        raise CounterfactualError("dataset.players is empty")
    engine = _engine()
    if base_draft is None:
        base_draft = _call_simulate(
            engine,
            players,
            our_seat=our_seat,
            seed=seed,
            opponent_mode="market",
            policy="marginal",
            deadline=deadline,
        )
    states = build_decision_states(base_draft, players, our_seat=our_seat)
    schedule = _continuation_schedule(seed, continuations)
    identity = _checkpoint_identity(
        dataset,
        base_draft,
        engine,
        seed=seed,
        continuations=continuations,
        our_seat=our_seat,
        schedule=schedule,
    )
    output = _initial_output(
        dataset,
        seed=seed,
        continuations=continuations,
        our_seat=our_seat,
        states=states,
        identity=identity,
        schedule=schedule,
    )
    path = Path(checkpoint_path) if checkpoint_path is not None else None
    trace_path = None
    if path is not None:
        trace_path = path.with_name(path.name + ".traces.jsonl.gz")
        output["trace_store"] = str(trace_path)
    output = _load_or_init(path, output)
    if trace_path is not None:
        output["trace_store"] = str(trace_path)
    baseline_cache_by_pick: dict[int, dict[tuple[int, str], Any]] = defaultdict(dict)
    world_cache: dict[int, Any] = {}

    def world_for(continuation: Mapping[str, Any]) -> Any:
        world_seed = int(continuation["seed"])
        if world_seed not in world_cache:
            world = _generate_continuation_world(engine, dataset, world_seed)
            world_cache[world_seed] = world
            generated = output["outcome_worlds"].setdefault("generated_world_seeds", [])
            if world_seed not in generated:
                generated.append(world_seed)
            if world is dataset or _get(world, "outcomes", None) is _get(dataset, "outcomes", None):
                output["outcome_worlds"]["world_fixed"] = True
                output["outcome_worlds"]["conditional_if_fixed"] = True
        return world_cache[world_seed]

    # Reserve a fifth of the remaining budget for the ordered-pair analysis so
    # it cannot be starved by the ten single-turn tables.
    single_deadline = deadline
    joint_deadline = deadline
    if deadline is not None:
        remaining = max(0.0, float(deadline) - time.monotonic())
        single_deadline = float(deadline) - remaining * 0.20
    # Materialize every expected group before doing work.  If a deadline lands
    # before a later own turn, the checkpoint still explains which groups were
    # never started and no silent subset is reported as an estimate.
    for state in states:
        turn = _turn_entry(output, int(state["pick_number"]), states)
        for candidate_id in turn["candidate_ids"]:
            turn["branches"].setdefault(
                candidate_id,
                {
                    "candidate_id": candidate_id,
                    "discovery_target": output["discovery_target"],
                    "evaluation_target": output["evaluation_target"],
                    "discovery_rows": [],
                    "evaluation_rows": [],
                    "discovery_summary": {},
                    "evaluation_summary": {},
                },
            )
    for continuation in schedule:
        for state in states:
            if single_deadline is not None and time.monotonic() >= single_deadline:
                output.setdefault("completion", {}).setdefault("deadline_reached", True)
                break
            turn = _turn_entry(output, int(state["pick_number"]), states)
            target = int(output["discovery_target"] if continuation["split"] == "discovery" else output["evaluation_target"])
            for candidate_id in turn["candidate_ids"]:
                branch = turn["branches"].setdefault(
                    candidate_id,
                    {
                        "candidate_id": candidate_id,
                        "discovery_target": output["discovery_target"],
                        "evaluation_target": output["evaluation_target"],
                        "discovery_rows": [],
                        "evaluation_rows": [],
                        "discovery_summary": {},
                        "evaluation_summary": {},
                    },
                )
                rows_key = f"{continuation['split']}_rows"
                continuation_index = continuation["continuation_index"]
                existing_rows = [
                    row for row in branch[rows_key]
                    if row.get("continuation_index") == continuation_index
                ]
                if existing_rows:
                    # Valid completed rows are immutable.  An incomplete or
                    # metric-invalid attempt must be retried on resume; keep
                    # the old row in a compact diagnostic archive first.
                    valid_existing = [row for row in existing_rows if _single_row_valid(row)]
                    terminal_unavailable = all(row.get("status") == "unavailable" for row in existing_rows)
                    if valid_existing or terminal_unavailable:
                        continue
                    branch.setdefault("retry_attempts", []).extend(
                        {
                            "split": continuation["split"],
                            "continuation_index": continuation_index,
                            "reason": "retrying incomplete or metric-invalid row",
                            "row": _jsonable(row),
                        }
                        for row in existing_rows
                    )
                    branch[rows_key] = [
                        row for row in branch[rows_key]
                        if row.get("continuation_index") != continuation_index
                    ]
                world = world_for(continuation)
                world_players = list(_get(world, "players", players) or players)
                row = _single_branch_row(
                    engine,
                    world,
                    world_players,
                    state,
                    continuation,
                    candidate_id,
                    our_seat=our_seat,
                    baseline_cache=baseline_cache_by_pick[int(state["pick_number"])],
                    deadline=single_deadline,
                    trace_path=trace_path,
                )
                branch[rows_key].append(row)
                branch[f"{continuation['split']}_summary"] = _branch_summary(branch[rows_key], target)
            if single_deadline is not None and time.monotonic() >= single_deadline:
                output.setdefault("completion", {})["deadline_reached"] = True
                break
        if single_deadline is not None and time.monotonic() >= single_deadline:
            break
        _finalize_single_stages(output, schedule, freeze_selection=False)
        _recompute_completion(output)
        _checkpoint(path, output)
    _finalize_single_stages(output, schedule)
    _recompute_completion(output)
    pair_target = min(8, continuations)
    _run_joint(
        output,
        engine,
        dataset,
        players,
        states,
        schedule,
        our_seat=our_seat,
        deadline=joint_deadline,
        pair_target=pair_target,
        pair_requested_target=len(schedule),
        world_for=world_for,
        trace_path=trace_path,
    )
    output["completion"]["generated_at_monotonic"] = time.monotonic()
    _checkpoint(path, output)
    return _jsonable(output)


__all__ = [
    "BASELINE_POLICIES",
    "CounterfactualError",
    "POLICIES",
    "build_decision_states",
    "run_counterfactuals",
    "select_strongest_alternative",
]
