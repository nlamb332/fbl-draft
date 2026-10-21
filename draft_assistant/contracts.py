"""Shared version-one contracts. Unknown observations are None, not guessed."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from mock_draft.fixture import SCORING_WEIGHTS

SCHEMA_VERSION = 1
SESSION_STATES = {"disconnected", "observing", "ready", "running", "degraded", "paused", "completed"}
ACTION_STATUSES = {"pending", "submitted", "uncertain", "confirmed", "cancelled", "failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_age(value: str, now: datetime | None = None) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return ((now or datetime.now(timezone.utc)) - parsed).total_seconds()


@dataclass(frozen=True)
class RoomIdentity:
    league_id: str
    season_id: str
    team_id: str
    practice: bool = False

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.isdigit() for value in (self.league_id, self.season_id, self.team_id)):
            raise ValueError("room IDs must be numeric strings")
        if not isinstance(self.practice, bool):
            raise ValueError("practice must be an explicit boolean")

    @classmethod
    def from_url(cls, url: str, *, practice: bool = False) -> RoomIdentity:
        """Extract the room identity from an ESPN team or draft URL.

        The team URL is the convenient setup input; ``safe_url`` emits the
        canonical draft-room URL used by the observer and coordinator.
        """
        parsed = urlparse(url)
        accepted_paths = {"/basketball/draft", "/basketball/team"}
        if parsed.scheme != "https" or parsed.hostname != "fantasy.espn.com" or parsed.path.rstrip("/") not in accepted_paths:
            raise ValueError("expected an HTTPS ESPN basketball team or draft-room URL")
        query = parse_qs(parsed.query)
        values = []
        for key in ("leagueId", "seasonId", "teamId"):
            if len(query.get(key, [])) != 1 or not query[key][0].isdigit():
                raise ValueError(f"missing or ambiguous {key}")
            values.append(query[key][0])
        return cls(*values, practice=practice)

    def safe_url(self) -> str:
        return f"https://fantasy.espn.com/basketball/draft?leagueId={self.league_id}&seasonId={self.season_id}&teamId={self.team_id}"


@dataclass(frozen=True)
class InjuryPolicy:
    """Opt-in availability haircut keyed on ``LivePlayer.injury_status``.

    Off by default: the haircut has not been re-validated against held-out
    drafts, so valuation ignores injury status unless ``enabled`` is set.
    See :mod:`draft_assistant.injury` for the status mapping.
    """

    enabled: bool = False
    out_factor: float = 0.5
    dtd_factor: float = 0.95

    def __post_init__(self) -> None:
        for name in ("out_factor", "dtd_factor"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"invalid {name}: must be within 0..1")

    def as_dict(self) -> dict[str, object]:
        return {"enabled": self.enabled, "out_factor": self.out_factor, "dtd_factor": self.dtd_factor}


@dataclass(frozen=True)
class LeagueConfig:
    league_id: str = "1000000000"
    season_id: str = "2027"
    team_id: str = "15"
    our_team: str = "Example Team"
    teams: int = 14
    rounds: int = 10
    starting_slots: tuple[str, ...] = ("C", "G", "G", "F", "F", "UTIL")
    bench_slots: int = 4
    ir_slots: int = 3
    scoring: dict[str, float] = field(default_factory=lambda: dict(SCORING_WEIGHTS))
    double_double_includes_triple_double: bool = True
    max_observation_age_seconds: float = 10.0
    recommendation_budget_seconds: float = 2.0
    injury_policy: InjuryPolicy = field(default_factory=InjuryPolicy)

    def __post_init__(self) -> None:
        if self.teams != 14 or self.rounds != 10 or tuple(self.starting_slots) != ("C", "G", "G", "F", "F", "UTIL") or self.bench_slots != 4:
            raise ValueError("this release requires the verified 14-team, ten-round league shape")


@dataclass(frozen=True)
class LivePlayer:
    player_id: str
    name: str
    eligibility: tuple[str, ...]
    stats: dict[str, float] = field(default_factory=dict)
    projected_gp: float | None = None
    draft_rank: float | None = None
    adp: float | None = None
    availability: float | None = None
    nba_team: str | None = None
    injury_status: str | None = None
    source: str = "unknown"
    missing_stats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.player_id or not self.name or not self.eligibility or not set(self.eligibility) <= {"C", "G", "F"}:
            raise ValueError("player identity and normalized C/G/F eligibility required")
        for key, value in self.stats.items():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid forecast statistic {key}")
        for key, value, lower, upper in (
            ("projected_gp", self.projected_gp, 0, 82),
            ("availability", self.availability, 0, 1),
            ("draft_rank", self.draft_rank, 1, math.inf),
            ("adp", self.adp, 1, math.inf),
        ):
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper):
                raise ValueError(f"invalid {key}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LivePlayer:
        record = dict(data)
        record["eligibility"] = tuple(record.get("eligibility", ()))
        record["missing_stats"] = tuple(record.get("missing_stats", ()))
        record["stats"] = dict(record.get("stats", {}))
        return cls(**record)


@dataclass(frozen=True)
class PickEvent:
    pick_number: int
    team: str
    player_id: str
    player_name: str = ""


@dataclass(frozen=True)
class DraftObservation:
    session_id: str
    room: RoomIdentity
    observed_at: str
    revision: int = 0
    phase: str = "unknown"  # pre_draft, draft, complete; never a control state
    current_pick: int | None = None
    on_clock_team: str | None = None
    seconds_remaining: float | None = None
    pick_order: tuple[str, ...] = ()
    history: tuple[PickEvent, ...] = ()
    roster: tuple[LivePlayer, ...] = ()
    available_players: tuple[LivePlayer, ...] = ()
    known_players: tuple[LivePlayer, ...] = ()
    autopick: bool | None = None
    rules: dict[str, Any] = field(default_factory=dict)
    complete: bool = False  # frame hydrated/consistent; phase marks draft completion
    history_complete: bool = False
    available_pool_complete: bool = False
    warnings: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DraftObservation:
        record = dict(data)
        record["room"] = RoomIdentity(**record["room"])
        record["history"] = tuple(PickEvent(**p) for p in record.get("history", ()))
        for key in ("roster", "available_players", "known_players"):
            record[key] = tuple(LivePlayer.from_dict(p) for p in record.get(key, ()))
        for key in ("pick_order", "warnings"):
            record[key] = tuple(record.get(key, ()))
        if record.get("schema_version", 1) != SCHEMA_VERSION:
            raise ValueError("unsupported observation schema version")
        timestamp_age(record["observed_at"])
        return cls(**record)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateScore:
    player_id: str
    name: str
    score: float
    components: dict[str, float] = field(default_factory=dict)
    explanation: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftRecommendation:
    session_id: str
    revision: int
    generated_at: str
    candidates: tuple[CandidateScore, ...] = ()
    lookahead_status: str = "advisory"
    explanation: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DraftRecommendation:
        record = dict(data)
        record["candidates"] = tuple(CandidateScore(**p) for p in record.get("candidates", ()))
        return cls(**record)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DraftAction:
    action_id: str
    session_id: str
    revision: int
    expected_pick: int
    expected_team: str
    player_id: str
    player_name: str
    action_type: str = "draft"
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in ACTION_STATUSES or self.action_type not in {"draft", "queue"}:
            raise ValueError("invalid draft action")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ControlIntent:
    kind: str
    session_id: str
    player_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.kind not in {"start", "pause", "resume", "manual_pick"}:
            raise ValueError("unknown control intent")


def snake_pick_numbers(seat: int, teams: int = 14, rounds: int = 10) -> tuple[int, ...]:
    if not 1 <= seat <= teams:
        raise ValueError("invalid draft seat")
    return tuple((r * teams) + (seat if r % 2 == 0 else teams + 1 - seat) for r in range(rounds))
