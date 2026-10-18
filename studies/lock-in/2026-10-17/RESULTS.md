# Lock-In research results

This bounded study uses synthetic game-level worlds and a fixed weekly assignment of nine roster players to the native PG/SG/G/SF/PF/F/C/UTIL/UTIL slots. The assignment is made before games and there is no bench swapping.

Each player may lock at most one completed game score per week. A nonfinal DNP advances to the next opportunity; a final scheduled DNP with no prior lock contributes an explicit zero. Negative played scores are legal and are never replaced by a best prior score.

Primary policy: `forecast_dp` shared across all draft strategies. Secondary rules: `always_first, mean_threshold`. Development selection uses nominal worlds only; heldout nominal rows are an audit.

The hindsight upper bound is report-only: it sums the best realized game for each fixed starter. It is never supplied to a draft scorer or online policy. `bench_points` is zero by definition because fixed weekly bench players are never scored; their games are not summed.

Forecast bias, forecast variance error, and injury stress are conditional synthetic sensitivity regimes. They describe how this fixture responds to those assumptions and are not calibrated injury rates, market timing, or real-player projections.

## Aggregated results

| split | scenario | strategy | lock rule | weekly points | paired win fraction | locked slots | unfilled zeros | hindsight upper bound |
|---|---|---|---|---:|---:|---:|---:|---:|
| development | forecast_shift | availability | always_first | 271.30 | 0.474 | 8.89 | 0.11 | 285.94 |
| development | forecast_shift | availability | forecast_dp | 277.88 | 0.556 | 8.78 | 0.22 | 285.94 |
| development | forecast_shift | availability | mean_threshold | 278.90 | 0.570 | 8.78 | 0.22 | 285.94 |
| development | forecast_shift | custom_per_game | always_first | 276.79 | 0.673 | 8.83 | 0.17 | 290.00 |
| development | forecast_shift | custom_per_game | forecast_dp | 278.31 | 0.629 | 8.61 | 0.39 | 290.00 |
| development | forecast_shift | custom_per_game | mean_threshold | 278.72 | 0.634 | 8.61 | 0.39 | 290.00 |
| development | forecast_shift | legacy_daily_lineup_coverage | always_first | 273.38 | 0.530 | 8.92 | 0.08 | 287.46 |
| development | forecast_shift | legacy_daily_lineup_coverage | forecast_dp | 278.68 | 0.574 | 8.79 | 0.21 | 287.46 |
| development | forecast_shift | legacy_daily_lineup_coverage | mean_threshold | 279.49 | 0.582 | 8.79 | 0.21 | 287.46 |
| development | forecast_shift | lock_in_value | always_first | 277.67 | 0.638 | 8.98 | 0.02 | 293.94 |
| development | forecast_shift | lock_in_value | forecast_dp | 281.27 | 0.636 | 8.74 | 0.26 | 293.94 |
| development | forecast_shift | lock_in_value | mean_threshold | 281.90 | 0.641 | 8.74 | 0.26 | 293.94 |
| development | forecast_shift | market_aware_timing | always_first | 277.17 | 0.618 | 8.97 | 0.03 | 293.36 |
| development | forecast_shift | market_aware_timing | forecast_dp | 281.35 | 0.647 | 8.76 | 0.24 | 293.36 |
| development | forecast_shift | market_aware_timing | mean_threshold | 282.00 | 0.654 | 8.76 | 0.24 | 293.36 |
| development | forecast_shift | replacement_aware | always_first | 274.74 | 0.545 | 8.97 | 0.03 | 291.09 |
| development | forecast_shift | replacement_aware | forecast_dp | 278.66 | 0.617 | 8.73 | 0.27 | 291.09 |
| development | forecast_shift | replacement_aware | mean_threshold | 279.45 | 0.623 | 8.74 | 0.26 | 291.09 |
| development | injury_stress | availability | always_first | 259.42 | 0.499 | 8.65 | 0.35 | 275.96 |
| development | injury_stress | availability | forecast_dp | 265.73 | 0.551 | 8.61 | 0.39 | 275.96 |
| development | injury_stress | availability | mean_threshold | 265.35 | 0.549 | 8.61 | 0.39 | 275.96 |
| development | injury_stress | custom_per_game | always_first | 261.43 | 0.548 | 8.43 | 0.57 | 273.66 |
| development | injury_stress | custom_per_game | forecast_dp | 264.46 | 0.530 | 8.40 | 0.60 | 273.66 |
| development | injury_stress | custom_per_game | mean_threshold | 264.38 | 0.532 | 8.40 | 0.60 | 273.66 |
| development | injury_stress | legacy_daily_lineup_coverage | always_first | 257.27 | 0.476 | 8.62 | 0.38 | 273.61 |
| development | injury_stress | legacy_daily_lineup_coverage | forecast_dp | 262.20 | 0.485 | 8.56 | 0.44 | 273.61 |
| development | injury_stress | legacy_daily_lineup_coverage | mean_threshold | 261.96 | 0.486 | 8.56 | 0.44 | 273.61 |
| development | injury_stress | lock_in_value | always_first | 267.62 | 0.622 | 8.82 | 0.18 | 284.98 |
| development | injury_stress | lock_in_value | forecast_dp | 272.62 | 0.643 | 8.77 | 0.23 | 284.98 |
| development | injury_stress | lock_in_value | mean_threshold | 272.70 | 0.647 | 8.77 | 0.23 | 284.98 |
| development | injury_stress | market_aware_timing | always_first | 269.77 | 0.646 | 8.84 | 0.16 | 287.56 |
| development | injury_stress | market_aware_timing | forecast_dp | 275.08 | 0.669 | 8.80 | 0.20 | 287.56 |
| development | injury_stress | market_aware_timing | mean_threshold | 275.08 | 0.669 | 8.80 | 0.20 | 287.56 |
| development | injury_stress | replacement_aware | always_first | 264.23 | 0.599 | 8.80 | 0.20 | 281.69 |
| development | injury_stress | replacement_aware | forecast_dp | 270.21 | 0.629 | 8.76 | 0.24 | 281.69 |
| development | injury_stress | replacement_aware | mean_threshold | 270.22 | 0.631 | 8.76 | 0.24 | 281.69 |
| development | nominal | availability | always_first | 271.19 | 0.461 | 8.89 | 0.11 | 285.83 |
| development | nominal | availability | forecast_dp | 280.04 | 0.554 | 8.81 | 0.19 | 285.83 |
| development | nominal | availability | mean_threshold | 279.18 | 0.529 | 8.82 | 0.18 | 285.83 |
| development | nominal | custom_per_game | always_first | 276.36 | 0.670 | 8.83 | 0.17 | 289.62 |
| development | nominal | custom_per_game | forecast_dp | 282.37 | 0.643 | 8.76 | 0.24 | 289.62 |
| development | nominal | custom_per_game | mean_threshold | 281.92 | 0.633 | 8.77 | 0.23 | 289.62 |
| development | nominal | legacy_daily_lineup_coverage | always_first | 272.86 | 0.501 | 8.92 | 0.08 | 287.14 |
| development | nominal | legacy_daily_lineup_coverage | forecast_dp | 281.67 | 0.560 | 8.85 | 0.15 | 287.14 |
| development | nominal | legacy_daily_lineup_coverage | mean_threshold | 280.75 | 0.542 | 8.85 | 0.15 | 287.14 |
| development | nominal | lock_in_value | always_first | 277.52 | 0.646 | 8.97 | 0.03 | 293.61 |
| development | nominal | lock_in_value | forecast_dp | 287.28 | 0.729 | 8.91 | 0.09 | 293.61 |
| development | nominal | lock_in_value | mean_threshold | 286.24 | 0.710 | 8.91 | 0.09 | 293.61 |
| development | nominal | market_aware_timing | always_first | 277.63 | 0.640 | 8.97 | 0.03 | 293.64 |
| development | nominal | market_aware_timing | forecast_dp | 287.23 | 0.727 | 8.91 | 0.09 | 293.64 |
| development | nominal | market_aware_timing | mean_threshold | 286.25 | 0.700 | 8.91 | 0.09 | 293.64 |
| development | nominal | replacement_aware | always_first | 274.73 | 0.547 | 8.97 | 0.03 | 290.79 |
| development | nominal | replacement_aware | forecast_dp | 284.61 | 0.651 | 8.91 | 0.09 | 290.79 |
| development | nominal | replacement_aware | mean_threshold | 283.51 | 0.630 | 8.91 | 0.09 | 290.79 |
| heldout | forecast_shift | availability | always_first | 276.10 | 0.562 | 8.89 | 0.11 | 289.96 |
| heldout | forecast_shift | availability | forecast_dp | 278.38 | 0.596 | 8.65 | 0.35 | 289.96 |
| heldout | forecast_shift | availability | mean_threshold | 278.73 | 0.595 | 8.65 | 0.35 | 289.96 |
| heldout | forecast_shift | custom_per_game | always_first | 278.78 | 0.678 | 8.84 | 0.16 | 292.58 |
| heldout | forecast_shift | custom_per_game | forecast_dp | 278.36 | 0.605 | 8.54 | 0.46 | 292.58 |
| heldout | forecast_shift | custom_per_game | mean_threshold | 278.55 | 0.601 | 8.54 | 0.46 | 292.58 |
| heldout | forecast_shift | legacy_daily_lineup_coverage | always_first | 276.18 | 0.560 | 8.92 | 0.08 | 290.98 |
| heldout | forecast_shift | legacy_daily_lineup_coverage | forecast_dp | 278.89 | 0.592 | 8.65 | 0.35 | 290.98 |
| heldout | forecast_shift | legacy_daily_lineup_coverage | mean_threshold | 279.32 | 0.589 | 8.66 | 0.34 | 290.98 |
| heldout | forecast_shift | lock_in_value | always_first | 281.11 | 0.689 | 8.95 | 0.05 | 296.96 |
| heldout | forecast_shift | lock_in_value | forecast_dp | 283.49 | 0.683 | 8.66 | 0.34 | 296.96 |
| heldout | forecast_shift | lock_in_value | mean_threshold | 283.70 | 0.675 | 8.66 | 0.34 | 296.96 |
| heldout | forecast_shift | market_aware_timing | always_first | 280.90 | 0.684 | 8.94 | 0.06 | 296.69 |
| heldout | forecast_shift | market_aware_timing | forecast_dp | 283.23 | 0.675 | 8.66 | 0.34 | 296.69 |
| heldout | forecast_shift | market_aware_timing | mean_threshold | 283.45 | 0.666 | 8.66 | 0.34 | 296.69 |
| heldout | forecast_shift | replacement_aware | always_first | 280.74 | 0.687 | 8.95 | 0.05 | 296.60 |
| heldout | forecast_shift | replacement_aware | forecast_dp | 281.92 | 0.657 | 8.61 | 0.39 | 296.60 |
| heldout | forecast_shift | replacement_aware | mean_threshold | 282.06 | 0.651 | 8.61 | 0.39 | 296.60 |
| heldout | injury_stress | availability | always_first | 260.57 | 0.552 | 8.60 | 0.40 | 276.78 |
| heldout | injury_stress | availability | forecast_dp | 267.00 | 0.598 | 8.56 | 0.44 | 276.78 |
| heldout | injury_stress | availability | mean_threshold | 266.76 | 0.593 | 8.56 | 0.44 | 276.78 |
| heldout | injury_stress | custom_per_game | always_first | 263.54 | 0.605 | 8.47 | 0.53 | 277.22 |
| heldout | injury_stress | custom_per_game | forecast_dp | 267.71 | 0.608 | 8.43 | 0.57 | 277.22 |
| heldout | injury_stress | custom_per_game | mean_threshold | 267.58 | 0.607 | 8.43 | 0.57 | 277.22 |
| heldout | injury_stress | legacy_daily_lineup_coverage | always_first | 262.63 | 0.568 | 8.67 | 0.33 | 278.96 |
| heldout | injury_stress | legacy_daily_lineup_coverage | forecast_dp | 269.25 | 0.634 | 8.64 | 0.36 | 278.96 |
| heldout | injury_stress | legacy_daily_lineup_coverage | mean_threshold | 269.11 | 0.633 | 8.64 | 0.36 | 278.96 |
| heldout | injury_stress | lock_in_value | always_first | 266.74 | 0.641 | 8.73 | 0.27 | 285.21 |
| heldout | injury_stress | lock_in_value | forecast_dp | 273.40 | 0.695 | 8.67 | 0.33 | 285.21 |
| heldout | injury_stress | lock_in_value | mean_threshold | 273.26 | 0.692 | 8.67 | 0.33 | 285.21 |
| heldout | injury_stress | market_aware_timing | always_first | 267.68 | 0.657 | 8.74 | 0.26 | 285.89 |
| heldout | injury_stress | market_aware_timing | forecast_dp | 274.18 | 0.700 | 8.67 | 0.33 | 285.89 |
| heldout | injury_stress | market_aware_timing | mean_threshold | 274.11 | 0.699 | 8.68 | 0.32 | 285.89 |
| heldout | injury_stress | replacement_aware | always_first | 266.42 | 0.637 | 8.72 | 0.28 | 284.48 |
| heldout | injury_stress | replacement_aware | forecast_dp | 272.88 | 0.687 | 8.66 | 0.34 | 284.48 |
| heldout | injury_stress | replacement_aware | mean_threshold | 272.90 | 0.685 | 8.67 | 0.33 | 284.48 |
| heldout | nominal | availability | always_first | 276.29 | 0.571 | 8.89 | 0.11 | 289.92 |
| heldout | nominal | availability | forecast_dp | 282.31 | 0.603 | 8.77 | 0.23 | 289.92 |
| heldout | nominal | availability | mean_threshold | 282.83 | 0.607 | 8.80 | 0.20 | 289.92 |
| heldout | nominal | custom_per_game | always_first | 278.74 | 0.680 | 8.82 | 0.18 | 292.67 |
| heldout | nominal | custom_per_game | forecast_dp | 283.68 | 0.650 | 8.73 | 0.27 | 292.67 |
| heldout | nominal | custom_per_game | mean_threshold | 283.84 | 0.664 | 8.75 | 0.25 | 292.67 |
| heldout | nominal | legacy_daily_lineup_coverage | always_first | 276.84 | 0.579 | 8.92 | 0.08 | 291.18 |
| heldout | nominal | legacy_daily_lineup_coverage | forecast_dp | 282.68 | 0.596 | 8.79 | 0.21 | 291.18 |
| heldout | nominal | legacy_daily_lineup_coverage | mean_threshold | 283.20 | 0.603 | 8.82 | 0.18 | 291.18 |
| heldout | nominal | lock_in_value | always_first | 281.17 | 0.699 | 8.94 | 0.06 | 296.98 |
| heldout | nominal | lock_in_value | forecast_dp | 288.21 | 0.739 | 8.84 | 0.16 | 296.98 |
| heldout | nominal | lock_in_value | mean_threshold | 288.25 | 0.744 | 8.86 | 0.14 | 296.98 |
| heldout | nominal | market_aware_timing | always_first | 280.78 | 0.692 | 8.93 | 0.07 | 296.69 |
| heldout | nominal | market_aware_timing | forecast_dp | 287.98 | 0.739 | 8.84 | 0.16 | 296.69 |
| heldout | nominal | market_aware_timing | mean_threshold | 288.04 | 0.744 | 8.85 | 0.15 | 296.69 |
| heldout | nominal | replacement_aware | always_first | 280.93 | 0.692 | 8.94 | 0.06 | 296.81 |
| heldout | nominal | replacement_aware | forecast_dp | 288.29 | 0.733 | 8.84 | 0.16 | 296.81 |
| heldout | nominal | replacement_aware | mean_threshold | 288.13 | 0.741 | 8.85 | 0.15 | 296.81 |

Development selected `lock_in_value` under `forecast_dp`. The selection is a within-fixture comparison and should be re-evaluated if forecast scoring bias or coverage assumptions change.

Raw balanced world rows are in `rows.csv`; `pool_seed` identifies the independent world cluster and `our_seat` gives all six seat replications. `summary.csv` groups the rows by split, scenario, strategy, and lock rule.
