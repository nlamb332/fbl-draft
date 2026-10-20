"""Historical Lock-In backtest of draft strategies on real 2025-26 NBA games.

Usage: python -I -X utf8 backtest.py REPO DATA_DIR OUT_DIR --scoring league_scoring.json [--seeds N] [--budget S]

Information discipline: draft-time forecasts are 2024-25 per-game averages,
restricted to the stat keys Sleeper's live projections carry (no pf, tf, ff,
oreb), so the live advisor's derivations are exercised.  Opponents draft by
2025 Sleeper ADP (adp_std) with noise.  Realized 2025-26 games are only used
for scoring locks after each game, never for draft or lock decisions.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("repo"); ap.add_argument("data"); ap.add_argument("out")
ap.add_argument("--scoring", required=True, help="JSON map of Sleeper scoring keys to points")
ap.add_argument("--seeds", type=int, default=6)
ap.add_argument("--budget", type=float, default=1.0)
ap.add_argument("--strategies", default="adp,fp_per_game_out_aware,advisor,advisor_out_aware")
ap.add_argument("--min-gp", type=int, default=10)
ap.add_argument("--room", default="adp", choices=["adp", "scoring_aware"])
args = ap.parse_args()
sys.path.insert(0, args.repo)

from sleeper_advisor import lock_in as LI  # noqa: E402
from sleeper_advisor.advisor import recommend  # noqa: E402
from sleeper_advisor.lineup import best_lineup  # noqa: E402
from sleeper_advisor.model import DraftState, SleeperLeague, SleeperPlayer  # noqa: E402
from sleeper_advisor.scoring import fantasy_points  # noqa: E402

SCORING = {k: float(v) for k, v in json.loads(Path(args.scoring).read_text(encoding="utf-8")).items()}
SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL")
BENCH = 5
TEAMS = 8
ROUNDS = len(SLOTS) + BENCH
LIVE_KEYS = ("pts", "reb", "ast", "stl", "blk", "to", "tpm", "tpa", "tpmi", "fgm", "fga",
             "fgmi", "ftm", "fta", "ftmi", "dreb", "dd", "td", "sp")
data = Path(args.data)
out = Path(args.out); out.mkdir(parents=True, exist_ok=True)


def load(name):
    return json.loads((data / name).read_text(encoding="utf-8"))


# -- draft-time player pool ------------------------------------------------
players_meta = load("players.json")
adp = {r["player_id"]: r["stats"]["adp_std"] for r in load("proj_2025.json")
       if (r.get("stats") or {}).get("adp_std")}
pool: dict[str, SleeperPlayer] = {}
for r in load("season_2024.json"):
    s = r.get("stats") or {}
    gp = s.get("gp") or 0
    pid = r["player_id"]
    meta = players_meta.get(pid) or {}
    positions = tuple(p for p in (meta.get("fantasy_positions") or []) if p in ("PG", "SG", "SF", "PF", "C"))
    if gp < 10 or not positions:
        continue
    stats = {k: float(s.get(k) or 0.0) / gp for k in LIVE_KEYS}
    pool[pid] = SleeperPlayer(pid, f"{meta.get('first_name','')} {meta.get('last_name','')}".strip(),
                              positions, meta.get("team"), None, stats, adp.get(pid), True)

league = SleeperLeague("bt", None, "2025", "snake", None, "pre_draft", TEAMS, ROUNDS,
                       SLOTS, BENCH, None, SCORING)
# Long-term injuries public before an October 2025 draft (a draft room would
# see Sleeper injury_status "Out"): torn Achilles for Haliburton, Lillard,
# Tatum and Dejounte Murray; torn ACL for Irving and VanVleet.
PRESEASON_OUT_NAMES = {"Tyrese Haliburton", "Damian Lillard", "Jayson Tatum", "Kyrie Irving",
                       "Dejounte Murray", "Fred VanVleet"}
preseason_out = {pid for pid, p in pool.items() if p.name in PRESEASON_OUT_NAMES}
assert len(preseason_out) == len(PRESEASON_OUT_NAMES), sorted(p.name for p in pool.values() if p.name in PRESEASON_OUT_NAMES)
forecast = {pid: LI.estimate_player(p, SCORING) for pid, p in pool.items()}
fp_game = {pid: f.fp_per_game for pid, f in forecast.items()}

# -- realized season ------------------------------------------------------
team_games: dict[tuple[str, int], list[str]] = defaultdict(set)
player_game: dict[tuple[str, str], tuple[float, dict]] = {}
player_team_week: dict[tuple[str, int], str] = {}
for f in sorted(glob.glob(str(data / "dates" / "*.json"))):
    for r in json.loads(Path(f).read_text(encoding="utf-8")):
        team, week, date, pid = r.get("team"), r.get("week"), r.get("date"), r.get("player_id")
        if not team or not week:
            continue
        team_games[(team, week)].add(date)
        player_team_week[(pid, week)] = team
        s = r.get("stats") or {}
        if s.get("sp"):
            player_game[(pid, date)] = (fantasy_points(s, SCORING)[0], s)
team_games = {k: sorted(v) for k, v in team_games.items()}
weeks = sorted({w for _, w in team_games})


def week_games(pid, week):
    """Scheduled dates for the player's team this week (team from game rows)."""
    team = player_team_week.get((pid, week))
    return team_games.get((team, week), []) if team else []


