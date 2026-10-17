# Mock draft results

The canonical runs completed with 14 total teams, 13 opponents, 10 rounds, and 140 unique picks in every draft. Each dataset contains 15 scenario conditions, 7 strategies, and all 14 draft seats: 1,470 draft simulations, 205,800 draft picks, and 10,290 daily lineup allocations across simulated seven-day weeks. Across both datasets that is 2,940 simulations, 411,600 picks, and 20,580 daily lineup allocations. Draft picks and lineup simulation runs are reported separately.

The exact roster validator found legal ten-player rosters, all 14 seats, and 140 unique picks in the completed rows. Daily lineups use `C, G, G, F, F, UTIL`; four bench spots are drafted and three IR slots are extra capacity. The lineup scorer uses exact dynamic programming. Greedy assignment failures are counted separately and are not treated as optimizer gains.

## Tested policy results

Policy selection chooses the highest mean paired weekly dynamic-programming delta versus the strict market baseline across development scenarios, with seat spread as a tie-break. This selects a policy only from the tested set; it does not establish global optimality or a fantasy win probability.

| input | development-selected policy | development paired delta | selected policy held-out delta | best held-out tested policy | best held-out delta |
|---|---|---:|---:|---|---:|
| synthetic fixture | `marginal_usable_lineup` | +325.92 | +244.14 | `marginal_usable_lineup` | +244.14 |
| supplied ESPN projection snapshot | `availability` | +407.09 | +80.83 | `marginal_usable_lineup` | +92.75 |

The synthetic experiment is a structural sensitivity test using generated players, generated team game/off-day schedules, and seeded health availability. Its positive deltas cannot establish a real-player draft advantage. The ESPN-input experiment uses the dated normalized snapshot at [`data/players_2026-27_espn_projection.csv`](data/players_2026-27_espn_projection.csv), but its market rank is rendered row order/% rostered proxy rather than true ADP, and availability is a rough implied proxy. Those results are projection sensitivity evidence only.

The synthetic report is [`results/synthetic/RESULTS.md`](results/synthetic/RESULTS.md); the sourced-input report is [`results/real_verified/RESULTS.md`](results/real_verified/RESULTS.md). Their raw paired rows, summaries, policy selections, and representative complete 140-pick logs are in the same directories.

## What changed across conditions

The matrix includes three market-agreement development seeds, three deliberate market-mismatch seeds, three held-out base seeds, three held-out base seeds with DD/TD overlap disabled, and three held-out availability-stress seeds. Forecast-noise held-out rows choose lineups from forecast values and score the chosen player IDs against a separate seeded realized-stat copy, avoiding future-stat leakage into lineup selection.

Market mismatch materially changes the experiment: in the synthetic development rows, `custom_value` averaged +52.27 weekly points in aligned bins versus +493.47 in mismatch bins; in the supplied snapshot, the corresponding values were +40.61 and +691.35. These are paired scenario effects under deliberately constructed rank conditions, not a calibrated estimate of how often ESPN rankings will be wrong.

The exact allocator exposes position and schedule effects through usable starter games, inactive starters, busy-day collisions, and seat spread. For example, the synthetic held-out selected policy averaged 27.67 usable starter games and 0.84 busy-day collisions per seven-day roster; the supplied snapshot's development-selected `availability` policy averaged 27.48 usable starter games and 1.27 collisions in held-out rows. These quantities describe the fixture's schedule and health model, not actual 2026-27 NBA schedules.

## Reproduction and limitations

Run from the repository root:

```powershell
python -m mock_draft.experiments --output-dir mock_draft/results/synthetic
python -m mock_draft.experiments --players mock_draft/data/players_2026-27_espn_projection.csv --output-dir mock_draft/results/real_verified
python -m unittest mock_draft.test_fixture
```

The DD/TD overlap switch is explicit and sensitivity-tested. The ESPN snapshot does not provide credible PF, EJ, TF, DD, TD, or QD forecasts. Those fields remain unknown in the data; the engine's missing-stat default omits their contribution, so these runs score only known components. No ESPN draft, roster transaction, acquisition, or lineup action was performed. These experiments do not support fantasy win probabilities, a calibrated draft board, or a claim that any tested strategy is universally optimal.
