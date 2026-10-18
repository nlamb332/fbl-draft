# Six-team points strategy research

This report is a bounded synthetic sensitivity study. It is advisory research and is not live LeagueConfig or production advisor validation.

Profile: `six_team_points_research`; teams: 6; rounds: 14; picks per draft: 84; development seeds: [101, 102, 103, 104]; held-out seeds: [201, 202, 203, 204, 205, 206, 207, 208].

Draft decisions and daily starter selection use forecast records. The selected starter IDs are then scored with a separate realized-stat world; realized values are not available to draft ranking or lineup selection.

`lineup_aware` uses this engine's transparent slot-coverage heuristic. It is not production marginal-lineup-v1.

## Paired strategy summary

| Split | Strategy | Runs | Realized points | Paired delta vs custom | Pair win fraction | Benched points | Coverage | Legal rosters |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| development | availability | 24 | 1469.17 | 31.64 | 0.833 | 132.61 | 1.000 | 1.000 |
| development | big_preference | 24 | 1364.56 | -72.97 | 0.367 | 113.69 | 1.000 | 1.000 |
| development | custom_per_game | 24 | 1437.54 | 0.00 | 0.642 | 62.38 | 1.000 | 1.000 |
| development | lineup_aware | 24 | 1479.53 | 42.00 | 0.875 | 110.28 | 1.000 | 1.000 |
| development | market | 24 | 1431.58 | -5.95 | 0.583 | 66.32 | 1.000 | 1.000 |
| heldout | availability | 48 | 1466.84 | 31.24 | 0.754 | 120.29 | 1.000 | 1.000 |
| heldout | big_preference | 48 | 1373.07 | -62.53 | 0.367 | 143.80 | 1.000 | 1.000 |
| heldout | custom_per_game | 48 | 1435.60 | 0.00 | 0.633 | 65.19 | 1.000 | 1.000 |
| heldout | lineup_aware | 48 | 1491.02 | 55.42 | 0.804 | 89.74 | 1.000 | 1.000 |
| heldout | market | 48 | 1428.52 | -7.08 | 0.621 | 71.90 | 1.000 | 1.000 |

Development-selected strategy: `lineup_aware`.
Held-out audit of that fixed choice: 55.42 points per weekly roster versus `custom_per_game`.

## Limits

- Pools, schedules, opponent profiles, availability, and realized shocks are generated assumptions; no private input data or live actions are used.
- Six-team scarcity and 14-round timing differ from the legacy 14-team fixture. This module does not validate live ESPN behavior.
- Bonus rates are synthetic expected rates. DD+TD and 40+50+ stacking follows the user's final correction; event rates remain model assumptions.
- Small seed counts support reproducibility and sensitivity checks, not calibrated win probabilities.
