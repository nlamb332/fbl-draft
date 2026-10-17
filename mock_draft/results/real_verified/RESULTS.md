# Mock draft experiment results

These results use the supplied player snapshot with synthetic team game/off-day schedules. They are projection sensitivity tests, not calibrated fantasy win probabilities or a claim that the displayed market proxy is ADP.

Experiment design: **15 scenarios × 7 strategies × 14 seats = 1470 draft simulations**. Each simulation has **140 draft picks** and **7 daily lineup allocation runs**. The 140 picks and lineup runs are reported separately.

Player source: `ESPN rendered projections UI snapshot 2026-10-10`. Schedule source: `synthetic_schedule`. The schedule matrix is reused for every paired strategy in each scenario. Opponents are the fixture's 13 named profiles.

Market conditions exercised: `agree, fixture, mismatch`. Forecast evaluation modes: `noise, none`; held-out noise rows draft from forecast records and score lineups against a separate seeded realized-stat copy.

Within the tested set, development selects `availability` by paired weekly DP delta; that policy's held-out delta is `80.83`. This is a within-experiment selection result, not a globally optimal policy claim.

## Paired strategy deltas

`weekly Δ` is exact dynamic-programming lineup points relative to the market baseline for the same scenario and seat. Positive values indicate more modeled points under that scenario's supplied records and synthetic schedule; they are not win probabilities.

| split | strategy | runs | weekly DP | weekly Δ vs market | usable starter games | busy collisions | inactive starters | greedy fails | seat spread |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| development | availability | 84 | 1345.55 | 407.09 | 30.25 | 1.20 | 11.75 | 0.00 | 151.92 |
| development | baseline_market | 84 | 938.46 | 0.00 | 25.98 | 0.55 | 16.02 | 0.00 | 164.08 |
| development | big_heavy | 84 | 1199.20 | 260.74 | 27.60 | 0.98 | 14.40 | 0.00 | 151.45 |
| development | custom_value | 84 | 1304.44 | 365.98 | 28.54 | 0.81 | 13.46 | 0.33 | 222.95 |
| development | flexibility_balanced | 84 | 1273.33 | 334.87 | 28.54 | 0.82 | 13.46 | 0.00 | 138.38 |
| development | marginal_usable_lineup | 84 | 1342.90 | 404.44 | 30.30 | 1.12 | 11.70 | 0.08 | 130.83 |
| development | upside | 84 | 1304.53 | 366.08 | 28.52 | 0.79 | 13.48 | 0.50 | 190.85 |
| heldout | availability | 126 | 1195.69 | 80.83 | 27.48 | 1.27 | 14.52 | 0.00 | 125.01 |
| heldout | baseline_market | 126 | 1114.86 | 0.00 | 26.68 | 0.55 | 15.32 | 0.11 | 160.30 |
| heldout | big_heavy | 126 | 1112.74 | -2.12 | 26.13 | 0.48 | 15.87 | 0.00 | 114.41 |
| heldout | custom_value | 126 | 1166.18 | 51.32 | 25.94 | 0.51 | 16.06 | 0.22 | 150.13 |
| heldout | flexibility_balanced | 126 | 1183.65 | 68.79 | 27.05 | 0.65 | 14.95 | 0.00 | 168.70 |
| heldout | marginal_usable_lineup | 126 | 1207.61 | 92.75 | 27.91 | 1.26 | 14.09 | 0.11 | 189.84 |
| heldout | upside | 126 | 1175.64 | 60.78 | 26.08 | 0.60 | 15.92 | 0.22 | 134.88 |

A seat spread is the range of per-seat means within the split. It describes draft-position sensitivity and should not be read as a probability of winning.

## Market agreement and mismatch

`market_mismatch` is the normalized distance between each drafted player's market rank and custom-points rank. The bins below are descriptive covariates; sample sizes are shown so sparse bins are visible.

| split | strategy | bin | observations | weekly Δ vs market | usable-games Δ |
|---|---|---|---:|---:|---:|
| development | custom_value | aligned | 42 | 40.61 | 1.07 |
| development | custom_value | mismatch | 42 | 691.35 | 4.05 |
| heldout | custom_value | aligned | 119 | 48.92 | -0.82 |
| heldout | custom_value | mixed | 7 | 91.97 | 0.43 |
| development | flexibility_balanced | aligned | 42 | 19.10 | 1.14 |
| development | flexibility_balanced | mismatch | 42 | 650.64 | 3.98 |
| heldout | flexibility_balanced | aligned | 126 | 68.79 | 0.37 |
| development | big_heavy | aligned | 42 | -42.98 | 0.62 |
| development | big_heavy | mismatch | 42 | 564.46 | 2.62 |
| heldout | big_heavy | aligned | 126 | -2.12 | -0.55 |
| development | availability | aligned | 42 | 128.89 | 3.83 |
| development | availability | mismatch | 42 | 685.29 | 4.71 |
| heldout | availability | aligned | 124 | 77.15 | 0.73 |
| heldout | availability | mixed | 2 | 308.83 | 5.00 |
| development | upside | aligned | 42 | 37.63 | 1.00 |
| development | upside | mismatch | 42 | 694.52 | 4.10 |
| heldout | upside | aligned | 125 | 61.49 | -0.58 |
| heldout | upside | mixed | 1 | -28.63 | -3.00 |
| development | marginal_usable_lineup | aligned | 42 | 118.07 | 3.71 |
| development | marginal_usable_lineup | mismatch | 42 | 690.81 | 4.93 |
| heldout | marginal_usable_lineup | aligned | 124 | 88.52 | 1.14 |
| heldout | marginal_usable_lineup | mixed | 2 | 354.71 | 7.00 |

## Assumptions and limitations

- Daily lineups are `C, G, G, F, F, UTIL`; four bench spots are drafted and three IR slots are extra capacity, not draft slots.
- The exact DP allocator and greedy allocator both preserve legal roster shape. `dp_greedy_gap` quantifies the assignment approximation gap.
- A greedy assignment failure is reported separately; its missing day is not interpreted as an optimizer improvement in the exact DP score.
- The held-out rows use new draft/schedule seeds and include an availability-stress shift. The overlap-off rows reuse held-out base seeds to isolate the DD/TD bonus convention.
- Forecast-noise rows separate draft inputs from realized evaluation stats with a seeded ±10% stat perturbation; this is a synthetic robustness regime, not an uncertainty calibration.
- The daily allocator sees the synthetic active/inactive schedule, while realized stat noise is used only after slot selection; late injury information is therefore not modeled.
- Triple-double overlap is controlled by `double_double_includes_triple_double`. The report does not claim which platform convention is correct.
- The supplied ESPN snapshot has a rendered row-order market proxy (% rostered), no true ADP, and rough implied availability. Missing PF/DD/TD/EJ fields remain unknown rather than forecasts of zero; draw domain conclusions only after sourcing those inputs.

Raw paired observations are in `experiment_runs.csv`; aggregate tables are in `experiment_summary.csv` and `agreement_summary.csv`. `policy_selection.json` records the development-selected tested policy and its held-out audit. `representative_draft.csv` is one complete 140-pick league log (illustrative custom-value seat 7) for auditability.
