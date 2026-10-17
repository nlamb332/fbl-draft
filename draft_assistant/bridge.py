"""Safe boundary between the coordinator and a browser operator.

This module intentionally has no HTTP/browser automation dependency.  A
browser agent supplies an operator callable which receives a guarded command.
The callable is invoked only after :meth:`Coordinator.final_guard` rereads
the latest control intent and observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .contracts import DraftAction
from .coordinator import Coordinator, CoordinatorError


class BrowserOperator(Protocol):
    def __call__(self, command: dict[str, Any]) -> Any:
        """Perform one visible, user-facing browser command."""


@dataclass(frozen=True)
class GuardedBrowserCommand:
    action_id: str
    session_id: str
    player_id: str
    player_name: str
    expected_pick: int
    expected_team: str
    revision: int
    command_type: str = "select_player"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.command_type,
            "action_id": self.action_id,
            "session_id": self.session_id,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "expected_pick": self.expected_pick,
            "expected_team": self.expected_team,
            "revision": self.revision,
        }


class BrowserBridge:
    """Turn claimed actions into one guarded command and reconcile the ack."""

    def __init__(self, coordinator: Coordinator) -> None:
        self.coordinator = coordinator

    def command_for(self, action_id: str) -> GuardedBrowserCommand:
        guard = self.coordinator.final_guard(action_id)
        action_row = self.coordinator._db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
        action = self.coordinator._action_from_row(action_row)
        return GuardedBrowserCommand(
            action_id=action_id,
            session_id=guard["session_id"],
            player_id=guard["player_id"],
            player_name=guard["player_name"],
            expected_pick=int(action.expected_pick),
            expected_team=action.expected_team,
            revision=int(guard["revision"]),
        )

    def execute(self, action_id: str, operator: BrowserOperator) -> DraftAction:
        """Run an operator click after a final guard.

        The click cannot be cancelled once the callable has started.  An
        exception or an ambiguous result is recorded as ``uncertain`` and
        blocks further automation until a fresh board observation reconciles
        it.
        """
        command = self.command_for(action_id)
        try:
            result = operator(command.to_dict())
        except Exception as exc:  # noqa: BLE001 - outcome is deliberately unknown
            return self.coordinator.ack_action(action_id, "uncertain", detail=f"browser outcome unknown: {exc}")
        if isinstance(result, dict):
            status = str(result.get("status", "submitted")).lower()
            detail = str(result.get("detail", ""))
        elif result is False or result is None:
            status, detail = "uncertain", "browser operator did not provide a submission acknowledgement"
        else:
            status, detail = "submitted", "browser operator returned a submission acknowledgement"
        if status not in {"submitted", "uncertain", "failed"}:
            status = "uncertain"
            detail = detail or "browser operator returned an unsupported outcome"
        return self.coordinator.ack_action(action_id, status, detail=detail)

    def guarded_click(self, action_id: str, click: Callable[[dict[str, Any]], Any]) -> DraftAction:
        """Alias used by browser agents; no network/browser calls are made here."""
        return self.execute(action_id, click)


def build_bridge(coordinator: Coordinator) -> BrowserBridge:
    return BrowserBridge(coordinator)

