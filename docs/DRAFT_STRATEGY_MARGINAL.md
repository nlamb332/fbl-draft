# Marginal-lineup policy (`marginal-lineup-v1`)

Issue #44 implements #40. The owner decision is a scoring fix: **no position cap,
quota, or soft position bonus of any kind**. Position counts below are
diagnostics, never targets. The policy is judged on usable optimized lineup
points, the same evaluator as the frozen foundation benchmark.

`foundation` (`valuation.py`, fingerprint `dc4c94b9...`) remains available as
the explicit fallback. The accepted `marginal-lineup-v1` promotion artifact is
the automatic default loaded by the CLI and coordinator; `--foundation-only`
opts out. The implementation lives in `draft_assistant/marginal_lineup.py`.

## Method

1. **Marginal usable lineup value.** A candidate scores the expected increase in
   optimized weekly C/G/G/F/F/UTIL points from adding the player to the roster.
   Each day's lineup is the exact maximum-weight multi-eligibility assignment.
   Because assignable player sets form a transversal matroid and active values
   are positive, a descending-value greedy that keeps a player only while the set
   stays matchable is exactly optimal (unit-tested against known cases), and it
   is fast enough for the 2.0 s budget.
2. **Representative schedules.** 12 deterministic weeks (84 dated days): every team
   plays 3 or 4 games a week with seeded day placement, teammates (by `nba_team`)
   share game days, and health is a stratified draw against GP/availability. The
   candidate's own health is integrated analytically so no player is helped or
   hurt by a lucky draw. Schedules are seeded from constant strings, never from an
   evaluation seed.
3. **Bench opportunity cost.** The remaining roster spots are filled with expected
   future picks: at each of our next snake picks, the best player of the
   lineup-maximizing eligibility class still on the board after the other teams
   have taken the players they are expected to take (blend of draft rank and
   expected production). A candidate is measured against a replacement-level pick
   made now with the same completion.
4. **Replacement level.** Per position, the expected per-game production of the
   k-th best available player, where k is the 14-team starter demand (slots plus
   a UTIL share: C 16.8, G 33.6, F 33.6) pro-rated by picks already made. It is
   recomputed from the current pool every call (monotone in demand, unit-tested)
   and appears as `replacement_level_fp_per_game` and `value_over_replacement_weekly`.
   The final score is `marginal_lineup_weekly + 0.5 * value_over_replacement_weekly`
   (the 0.5 weight was tuned on development seeds).
5. **Forecast-gap imputation (tagged).**
   - PF: per-36 rate by eligibility class (G 2.5, G/F 2.6, F 2.8, C/F 3.2, C 3.4)
     times minutes (projected MIN, else a least-squares estimate from PTS/REB/AST/
     STL+BLK fit on the ESPN snapshot: R2 0.67, MAE 1.9 min). **Source caveat:**
     these rates are analyst estimates anchored on the NBA league-average of about
     2.8 PF/36 and the guard < forward < center ordering; no external dataset was
     fetched. They are flagged `imputed` in warnings and components and swept
     +-25% in the sensitivity table.
   - DD/TD: per-game probabilities from independent continuity-corrected normal
     tails of PTS, REB and AST with a documented spread model. Independence
     understates the PTS/REB/AST correlation of big passers.
   - A supplied stat (including an explicit 0) is never overwritten. TF, EJ and QD
     stay missing (negligible, no defensible rate) and keep their warnings.
6. **Budget and fallback.** The deadline is 85% of `recommendation_budget_seconds`
   (2.0 s). On overrun the unmodified foundation ranking is returned,
   `lookahead_status` is `fallback`, the explanation names the reason, and the
   coordinator's `recommendation` journal event records `status` and `explanation`.
7. **Explainability.** Each candidate carries `custom_fp`, `expected_fp_per_game`,
   `raw_weekly_value`, `replacement_level_fp_per_game`,
   `value_over_replacement_weekly`, `lineup_with_candidate_weekly`,
   `lineup_baseline_weekly`, `marginal_lineup_weekly`, `c_only`, imputed PF/DD/TD
   values and the foundation score/rank, plus a one-line explanation.

## Selection and promotion

`recommend()` selects the policy when `promotion_path` is an artifact whose
`policy` is `marginal-lineup-v1`. The artifact is accepted only if its status is
`valid`/`promotable`/`approved`, its algorithm version and parameter fingerprint
match, and both the synthetic and real-catalog nominal rows have at least 100
seed clusters, 14 seats per seed, a positive paired 95% lower bound and no seat
mean loss over 5%. Otherwise the foundation ordering stays first and the marginal
view is advisory in the explanation. With no artifact the behavior is exactly the
foundation's.

## Validation protocol

