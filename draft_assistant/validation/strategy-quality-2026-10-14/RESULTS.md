# Draft strategy quality results — October 14, 2026

The production strategy was frozen. These are conditional simulation results, not historical NBA backtest results or a proof of globally optimal drafting.

The campaign completed **51 of 96 tournament jobs**, or **4,284 full drafts**. The pooled comparison uses **8 complete outcome worlds**, fourteen seats, all six policies, three opponent populations, and two outcome environments. Incomplete jobs and incomplete scenario worlds are excluded from pooled estimates.

## Policy comparisons

Positive differences favor the current marginal strategy. Points are simulated season totals from legal daily lineups selected with forecasts before realized outcomes were revealed.

| Baseline | Additional usable points | Relative gain | 95% world-cluster interval | Classification |
|---|---:|---:|---|---|
| foundation | 1,436.6 | 5.1% | 1,051.0 to 1,855.7 | advantage supported |
| custom_ppg | 6,202.8 | 26.4% | 5,719.2 to 6,702.9 | advantage supported |
| custom_total | 1,339.4 | 4.7% | 927.4 to 1,793.9 | advantage supported |
| positional_vor | 4,649.8 | 18.5% | 4,184.0 to 5,114.4 | advantage supported |
| market | 3,321.9 | 12.6% | 2,615.9 to 4,266.7 | advantage supported |

## Strong opponents and downside

| Opponent population | Advantage over foundation | 95% interval | Worlds |
|---|---:|---|---:|
| custom | -21.3 | -626.7 to 623.7 | 9 |
| market | 3,027.4 | 2,554.8 to 3,489.0 | 9 |
| nominal | 1,334.9 | 991.2 to 1,722.9 | 9 |

## Seat and environment breakdowns

These descriptive cells can include additional completed scenario worlds excluded from the balanced pooled comparison. Positive values favor marginal; all cells remain exploratory.

| Seat | Advantage over foundation | 95% interval | Worlds |
|---|---:|---|---:|
| 1 | 1,723.3 | 1,225.5 to 2,268.8 | 9 |
| 2 | 1,579.5 | 996.1 to 2,150.6 | 9 |
| 3 | 1,267.4 | 702.9 to 1,807.6 | 9 |
| 4 | 1,234.3 | 940.6 to 1,586.2 | 9 |
| 5 | 1,205.7 | 712.2 to 1,717.4 | 9 |
| 6 | 1,631.1 | 952.0 to 2,261.4 | 9 |
| 7 | 1,222.8 | 547.4 to 1,849.8 | 9 |
| 8 | 1,469.9 | 1,000.7 to 1,921.5 | 9 |
| 9 | 1,286.7 | 523.0 to 2,181.4 | 9 |
| 10 | 1,506.0 | 640.6 to 2,331.9 | 9 |
| 11 | 1,108.0 | -103.3 to 2,245.8 | 9 |
| 12 | 1,204.9 | 198.5 to 2,117.4 | 9 |
| 13 | 2,007.4 | 1,670.2 to 2,426.4 | 9 |
| 14 | 1,811.2 | 1,085.0 to 2,555.9 | 9 |

| Outcome environment | Advantage over foundation | 95% interval |
|---|---:|---|
| injury | 1,354.8 | 881.4 to 1,872.5 |
| moderate | 1,519.7 | 1,237.7 to 1,806.5 |

## Lineup utilization

Hindsight points are a separately labeled upper bound and were never supplied to draft policies or ordinary lineup selection.

| Policy | Usable points | Usable starts | Benched points | Hindsight upper bound | Weekly win fraction |
|---|---:|---:|---:|---:|---:|
| marginal | 29,731.2 | 677.4 | 1,007.6 | 30,265.5 | 66.0% |
| foundation | 28,294.7 | 642.1 | 2,873.0 | 29,300.8 | 58.3% |
| custom_ppg | 23,528.4 | 485.2 | 2,694.4 | 24,881.5 | 29.2% |
| custom_total | 28,391.8 | 644.9 | 2,782.5 | 29,394.3 | 58.8% |
| positional_vor | 25,081.4 | 527.4 | 1,166.6 | 25,898.2 | 37.5% |
| market | 26,409.4 | 636.1 | 1,909.3 | 27,325.5 | 45.6% |

Against foundation, the strategy won 74.6% of paired season comparisons. Its worst-decile average difference was -2,808.6 points. An average advantage does not mean every seat or outcome improves.

## Pick regret

These ten turns come from a frozen simulated seat-13 draft. They do not reconstruct the attended practice room exactly. Alternatives were selected on discovery worlds; the table uses separate holdout worlds. Positive regret means the tested alternative scored more points than the recommendation.

