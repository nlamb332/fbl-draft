"""Write a sanitized export from a completed coordinator session and observed evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from draft_assistant.coordinator import Coordinator  # noqa: E402
from draft_assistant.session_export import build_sanitized_export  # noqa: E402


def _contains_forbidden_keys(value: Any) -> bool:
    forbidden = {"owner_token", "claim_token", "available_players", "known_players"}
    if isinstance(value, dict):
        return any(key in forbidden or _contains_forbidden_keys(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_keys(child) for child in value)
    return False


def _validate_extra(extra: Any) -> None:
    if extra is None:
        return
    if not isinstance(extra, dict):
        raise ValueError("extra must be an object")
    # build_sanitized_export merges extra into the artifact root. Prevent
    # evidence metadata from replacing the session, verification, or version.
    reserved = {"artifact_version", "exported_at", "session", "practice_attestation", "actions", "journal", "final_observation", "latency", "release_limits", "verification"}
    conflict = sorted(reserved.intersection(extra))
    if conflict:
        raise ValueError(f"extra cannot override reserved export fields: {', '.join(conflict)}")
    if _contains_forbidden_keys(extra):
        raise ValueError("extra contains credentials or full player catalogs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="runtime/control.sqlite3", help="coordinator SQLite database")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--evidence", required=True, help="JSON with observed latency and foreground metadata")
    parser.add_argument("--output", required=True, help="destination JSON path")
    args = parser.parse_args()

    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    required = {"latency_rows", "foreground", "provenance", "release_limits"}
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"evidence JSON is missing: {', '.join(missing)}")
    if not isinstance(evidence["latency_rows"], list) or not isinstance(evidence["foreground"], dict) or not isinstance(evidence["release_limits"], list):
        raise ValueError("latency_rows and release_limits must be arrays; foreground must be an object")
    if not isinstance(evidence["provenance"], str) or not evidence["provenance"].strip():
        raise ValueError("provenance must describe the evidence source")
    _validate_extra(evidence.get("extra"))

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    coordinator = Coordinator(db_path, recover=False)
    try:
        status = coordinator.status(args.session_id)
        if status["state"] != "completed":
            raise ValueError(f"session is {status['state']!r}; only completed sessions can be exported")
        document = build_sanitized_export(
            coordinator,
            args.session_id,
            latency_rows=evidence["latency_rows"],
            foreground=evidence["foreground"],
            provenance=evidence["provenance"],
            release_limits=evidence["release_limits"],
            extra=evidence.get("extra"),
        )
    finally:
        coordinator.close()

    if _contains_forbidden_keys(document):
        raise ValueError("sanitized export contains credentials or full player catalogs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"artifact_version": document["artifact_version"], "output": str(output_path), "session_id": args.session_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