Parameters were chosen on development seeds 20260000-20260023 only (36-point grid,
`tuning_dev.json`), frozen in `frozen_params.json`, and committed before the
held-out run. Held-out seeds 20280000-20280099 (100 independent clusters) x 14
seats x 13 opponents, both catalogs (synthetic; real ESPN snapshot with a seeded
synthetic schedule), nominal plus market-misspecified and custom-misspecified
opponent rows. Deltas are paired by seed and seat; the bootstrap (4000 resamples)
is clustered by seed. On the real catalog the pool is the same 195 players every
seed, so seeds vary opponent noise, schedule and realized-stat noise only. The
evaluator is `validation_runner._weekly_points` and scores known components only
on the real catalog (PF/DD/TD are absent from the realized stats); the
"imputed-truth" rows rescore the same rosters with imputed PF/DD/TD as a
sensitivity, not as a gate.

## Held-out results
| Catalog | Opponents | Mean delta (pts/wk) | 95% CI | Worst seat (mean delta, % of foundation) | C-only median (found. / policy) | Usable starts (found. / policy) | Games benched | Busy-collision days | Gate |
|---|---|---|---|---|---|---|---|---|---|
| synthetic | nominal | 32.1 | [25.3, 39.6] | seat 4: 6.6 (0.55%) | 2.0 / 1.0 | 30.1 / 31.5 | 3.17 / 1.42 | 2.10 / 1.08 | pass |
| synthetic | market_misspecified | 23.3 | [16.6, 30.1] | seat 4: 11.7 (0.91%) | 2.0 / 1.0 | 30.1 / 31.3 | 2.92 / 1.46 | 2.01 / 1.12 | pass |
| synthetic | custom_misspecified | 22.9 | [17.7, 28.1] | seat 4: 4.9 (0.42%) | 2.0 / 1.0 | 30.5 / 31.7 | 2.94 / 1.43 | 1.97 / 1.08 | pass |
| real | nominal | 12.1 | [6.2, 18.1] | seat 11: -9.1 (-0.71%) | 2.0 / 2.0 | 29.4 / 30.0 | 2.29 / 1.28 | 1.66 / 1.00 | pass |
| real | market_misspecified | 48.5 | [42.8, 54.3] | seat 13: 29.4 (2.32%) | 4.0 / 2.0 | 28.0 / 29.4 | 3.21 / 1.39 | 2.24 / 1.09 | pass |
| real | custom_misspecified | -1.6 | [-6.6, 3.6] | seat 7: -23.3 (-1.75%) | 1.0 / 2.0 | 30.0 / 30.3 | 2.00 / 1.24 | 1.43 / 0.94 | fail |

C-only count histograms (nominal opponents):

- synthetic: foundation {"0": 65, "1": 261, "2": 507, "3": 398, "4": 130, "5": 35, "6": 4}, policy {"0": 110, "1": 596, "2": 682, "3": 12}
- real: foundation {"0": 30, "1": 252, "2": 571, "3": 400, "4": 125, "5": 22}, policy {"0": 30, "1": 387, "2": 852, "3": 129, "4": 2}

| Real catalog comparison | Metric | Mean delta | 95% CI | Worst-seat % |
|---|---|---|---|---|
| marginal_no_imputation vs foundation (nominal) | weekly_points | 7.8 | [1.6, 14.1] | -0.73% |
| marginal_no_imputation vs foundation (market_misspecified) | weekly_points | 51.7 | [45.6, 57.7] | 1.32% |
| marginal_no_imputation vs foundation (custom_misspecified) | weekly_points | -4.0 | [-9.3, 1.4] | -2.21% |
| marginal vs foundation (nominal) | imputed_truth_points | 14.1 | [8.5, 19.6] | -0.60% |
| marginal_no_imputation vs foundation (nominal) | imputed_truth_points | 7.6 | [1.8, 13.2] | -1.21% |
| marginal vs foundation (market_misspecified) | imputed_truth_points | 48.3 | [42.8, 54.0] | 2.00% |
| marginal_no_imputation vs foundation (market_misspecified) | imputed_truth_points | 49.3 | [43.6, 55.1] | 1.02% |
| marginal vs foundation (custom_misspecified) | imputed_truth_points | 1.1 | [-3.4, 5.8] | -1.83% |
| marginal_no_imputation vs foundation (custom_misspecified) | imputed_truth_points | -3.1 | [-7.9, 1.8] | -2.29% |
| marginal_pf_x0.75 vs foundation (nominal) | weekly_points | 10.3 | [4.9, 16.0] | -0.50% |
| marginal_pf_x0.75 vs foundation (nominal) | imputed_truth_points | 12.8 | [7.3, 18.2] | -0.39% |
| marginal_pf_x1.25 vs foundation (nominal) | weekly_points | 13.2 | [7.1, 19.6] | -0.55% |
| marginal_pf_x1.25 vs foundation (nominal) | imputed_truth_points | 16.8 | [10.7, 23.0] | -0.23% |
| marginal vs marginal_no_imputation (nominal) | weekly_points | 4.3 | [-1.3, 9.6] | -1.07% |
| marginal vs marginal_no_imputation (nominal) | imputed_truth_points | 6.5 | [1.4, 11.3] | -0.86% |

