# Eight-team Sleeper Lock-In strategy ("Basketball is optional")

October 19, 2026 (America/Chicago). Replaces the [six-team profile](SIX_TEAM_LOCK_IN_STRATEGY.md) for the eight-team league with the custom scoring below. Machine-readable settings: [eight_team_lock_in.json](../profiles/eight_team_lock_in.json).

**Lock-In mode is confirmed** (user, 2026-10-19). **Roster assumed, not confirmed:** the six-team roster (PG, SG, G, SF, PF, F, C, UTIL, UTIL; five bench; one IR), giving a 14-round, 112-pick snake. The advisor reads the real team count, roster slots and scoring from the Sleeper draft, so the live recommendations do not depend on this assumption; only this document does.

## Scoring

| Sleeper key | Points | Sleeper key | Points | Sleeper key | Points |
| --- | ---: | --- | ---: | --- | ---: |
| `fgm` | +3 | `ftm` | +1 | `tpm` | +10 |
| `fga` | −1 | `fta` | 0 | `tpa` | −2 |
| `fgmi` | +5 | `ftmi` | +12 | `tpmi` | +6 |
| `dd` | +35 | `td` | −200 | `pf` | +5 |
| `tf` | +30 | `ff` | +75 | `pts` | +0.25 |
| `reb` | +1 | `oreb` | +7 | `dreb` | −2 |
| `ast` | +4 | `stl` | +15 | `blk` | −10 |
| `to` | +8 | | | | |

Net value of one event once every key it triggers is added (a 3 is also a field goal; a rebound is also `oreb` or `dreb`):

| Event | Net | Event | Net |
| --- | ---: | --- | ---: |
| Made 2 | +2.5 | Missed 2 | **+4** |
| Made 3 | +10.75 | Missed 3 | +8 |
| Made FT | +1.25 | Missed FT | **+12** |
| Offensive rebound | +8 | Defensive rebound | −1 |
| Steal | +15 | Turnover | +8 |
| Assist | +4 | Block | **−10** |
| Personal foul | +5 | Technical / flagrant | +30 / +75 |
| Double-double | +35 | Triple-double (DD + TD) | **−165** |

## What the scoring rewards

Scored against Sleeper's 2026 season projections (403 players with real projections; default Lock-In assumptions):

* **Shot volume, not efficiency.** A miss is worth more than a made 2. Field-goal misses are the single largest source of points (~40/game for a top-96 player), followed by 3PT misses and makes. High-usage, high-3PA guards dominate.
* **Ball-dominant guards.** Assists (+4) and turnovers (+8) both pay. Of the top 72 players by weekly Lock-In value, about 40 are PG-eligible and 36 SG-eligible; only about 10 are C-eligible.
* **Steals are gold, blocks are poison.** +15 per steal, −10 per block. Wembanyama falls from ADP 1 to about 30th; Holmgren, Mobley and Davis fall out of the top 70.
* **Offensive rebounders over defensive rebounders.** Glass-cleaning defensive centers lose points on every board (Towns ~54th).
* **Bad free-throw shooters with big FTA.** A missed FT (+12) is worth nearly ten makes. Giannis stays top 8 largely through `ftmi`.
* **Triple-doubles cost a full −200 (−165 net of the DD bonus).** Lock-In does not make this avoidable. You are forced into a triple-double game whenever it is the player's last game of the week, and passing on one does not reliably leave a better game. Jokić projects a triple-double in about 49% of games and is worth about 143 FP/game (around 82nd). Do not draft him at ADP 2. Jalen Johnson and Giddey (16–17% triple-double rate) carry the same risk on a smaller scale. The backtest below tested a model that treated triple-doubles as passable. It drafted more of these players, locked about three times as many triple-double games and scored worse, so it was reverted.

## Draft guidance

1. **Rounds 1–2:** Dončić, Trae Young, LaMelo Ball, Cunningham, Mitchell, Edwards, Giannis, Curry, Banchero. Trae (ADP ~22) and LaMelo (ADP ~33) are the clearest early edges. Tatum ranks 7th on per-game projection; check his health before taking him that high.
2. **Fade ADP-driven bigs and triple-double players:** Jokić, Wembanyama, Towns, Şengün, Davis, Mobley, Holmgren, Amen Thompson and Scottie Barnes all rank far below ADP. Let opponents take them.
3. **Treat a huge ADP gap as availability risk, not free value.** This is the backtest's main lesson. Per-game projections pick the right *type* of player, but season totals depend on games played, and ADP knows things about injuries and roles that per-game projections miss. Sleeper's live projections are per game (`gp = 1`) even for players listed `Out` for the season, and the advisor does not use injury labels. Before taking any player whose ADP is far later than the pick, check the injury note and recent news. This year's main examples are Kyrie Irving (ADP ~111), Damian Lillard (~147) and Dejounte Murray (~113).
4. **Moderate-gap value targets:** Garland, Herro, Michael Porter Jr., Keyonte George, Stephon Castle, Coby White, Jalen Green, Kon Knueppel, Immanuel Quickley and Julius Randle. They are high-volume guards and forwards whose ADP is later than their value, with no single long-term injury explaining the gap.
5. **Still fill C.** With one C slot per team and few good C-eligible players, take a guard-friendly C (Adebayo, Siakam, Sabonis, Randle) before the C pool collapses. The advisor's lineup-fit model handles this from the actual roster; there is no position quota.
6. **Tiebreaks the projections cannot see:** players prone to technical and flagrant fouls (+30 / +75 per event; not projected, counted as 0), and heavy foulers relative to their minutes.

