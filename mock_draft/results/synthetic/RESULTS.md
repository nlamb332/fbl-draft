# Mock draft experiment results

These results come from an explicitly synthetic player pool and synthetic availability schedules. They are sensitivity tests of the fixture and strategy rules, not real-player projections or calibrated fantasy win probabilities.

Experiment design: **15 scenarios × 7 strategies × 14 seats = 1470 draft simulations**. Each simulation has **140 draft picks** and **7 daily lineup allocation runs**. The 140 picks and lineup runs are reported separately.

Player source: `synthetic_fixture`. Schedule source: `synthetic_schedule`. The schedule matrix is reused for every paired strategy in each scenario. Opponents are the fixture's 13 named profiles.

Market conditions exercised: `agree, fixture, mismatch`. Forecast evaluation modes: `noise, none`; held-out noise rows draft from forecast records and score lineups against a separate seeded realized-stat copy.

Within the tested set, development selects `marginal_usable_lineup` by paired weekly DP delta; that policy's held-out delta is `244.14`. This is a within-experiment selection result, not a globally optimal policy claim.

## Paired strategy deltas

`weekly Δ` is exact dynamic-programming lineup points relative to the market baseline for the same scenario and seat. Positive values indicate more modeled points under that scenario's supplied records and synthetic schedule; they are not win probabilities.

| split | strategy | runs | weekly DP | weekly Δ vs market | usable starter games | busy collisions | inactive starters | greedy fails | seat spread |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| development | availability | 84 | 1183.23 | 292.63 | 29.95 | 1.98 | 12.05 | 0.33 | 141.20 |
| development | baseline_market | 84 | 890.60 | 0.00 | 27.71 | 1.77 | 14.29 | 0.33 | 75.80 |
| development | big_heavy | 84 | 1007.43 | 116.83 | 26.06 | 2.32 | 15.94 | 0.00 | 86.93 |
| development | custom_value | 84 | 1163.47 | 272.87 | 28.96 | 1.05 | 13.04 | 0.83 | 117.31 |
| development | flexibility_balanced | 84 | 1148.04 | 257.45 | 28.99 | 1.43 | 13.01 | 0.00 | 72.82 |
| development | marginal_usable_lineup | 84 | 1216.52 | 325.92 | 30.79 | 1.64 | 11.21 | 0.08 | 98.71 |
| development | upside | 84 | 1045.16 | 154.56 | 29.04 | 1.13 | 12.96 | 0.00 | 149.39 |
| heldout | availability | 126 | 1057.72 | 199.59 | 26.52 | 1.01 | 15.48 | 0.11 | 94.90 |
| heldout | baseline_market | 126 | 858.13 | 0.00 | 27.38 | 0.98 | 14.62 | 0.00 | 38.84 |
| heldout | big_heavy | 126 | 1028.90 | 170.77 | 26.54 | 0.94 | 15.46 | 0.00 | 59.45 |
| heldout | custom_value | 126 | 1097.49 | 239.36 | 27.06 | 0.67 | 14.94 | 0.67 | 107.39 |
| heldout | flexibility_balanced | 126 | 1097.61 | 239.48 | 27.71 | 0.85 | 14.29 | 0.00 | 132.39 |
| heldout | marginal_usable_lineup | 126 | 1102.27 | 244.14 | 27.67 | 0.84 | 14.33 | 0.00 | 58.74 |
| heldout | upside | 126 | 1027.44 | 169.31 | 28.52 | 0.77 | 13.48 | 0.00 | 91.13 |

A seat spread is the range of per-seat means within the split. It describes draft-position sensitivity and should not be read as a probability of winning.

## Market agreement and mismatch

`market_mismatch` is the normalized distance between each drafted player's market rank and custom-points rank. The bins below are descriptive covariates; sample sizes are shown so sparse bins are visible.

| split | strategy | bin | observations | weekly Δ vs market | usable-games Δ |
|---|---|---|---:|---:|---:|
| development | custom_value | aligned | 42 | 52.27 | 1.57 |
| development | custom_value | mismatch | 42 | 493.47 | 0.93 |
| heldout | custom_value | mixed | 39 | 234.46 | -0.41 |
| heldout | custom_value | mismatch | 87 | 241.56 | -0.28 |
| development | flexibility_balanced | aligned | 42 | 50.22 | 1.98 |
| development | flexibility_balanced | mismatch | 42 | 464.67 | 0.57 |
| heldout | flexibility_balanced | aligned | 5 | 226.94 | 0.20 |
| heldout | flexibility_balanced | mixed | 63 | 249.41 | 0.60 |
| heldout | flexibility_balanced | mismatch | 58 | 229.79 | 0.05 |
| development | big_heavy | aligned | 42 | -96.84 | -1.26 |
| development | big_heavy | mismatch | 42 | 330.51 | -2.05 |
| heldout | big_heavy | aligned | 3 | 174.32 | -0.33 |
| heldout | big_heavy | mixed | 104 | 172.19 | -0.74 |
| heldout | big_heavy | mismatch | 19 | 162.46 | -1.47 |
| development | availability | aligned | 42 | 74.36 | 2.67 |
| development | availability | mismatch | 42 | 510.90 | 1.81 |
| heldout | availability | mixed | 42 | 222.11 | -0.29 |
| heldout | availability | mismatch | 84 | 188.34 | -1.14 |
| development | upside | aligned | 42 | -55.72 | 2.02 |
| development | upside | mixed | 19 | 314.40 | -0.32 |
| development | upside | mismatch | 23 | 406.50 | 1.39 |
| heldout | upside | aligned | 1 | 130.09 | 1.00 |
| heldout | upside | mixed | 114 | 169.85 | 1.16 |
| heldout | upside | mismatch | 11 | 167.29 | 0.91 |
| development | marginal_usable_lineup | aligned | 42 | 115.81 | 3.64 |
| development | marginal_usable_lineup | mismatch | 42 | 536.04 | 2.50 |
| heldout | marginal_usable_lineup | mixed | 58 | 248.77 | 0.50 |
| heldout | marginal_usable_lineup | mismatch | 68 | 240.19 | 0.12 |

## Assumptions and limitations

- Daily lineups are `C, G, G, F, F, UTIL`; four bench spots are drafted and three IR slots are extra capacity, not draft slots.
- The exact DP allocator and greedy allocator both preserve legal roster shape. `dp_greedy_gap` quantifies the assignment approximation gap.
- A greedy assignment failure is reported separately; its missing day is not interpreted as an optimizer improvement in the exact DP score.
- The held-out rows use new draft/schedule seeds and include an availability-stress shift. The overlap-off rows reuse held-out base seeds to isolate the DD/TD bonus convention.
- Forecast-noise rows separate draft inputs from realized evaluation stats with a seeded ±10% stat perturbation; this is a synthetic robustness regime, not an uncertainty calibration.
- The daily allocator sees the synthetic active/inactive schedule, while realized stat noise is used only after slot selection; late injury information is therefore not modeled.
- Triple-double overlap is controlled by `double_double_includes_triple_double`. The report does not claim which platform convention is correct.
- No ESPN transaction, roster, or real-player projection was used; re-run with an explicitly sourced player file through `load_player_records` before drawing domain conclusions.

Raw paired observations are in `experiment_runs.csv`; aggregate tables are in `experiment_summary.csv` and `agreement_summary.csv`. `policy_selection.json` records the development-selected tested policy and its held-out audit. `representative_draft.csv` is one complete 140-pick league log (illustrative custom-value seat 7) for auditability.