| Pick | Actual | Policy pick (full-policy path) |
|---|---|---|
| 13 | Karl-Anthony Towns | Jalen Duren |
| 16 | Jalen Duren | Anthony Davis |
| 41 | Jarrett Allen | Paolo Banchero |
| 44 | Julius Randle | Walker Kessler |
| 69 | Walker Kessler | Julius Randle |
| 72 | Kel'el Ware | Cameron Boozer |
| 97 | Nikola Vucevic | Jaime Jaquez Jr. |
| 100 | John Collins | Ayo Dosunmu |
| 125 | Kyshawn George | Julian Champagnie |
| 128 | Jrue Holiday | Kyshawn George |

Opening week (captured NBA schedule): actual 25/31 usable starts (C-only 6); policy path 29/35 (C-only 2).


## Promotion decision

**Gate passed on both catalogs (nominal opponents): `promotion_artifact.json` status `promotable`.**
Synthetic: mean +32.1 pts/week, 95% lower bound +25.3, worst seat +0.55%. Real ESPN
catalog: mean +12.1, lower bound +6.2, worst seat -0.71% (seat 11), no budget
fallbacks. Both exceed the lower-bound > 0 and seat-loss <= 5% requirements.

Robustness rows are reported, not gating. The policy keeps a positive paired
interval under market-misspecified opponents on both catalogs and under
custom-misspecified opponents on the synthetic catalog. **On the real catalog with
custom-misspecified opponents the delta is -1.6 pts/week with an interval
spanning zero (worst seat -1.75%): no demonstrated edge there.**

The repository ships the accepted passing artifact as the default
`promotion_path`; the live recommender uses it automatically. Supplying
`--foundation-only` disables promotion and uses foundation. This policy decision
does not clear the separate uninterrupted browser release gate (#41).

## Latency

`recommend()` with a promoted artifact, 40 calls per cell, full 195-player pool
(`.scratch` benchmark; also asserted by `test_p95_recommendation_time_within_budget`):

| Catalog / pick | p50 | p95 | max |
|---|---|---|---|
| real, pick 13 (empty roster) | 0.042 s | 0.046 s | 0.066 s |
| real, pick 128 (9 rostered) | 0.014 s | 0.015 s | 0.017 s |
| synthetic, pick 13 | 0.040 s | 0.045 s | 0.050 s |
| synthetic, pick 128 | 0.012 s | 0.014 s | 0.015 s |

The 2.0 s budget is enforced with a foundation fallback (see Method 6).

## Limits and risks

- Imputation: PF rates are analyst estimates, not a sourced dataset; DD/TD assume
  independent stat tails. The frozen evaluator cannot credit imputation on the real
  catalog (known components only); the with/without rows above bracket the effect.
- The real catalog is one fixed pool; seeds vary opponents, schedule and noise only.
  Real ESPN projections, ADP (market rank is a row-order proxy) and draft-date health
  are not validated by this matrix.
- The policy models opponents as a draft-rank/production blend; the custom-opponent
  row shows the weakest result.
- Gate parameters were tuned on the same simulator family (development seeds), so the
  held-out numbers measure generalization across seeds, not across simulators.

## Injury-status availability haircut (opt-in, off by default)

`LivePlayer.injury_status` is ignored by valuation unless
`LeagueConfig(injury_policy=InjuryPolicy(enabled=True, ...))` is set. When enabled, a
recognised status multiplies the player's GP/availability factor, in both the foundation
score (`custom_fp x gp_factor`) and the marginal `activity_vector`/expected value. Out of
the box nothing changes and no injury fields are added to candidates.

| Status (case-insensitive) | Factor | Default |
| --- | --- | --- |
| healthy: blank, `ACTIVE`, `HEALTHY` | 1.0 | - |
| `O`, `OUT`, `IR`, `SUSPENDED` | `out_factor` | 0.5 (draft-time status does not give the absence length) |
| `DTD`, `Q`, `QUESTIONABLE`, `GTD` | `dtd_factor` | 0.95 |
| anything else | 1.0 | candidate warning `unknown injury status` |

Each candidate gains `injury_factor` and `injury_adjustment` components (season-value
delta in the foundation; weekly lineup points in the marginal path) and an explanation
clause. It is purely a scoring adjustment: no position cap, quota or exclusion. The
defaults are unvalidated placeholders; when enabled, the policy is folded into the
strategy and marginal fingerprints, so an existing promotion artifact does not cover it
and it must be re-validated before promotion.
