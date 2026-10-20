"""Rank correlation of 2024-25 forecasts and 2025 ADP with realized 2025-26 results.

Usage: python -I -X utf8 diag.py REPO DATA_DIR
"""
import glob
import json
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, sys.argv[1])
from sleeper_advisor.scoring import fantasy_points  # noqa: E402

DATA = sys.argv[2]
S = {"fgm": 3, "fga": -1, "fgmi": 5, "ftm": 1, "fta": 0, "ftmi": 12, "tpm": 10, "tpa": -2,
     "tpmi": 6, "dd": 35, "td": -200, "tf": 30, "ff": 75, "pf": 5, "pts": 0.25, "reb": 1,
     "oreb": 7, "dreb": -2, "ast": 4, "stl": 15, "blk": -10, "to": 8}
LIVE = ("pts", "reb", "ast", "stl", "blk", "to", "tpm", "tpa", "tpmi", "fgm", "fga", "fgmi",
        "ftm", "fta", "ftmi", "dreb", "dd", "td", "sp")


def load(name):
    return json.load(open(f"{DATA}/{name}", encoding="utf-8"))


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0] * len(values)
    for r, i in enumerate(order):
        out[i] = r
    return out


def spearman(a, b):
    return statistics.correlation(ranks(a), ranks(b))


adp = {r["player_id"]: r["stats"]["adp_std"] for r in load("proj_2025.json")
       if (r.get("stats") or {}).get("adp_std")}
real, gp = defaultdict(float), defaultdict(int)
for f in glob.glob(f"{DATA}/dates/*.json"):
    for r in json.load(open(f, encoding="utf-8")):
        s = r.get("stats") or {}
        if s.get("sp"):
            real[r["player_id"]] += fantasy_points(s, S)[0]
            gp[r["player_id"]] += 1

rows = []
for r in load("season_2024.json"):
    s, pid = r.get("stats") or {}, r["player_id"]
    games = s.get("gp") or 0
    if games < 10 or adp.get(pid, 999) > 150:
        continue
    forecast = fantasy_points({k: float(s.get(k) or 0) / games for k in LIVE}, S)[0]
    rows.append((forecast, -adp[pid], real[pid], gp[pid], real[pid] / max(1, gp[pid])))

fc, ad, total, played, per_game = (list(c) for c in zip(*rows))
print("players (ADP <= 150, 2024-25 gp >= 10):", len(rows))
print(f"realized season total  ~ forecast {spearman(fc, total):.3f}  ~ ADP {spearman(ad, total):.3f}")
print(f"realized FP per game   ~ forecast {spearman(fc, per_game):.3f}  ~ ADP {spearman(ad, per_game):.3f}")
print(f"games played           ~ forecast {spearman(fc, played):.3f}  ~ ADP {spearman(ad, played):.3f}")
rf, ra = ranks(fc), ranks(ad)
for w in (0.25, 0.5, 0.75):
    blend = [w * a + (1 - w) * b for a, b in zip(rf, ra)]
    print(f"rank blend {w:.2f} forecast + {1 - w:.2f} ADP ~ season total {spearman(blend, total):.3f}")
