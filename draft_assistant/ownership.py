"""Session/tab ownership for the operator write path.

Practice room 1000000002 lost its tab claim when the agent handed the room to
a successor: later updates were refused and the room was orphaned.  This module
defines the explicit protocol:

* ``claim``    - first owner takes an unowned session and receives a secret
  owner token (only its SHA-256 digest is stored).
* ``transfer`` - a new owner presents the session ID and the last revision it
  confirmed.  The coordinator accepts only when that revision is the session's
  latest revision, revokes the previous token, mints a new one, pauses a
  running session (the new owner must Resume explicitly), and journals the
  hand-over.  The old owner's token is never consulted, so a crashed agent
  cannot orphan the room.
* ``require``  - every operator write presents its token.  An old, missing or
  wrong token is rejected once a session has an owner.

Sessions that were never claimed keep the previous behaviour, so existing
single-agent flows and tests are unchanged.  Ownership only gates the operator
HTTP write operations; local Pause (panel/CLI) is always available.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from .coordinator import ActionConflict, Coordinator, CoordinatorError, IdentityError


class OwnershipError(CoordinatorError):
    """Raised when an operator write is not made by the current owner."""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionOwnership:
    def __init__(self, coordinator: Coordinator) -> None:
        self.coordinator = coordinator
        with coordinator._transaction() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS session_owners (
                    session_id TEXT PRIMARY KEY,
                    token_digest TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    epoch INTEGER NOT NULL,
                    claimed_revision INTEGER,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
                """
            )

    def _row(self, session_id: str):
        return self.coordinator._db.execute("SELECT * FROM session_owners WHERE session_id=?", (session_id,)).fetchone()

    def session_for_action(self, action_id: str) -> str | None:
        row = self.coordinator._db.execute("SELECT session_id FROM actions WHERE action_id=?", (action_id,)).fetchone()
        return row["session_id"] if row else None

    def session_for_queue_mutation(self, mutation_id: str) -> str | None:
        row = self.coordinator._db.execute("SELECT session_id FROM queue_mutations WHERE mutation_id=?", (mutation_id,)).fetchone()
        return row["session_id"] if row else None

    def describe(self, session_id: str) -> dict[str, Any] | None:
        row = self._row(session_id)
        if row is None:
            return None
        return {"epoch": row["epoch"], "label": row["label"], "claimed_revision": row["claimed_revision"], "updated_at": row["updated_at"]}

    def claim(self, session_id: str, *, label: str = "") -> dict[str, Any]:
        coordinator = self.coordinator
        session = coordinator._session_row(session_id)
        now = coordinator.clock()
        token = secrets.token_urlsafe(32)
        with coordinator._transaction() as db:
            if db.execute("SELECT 1 FROM session_owners WHERE session_id=?", (session_id,)).fetchone():
                raise ActionConflict("session already has an owner; use an ownership transfer")
            db.execute(
                "INSERT INTO session_owners(session_id,token_digest,label,epoch,claimed_revision,updated_at) VALUES(?,?,?,?,?,?)",
                (session_id, _digest(token), label[:80], 1, session["latest_revision"], now),
            )
            coordinator._journal_db(db, session_id, "ownership_claimed", {"epoch": 1, "label": label[:80], "revision": session["latest_revision"]}, now)
        return {"session_id": session_id, "owner_token": token, "epoch": 1, "revision": session["latest_revision"]}

    def transfer(self, session_id: str, *, last_confirmed_revision: Any, label: str = "") -> dict[str, Any]:
        coordinator = self.coordinator
        session = coordinator._session_row(session_id)
        if isinstance(last_confirmed_revision, bool) or not (last_confirmed_revision is None or isinstance(last_confirmed_revision, int)):
            raise IdentityError("last confirmed revision must be an integer (or null before any observation)")
        latest = session["latest_revision"]
        if last_confirmed_revision != latest:
            raise IdentityError(f"stale ownership transfer: presented revision {last_confirmed_revision}, session is at {latest}")
        now = coordinator.clock()
        token = secrets.token_urlsafe(32)
        with coordinator._transaction() as db:
            # Recheck freshness and ownership under the write lock. The state
            # transition and token rotation commit together, so no heartbeat
            # can recover a degraded session in the handoff window.
            current_session = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            latest = current_session["latest_revision"]
            if last_confirmed_revision != latest:
                raise IdentityError(f"stale ownership transfer: presented revision {last_confirmed_revision}, session is at {latest}")
            current = db.execute("SELECT * FROM session_owners WHERE session_id=?", (session_id,)).fetchone()
            if current is None:
                raise OwnershipError("session has no owner to transfer from; claim it instead")
            was_active = current_session["state"] in {"running", "degraded"}
            if was_active:
                reason = "ownership transferred; explicit resume by the new owner required"
                coordinator._pause_db(db, session_id, now, reason=reason)
            epoch = current["epoch"] + 1
            db.execute(
                "UPDATE session_owners SET token_digest=?,label=?,epoch=?,claimed_revision=?,updated_at=? WHERE session_id=?",
                (_digest(token), label[:80], epoch, latest, now, session_id),
            )
            coordinator._journal_db(
                db,
                session_id,
                "ownership_transferred",
                {"from_epoch": current["epoch"], "to_epoch": epoch, "from_label": current["label"], "to_label": label[:80], "revision": latest, "paused_running_session": was_active},
                now,
            )
        return {
            "session_id": session_id,
            "owner_token": token,
            "epoch": epoch,
            "revision": latest,
            "paused_running_session": was_active,
            "unresolved_actions": coordinator.status(session_id)["unresolved_actions"],
        }

    def require(self, session_id: str, token: Any) -> None:
        row = self._row(session_id)
        if row is None:
            return
        if not isinstance(token, str) or not token or not hmac.compare_digest(_digest(token), row["token_digest"]):
            raise OwnershipError("operator write rejected: caller does not hold the current session ownership claim")
