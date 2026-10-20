# Eight-team Lock-In backtest on 2025-26 games

October 19, 2026. Strategy guidance and the results table: [EIGHT_TEAM_LOCK_IN_STRATEGY.md](../../../docs/EIGHT_TEAM_LOCK_IN_STRATEGY.md#backtest-on-the-2025-26-season).

A replay of real 2025-26 NBA regular-season games under the league's custom scoring (blocks −10, triple-double −200) and Lock-In rules, for eight-team, 14-round snake drafts. Unlike `studies/lock-in/2026-10-17`, nothing here is synthetic.

## Files

* `fetch.py DATA_DIR` downloads the public Sleeper inputs (about 52 MB, not committed): 2024-25 and 2025-26 season stats, 2025 projections (used only for `adp_std`), the player list and one file per game day from 2025-10-21 to 2026-04-13.
* `backtest.py REPO DATA_DIR OUT_DIR [--seeds N] [--budget S] [--strategies ...] [--room adp|scoring_aware]` runs the drafts and the Lock-In season replay.
* `diag.py REPO DATA_DIR` compares rank correlations of forecasts and ADP with realized results.
* `data/rows_*.json` holds one row per draft: seat, seed, strategy, season points, all-play rate, rank, triple-double games locked and the drafted roster. `data/summary_*.txt` and `data/forecast_vs_adp_correlation.txt` hold the printed summaries.

## Reproducing

The results were produced against `sleeper_advisor` at commit `227d6a2`. That version contained the triple-double passable-outcome model (`lock_in._bonus_outcomes`), which this study evaluated and which was reverted afterwards. `backtest.py` patches that function for the averaged variants, so run it against that commit:

```
git worktree add ../fbl-draft-227d6a2 227d6a2
python -I -X utf8 fetch.py DATA_DIR
python -I -X utf8 backtest.py ../fbl-draft-227d6a2 DATA_DIR OUT --seeds 6 --budget 1.0 --room adp \
  --strategies adp,fp_per_game_out_aware,advisor,advisor_out_aware,advisor_averaged_bonus_out_aware
```

Repeat with `--room scoring_aware`. Each room takes about 25 minutes. The advisor's time budget makes runs slightly nondeterministic.

## Design notes

* **Information discipline:** draft forecasts are 2024-25 per-game averages, limited to the keys in Sleeper's live projections (no `pf`, `tf`, `ff`, `oreb`). Sleeper's 2025 projection file was last modified in June–July 2026, after the season, so only its ADP field is used. That ADP may also have been revised; this is not verified.
* **Player pool:** players with at least 10 games in 2024-25. Rookies are excluded for every team.
* **Opponents:** rank by ADP (`adp` room) or by per-game FP under this scoring (`scoring_aware` room), plus Gaussian noise of 2 + 12% of rank, with a lineup-feasibility check.
* **Out-aware strategies:** the draft never offers six players whose long-term injuries were public before October 2025 (Haliburton, Lillard, Tatum, Dejounte Murray, Irving, VanVleet). Opponents can still draft them.
* **Season replay:** before each week, the manager starts the best lineup by forecast weekly value and benches anyone who missed all of the previous week. A starter's played game is locked when its score is at least the forecast value of his remaining games that week. The final game is forced, and a DNP scores 0. The lock rule used the passable-triple-double forecast for every team in every strategy.
* **Not modelled:** waivers, trades and IR moves.