# -- draft simulation -------------------------------------------------------
def feasible(roster, cand, picks_left_after):
    entries = [(p, pool[p].positions, 1.0) for p in (*roster, cand)]
    filled, _ = best_lineup(entries, SLOTS)
    return filled + picks_left_after >= len(SLOTS)


def greedy(order, roster, available, picks_left_after):
    for pid in order:
        if pid in available and feasible(roster, pid, picks_left_after):
            return pid
    return next(p for p in order if p in available)


def snake_slot(pick):
    rnd, idx = divmod(pick - 1, TEAMS)
    return idx + 1 if rnd % 2 == 0 else TEAMS - idx


def draft(our_seat, strategy, seed):
    rng = random.Random(seed * 1000 + our_seat)
    rosters = {s: [] for s in range(1, TEAMS + 1)}
    available = set(pool)
    by_adp = sorted(pool, key=lambda p: (pool[p].adp or 999.0, -fp_game[p]))
    by_fp = sorted(pool, key=lambda p: -fp_game[p])
    out_aware = strategy.endswith("_out_aware")
    if out_aware:
        by_fp = [p for p in by_fp if p not in preseason_out]
    picks_log = []
    for pick in range(1, TEAMS * ROUNDS + 1):
        seat = snake_slot(pick)
        roster = rosters[seat]
        left_after = ROUNDS - len(roster) - 1
        if seat != our_seat:
            base = by_adp if args.room == "adp" else by_fp
            rank = {p: i for i, p in enumerate(base)}
            noisy = sorted((p for p in base[:60 + pick] if p in available),
                           key=lambda p: rank[p] + rng.gauss(0, 2 + 0.12 * rank[p]))
            choice = greedy(noisy, roster, available, left_after)
        elif strategy == "adp":
            choice = greedy(by_adp, roster, available, left_after)
        elif strategy.startswith("fp_per_game"):
            choice = greedy(by_fp, roster, available, left_after)
        else:
            our_next = tuple(p for p in range(pick, TEAMS * ROUNDS + 1) if snake_slot(p) == our_seat)
            state = DraftState(league, our_seat, pick, our_seat, tuple(picks_log),
                               frozenset(set(pool) - available), tuple(pool[p] for p in roster),
                               tuple(pool[p] for p in available if not (out_aware and p in preseason_out)),
                               our_next, (), "matched")
            recs = recommend(state, top_n=1, budget=args.budget)
            choice = recs[0].player_id if recs else greedy(by_fp, roster, available, left_after)
        roster.append(choice)
        available.discard(choice)
        picks_log.append({"pick_no": pick, "player_id": choice, "draft_slot": seat})
    return rosters


