"""Practice-room preflight for native Start/Resume.

Room 1000000016 ran with a 30 second timer and lost both owned turns to native
fallback before a command could be dispatched because nothing read the timer
back.  A practice Start/Resume is now refused unless the room was read back as:

* a readable, positive configured pick timer (the setting, not the running
  clock).  Any value is accepted at the first Start; the coordinator records
  it as the session's pick timer and every later Start/Resume must read back
  that same value,
* Autopick explicitly off,
* our team and draft seat verified against the visible pick order, and
* exactly one coordinator listener on the control port (an older listener makes
  the panel answer from stale state).

Everything is fail-closed: an unknown value is a refusal.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .contracts import DraftObservation, LeagueConfig

@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reasons: tuple[str, ...] = ()
    checks: Mapping[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons), "checks": dict(self.checks)}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def practice_preflight(
    observation: DraftObservation,
    config: LeagueConfig,
    *,
    native_timer_seconds: Any,
    expected_seat: Any,
    listener_count: int | None,
    recorded_timer_seconds: float | None = None,
) -> PreflightResult:
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    timer = _number(native_timer_seconds)
    readable = timer is not None and math.isfinite(timer) and timer > 0
    checks["timer"] = readable and (recorded_timer_seconds is None or timer == recorded_timer_seconds)
    if not readable:
        shown = "missing" if timer is None else f"{timer:g} s"
        reasons.append(f"native pick timer read-back is {shown}; a readable positive pick timer is required")
    elif not checks["timer"]:
        reasons.append(f"native pick timer read-back is {timer:g} s but this draft started with {recorded_timer_seconds:g} s")

    checks["autopick"] = observation.autopick is False
    if not checks["autopick"]:
        reasons.append("Autopick is not explicitly off")

    order = tuple(observation.pick_order)
    own = [index for index, team in enumerate(order) if team == config.our_team]
    checks["team"] = len(order) == 14 and len(own) == 1 and observation.room.team_id == config.team_id
    if not checks["team"]:
        reasons.append("our team is not verified exactly once in the fourteen-team pick order for the configured team page")
    seat_ok = (
        isinstance(expected_seat, int)
        and not isinstance(expected_seat, bool)
        and checks["team"]
        and own[0] + 1 == expected_seat
    )
    checks["seat"] = bool(seat_ok)
    if not checks["seat"]:
        reasons.append("draft seat is not verified against the visible pick order")

    checks["single_listener"] = listener_count == 1
    if not checks["single_listener"]:
        shown = "unknown" if listener_count is None else str(listener_count)
        reasons.append(f"exactly one coordinator listener is required; found {shown}")

    return PreflightResult(all(checks.values()), tuple(reasons), checks)


def _windows_listener_count(port: int) -> int | None:
    result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return None
    pattern = re.compile(rf"^\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+\d+\s*$", re.IGNORECASE)
    return sum(1 for line in result.stdout.splitlines() if pattern.match(line))


def _posix_listener_count(port: int) -> int | None:
    try:
        result = subprocess.run(["ss", "-H", "-ltn", f"sport = :{port}"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0:
        return len([line for line in result.stdout.splitlines() if line.strip()])
    try:
        total = 0
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            if not os.path.exists(name):
                continue
            with open(name, encoding="utf-8") as handle:
                for line in handle.readlines()[1:]:
                    fields = line.split()
                    if len(fields) > 3 and fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                        total += 1
        return total if os.path.exists("/proc/net/tcp") else None
    except (OSError, ValueError):
        return None


def count_local_listeners(port: int) -> int | None:
    """Count TCP sockets listening on ``port``; None when it cannot be determined."""
    try:
        return _windows_listener_count(port) if os.name == "nt" else _posix_listener_count(port)
    except (OSError, subprocess.SubprocessError):
        return None


ListenerCounter = Callable[[int], "int | None"]