| Pick | Recommendation | Discovery-selected alternative | Held-out regret | 95% interval | Holdout / requested | Evidence for switching |
|---|---|---|---:|---|---|---|
| 13 | Jalen Duren | Alperen Sengun | 55.7 | -798.8 to 834.9 | 12 / 16 | inconclusive |
| 16 | Bam Adebayo | Alperen Sengun | 274.5 | -295.2 to 907.1 | 12 / 16 | inconclusive |
| 41 | Naz Reid | Michael Porter Jr. | 23.1 | -410.3 to 456.6 | 12 / 16 | inconclusive |
| 44 | Dyson Daniels | Michael Porter Jr. | 236.4 | -341.7 to 769.1 | 12 / 16 | inconclusive |
| 69 | Dejounte Murray | Caleb Wilson | 57.4 | -270.6 to 369.6 | 12 / 16 | inconclusive |
| 72 | Caleb Wilson | VJ Edgecombe | -406.3 | -769.5 to -51.2 | 12 / 16 | disadvantage supported |
| 97 | Cedric Coward | RJ Barrett | -166.5 | -580.5 to 219.6 | 12 / 16 | inconclusive |
| 100 | Jakob Poeltl | Tobias Harris | -102.7 | -624.9 to 394.9 | 12 / 16 | inconclusive |
| 125 | Jaime Jaquez Jr. | Egor Demin | -3.8 | -213.4 to 192.0 | 12 / 16 | inconclusive |
| 128 | Oso Ighodaro | Quentin Grimes | -6.8 | -147.3 to 152.5 | 12 / 16 | inconclusive |

Joint picks 13/16: 34 of 56 ordered plans have a complete tested batch under the reduced eight-continuation cap (32 requested). These plans are descriptive only: no independently selected and held-out best joint plan is claimed. Requested coverage and actual sample counts remain in the JSON artifact; unavailable second picks use the recorded fallback rather than being dropped.

Coward at pick 97: the discovery-selected Barrett alternative had negative held-out regret and an interval crossing zero; this does not establish a better pick. Filipowski was not selected in the reproducible fallback draft, so the campaign does not estimate regret for his actual practice-room selection. The fallback roster contains four center-eligible players, two also forward-eligible; positional concentration remains a hypothesis to test with matched roster-composition interventions, not a demonstrated causal defect.

## What to do next

- Investigate pick 16: Alperen Sengun versus Bam Adebayo, with estimated holdout improvement of 274.5 points. Confirm on fresh worlds before changing the policy.
- Investigate pick 44: Michael Porter Jr. versus Dyson Daniels, with estimated holdout improvement of 236.4 points. Confirm on fresh worlds before changing the policy.
- Investigate pick 69: Caleb Wilson versus Dejounte Murray, with estimated holdout improvement of 57.4 points. Confirm on fresh worlds before changing the policy.
- Obtain historical game logs, schedules, and historical eligibility to complete the three veteran-cohort backtests. Official NBA acquisition attempts timed out or failed DNS; no historical results were fabricated.
- Repeat with the exact complete latest draft catalog. This campaign used the audited 195-player October 10 snapshot after rejecting an older capture with 94 incomplete nonempty projection rows.
- Calibrate the outcome model independently before treating simulated gains as evidence of a real draft advantage. Keep subsequent tuning and final evaluation samples separate.

## Limits and validation

- Market order is a row-order proxy, not measured ADP. Availability is inferred from rounded ESPN fantasy totals and averages, not a calibrated health probability.
- Schedules and realized game outcomes are synthetic. PF, TF, and EJ outcomes are unavailable; DD/TD/QD are derived from simulated primitive box scores. Results concern modeled/common scoring components.
- Rosters are fixed, without trades, streaming, waivers, or IR replacements. Weekly wins are opponent-pair fractions rather than championship probabilities.
- Confidence intervals are clustered by outcome world, not by seat or week. Fewer than thirty worlds is exploratory. Intervals across multiple policies, turns, and scenario cells are not multiplicity-adjusted.
- Forty-three research tests and twenty-seven existing strategy/fixture checks passed. No production policy or promotion artifact was changed.
- Invalid model/adapter pilots were excluded before final inference. Valid interrupted samples were imported only after source-hash and scoring-function checks, with audit records retained.
- Unmet targets: historical results for all three seasons; pre-2022 historical calibration of uncertainty; sixteen complete Monte Carlo worlds; sixteen discovery plus sixteen holdout continuations for every candidate. This is a bounded initial campaign, not completion of those statistical targets.

Detailed traces, checkpoints, source snapshots, manifests, and migration audits are retained in `runtime/strategy-quality-2026-10-14-final`. The portable summary is stored beside this report in the validation directory.
