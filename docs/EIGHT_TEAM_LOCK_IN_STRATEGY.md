# Eight-team Sleeper Lock-In strategy ("Basketball is optional")

October 20, 2026 (America/Chicago). Applies to the eight-team league "THE FAMILY" and replaces the [six-team profile](SIX_TEAM_LOCK_IN_STRATEGY.md). Machine-readable settings: [eight_team_lock_in.json](../profiles/eight_team_lock_in.json).

**Confirmed from the Sleeper league API:** Lock-In mode (confirmed by the user), eight teams, 14 rounds, and roster PG, SG, G, SF, PF, F, C, UTIL, UTIL + 5 bench + 1 IR. The scoring below is the league's actual `scoring_settings`. It differs from the list first supplied for this league: free-throw *attempts* are +12 (misses 0), defensive rebounds −8, blocks −5, points +0.5, and there are game-threshold penalties. An earlier version of this document and its backtest used the supplied list; they are kept in [studies/eight-team-backtest/2026-10-19](../studies/eight-team-backtest/2026-10-19/README.md) for reference only.

## Scoring

| Sleeper key | Points | Sleeper key | Points | Sleeper key | Points |
| --- | ---: | --- | ---: | --- | ---: |
| `fgm` | +3 | `ftm` | +1 | `tpm` | +10 |
| `fga` | −1 | `fta` | **+12** | `tpa` | −2 |
| `fgmi` | +5 | `ftmi` | 0 | `tpmi` | +6 |
| `dd` | +35 | `td` | −200 | `pf` | +5 |
| `tf` | +30 | `ff` | +75 | `pts` | +0.5 |
| `reb` | +1 | `oreb` | +7 | `dreb` | **−8** |
| `ast` | +4 | `stl` | +15 | `blk` | −5 |
| `to` | +8 | `bonus_pt_40p` | −40 | `bonus_pt_50p` | −50 |
| `bonus_reb_20p` | −20 | `bonus_ast_15p` | −15 | | |

Net value of one event once every key it triggers is added (a 3 is also a field goal; a rebound is also `oreb` or `dreb`; a 50-point game also triggers the 40-point penalty):

| Event | Net | Event | Net |
| --- | ---: | --- | ---: |
| Made 2 | +3 | Missed 2 | +4 |
| Made 3 | +11.5 | Missed 3 | +8 |
| Made FT | **+13.5** | Missed FT | **+12** |
| Offensive rebound | +8 | Defensive rebound | **−7** |
| Steal | +15 | Turnover | +8 |
| Assist | +4 | Block | −5 |
| Personal foul | +5 | Technical / flagrant | +30 / +75 |
| Double-double | +35 | Triple-double (DD + TD) | **−165** |
| 40-point game | −40 | 50-point game | −90 |
| 20-rebound game | −20 | 15-assist game | −15 |

## What the scoring rewards

Scored against Sleeper's 2026 season projections (403 players with real projections; default Lock-In assumptions):

* **Free-throw attempts above everything.** Every attempt is worth +12 or more, made or missed. FTA is the largest scoring source (~57 points per game for a top-96 player), ahead of field-goal misses (~40). Players who live at the line rise: Dončić, Trae Young, Booker, Gilgeous-Alexander, Giannis, Banchero, Reaves, Avdija, Zion and Embiid.
* **Shot volume, not efficiency.** A missed 2 is worth more than a made 2, and threes pay either way.
* **Defensive rebounds are the biggest penalty** (−7 net, about −35 per game for a top-96 player). Rebounding centers collapse: Sabonis ~82nd, Towns ~79th, Jokić ~86th, Gobert outside the top 200. Only 7 C-eligible players make the top 72.
* **Ball-dominant guards.** Assists (+4), turnovers (+8) and steals (+15) all pay. About 39 of the top 72 are PG-eligible and 38 SG-eligible.
* **Triple-doubles cost a full −200 (−165 net of the DD bonus).** Lock-In does not make this avoidable: you are forced into a triple-double game whenever it is the player's last game of the week. Jokić projects one in about 49% of games. Do not draft him at ADP 2.
* **Threshold penalties are not in the projections.** Sleeper does not project 40-point, 50-point, 20-rebound or 15-assist games, so the advisor counts them as 0 with a warning. They mainly dent elite scorers (Dončić, Gilgeous-Alexander, Edwards) in their best weeks; Lock-In lets you pass such a game only if a later game remains.

Weekly Lock-In value falls gradually: about 275 at the 8th player, 241 at 24th, 203 at 48th, 180 at 72nd and 147 at 112th.

## Draft guidance

