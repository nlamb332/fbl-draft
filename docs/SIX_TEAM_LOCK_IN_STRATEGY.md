# Six-team Sleeper Lock-In strategy

October 17, 2026 (America/Chicago). This replaces the daily-accumulation assumption in the [earlier profile](SIX_TEAM_STRATEGY.md). Lock-In is the working mode authorized by the user, based on Sleeper's default for new leagues; an individual league API confirmation has not been supplied.

Settings: six teams, nine starters (PG, SG, G, SF, PF, F, C, UTIL, UTIL), five bench players and one IR slot. Under a no-keeper snake draft assumption, there are fourteen rounds and 84 unique picks. The screenshot confirms rolling waivers, a two-day waiver period, immediate free-agent availability after games, draft-pick trading, a week-18 trade deadline and a six-team playoff field starting week 22. Acquisition limits, exact playoff schedule and traded picks are not supplied. Transaction behavior is outside this draft study.

Machine-readable settings: [six_team_lock_in.json](../profiles/six_team_lock_in.json). Study and replacement plots: [Lock-In outcomes](../studies/lock-in/2026-10-17/README.md).

## Scoring and mode

Keep the supplied scoring unchanged:

`FP = 0.5*PTS + REB + AST + 2*STL + 2*BLK - TO + 0.5*3PM - 2*TF - 2*FLAGRANT_FOUL + DD + 2*TD + 2*(PTS>=40) + 2*(PTS>=50)`.

Both pairs stack: a triple-double earns three bonus points, and 50+ points earns four threshold bonus points. Actual game DD/TD indicators must come from double figures in the relevant box-score categories; expected rates are probabilities, never thresholding a season average.

Sleeper counts **one game per player per week**, chosen after the game finishes and before that player's next game starts. Locked players cannot be moved to another position or used again that week. The player must have been in the starting lineup before the game; a bench or newly acquired player's past score cannot be claimed. If no game is locked, the final scheduled team game is the default, potentially zero for a DNP; there is no return to an earlier good game. Sources: [Lock-In rules](https://support.sleeper.com/en/articles/6522833-lock-in-mode-details), [league formats](https://support.sleeper.com/en/articles/4644860-league-formats-game-modes).

## Strategy hypotheses

The [completed comparison](../studies/lock-in/2026-10-17/README.md) supports forecast Lock-In value as the research benchmark: 288.21 modeled weekly points versus 283.68 for common-forecast per-game ranking (+1.6%, exploratory paired interval +1.36 to +8.08 points). Replacement-aware and market-timing variants are statistically tied with it. Do not add their complexity on the strength of the nominal mean ordering alone. A simple mean-threshold lock rule also matched the forecast stopping rule in this sample; precise thresholds require calibrated real-player forecasts.

1. **Forecast Lock-In value:** rank by attainable expected weekly score under a sequential lock/pass rule. Game count provides additional opportunities to accept a score, rather than additive production from every game.
2. **Availability and risk:** value the chance of recording a useful game, including the risk of passing and later getting a DNP. Avoid multiplying season games played directly into a one-game weekly score.
3. **Ceiling and variance:** test players with equal means and different game distributions. Extra upside can help when weak games can be passed, but a rule that always selects the future maximum is hindsight, not an executable strategy.
4. **Native-position replacement:** compare players with realistic legal alternatives and keep simultaneous PG/SG/G/SF/PF/F/C/UTIL/UTIL coverage. Two UTIL positions permit flexibility without eliminating C-only congestion.
5. **Market timing:** test the opportunity cost of reaching now versus taking another useful player and waiting until the next own pick. Any market-based survival estimate is an assumption until ADP and opponent behavior are audited.

Late-round upside and injury replacement depend on transaction and IR rules. One IR slot is recorded, but its management advantage is not established by a fixed-roster experiment.

## Interpretation

The earlier +3.9% daily-lineup result does not establish a Lock-In advantage. New results must compare draft policies under the same management rule and keep lock/pass-rule comparisons separate. Development data selects a policy; held-out worlds audit the fixed selection. Confidence intervals resample independent worlds, retaining correlated seats and weeks within each world.

A forecast-based sequential policy observes the just-completed game and remaining scheduled opportunities. It must not inspect future realized scores, future DNPs or the best eventual game. A hindsight bound, if shown, is labeled separately and is not an ordinary scoring result.

This is research and manual strategy guidance. It does not change the ESPN advisor or Claude's separate Sleeper advisor and is not production-policy validation.
