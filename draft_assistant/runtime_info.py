"""Credential-free identity for the coordinator serving a localhost link."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .marginal_lineup import load_promotion_payload, promotion_valid


def startup_source() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    revision = None
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            revision = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    names = ("server.py", "coordinator.py", "ownership.py", "valuation.py", "marginal_lineup.py", "page_bundle.py")
    return {
        "git_revision": revision,
        "source_hashes_sha256": {
            f"draft_assistant/{name}": hashlib.sha256((root / "draft_assistant" / name).read_bytes()).hexdigest()
            for name in names
        },
    }


def runtime_info(server: Any) -> dict[str, Any]:
    coordinator = server.coordinator
    payload = load_promotion_payload(coordinator.promotion_path)
    promoted = payload is not None and promotion_valid(payload, coordinator.config)
    return {
        "service": "fbl-draft-control",
        "schema_version": 1,
        "startup_source": server.startup_source,
        "policy": {
            "name": "marginal-lineup-v1" if promoted else "foundation",
            "promotion_valid": promoted,
            "config_fingerprint": payload.get("config_fingerprint") if payload else None,
        },
        "page_dispatch_enabled": server.page_dispatch_enabled,
        "page_runner_enabled": getattr(server, "page_runner_enabled", False),
        "note": "Source hashes describe startup files; policy describes current server configuration. Per-pick journal records actual promotion or fallback.",
    }
