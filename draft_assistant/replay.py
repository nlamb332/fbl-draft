"""Offline draft replay and fault injection helpers.

Replay consumes serialized observations or simple event dictionaries and calls
the same deterministic policy as the coordinator.  It is intentionally local:
no browser, network, credentials, or external state are used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .contracts import DraftObservation, DraftRecommendation, PickEvent, RoomIdentity
from .valuation import recommend


@dataclass(frozen=True)
class ReplayFault:
    index: int
    kind: str
    detail: str


@dataclass
class ReplayResult:
    recommendations: list[DraftRecommendation] = field(default_factory=list)
    faults: list[ReplayFault] = field(default_factory=list)
    processed: int = 0

    @property
    def ok(self) -> bool:
        return not self.faults

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "faults": [fault.__dict__ for fault in self.faults],
            "recommendations": [item.to_dict() for item in self.recommendations],
        }


def load_replay(source: str | Path | Mapping[str, Any] | Sequence[Any]) -> list[Any]:
    """Load JSON replay records or return an already materialized sequence."""

    if isinstance(source, Mapping):
        return list(source.get("observations", source.get("events", ())))
    if isinstance(source, (str, Path)):
        path = Path(source)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            payload = payload.get("observations", payload.get("events", ()))
        if not isinstance(payload, list):
            raise ValueError("replay JSON must be a list or observations/events object")
        return payload
    return list(source)


def _observation(record: Any, previous: DraftObservation | None = None) -> DraftObservation:
    if isinstance(record, DraftObservation):
        return record
    if isinstance(record, Mapping) and isinstance(record.get("observation"), Mapping):
        record = record["observation"]
    if isinstance(record, Mapping):
        # Full serialized observations are the preferred replay format.
        required = {"session_id", "room", "observed_at"}
        if required.issubset(record):
            return DraftObservation.from_dict(record)
        if previous is not None:
            # Fault-replay fixtures often contain sparse patches.  Apply a
            # patch against the prior typed observation without guessing values.
            payload = previous.to_dict()
            payload.update(record)
            payload["room"] = record.get("room", previous.to_dict()["room"])
            return DraftObservation.from_dict(payload)
    raise ValueError("replay record is not a DraftObservation or sparse observation patch")


def replay(
    records: Iterable[Any],
    config,
    *,
    lookahead_enabled: bool = False,
    promotion_path: Any = None,
    recommendation_fn: Callable[..., DraftRecommendation] = recommend,
) -> ReplayResult:
    """Replay observations, retaining malformed frames as explicit faults."""

    result = ReplayResult()
    previous: DraftObservation | None = None
    for index, record in enumerate(records):
        try:
            observation = _observation(record, previous)
            recommendation = recommendation_fn(
                observation,
                config,
                lookahead_enabled=lookahead_enabled,
                promotion_path=promotion_path,
            )
            result.recommendations.append(recommendation)
            previous = observation
            result.processed += 1
        except Exception as exc:
            result.faults.append(ReplayFault(index, type(exc).__name__, str(exc)))
    return result


def replay_file(
    source: str | Path,
    config,
    **kwargs: Any,
) -> ReplayResult:
    return replay(load_replay(source), config, **kwargs)


def make_observation_stream(
    *,
    session_id: str,
    room: RoomIdentity,
    observed_at: str,
    events: Sequence[PickEvent] = (),
    **kwargs: Any,
) -> list[DraftObservation]:
    """Build a one-frame stream useful for deterministic unit tests."""

    return [
        DraftObservation(
            session_id=session_id,
            room=room,
            observed_at=observed_at,
            history=tuple(events),
            **kwargs,
        )
    ]


# Names used by older replay callers.
run_replay = replay
load_events = load_replay

__all__ = [
    "ReplayFault",
    "ReplayResult",
    "load_events",
    "load_replay",
    "make_observation_stream",
    "replay",
    "replay_file",
    "run_replay",
]