# -- Lock-In season evaluation ----------------------------------------------
_cont_cache = {}


def continuation(pid, remaining):
    """Forecast value of the remaining scheduled games (pre-draft forecast only)."""
    key = (pid, remaining)
    if key not in _cont_cache:
        games = LI._default_games(fp_game[pid], LI.ForecastDefaults(scheduled_games_per_week=remaining))
        _cont_cache[key] = LI.forecast_stopping_value(games)
    return _cont_cache[key]


def lock_week(pid, week):
    dates = week_games(pid, week)
    for i, date in enumerate(dates):
        played = player_game.get((pid, date))
        last = i == len(dates) - 1
        if played is None:
            if last:
                return 0.0, None
            continue
        score, stats = played
        if last or score >= continuation(pid, len(dates) - 1 - i):
            return score, stats
    return 0.0, None


def season(roster):
    totals, tds_locked, weekly = 0.0, 0, []
    for week in weeks:
        prev_out = set()
        if week > weeks[0]:
            for pid in roster:  # a manager benches a player who missed all of last week
                dates = week_games(pid, week - 1)
                if dates and not any((pid, d) in player_game for d in dates):
                    prev_out.add(pid)
        entries = [(pid, pool[pid].positions,
                    0.0 if pid in prev_out else continuation(pid, len(week_games(pid, week))))
                   for pid in roster]
        _, lineup = best_lineup(entries, SLOTS)
        pts = 0.0
        for pid in lineup:
            score, stats = lock_week(pid, week)
            pts += score
            tds_locked += bool(stats and stats.get("td"))
        weekly.append(pts)
        totals += pts
    return totals, weekly, tds_locked


strategies = args.strategies.split(",")
rows = []
t0 = time.time()
for seed in range(args.seeds):
    for seat in range(1, TEAMS + 1):
        for strategy in strategies:
            rosters = draft(seat, strategy, seed)
            results = {s: season(r) for s, r in rosters.items()}
            ours, weekly, tds = results[seat]
            opp = [results[s] for s in results if s != seat]
            all_play = statistics.mean(
                sum(w > o[1][i] for o in opp) / len(opp) for i, w in enumerate(weekly))
            rank = 1 + sum(o[0] > ours for o in opp)
            rows.append({"seed": seed, "seat": seat, "strategy": strategy, "season_points": ours,
                         "weekly_mean": ours / len(weeks), "opp_mean": statistics.mean(o[0] for o in opp),
                         "all_play_win": all_play, "rank": rank, "td_games_locked": tds,
                         "roster": [pool[p].name for p in rosters[seat]]})
    print(f"seed {seed} done {time.time()-t0:.0f}s", flush=True)

(out / f"rows_{args.room}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"room={args.room} weeks={len(weeks)} pool={len(pool)} drafts per strategy={args.seeds*TEAMS}")
base = {(r["seed"], r["seat"]): r for r in rows if r["strategy"] == "adp"}
for strategy in strategies:
    rs = [r for r in rows if r["strategy"] == strategy]
    diffs = [r["season_points"] - base[(r["seed"], r["seat"])]["season_points"] for r in rs]
    md = statistics.mean(diffs); se = statistics.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) > 1 else 0
    print(f"{strategy:24} wk {statistics.mean(r['weekly_mean'] for r in rs):7.1f}  "
          f"vs-ADP {md/len(weeks):+6.1f}/wk (±{1.96*se/len(weeks):.1f})  "
          f"all-play {statistics.mean(r['all_play_win'] for r in rs):.3f}  "
          f"rank {statistics.mean(r['rank'] for r in rs):.2f}  1st {sum(r['rank']==1 for r in rs)/len(rs):.0%}  "
          f"TD locked/season {statistics.mean(r['td_games_locked'] for r in rs):.2f}")