Weekly Lock-In value falls gradually: about 225 at the 8th player, 199 at 24th, 180 at 48th, 159 at 72nd and 139 at 112th.

## Backtest on the 2025-26 season

Study files: [studies/eight-team-backtest/2026-10-19](../studies/eight-team-backtest/2026-10-19/README.md).

**Setup.** This is a replay on real data, not a synthetic world. Each run is an eight-team, 14-round snake draft with the assumed roster. Our seat drafts with the strategy under test. Seven opponents draft by noisy rank, with a check that their lineup can still be filled.

* **Forecasts:** draft-time forecasts are 2024-25 per-game averages, restricted to the stat keys Sleeper's live projections carry, so the advisor's `oreb` and `pf` derivations get exercised. Sleeper's 2025 projections were revised after the season, so they are not used.
* **ADP:** opponent ADP is Sleeper's 2025 `adp_std`.
* **Season scoring:** each roster is scored on every 2025-26 regular-season game (25 weeks, 166 game days) under Lock-In rules. Each week the manager starts the best forecast lineup and benches anyone who missed all of the previous week. A starter's game is locked when it beats the forecast value of his remaining games. The last game is forced, and a DNP scores 0.
* **Not modelled:** waivers, trades and IR moves. The in-season lock rule used the passable-triple-double forecast for every team (see the study README).
* **Sample:** 48 drafts per strategy (every seat × 6 seeds). The advisor strategies run the production `recommend()` with a 1-second budget.

Six long-term injuries were public before an October 2025 draft: Haliburton, Lillard, Tatum, Dejounte Murray, Irving and VanVleet. The "out-aware" strategies don't see those players, as if a draft-day injury check had removed them.

Mean weekly points versus drafting by ADP in the same seat and seed (± 95% interval):

| Strategy | Room drafts by ADP | Room drafts by this scoring |
| --- | ---: | ---: |
| Draft by ADP (baseline) | 1512 /wk | 1490 /wk |
| Per-game FP, out-aware | −215 ± 32 | +24 ± 33 |
| Advisor, no injury check, triple-doubles passable | −223 ± 30 | +57 ± 28 |
| Advisor, out-aware, triple-doubles passable | −40 ± 27 | +32 ± 30 |
| **Advisor, out-aware, triple-doubles averaged (current)** | **−36 ± 27** | **+95 ± 33** |

For the current advisor, the all-play weekly win rate is 0.49 in the ADP room and 0.64 in the scoring-aware room (0.54 and 0.56 for ADP drafting). Its average finish is 4.75 of 8 in the ADP room and 2.46 of 8 in the scoring-aware room.

**What it shows.**

* The injury check is the largest single effect, worth about +185 weekly points in the ADP room. Skipping it filled rosters with players who barely played.
* Averaging triple-double penalties into every game did as well or better than treating them as passable in both rooms, so the averaged model stays.
* Against a room that also drafts for this scoring, the advisor wins clearly. Against a room drafting by ADP, it does not beat ADP drafting when it uses these forecasts. The forecast predicts realized 2025-26 FP per game better than ADP does (rank correlation 0.70 vs 0.61), but ADP predicts season totals better (0.42 vs 0.35), because in a fixed-roster season availability dominates.

**Limits.** Prior-season averages are a weaker forecast than Sleeper's live preseason projections, and they miss rookies, who are excluded from the pool. Positions are current, not 2025 preseason. With no waivers, a missed game costs more than it would in the real league, which favors ADP. The injury list is a hand-coded record of public preseason news. Only one season was tested.

## Advisor changes for this scoring

* `oreb` is derived as `reb − dreb` (Sleeper projects `dreb` but not `oreb`). Missing shot components are rebuilt from the identities `fga = fgm + fgmi`, `fta = ftm + ftmi`, `tpa = tpm + tpmi`.
* `pf` is not projected. It is estimated at 0.08 fouls per minute from projected minutes (about 2.4 fouls in 30 minutes, ~12 FP/game). This is a labelled league-average assumption and favors high-minute players.
* `tf` and `ff` are not projected and count as 0 with a warning.
* Double-double and triple-double rates are averaged into every game, as in the original advisor.
* Not yet changed: the advisor still ignores injury labels when valuing players. Check them yourself at draft time (guidance item 3).
