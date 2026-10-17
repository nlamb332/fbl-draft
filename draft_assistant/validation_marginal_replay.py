"""Replay the real-catalog board of practice room ``uninterrupted-1000000007``.

For each of our ten turns the board is rebuilt from the sanitized draft
history (pool = ESPN catalog minus players already picked, roster = our actual
earlier picks) and ``marginal-lineup-v1`` ranks it with explanations.  A second
pass lets the policy make all ten of our picks while opponents keep their actual
picks (a conflicting opponent pick falls to the best remaining draft rank).  Both
rosters get the captured opening-week schedule review, to compare usable starts
with the actual 25 of 31 scheduled player-games.

Nothing here touches a browser; it is a replay of a checked-in artifact.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .contracts import DraftObservation, LeagueConfig, LivePlayer, PickEvent, RoomIdentity
from .marginal_lineup import DEFAULT_PARAMS, MarginalParams, rank_marginal
from .utilization_review import analyze_roster, build_schedule_matrix
from .validation_utilization_release import DATES, GAMES, _load_catalog, _team_games
from .valuation import rank_players

ROOT = Path(__file__).resolve().parent.parent
ROOM_FILE = Path(__file__).resolve().parent / "validation" / "browser-recovery-2026-10-11" / "uninterrupted-1000000007.json"
CATALOG = ROOT / "mock_draft" / "data" / "players_2026-27_espn_projection.csv"
OUT = Path(__file__).resolve().parent / "validation" / "marginal-lineup-v1"


def _live_catalog() -> dict[str, LivePlayer]:
    players: dict[str, LivePlayer] = {}
    with CATALOG.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            stats = {key: float(value) for key, value in json.loads(raw["stats"]).items()}
            players[raw["name"]] = LivePlayer(
                player_id=raw["player_id"],
                name=raw["name"],
                eligibility=tuple(raw["eligibility"].split("|")),
                stats=stats,
                availability=float(raw["projected_availability"]),
                draft_rank=float(raw["market_rank"]),
                nba_team=raw["team"] or None,
                missing_stats=("EJ", "PF", "TF", "DD", "TD", "QD"),
                source=raw["source_label"],
            )
    return players


def _observation(config: LeagueConfig, order: tuple[str, ...], pick: int, history: list[PickEvent], roster: list[LivePlayer], pool: list[LivePlayer]) -> DraftObservation:
    return DraftObservation(
        session_id="replay-uninterrupted-1000000007",
        room=RoomIdentity("1000000000", "2027", "15", True),
        observed_at="2026-10-11T15:16:04.271+00:00",
        revision=pick,
        phase="draft",
        current_pick=pick,
        on_clock_team=config.our_team,
        pick_order=order,
        history=tuple(history),
        roster=tuple(roster),
        available_players=tuple(pool),
        available_pool_complete=True,
        history_complete=True,
    )


def _top(result_candidates, limit: int = 5) -> list[dict[str, Any]]:
    rows = []
    for item in result_candidates[:limit]:
        row = {"name": item.name, "score": round(item.score, 2), "explanation": item.explanation}
        for key in ("raw_weekly_value", "replacement_level_fp_per_game", "marginal_lineup_weekly", "c_only", "foundation_rank"):
            if key in item.components:
                row[key] = round(item.components[key], 2)
        rows.append(row)
    return rows


def _opening_week(names: list[str]) -> dict[str, Any]:
    catalog = _load_catalog()
    by_name = {item["name"]: item for item in catalog}
    roster = [dict(by_name[name]) for name in names]
    schedule = build_schedule_matrix(
        roster, dates=DATES, team_games=_team_games(),
        source="NBA 2026-27 Regular Season Schedule By Date PDF (as of 2026-08-13)", captured_at="2026-10-11",
    )
    values = {item["player_id"]: item["value"] for item in roster}
    report = analyze_roster(roster, schedule=schedule.available, values=values)
    slots = 6 * report.days_analyzed
    starts = round((report.active_starter_rate or 0.0) * slots)
    games = round(starts / report.active_starter_rate_of_scheduled_games) if report.active_starter_rate_of_scheduled_games else None
    return {
        "players": names,
        "usable_starts": starts,
        "lineup_slots": slots,
        "scheduled_player_games": games,
        "center_only_count": report.center_only_count,
        "eligibility_counts": dict(report.eligibility_counts),
        "weekly_known_points": round(sum(day.score for day in report.daily), 1),
    }


def replay(params: MarginalParams = DEFAULT_PARAMS) -> dict[str, Any]:
    config = LeagueConfig()
    room = json.loads(ROOM_FILE.read_text(encoding="utf-8"))
    final = room["final_observation"]
    order = tuple(final["pick_order"])
    history = [PickEvent(**event) for event in final["history"]]
    catalog = _live_catalog()
    ours = [event for event in history if event.team == config.our_team]
    # Pass 1: our actual roster, one board per turn.
    turns = []
    for event in ours:
        before = [item for item in history if item.pick_number < event.pick_number]
        taken = {item.player_name for item in before}
        roster = [catalog[item.player_name] for item in before if item.team == config.our_team]
        pool = [player for name, player in catalog.items() if name not in taken]
        obs = _observation(config, order, event.pick_number, before, roster, pool)
        marginal = rank_marginal(obs, config, params)
        foundation = rank_players(obs, config)
        actual_rank = next((i for i, c in enumerate(marginal.candidates, start=1) if c.name == event.player_name), None)
        turns.append({
            "pick": event.pick_number,
            "actual_pick": event.player_name,
            "roster_before": [p.name for p in roster],
            "marginal_status": marginal.status,
            "marginal_seconds": round(marginal.elapsed_seconds, 4),
            "marginal_top5": _top(marginal.candidates),
            "foundation_top5": [{"name": c.name, "score": round(c.score, 2)} for c in foundation[:5]],
            "actual_pick_marginal_rank": actual_rank,
            "actual_pick_marginal_detail": next((_top([c], 1)[0] for c in marginal.candidates if c.name == event.player_name), None),
        })
    # Pass 2: the policy picks all ten turns; opponents keep their actual picks.
    taken_names: set[str] = set()
    mine: list[LivePlayer] = []
    path = []
    replayed: list[PickEvent] = []
    for event in history:
        if event.team == config.our_team:
            pool = [player for name, player in catalog.items() if name not in taken_names]
            obs = _observation(config, order, event.pick_number, replayed, list(mine), pool)
            result = rank_marginal(obs, config, params)
            choice = result.candidates[0]
            player = catalog[choice.name]
            path.append({"pick": event.pick_number, "policy_pick": choice.name, "actual_pick": event.player_name, "explanation": choice.explanation, "eligibility": list(player.eligibility)})
            mine.append(player)
            taken_names.add(choice.name)
            replayed.append(PickEvent(event.pick_number, event.team, player.player_id, player.name))
            continue
        name = event.player_name
        if name in taken_names:
            # The policy already holds this player; the opponent falls to the best draft rank left.
            name = min((n for n in catalog if n not in taken_names and n not in {e.player_name for e in history if e.pick_number > event.pick_number}), key=lambda n: (catalog[n].draft_rank, n))
        taken_names.add(name)
        replayed.append(PickEvent(event.pick_number, event.team, catalog[name].player_id, name))
    actual_names = [event.player_name for event in ours]
    policy_names = [item["policy_pick"] for item in path]
    return {
        "room": "uninterrupted-1000000007",
        "algorithm_version": "marginal-lineup-v1",
        "params": params.as_dict(),
        "catalog": "mock_draft/data/players_2026-27_espn_projection.csv (all 140 drafted players matched by name)",
        "limits": [
            "Pass 1 replays the real board at each of our ten turns with our actual earlier picks.",
            "Pass 2 keeps opponent picks fixed; a pick we took first is replaced by the best remaining ESPN rank, so opponent behaviour is not re-simulated.",
            "Opening-week starts use the captured NBA opening-week schedule and a schedule-only matrix (no draft-date health).",
            "PF/DD/TD are imputed (tagged) for the policy; the opening-week review scores known components only.",
        ],
        "turns": turns,
        "policy_path": path,
        "opening_week": {"actual": _opening_week(actual_names), "policy": _opening_week(policy_names)},
    }


def main() -> None:
    from .validation_marginal import OUT_DIR  # noqa: F401  (keeps one output location)

    frozen = OUT / "frozen_params.json"
    params = MarginalParams(**json.loads(frozen.read_text(encoding="utf-8"))["params"]) if frozen.exists() else DEFAULT_PARAMS
    payload = replay(params)
    (OUT / "replay_uninterrupted-1000000007.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    week = payload["opening_week"]
    print(json.dumps({"actual": [week["actual"]["usable_starts"], week["actual"]["scheduled_player_games"]], "policy": [week["policy"]["usable_starts"], week["policy"]["scheduled_player_games"]], "policy_picks": [p["policy_pick"] for p in payload["policy_path"]]}, indent=2))


if __name__ == "__main__":
    main()
