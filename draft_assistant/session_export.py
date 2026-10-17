"""Sanitized session export (``sanitized-session-v1``).

This is the shape of the durable live validations under
``draft_assistant/validation/`` (see ``browser-recovery-2026-10-11/
uninterrupted-1000000007.json``): session, practice attestation, direct
actions, a journal projection, the terminal observation without the player
catalog, latency rows, and a verification block.  The offline dress rehearsal
emits the same shape so a live export and a rehearsal export can be diffed key
for key.  Claim tokens, owner tokens, repeated observation payloads, and the
full catalog are never exported.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .coordinator import Coordinator

ARTIFACT_VERSION = "sanitized-session-v1"

_OMITTED = [
    "claim_token",
    "owner_token",
    "available_players",
    "known_players",
    "recommendation payloads",
    "repeated observation payloads",
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _journal_rows(coordinator: Coordinator, session_id: str) -> list[dict[str, Any]]:
    rows = coordinator._db.execute(
        "SELECT seq,event,payload,created_at FROM journal WHERE session_id=? ORDER BY seq", (session_id,)
    ).fetchall()
    return [{"seq": r["seq"], "event": r["event"], "payload": json.loads(r["payload"] or "{}"), "created_at": r["created_at"]} for r in rows]


def _snake_team(pick: int, order: list[str]) -> str:
    round_index, offset = divmod(pick - 1, 14)
    return order[offset if round_index % 2 == 0 else 13 - offset]


def build_sanitized_export(
    coordinator: Coordinator,
    session_id: str,
    *,
    latency_rows: list[Mapping[str, Any]],
    foreground: Mapping[str, Any],
    provenance: str,
    release_limits: list[str],
    extra: Mapping[str, Any] | None = None,
    exported_at: str | None = None,
) -> dict[str, Any]:
    status = coordinator.status(session_id)
    session_row = coordinator._session_row(session_id)
    observation = coordinator._latest_observation(session_id)
    if observation is None:
        raise ValueError("cannot export a session with no observation")
    final = observation.to_dict()
    for key in ("available_players", "known_players"):
        final.pop(key, None)
    history = final["history"]
    order = list(final["pick_order"])
    own = coordinator.config.our_team
    own_picks = [event["pick_number"] for event in history if event["team"] == own]
    mismatches = [event["pick_number"] for event in history if event["team"] != _snake_team(event["pick_number"], order)]
    roster = final["roster"]
    eligibility_counts = {"C": 0, "G": 0, "F": 0}
    for player in roster:
        for slot in player["eligibility"]:
            eligibility_counts[slot] += 1
    final["roster"] = [{"player_id": p["player_id"], "name": p["name"], "eligibility": p["eligibility"]} for p in roster]
    final["history_summary"] = {
        "count": len(history),
        "unique_player_ids": len({event["player_id"] for event in history}),
        "first_pick": history[0]["player_name"] if history else None,
        "last_pick": history[-1]["player_name"] if history else None,
        "our_confirmed_picks": own_picks,
    }
    final["roster_summary"] = {
        "players": len(roster),
        "eligibility_counts": eligibility_counts,
        "legal_roster": coordinator._roster_can_fill_starters(observation.roster),
    }

    journal = _journal_rows(coordinator, session_id)
    counts: dict[str, int] = {}
    for entry in journal:
        counts[entry["event"]] = counts.get(entry["event"], 0) + 1
    control_names = {"connect", "practice_attested", "start", "resume", "pause", "stale_pause", "restart_pause", "manual_override", "completed", "ownership_claimed", "ownership_transferred"}
    control_events = [
        {"seq": e["seq"], "event": e["event"], "created_at": e["created_at"], "payload": e["payload"]}
        for e in journal
        if e["event"] in control_names
    ]
    started_seq = next((e["seq"] for e in journal if e["event"] == "start"), None)
    after_start = [
        {"seq": e["seq"], "event": e["event"], "created_at": e["created_at"], "reason": e["payload"].get("reason")}
        for e in journal
        if started_seq is not None and e["seq"] > started_seq and e["event"] in {"pause", "stale_pause", "resume", "restart_pause", "ownership_transferred"}
    ]
    lifecycle: dict[str, list[str]] = {}
    for entry in journal:
        action_id = entry["payload"].get("action_id") if isinstance(entry["payload"], dict) else None
        if action_id and entry["event"].startswith("action_"):
            lifecycle.setdefault(action_id, []).append(entry["event"])

    actions = [
        {
            "action_id": a["action_id"],
            "pick": a["expected_pick"],
            "player_id": a["player_id"],
            "player_name": a["player_name"],
            "revision": a["revision"],
            "status": a["status"],
            "action_type": a["action_type"],
            "claimed": a["claimed"],
            "dispatched": a["dispatched"],
            "created_at": a["created_at"],
            "updated_at": a["updated_at"],
        }
        for a in status["actions"]
        if a["action_type"] == "draft"
    ]
    confirmed = [a for a in actions if a["status"] == "confirmed"]
    history_by_pick = {event["pick_number"]: event for event in history}
    attestation = status["practice_attestation"]
    document: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "exported_at": exported_at or _utc_iso(),
        "sanitization": {
            "projection": "session/actions/journal/final_observation",
            "omitted": _OMITTED,
            "reason": "Durable audit projection keeps control and final-state evidence without credentials, claim material, or the full player catalog.",
        },
        "session": {
            "session_id": session_id,
            "room": status["room"],
            "state": status["state"],
            "reason": status["reason"],
            "manual_override": status["manual_override"],
            "control_version": status["control_version"],
            "latest_revision": status["latest_revision"],
            "created_at": session_row["created_at"],
            "updated_at": session_row["updated_at"],
            "foreground_invocation": dict(foreground),
        },
        "practice_attestation": attestation,
        "actions": actions,
        "journal": {
            "source_table": "coordinator.journal",
            "sequence_start": journal[0]["seq"] if journal else None,
            "sequence_end": journal[-1]["seq"] if journal else None,
            "event_counts": counts,
            "control_events": control_events,
            "action_lifecycle": [{"action_id": action_id, "events": events} for action_id, events in lifecycle.items()],
            "confirmation_detail": "confirmed by authoritative board history and roster",
            "pause_resume_events_after_start": after_start,
            "omitted_event_classes": ["observation", "recommendation"],
            "omitted_event_note": "The final observation below is the terminal observation projection; repeated observation and recommendation payloads were omitted from this sanitized journal export.",
        },
        "final_observation": final,
        "latency": {
            "provenance": provenance,
            "units": {
                "observation_to_click": "milliseconds",
                "click_to_confirm": "milliseconds",
                "clock": "seconds remaining at prepare observation",
            },
            "rows": [dict(row) for row in latency_rows],
            "clean_timing_gate": "not_evaluated_offline",
        },
        "release_limits": list(release_limits),
        "verification": {
            "source": f"coordinator terminal observation revision {final['revision']}",
            "history_rows_exported": len(history),
            "unique_history_player_ids": len({event["player_id"] for event in history}),
            "contiguous_pick_numbers": [event["pick_number"] for event in history] == list(range(1, len(history) + 1)),
            "snake_order_matches_pick_order": not mismatches,
            "snake_order_mismatches": mismatches,
            "confirmed_action_player_ids_match_history": all(
                history_by_pick.get(a["pick"], {}).get("player_id") == a["player_id"] and history_by_pick.get(a["pick"], {}).get("team") == own
                for a in confirmed
            ),
        },
    }
    if extra:
        document.update(dict(extra))
    return document
