# Eight-team Sleeper Lock-In strategy ("Basketball is optional")

October 19, 2026 (America/Chicago). Replaces the [six-team profile](SIX_TEAM_LOCK_IN_STRATEGY.md) for the eight-team league with the custom scoring below. Machine-readable settings: [eight_team_lock_in.json](../profiles/eight_team_lock_in.json).

**Assumed, not confirmed:** Lock-In mode and the six-team roster (PG, SG, G, SF, PF, F, C, UTIL, UTIL; five bench; one IR), giving a 14-round, 112-pick snake. The advisor reads the real team count, roster slots and scoring from the Sleeper draft, so the live recommendations do not depend on these assumptions; only this document does.

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
* **Ball-dominant guards.** Assists (+4) and turnovers (+8) both pay. Of the top 72 players by weekly Lock-In value, 40 are PG-eligible and 36 SG-eligible; only 10 are C-eligible.
* **Steals are gold, blocks are poison.** +15 per steal, −10 per block. Wembanyama falls from ADP 1 to about 34th; Holmgren, Mobley and Davis fall out of the top 70.
* **Offensive rebounders over defensive rebounders.** Glass-cleaning defensive centers lose points on every board (Towns ~55th).
* **Bad free-throw shooters with big FTA.** A missed FT (+12) is worth nearly ten makes. Giannis stays top 6 largely through `ftmi`.
* **Triple-doubles cost a full −200 (−165 net of the DD bonus) whenever the game counts.** Lock-In does not remove the penalty: the score counts if that game is the one you lock, and you are forced into it when it is the player's last game of the week. You can only decline to lock a triple-double game while the player still has a later game that week, and that later game might be one too. Jokić projects a triple-double in about 49% of games: averaged, 143 FP/game (outside the top 90); valued as a weekly Lock-In pick, including the weeks you are stuck with a −165 game, about 21st. In a non-Lock-In league every triple-double counts and he should not be drafted early. Either way he is a poor pick at ADP 2. Other triple-double threats (Jalen Johnson, Giddey) carry the same risk on a smaller scale.

## Draft guidance

1. **Rounds 1–2:** Dončić, Trae Young, Cunningham, LaMelo Ball, Mitchell, Giannis, Edwards, Tatum, Banchero, Curry. Trae (ADP ~22) and LaMelo (ADP ~33) are the clearest early edges if the room drafts from Sleeper's default ADP.
2. **Fade ADP-driven bigs:** Wembanyama, Jokić, Towns, Şengün, Davis, Mobley, Holmgren, Amen Thompson and Scottie Barnes all rank far below ADP. Let opponents take them.
3. **Value targets after ADP 60:** Dejounte Murray, Coby White, Michael Porter Jr., Keyonte George, Stephon Castle, Julius Randle, Jalen Green, Kon Knueppel, Immanuel Quickley, Payton Pritchard, Zach LaVine. Kyrie Irving and Damian Lillard project well but their low ADP likely reflects injury; the advisor does not infer availability from injury labels.
4. **Still fill C.** With one C slot per team and few good C-eligible players, take a guard-friendly C (Sabonis, Adebayo, Siakam, Randle) before the C pool collapses. The advisor's lineup-fit model handles this from the actual roster; there is no position quota.
5. **Tiebreaks the projections cannot see:** known technical-foul and flagrant-prone players (+30 / +75 per event; not projected, counted as 0), and heavy foulers relative to their minutes.

Value falls off gradually: weekly Lock-In value is ~232 at the 8th player, ~202 at 24th, ~184 at 48th, ~163 at 72nd and ~141 at 112th. The cliff between tiers is smaller than the gap between ADP and this scoring, so exploiting the room's ADP matters more than reaching for a specific tier.

## Advisor changes for this scoring

* `oreb` is derived as `reb − dreb` (Sleeper projects `dreb` but not `oreb`). Missing shot components are rebuilt from the identities `fga = fgm + fgmi`, `fta = ftm + ftmi`, `tpa = tpm + tpmi`.
* `pf` is not projected; it is estimated at 0.08 fouls per minute from projected minutes (about 2.4 fouls in 30 minutes, ~12 FP/game). This is a labelled league-average assumption and favors high-minute players.
* `tf` and `ff` are not projected and count as 0 with a warning.
* Projected `dd`/`td` rates are modelled as discrete game outcomes (no bonus, double-double, triple-double) rather than averaged into every game, so the lock/pass rule sees the penalty as a separate outcome. A triple-double game is still charged in full when it is the final game or the player has no better later game.

## Limits

These rankings use Sleeper's season-average projections, the advisor's default game count, variance and availability assumptions, and assume opponents draft roughly by ADP. If other managers draft for this scoring, ADP-based value gaps will shrink; the advisor's "vs wait" survivor estimate will then be optimistic. No simulation study has been run for this league.
