"""Command line entrypoint for the local draft control plane."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .coordinator import Coordinator
from .contracts import LeagueConfig
from .bridge import BrowserBridge
from .marginal_lineup import DEFAULT_PROMOTION_PATH
from .server import serve


def _value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _value(v) for k, v in value.items()}
    return value


def _read_json(path: str | None) -> Any:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m draft_assistant", description="Local FBL draft control")
    parser.add_argument("--db", default="runtime/control.sqlite3", help="SQLite control database")
    parser.add_argument("--league-config", type=Path, help="private local league configuration JSON (keep under runtime/)")
    parser.add_argument("--promotion-path", dest="default_promotion_path", default=str(DEFAULT_PROMOTION_PATH), help="promotion artifact for automatic selection (default: accepted marginal-lineup-v1)")
    parser.add_argument("--foundation-only", action="store_true", help="ignore promotion artifacts and select with the foundation policy")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_cmd = sub.add_parser("serve", help="serve the local control panel")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--page-dispatch", action="store_true", help="allow the ESPN page to run the guarded prepare/ack/reconcile operations (default off)")
    serve_cmd.add_argument("--page-runner", action="store_true", help="serve /page-runner.js and let the ESPN page drive an owned session's operator sequence (default off)")
    serve_cmd.add_argument("--open-browser", action="store_true", help="open the local panel after the server binds successfully")
    loader = sub.add_parser("page-loader", help="print the one-shot in-page loader snippet for the running panel")
    loader.add_argument("--base", default="http://127.0.0.1:8765")
    advisor_loader = sub.add_parser("advisor-loader", help="print the reusable room-agnostic recommendation-only loader")
    advisor_loader.add_argument("--base", default="http://127.0.0.1:8765")
    connect = sub.add_parser("connect")
    connect.add_argument("url")
    connect.add_argument("--practice", action="store_true")
    connect.add_argument("--session-id")
    verify_practice = sub.add_parser(
        "verify-practice",
        aliases=("attest-practice",),
        help="attest the visible practice heading and its configured source league",
    )
    verify_practice.add_argument("session_id")
    verify_practice.add_argument("--heading", required=True, help="exact one-line ESPN practice heading observed in the room")
    verify_practice.add_argument(
        "--source-league-id",
        "--source-league",
        dest="source_league_id",
        required=True,
        help="configured live league ID that launched this practice room",
    )
    observation = sub.add_parser("publish-observation")
    observation.add_argument("--file", "-f")
    recommend = sub.add_parser("recommend")
    recommend.add_argument("session_id")
    recommend.add_argument("--lookahead-enabled", action="store_true")
    recommend.add_argument("--promotion-path")
    next_action = sub.add_parser("next-action")
    next_action.add_argument("session_id")
    claim = sub.add_parser("claim-action")
    claim.add_argument("action_id")
    dispatch = sub.add_parser("dispatch-command", help="consume a claimed action's one-time final guard")
    dispatch.add_argument("action_id")
    prepare = sub.add_parser("prepare-action", help="publish, recommend, claim, and emit one guarded command in one process")
    prepare.add_argument("session_id")
    prepare.add_argument("--file", "-f", help="fresh observation JSON")
    prepare.add_argument("--lookahead-enabled", action="store_true")
    prepare.add_argument("--promotion-path")
    ack = sub.add_parser("ack-action")
    ack.add_argument("action_id")
    ack.add_argument("status", choices=("submitted", "uncertain", "confirmed", "failed", "cancelled"))
    ack.add_argument("--detail", default="")
    ack.add_argument("--file", "-f")
    status = sub.add_parser("status")
    status.add_argument("session_id", nargs="?")
    control = sub.add_parser("control")
    control.add_argument("session_id")
    control.add_argument("action", choices=("start", "pause", "resume", "manual-override", "manual-pick", "confirm-manual-pick"))
    control.add_argument("--player-id")
    control.add_argument("--action-id")
    control.add_argument("--reason", default="")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("session_id")
    return parser


def _promotion(args: argparse.Namespace) -> str | None:
    return None if args.foundation_only else args.default_promotion_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = None
    if args.league_config is not None:
        values = json.loads(args.league_config.read_text(encoding="utf-8-sig"))
        if "starting_slots" in values:
            values["starting_slots"] = tuple(values["starting_slots"])
        config = LeagueConfig(**values)
    if args.command == "serve":
        serve(coordinator=Coordinator(args.db, config=config, recover=True, promotion_path=_promotion(args)), host=args.host, port=args.port, page_dispatch_enabled=args.page_dispatch, page_runner_enabled=args.page_runner, open_browser=args.open_browser)
        return 0
    if args.command == "page-loader":
        from .page_bundle import loader_snippet

        print(loader_snippet(args.base))
        return 0
    if args.command == "advisor-loader":
        from .page_bundle import recommendation_loader_snippet

        print(recommendation_loader_snippet(args.base))
        return 0
    # A short-lived CLI client must not look like a service restart and pause
    # a session owned by the long-lived panel/browser process.
    coordinator = Coordinator(args.db, config=config, recover=False, promotion_path=_promotion(args))
    try:
        if args.command == "connect":
            result = coordinator.connect(args.url, practice=args.practice, session_id=args.session_id)
        elif args.command in {"verify-practice", "attest-practice"}:
            result = coordinator.attest_practice(
                args.session_id,
                heading=args.heading,
                source_league_id=args.source_league_id,
            )
        elif args.command == "publish-observation":
            result = coordinator.ingest_observation(_read_json(args.file))
        elif args.command == "recommend":
            result = coordinator.recommend(args.session_id, lookahead_enabled=args.lookahead_enabled, promotion_path=args.promotion_path)
        elif args.command == "next-action":
            result = coordinator.next_action(args.session_id)
        elif args.command == "claim-action":
            result = coordinator.claim_action(args.action_id)
        elif args.command == "dispatch-command":
            result = BrowserBridge(coordinator).command_for(args.action_id).to_dict()
        elif args.command == "prepare-action":
            if args.file:
                coordinator.ingest_observation(_read_json(args.file))
            coordinator.recommend(args.session_id, lookahead_enabled=args.lookahead_enabled, promotion_path=args.promotion_path)
            action = coordinator.next_action(args.session_id)
            if action is None:
                result = None
            else:
                coordinator.claim_action(action.action_id)
                result = BrowserBridge(coordinator).command_for(action.action_id).to_dict()
        elif args.command == "ack-action":
            result = coordinator.ack_action(args.action_id, args.status, detail=args.detail, observation=_read_json(args.file) if args.file else None)
        elif args.command == "status":
            result = coordinator.status(args.session_id)
        elif args.command == "control":
            result = coordinator.control(args.session_id, args.action, player_id=args.player_id, action_id=args.action_id, reason=args.reason)
        elif args.command == "reconcile":
            result = coordinator.reconcile(args.session_id)
        else:  # pragma: no cover
            raise RuntimeError(f"unsupported command {args.command}")
        print(json.dumps(_value(result), sort_keys=True, default=str))
        return 0
    except Exception as exc:  # CLI should be machine-readable and non-traceback by default.
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        coordinator.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

