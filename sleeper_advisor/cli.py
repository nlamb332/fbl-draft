"""Command line front end: ``once``, ``watch``, ``serve`` and ``config``."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Sequence

from .advisor import POLICY_ID, recommend
from .api import SleeperClient, parse_draft_id
from .lineup import best_lineup
from .lock_in import ForecastConfig, estimate_player, load_forecasts
from .model import build_state
from .scoring import fantasy_points

DEFAULT_CACHE_DIR = "runtime/sleeper"
DEFAULT_PORT = 8766
DEFAULT_INTERVAL = 3.0
LEGEND = ("Added/wk = estimated starting-lineup Lock-In points gained. "
          "Lock-In/wk values one selected game per player each week; depth ties use that value. "
          "Forecast assumptions are not calibrated player predictions.")
CONFIG_KEYS = ("draft_url", "username")
_MEMO_SECONDS = 600.0


# -- client wrapper ---------------------------------------------------------
class PollingClient:
    """Wraps a SleeperClient for repeated polling.

    Players and projections are kept in memory between ticks (the underlying
    client still owns the file cache and TTL); the latest draft response is
    remembered so display code can show the draft name.
    """

    def __init__(self, client: SleeperClient, clock: Callable[[], float] = time.monotonic):
        self._client = client
        self._clock = clock
        self._memo: dict[Any, tuple[float, Any]] = {}
        self.last_draft: dict = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def _memoized(self, key: Any, loader: Callable[[], Any]) -> Any:
        hit = self._memo.get(key)
        now = self._clock()
        if hit is not None and now - hit[0] < _MEMO_SECONDS:
            return hit[1]
        value = loader()
        self._memo[key] = (now, value)
        return value

    def players(self) -> dict:
        return self._memoized("players", self._client.players)

    def projections(self, season: Any) -> list:
        return self._memoized(("projections", str(season)), lambda: self._client.projections(season))

    def draft(self, draft_id: str) -> dict:
        draft = self._client.draft(draft_id)
        if isinstance(draft, dict):
            self.last_draft = draft
        return draft


# -- snapshot ---------------------------------------------------------------
def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _player_fp(player: Any, scoring: dict) -> float:
    if not player.has_projection:
        return 0.0
    return fantasy_points(player.stats, scoring)[0]


def build_snapshot(state: Any, recs: Sequence[Any], draft: dict | None = None,
                   forecasts: ForecastConfig | None = None) -> dict:
    """Plain-data (JSON-safe) description of the draft, our team and advice."""
    league = state.league
    if forecasts is not None:
        forecasts.validate_for(league.season, league.scoring)
    draft = draft or {}
    metadata = draft.get("metadata") or {}
    name = metadata.get("name") or draft.get("name")

    projections = {p.player_id:estimate_player(p, league.scoring, forecasts) for p in state.our_roster}
    entries = [(p.player_id, tuple(p.positions), projections[p.player_id].weekly_points)
               for p in state.our_roster]
    total, assignment = best_lineup(entries, league.starting_slots)
    by_id = {p.player_id: p for p in state.our_roster}

    def row(pid: str, slot: str | None) -> dict:
        p = by_id[pid]
        return {"slot": slot, "player_id": pid, "name": p.name,
                "positions": list(p.positions), "fp_per_game": round(projections[pid].fp_per_game, 2),
                "lock_in_weekly_points":round(projections[pid].weekly_points,2),
                "forecast_source":projections[pid].source}

    by_slot: dict[str, list[str]] = {}
    for pid, slot in assignment.items():
        by_slot.setdefault(slot, []).append(pid)
    lineup = []
    for slot in [s for s in league.starting_slots if s != "BN"]:
        queue = by_slot.get(slot) or []
        lineup.append(row(queue.pop(0), slot) if queue else
                      {"slot": slot, "player_id": None, "name": None,
                       "positions": [], "fp_per_game": None, "lock_in_weekly_points":None,
                       "forecast_source":None})
    bench = [row(p.player_id, None) for p in state.our_roster if p.player_id not in assignment]
    bench.sort(key=lambda r: -(r["lock_in_weekly_points"] or 0.0))

    current = state.current_pick
    upcoming = [n for n in state.our_next_picks if current is None or n >= current]
    picks_away = (upcoming[0] - current) if (upcoming and current is not None) else None
    on_clock = (current is not None and state.our_slot is not None
                and state.on_clock_slot == state.our_slot)
    if on_clock:
        picks_away = 0

    warnings = list(dict.fromkeys(
        list(state.warnings) + [w for r in recs for w in r.warnings]
        + [warning for projection in projections.values() for warning in projection.warnings]))

    return {
        "draft": {
            "draft_id": league.draft_id, "name": name, "league_id": league.league_id,
            "mock_type": league.mock_type, "status": league.status,
            "type": league.draft_type, "season": league.season,
            "teams": league.teams, "rounds": league.rounds,
        },
        "picks_made": len(state.picks),
        "current_pick": current,
        "on_clock_slot": state.on_clock_slot,
        "our_slot": state.our_slot,
        "our_next_picks": upcoming,
        "picks_away": picks_away,
        "on_clock": on_clock,
        "lineup": lineup,
        "lineup_total": round(total, 2),
        "strategy": {"id":POLICY_ID, "mode":"lock_in", "value_units":"estimated weekly Lock-In points",
                     "local_forecast_file":forecasts is not None,
                     "assumptions_notice":"Season averages do not supply game variance, schedules or health probabilities. Unlisted players use labeled assumptions."},
        "bench": bench,
        "recommendations": [
            {"rank": i, "player_id": r.player_id, "name": r.name,
             "positions": list(r.positions), "fp_per_game": r.fp_per_game,
             "adp": r.adp, "score": r.score, "components": dict(r.components),
             "explanation": r.explanation, "warnings": list(r.warnings)}
            for i, r in enumerate(recs, 1)],
        "legend": LEGEND,
        "warnings": warnings,
        "updated_at": _now_text(),
        "error": None,
    }


# -- polling ----------------------------------------------------------------
class Poller:
    """Fetches draft state; recomputes advice when draft or forecast inputs change."""

    def __init__(self, client: Any, draft_id: str, username: str | None = None,
                 slot: int | None = None, top: int = 10,
                 recommend_fn: Callable[..., list] = recommend,
                 forecast_path: str | Path | None = None):
        self.client = client if isinstance(client, PollingClient) else PollingClient(client)
        self.draft_id = draft_id
        self.username = username
        self.slot = slot
        self.top = top
        self.recommend_fn = recommend_fn
        self.forecast_path = Path(forecast_path) if forecast_path is not None else Path(getattr(self.client, "cache_dir", DEFAULT_CACHE_DIR)) / "lock_in_forecasts.json"
        self.forecast_explicit = forecast_path is not None
        self.snapshot: dict | None = None
        self.error: str | None = None
        self._key: tuple | None = None
        self._recs: list = []

    @property
    def complete(self) -> bool:
        return bool(self.snapshot and self.snapshot["draft"]["status"] == "complete")

    def poll(self) -> bool:
        """One tick. Returns True when the advice was recomputed."""
        loading_forecasts = False
        try:
            state = build_state(self.client, self.draft_id, self.username, self.slot)
            forecasts = None
            forecast_bytes = b""
            if self.forecast_path.exists() or self.forecast_explicit:
                loading_forecasts = True
                forecast_bytes = self.forecast_path.read_bytes()
                forecasts = load_forecasts(self.forecast_path, season=state.league.season, scoring=state.league.scoring)
                if self.forecast_path.read_bytes() != forecast_bytes:
                    raise ValueError("Forecast file changed while loading; retrying")
                loading_forecasts = False
            # A pick count alone misses changed forecasts, scoring, ownership
            # or player projections. Bind advice to the full observed inputs.
            state_bytes = json.dumps(asdict(state), sort_keys=True, allow_nan=False,
                                     default=lambda value: sorted(value) if isinstance(value,(set,frozenset)) else str(value)).encode("utf-8")
            key = (hashlib.sha256(state_bytes).digest(), hashlib.sha256(forecast_bytes).digest())
            changed = key != self._key
            if changed:
                self._recs = (self.recommend_fn(state, self.top, forecasts=forecasts) if forecasts is not None
                              else self.recommend_fn(state, self.top))
            snapshot = build_snapshot(state, self._recs, self.client.last_draft, forecasts)
        except Exception as exc:  # network or data problems must not kill polling
            self.error = f"{type(exc).__name__}: {exc}"
            if self.snapshot is not None:
                self.snapshot = dict(self.snapshot, error=self.error)
                if loading_forecasts or isinstance(exc, ValueError):
                    self.snapshot["recommendations"] = []
                    self._key = None
            return False
        self._key = key
        self.error = None
        self.snapshot = snapshot
        return changed


def poll_loop(poller: Poller, interval: float, *,
              sleep: Callable[[float], Any] = time.sleep,
              max_iterations: int | None = None,
              on_update: Callable[[Poller, bool], Any] | None = None,
              should_stop: Callable[[], bool] = lambda: False,
              stop_when_complete: bool = False) -> int:
    """Poll until stopped. Returns the number of ticks performed."""
    ticks = 0
    while not should_stop():
        changed = poller.poll()
        ticks += 1
        if on_update is not None:
            on_update(poller, changed)
        if max_iterations is not None and ticks >= max_iterations:
            break
        if stop_when_complete and poller.complete:
            break
        sleep(interval)
    return ticks


# -- text rendering -----------------------------------------------------------
def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def render_text(snap: dict) -> str:
    d = snap["draft"]
    lines: list[str] = []
    title = d["name"] or f"Draft {d['draft_id']}"
    kind = d["mock_type"] or "league draft"
    lines.append(f"{title}  [{kind}]  {d['type']}, {d['teams']} teams x {d['rounds']} rounds")
    cur = snap["current_pick"]
    clock = "complete" if cur is None else f"pick {cur} (slot {snap['on_clock_slot']} on the clock)"
    lines.append(f"Status: {d['status']}  |  {snap['picks_made']} picks made  |  {clock}")
    if snap["our_slot"] is None:
        lines.append("Our slot: unknown (use --slot N or --user USERNAME)")
    else:
        nxt = ", ".join(str(n) for n in snap["our_next_picks"][:6]) or "none"
        lines.append(f"Our slot: {snap['our_slot']}  |  next picks: {nxt}")
    if snap["on_clock"]:
        lines.append(">>> YOU ARE ON THE CLOCK <<<")
    elif snap["picks_away"] is not None and 0 < snap["picks_away"] <= 2:
        lines.append(f"Your pick in {snap['picks_away']}")

    lines.append("")
    lines.append(f"Best lineup ({snap['lineup_total']:.1f} estimated Lock-In FP/week):")
    for r in snap["lineup"]:
        if r["name"] is None:
            lines.append(f"  {r['slot']:<5} -")
        else:
            lines.append(f"  {r['slot']:<5} {_clip(r['name'], 26):<26} "
                         f"{'/'.join(r['positions']):<8} {r['lock_in_weekly_points']:>5.1f}")
    if snap["bench"]:
        lines.append("Bench: " + ", ".join(
            f"{b['name']} ({'/'.join(b['positions'])})" for b in snap["bench"]))

    lines.append("")
    recs = snap["recommendations"]
    if not recs:
        lines.append("No recommendations available.")
    else:
        lines.append(f"{'#':>2}  {'Player':<24} {'Pos':<8} {'Lock/wk':>7} {'ADP':>5} {'Added/wk':>8}  Why")
        for r in recs:
            adp = "-" if r["adp"] is None else f"{r['adp']:.1f}"
            lines.append(
                f"{r['rank']:>2}  {_clip(r['name'], 24):<24} {'/'.join(r['positions']):<8} "
                f"{r['components']['lock_in_weekly_points']:>7.1f} {adp:>5} {r['score']:>+8.1f}  {r['explanation']}")
        lines.append(LEGEND)
    for w in snap["warnings"]:
        lines.append(f"warning: {w}")
    return "\n".join(lines)


# -- commands ---------------------------------------------------------------
def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def cmd_once(args: argparse.Namespace, client: Any, out: Any) -> int:
    poller = Poller(client, parse_draft_id(args.draft), args.user, args.slot, args.top,
                    forecast_path=args.lock_in_forecasts)
    poller.poll()
    if poller.snapshot is None or poller.error:
        print(f"error: could not load the draft ({poller.error})", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(poller.snapshot, ensure_ascii=False, indent=2), file=out)
    else:
        print(render_text(poller.snapshot), file=out)
    return 0


def cmd_watch(args: argparse.Namespace, client: Any, out: Any, sleep: Callable[[float], Any],
              max_iterations: int | None) -> int:
    poller = Poller(client, parse_draft_id(args.draft), args.user, args.slot, args.top,
                    forecast_path=args.lock_in_forecasts)
    state = {"printed_error": None}

    def on_update(p: Poller, changed: bool) -> None:
        if p.error:
            state["printed_error"] = p.error
            print(f"[{_stamp()}] warning: update failed ({p.error}); retrying", file=out, flush=True)
            return
        if changed:
            print(f"\n=== {_stamp()} " + "=" * 40, file=out)
            print(render_text(p.snapshot), file=out, flush=True)
        elif state["printed_error"]:
            print(f"[{_stamp()}] connection restored", file=out, flush=True)
        state["printed_error"] = None

    try:
        poll_loop(poller, args.interval, sleep=sleep, max_iterations=max_iterations,
                  on_update=on_update, stop_when_complete=True)
    except KeyboardInterrupt:
        print("\nStopped.", file=out)
        return 0
    if poller.complete:
        print("Draft complete.", file=out)
    return 0


def cmd_config(args: argparse.Namespace, out: Any) -> int:
    path = Path(args.cache_dir) / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if args.action == "get":
        if args.key is None:
            print(json.dumps(data, indent=2), file=out)
        else:
            print(data.get(args.key, ""), file=out)
        return 0
    if args.key not in CONFIG_KEYS:
        print(f"error: unknown key {args.key!r}; expected one of {', '.join(CONFIG_KEYS)}",
              file=sys.stderr)
        return 2
    if args.key == "draft_url" and args.value:
        parse_draft_id(args.value)  # ValueError -> clean error, nothing saved
    data[args.key] = args.value if args.value is not None else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sleeper_advisor",
                                     description="Sleeper NBA draft advisor (read-only).")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("draft", help="Sleeper draft URL or numeric draft id")
        p.add_argument("--user", metavar="USERNAME", help="your Sleeper username")
        p.add_argument("--slot", type=int, metavar="N", help="your draft slot if it cannot be detected")
        p.add_argument("--top", type=int, default=10, help="recommendations to show (default 10)")
        p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                       help=f"cache folder (default {DEFAULT_CACHE_DIR})")
        p.add_argument("--lock-in-forecasts", default=None, metavar="PATH",
                       help="optional season/scoring-bound weekly forecasts; otherwise reads lock_in_forecasts.json in the cache folder")

    once = sub.add_parser("once", help="print one snapshot and exit")
    common(once)
    once.add_argument("--json", action="store_true", help="print state and recommendations as JSON")

    watch = sub.add_parser("watch", help="poll the draft and print updates")
    common(watch)
    watch.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                       help="seconds between polls (default 3)")
    watch.add_argument("--iterations", type=int, default=None, help=argparse.SUPPRESS)

    serve = sub.add_parser("serve", help="run the local web panel")
    common(serve)
    serve.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                       help="seconds between polls (default 3)")
    serve.add_argument("--host", default="127.0.0.1", help="loopback only (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    serve.add_argument("--open-browser", action="store_true", help="open the panel in a browser")

    config = sub.add_parser("config", help="read or write the saved draft URL and username")
    config.add_argument("action", choices=("get", "set"))
    config.add_argument("key", nargs="?", help=f"one of: {', '.join(CONFIG_KEYS)}")
    config.add_argument("value", nargs="?")
    config.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    return parser


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Sequence[str] | None = None, *, client: Any = None, out: Any = None,
         sleep: Callable[[float], Any] = time.sleep) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = out if out is not None else sys.stdout
    if args.command == "config":
        if args.action == "set" and args.key is None:
            parser.error("config set needs a key and a value")
        try:
            return cmd_config(args, out)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if getattr(args, "top", 1) < 1:
        parser.error("--top must be at least 1")
    if client is None:
        client = SleeperClient(cache_dir=args.cache_dir)
    try:
        if args.command == "once":
            return cmd_once(args, client, out)
        if args.command == "watch":
            return cmd_watch(args, client, out, sleep, args.iterations)
        if args.command == "serve":
            from .panel import cmd_serve
            return cmd_serve(args, client, out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    parser.error(f"unknown command {args.command}")
    return 2