1. **Rounds 1–2:** Dončić, Trae Young, Booker, Gilgeous-Alexander, Mitchell, Edwards, Cunningham, Giannis, Banchero, Maxey. Trae (ADP ~22), Booker (~19) and Banchero (~29) are the early edges over ADP, but see item 3: Trae and the other high-variance-availability guards are exactly where the backtest lost points.
2. **Fade ADP-driven bigs:** Jokić, Towns, Şengün, Davis, Mobley, Holmgren, Sabonis, Amen Thompson and Scottie Barnes rank far below ADP. Wembanyama (ADP 1) is about 25th.
3. **Price availability yourself.** The backtest's clearest result: the advisor's players scored more per game played than ADP picks, but missed more games, and in a no-waiver season that cost more than the per-game edge. Sleeper's live projections are per game (`gp = 1`), and the advisor ignores injury labels and injury history. When the advisor wants a player well ahead of ADP, ask why the market is lower; if the answer is health, take the safer player of similar value. This year that applies to Kyrie Irving (ADP ~111), Damian Lillard (~147), Dejounte Murray (~113), Embiid and Morant.
4. **Moderate-gap value targets:** Keyonte George, Coby White, Stephon Castle, Michael Porter Jr., Julius Randle, Jalen Green, Nickeil Alexander-Walker, Norman Powell, Immanuel Quickley, Garland and Herro.
5. **Back up your center.** With few useful C-eligible players, a C/PF bench player (Siakam, Randle, Boozer, Derik Queen, Aaron Gordon) is the only cover for the C slot when your starter misses a week. The advisor's absence model did not show this edge late in the 2026 draft (every candidate scored "+0.0 vs waiting"), so make that call yourself.
6. **Tiebreaks the projections cannot see:** technical- and flagrant-prone players (+30 / +75), heavy foulers, and players who draw contact.

## Backtest on the 2025-26 season (league scoring)

Study files: [studies/eight-team-backtest/2026-10-19-league-scoring](../studies/eight-team-backtest/2026-10-19-league-scoring/README.md).

**Setup.** This is a replay on real data, using the current advisor code and the league's scoring file. Each run is an eight-team, 14-round snake draft with the confirmed roster. Our seat drafts with the strategy under test. Seven opponents draft by noisy rank, either ADP or per-game FP under this scoring, with a check that their lineup can still be filled.

* **Forecasts:** draft-time forecasts are 2024-25 per-game averages, restricted to the keys Sleeper's live projections carry. Sleeper's 2025 projections were revised after the season, so only their ADP is used.
* **Season scoring:** every 2025-26 regular-season game (25 weeks) is scored under Lock-In rules, including the threshold penalties the forecasts cannot see. Each week the manager starts the best forecast lineup and benches anyone who missed all of the previous week. A game is locked when it beats the forecast value of that player's remaining games; the last game is forced, and a DNP scores 0.
* **Not modelled:** waivers, trades and IR moves.
* **Injury check:** "injury-aware" strategies don't see six players whose long-term injuries were public before October 2025.
* **Sample:** 48 drafts per strategy.

Mean weekly points versus drafting by ADP in the same seat and seed (± 95% interval):

| Strategy | Room drafts by ADP | Room drafts by this scoring |
| --- | ---: | ---: |
| Draft by ADP (baseline) | 1786 /wk | 1803 /wk |
| Per-game FP, injury-aware | −384 ± 40 | −82 ± 36 |
| Advisor, no injury check | −382 ± 49 | −33 ± 37 |
| **Advisor, injury-aware (current advisor + manual check)** | **−146 ± 39** | **+14 ± 35** |

All-play weekly win rate for the injury-aware advisor: 0.42 in the ADP room, 0.60 in the scoring-aware room (ADP drafting: 0.56 / 0.62). Average finish 6.54 and 2.83 of 8.

**What it shows.**

* **Under the real scoring, the backtest does not support the advisor over drafting by ADP.** It loses clearly in a room that drafts by ADP and ties in a room that drafts for this scoring.
* The cause is availability, not player type. In the ADP room the advisor's picks averaged 188 realized points per game against 178 for ADP picks, but 52 games played against 60. Its early picks concentrated on players who missed time or regressed (Trae Young 15 games, Morant 20, Embiid 38; Cam Thomas forecast 232 per game, realized 140). ADP prices that risk; the advisor does not.
* The forecast ranks per-game value better than ADP (rank correlation with realized 2025-26 FP per game 0.75 vs 0.60), and nearly matches it on season totals (0.42 vs 0.45). A 50/50 rank blend of the two did best on season totals (0.48).
* The injury check alone is worth about +235 weekly points in the ADP room.

**Limits.** Prior-season averages are a weaker forecast than Sleeper's live preseason projections and miss rookies (excluded). Positions are current, not 2025 preseason. With no waivers, a missed game costs more than in the real league, which favors ADP. The injury list is hand-coded from public preseason news. One season only.

## Advisor behaviour for this scoring

* The advisor reads the league's scoring from Sleeper, so it already uses the table above.
* `oreb` is derived as `reb − dreb`; missing shot components are rebuilt from `fga = fgm + fgmi`, `fta = ftm + ftmi`, `tpa = tpm + tpmi`.
* `pf` is estimated at 0.08 fouls per projected minute (labelled assumption).
* `tf`, `ff` and the 40/50-point, 20-rebound and 15-assist penalties are not projected and count as 0 with warnings.
* Double-double and triple-double rates are averaged into every game. A model that treated triple-doubles as passable was backtested and reverted.
* Not yet changed: the advisor does not account for injury status, injury history or the market's availability discount. The backtest identifies this as the main improvement to make.
