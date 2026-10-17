"""Small explicit lifecycle; the coordinator supplies readiness checks."""
from dataclasses import dataclass
from .contracts import SESSION_STATES


@dataclass
class DraftSession:
    session_id: str
    state: str = "disconnected"
    reason: str = ""

    def transition(self, target: str, *, ready: bool = False, reason: str = "") -> None:
        if target not in SESSION_STATES:
            raise ValueError("unknown session state")
        allowed = {
            "disconnected": {"observing"},
            "observing": {"ready", "paused", "disconnected", "completed"},
            "ready": {"running", "observing", "paused", "disconnected", "completed"},
            "running": {"degraded", "paused", "disconnected", "completed"},
            "degraded": {"running", "paused", "disconnected", "completed"},
            "paused": {"running", "observing", "disconnected", "completed"},
            "completed": set(),
        }
        if target == self.state:
            return
        if target not in allowed[self.state]:
            raise ValueError(f"invalid transition {self.state} -> {target}")
        if target in {"ready", "running"} and not ready:
            raise ValueError("verified readiness required")
        self.state, self.reason = target, reason
