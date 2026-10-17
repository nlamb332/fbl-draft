"""Guarded, transport-neutral procedures for a live draft-room controller.

The operator does not know how to find a browser or submit a click.  A control
agent may pass a callable for the final visible UI action, but only after an
explicit confirmation and a fresh observation have passed every guard here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from uuid import uuid4

from .browser_state import ObservationConflict
from .contracts import DraftAction, DraftObservation, LeagueConfig, LivePlayer, RoomIdentity
from .lifecycle import DraftSession


class BrowserOperatorError(RuntimeError):
    """Base class for guarded operator failures."""


class ActionBlocked(BrowserOperatorError):
    """An action was not safe to prepare or submit."""


class StaleExpectedTurn(ActionBlocked):
    """The observed turn no longer matches a prepared action."""


class SessionBlocked(ActionBlocked):
    """The lifecycle or room/session identity blocks mutation."""


class UnknownActionOutcome(BrowserOperatorError):
    """A submit may have reached ESPN; inspect before retrying."""


@dataclass(frozen=True)
class QueueFallbackPlan:
    """A reviewable queue fallback; creating it does not mutate the UI."""

    session_id: str
    revision: int
    expected_pick: int
    expected_team: str
    candidates: tuple[DraftAction, ...]
    reason: str
    requires_explicit_submit: bool = True


@dataclass(frozen=True)
class ActionConfirmation:
    action: DraftAction
    status: str
    detail: str
    inspected_revision: int


def _same_player(left: str, right: str) -> bool:
    return left == right or left.lower() == right.lower()


def _event_matches(action: DraftAction, event: object) -> bool:
    return (
        getattr(event, "pick_number", None) == action.expected_pick
        and _same_player(str(getattr(event, "player_id", "")), action.player_id)
        and _same_player(str(getattr(event, "player_name", "")), action.player_name)
        and _same_player(str(getattr(event, "team", "")), action.expected_team)
    )


class BrowserOperator:
    """Stateful guards shared by a browser control agent and the coordinator."""

    def __init__(
        self,
        *,
        session: DraftSession,
        room: RoomIdentity,
        config: LeagueConfig,
        observation: DraftObservation | None = None,
        require_explicit_player_id: bool = True,
        coordinator_guard: Callable[[DraftAction, DraftObservation], object] | None = None,
    ) -> None:
        if not session.session_id:
            raise SessionBlocked("session_id is required")
        if not isinstance(room, RoomIdentity):
            raise SessionBlocked("an explicit RoomIdentity is required")
        if not isinstance(config, LeagueConfig):
            raise SessionBlocked("an explicit LeagueConfig is required")
        self.session = session
        self.room = room
        self.config = config
        self.observation: DraftObservation | None = None
        self.require_explicit_player_id = require_explicit_player_id
        # The durable coordinator owns final_guard.  This pure operator may
        # prepare actions without it, but cannot mutate a room until the
        # coordinator has reread its intent and latest observation.
        self.coordinator_guard = coordinator_guard
        self._pending: dict[str, DraftAction] = {}
        self._inspected_unknown: set[str] = set()
        if observation is not None:
            self.attach_observation(observation)

    @property
    def paused(self) -> bool:
        return self.session.state == "paused"

    def attach_observation(self, observation: DraftObservation) -> DraftObservation:
        """Attach a fresh read-only observation after identity validation."""
        if observation.session_id != self.session.session_id:
            raise SessionBlocked("observation session_id does not match operator")
        if observation.room != self.room:
            raise SessionBlocked("observation room identity does not match operator")
        if self.observation is not None and observation.revision < self.observation.revision:
            raise StaleExpectedTurn("observation revision regressed")
        self.observation = observation
        return observation

    def begin_observing(self) -> None:
        if self.session.state == "disconnected":
            self.session.transition("observing")

    def mark_ready(self, *, verified: bool = True) -> None:
        if self.session.state == "disconnected":
            self.begin_observing()
        self.session.transition("ready", ready=verified, reason="explicit browser and room checks passed")

    def start(self, *, verified: bool = True) -> None:
        """Enable guarded mutation only after the caller's readiness checks."""
        if self.session.state == "disconnected":
            self.begin_observing()
        if self.session.state == "observing":
            self.mark_ready(verified=verified)
        self.session.transition("running", ready=verified, reason="explicit operator start")

    def pause(self, reason: str = "manual pause") -> None:
        if self.session.state in {"observing", "ready", "running"}:
            self.session.transition("paused", reason=reason)

    def resume(self, observation: DraftObservation | None = None, *, verified: bool = True) -> None:
        if observation is not None:
            self.attach_observation(observation)
        if self.observation is None:
            raise SessionBlocked("fresh observation required before resume")
        if self.session.state != "paused":
            raise SessionBlocked(f"cannot resume from {self.session.state}")
        self.session.transition("running", ready=verified, reason="fresh observation after pause")

    def _require_observation(self, observation: DraftObservation | None = None) -> DraftObservation:
        current = observation or self.observation
        if current is None:
            raise SessionBlocked("fresh observation required")
        if current.session_id != self.session.session_id or current.room != self.room:
            raise SessionBlocked("observation identity mismatch")
        return current

    def _guard_turn(self, observation: DraftObservation, *, expected_pick: int, expected_team: str, player: LivePlayer | None = None) -> None:
        if self.session.state in {"paused", "disconnected", "completed"}:
            raise SessionBlocked(f"session state {self.session.state} blocks action")
        if observation.phase.lower() == "complete":
            raise SessionBlocked("draft is complete")
        if observation.current_pick != expected_pick or observation.on_clock_team is None or not _same_player(observation.on_clock_team, expected_team):
            raise StaleExpectedTurn(
                f"expected pick/team {expected_pick}/{expected_team}; observed {observation.current_pick}/{observation.on_clock_team}"
            )
        if _same_player(expected_team, self.config.our_team) and observation.autopick is True:
            raise SessionBlocked("our team is in Autopick; inspect and take over manually")
        if observation.seconds_remaining is None:
            raise SessionBlocked("visible clock is required before an action")
        if observation.seconds_remaining <= 0:
            raise StaleExpectedTurn("visible clock expired")
        if player is not None and self.require_explicit_player_id and player.player_id.startswith("visible:"):
            raise ActionBlocked("player row has no explicit DOM/player id; direct submit is blocked")

    def _find_player(self, observation: DraftObservation, player_id: str | None = None, player_name: str | None = None) -> LivePlayer:
        for player in observation.available_players:
            if player_id and _same_player(player.player_id, player_id):
                return player
            if player_name and _same_player(player.name, player_name):
                return player
        label = player_id or player_name or "<empty>"
        raise ActionBlocked(f"player {label!r} is not visible in the current Draft/QUEUE player table")

    def plan_pick(self, player_id: str, *, player_name: str | None = None, observation: DraftObservation | None = None) -> DraftAction:
        """Prepare a direct draft action; no browser mutation occurs."""
        current = self._require_observation(observation)
        if current.on_clock_team is None:
            raise StaleExpectedTurn("on-clock team is unknown")
        player = self._find_player(current, player_id=player_id, player_name=player_name)
        if current.current_pick is None:
            raise StaleExpectedTurn("current pick is unknown")
        self._guard_turn(current, expected_pick=current.current_pick, expected_team=self.config.our_team, player=player)
        action = DraftAction(
            action_id=uuid4().hex,
            session_id=self.session.session_id,
            revision=current.revision,
            expected_pick=current.current_pick,
            expected_team=self.config.our_team,
            player_id=player.player_id,
            player_name=player.name,
            action_type="draft",
            detail="prepared from explicit visible Draft table row; requires direct confirmation",
        )
        self._pending[action.action_id] = action
        return action

    def plan_queue(self, player_ids: Sequence[str], *, observation: DraftObservation | None = None, reason: str = "direct pick unavailable") -> QueueFallbackPlan:
        """Build a queue fallback plan without submitting or clicking anything."""
        current = self._require_observation(observation)
        if current.current_pick is None:
            raise StaleExpectedTurn("current pick is unknown")
        self._guard_turn(current, expected_pick=current.current_pick, expected_team=self.config.our_team)
        candidates: list[DraftAction] = []
        for player_id in player_ids:
            player = self._find_player(current, player_id=player_id)
            action = DraftAction(
                action_id=uuid4().hex,
                session_id=self.session.session_id,
                revision=current.revision,
                expected_pick=current.current_pick,
                expected_team=self.config.our_team,
                player_id=player.player_id,
                player_name=player.name,
                action_type="queue",
                detail="queue fallback plan; caller must review and submit directly",
            )
            self._pending[action.action_id] = action
            candidates.append(action)
        if not candidates:
            raise ActionBlocked("queue plan requires at least one visible candidate")
        return QueueFallbackPlan(self.session.session_id, current.revision, current.current_pick, self.config.our_team, tuple(candidates), reason)

    def guard_action(self, action: DraftAction, observation: DraftObservation | None = None) -> DraftObservation:
        current = self._require_observation(observation)
        if action.session_id != self.session.session_id:
            raise SessionBlocked("action session_id does not match operator")
        if action.action_id not in self._pending and action.status not in {"pending", "submitted"}:
            raise ActionBlocked("action is no longer pending")
        if current.revision != action.revision:
            raise StaleExpectedTurn(f"action revision {action.revision} is stale; observed {current.revision}")
        player = self._find_player(current, player_id=action.player_id, player_name=action.player_name)
        self._guard_turn(current, expected_pick=action.expected_pick, expected_team=action.expected_team, player=player)
        return current

    def submit(
        self,
        action: DraftAction,
        *,
        observation: DraftObservation | None = None,
        confirm: bool = False,
        submitter: Callable[[DraftAction], object] | None = None,
    ) -> DraftAction:
        """Submit through a caller-owned visible UI callback after confirmation.

        ``submitter`` is intentionally injected.  It is never created here and
        must not be a network/API client.  If the callback raises, outcome is
        ``uncertain`` because the click may have reached the draft room.
        """
        if not confirm:
            raise ActionBlocked("direct submit requires explicit confirmation")
        if submitter is None:
            raise ActionBlocked("a visible-UI submitter callback is required")
        self.guard_action(action, observation)
        if self.paused:
            raise SessionBlocked("paused session blocks mutation")
        current = self._require_observation(observation)
        if self.coordinator_guard is None:
            raise SessionBlocked("coordinator final guard is required before browser mutation")
        try:
            authority_result = self.coordinator_guard(action, current)
        except Exception as exc:
            raise SessionBlocked(f"coordinator final guard rejected action: {exc}") from exc
        if authority_result is False:
            raise SessionBlocked("coordinator final guard rejected action")
        action.status = "pending"
        try:
            submitter(action)
        except Exception as exc:
            action.status = "uncertain"
            action.detail = f"submitter raised after possible UI mutation: {exc}"
            raise UnknownActionOutcome(action.detail) from exc
        action.status = "submitted"
        return action

    def confirm_action(self, action: DraftAction, after: DraftObservation) -> ActionConfirmation:
        """Confirm using both history and the explicit roster read-back."""
        if action.session_id != after.session_id or after.room != self.room:
            raise SessionBlocked("read-back identity mismatch")
        history_match = any(_event_matches(action, event) for event in after.history)
        roster_match = any(_same_player(player.player_id, action.player_id) or _same_player(player.name, action.player_name) for player in after.roster)
        if history_match and roster_match:
            action.status = "confirmed"
            action.updated_at = action.created_at
            action.detail = "confirmed by pick history and roster read-back"
            self._pending.pop(action.action_id, None)
            return ActionConfirmation(action, "confirmed", action.detail, after.revision)
        # A different player occupying the expected pick or an advanced clock
        # is a possible success/failure race.  Never retry from this method.
        action.status = "uncertain"
        action.detail = "read-back did not confirm both pick history and roster; inspect before retry"
        return ActionConfirmation(action, "uncertain", action.detail, after.revision)

    def inspect_unknown(self, action: DraftAction, after: DraftObservation) -> ActionConfirmation:
        """Mark an uncertain result as inspected without silently retrying."""
        result = self.confirm_action(action, after)
        self._inspected_unknown.add(action.action_id)
        return result

    def retry(self, action: DraftAction, *, observation: DraftObservation | None = None, confirm: bool = False, submitter: Callable[[DraftAction], object] | None = None) -> DraftAction:
        if action.status == "uncertain" and action.action_id not in self._inspected_unknown:
            raise UnknownActionOutcome("inspect the post-submit observation before retrying")
        return self.submit(action, observation=observation, confirm=confirm, submitter=submitter)


def guard_action(action: DraftAction, observation: DraftObservation, *, session: DraftSession, room: RoomIdentity, config: LeagueConfig) -> None:
    """Functional convenience wrapper for controllers that do not retain an operator."""
    operator = BrowserOperator(session=session, room=room, config=config, observation=observation)
    operator._pending[action.action_id] = action
    operator.guard_action(action, observation)


__all__ = [
    "ActionBlocked",
    "ActionConfirmation",
    "BrowserOperator",
    "BrowserOperatorError",
    "QueueFallbackPlan",
    "SessionBlocked",
    "StaleExpectedTurn",
    "UnknownActionOutcome",
    "guard_action",
]
