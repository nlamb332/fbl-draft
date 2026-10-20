# Eight-team Lock-In backtest on 2025-26 games (league scoring)

October 20, 2026. Strategy guidance and the results table: [EIGHT_TEAM_LOCK_IN_STRATEGY.md](../../../docs/EIGHT_TEAM_LOCK_IN_STRATEGY.md#backtest-on-the-2025-26-season-league-scoring).

This reruns [the first backtest](../2026-10-19/README.md) with the league's actual Sleeper scoring (`league_scoring.json`, read from the league API) in place of the list first supplied. The main differences are free-throw attempts +12, defensive rebounds −8, blocks −5, points +0.5, and the 40/50-point, 20-rebound and 15-assist penalties. It runs against the current advisor code (commit 46e38c5 or later), where double- and triple-double rates are averaged into every game.

## Files

* `fetch.py DATA_DIR` downloads the public Sleeper inputs (about 52 MB, not committed).
* `backtest.py REPO DATA_DIR OUT_DIR --scoring league_scoring.json [--seeds N] [--budget S] [--strategies ...] [--room adp|scoring_aware]` runs the drafts and the Lock-In season replay.
* `diag.py REPO DATA_DIR league_scoring.json` compares rank correlations of forecasts and ADP with realized results.
* `data/rows_*.json` holds one row per draft (seat, seed, strategy, season points, all-play rate, rank, triple-double games locked, roster). `data/summary_*.txt` and `data/forecast_vs_adp_correlation.txt` hold the printed summaries.

## Reproducing

```
python -I -X utf8 fetch.py DATA_DIR
python -I -X utf8 backtest.py REPO DATA_DIR OUT --scoring league_scoring.json --seeds 6 --budget 1.0 --room adp
python -I -X utf8 backtest.py REPO DATA_DIR OUT --scoring league_scoring.json --seeds 6 --budget 1.0 --room scoring_aware
```

Each room takes about 20 minutes. The advisor's time budget makes runs slightly nondeterministic.

## Design notes

These match the first study, except that the season replay's lock rule now uses the averaged forecast:

* **Forecasts:** 2024-25 per-game averages, limited to the keys in Sleeper's live projections. Only ADP is taken from the 2025 projection file, which was revised after the season.
* **Player pool:** players with at least 10 games in 2024-25; rookies are excluded for every team. Team-total rows in the game logs never enter the pool.
* **Opponents:** noisy ADP or noisy per-game FP rank, with a lineup-feasibility check.
* **Injury-aware strategies** never see six players whose long-term injuries were public before October 2025.
* **Weekly lineups:** the manager starts the best forecast lineup and benches anyone who missed all of the previous week. A game is locked when it beats the forecast value of that player's remaining games; the last game is forced.
* **Not modelled:** waivers, trades and IR moves.

## Diagnosis of the ADP-room result

Among the injury-aware advisor's 48 drafts in the ADP room, its players averaged 188 realized points per game played, against 178 for ADP drafting. But they averaged 52 games played against 60, and in a no-waiver season the missed games outweighed the per-game edge. Its most frequent early picks were Trae Young (15 games), LaMelo Ball, Harden, Embiid (38 games), De'Aaron Fox, Cam Thomas (forecast 232 per game, realized 140), Zion Williamson and Ja Morant (20 games).
