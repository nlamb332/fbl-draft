# Preliminary fantasy basketball draft strategy

Updated: October 10, 2026 (America/Chicago). Status: preliminary strategy with initial local simulation results; real-world validation remains outstanding.

## League and authority

- League: Example League; ESPN league ID 1000000000.
- Team: Example Team; ESPN team ID 15. Browser access verified October 10; roster is empty before the draft.
- Draft: snake, October 18, 2026 at 5:00 PM CDT; order randomized at 4:00 PM CDT; 90 seconds per pick.
- **14 total teams: our team and 13 opponents.** Every league-sized mock must use this field, not 14 opponents.
- 10 ordinary roster slots: six starters and four bench. Three IR slots are additional; they do not make this a 13-round draft.
- Starting slots: C, G, G, F, F, UTIL. ESPN eligibility governs which slots each player can occupy.
- Daily individual locks at scheduled game time; no games-played cap. These
  constraints affect projected availability and daily lineup value, while
  transaction management belongs to the season repository.
- No keepers: value current-season production and playoff utility.
- Research and local mock simulations are authorized; this document does not
  authorize live ESPN actions.

## Exact scoring

```text
FP = PTS + 2*FGM - FGA + FTM - FTA + 3PM
     + 2*REB + 2*AST + 4*STL + 4*BLK - 2*TO
     - PF - 2*TF - 5*EJ + 3*DD + 5*TD + 100*QD
```

Stat primitives must use a consistent per-game or total basis. If percentages are provided, reconstruct makes only when attempts on the same basis are available. Do not confuse NBA PTS with fantasy FP.

Made two-pointer: 3 FP. Made three-pointer: 5 FP. Missed field goal: -1 FP. Made free throw: 1 FP. Missed free throw: -1 FP. These include the NBA points scored.

The extra rebound point versus ESPN default, plus DD/TD bonuses, favors productive rebounders and efficient bigs; PF penalties offset some of that advantage. All-around guards remain valuable. No category-punting strategy is necessary in a single-total points league.

DD/TD/QD overlap behavior remains unverified. A triple-double must not automatically receive both bonuses without an explicit assumption. Model stacking and exclusive alternatives. Estimate DD/TD frequency from game outcomes or rates, not from rounded season averages; a 10/10 average does not imply a DD every game. QD deserves negligible ordinary draft value; default to zero projected QDs and disclose that choice.

## Roster hypothesis

Start with roughly three guards, three forwards, two center-eligible players, and two best-value/flexible picks. Overlapping eligibility counts toward coverage; these are soft preferences, not quotas. Target at least three G-eligible and three F-eligible players, with a fourth where available at little cost.

PF/C eligibility is attractive because it combines big-man production with F-slot access. SG/SF flexibility can bridge the two G and two F slots. Multiple C-only players may collide in the C and UTIL slots. Prefer high-value players over rigidly enforcing any shape, but ensure the final roster can fill all six slots simultaneously.

Bench roles: dependable rotating contributors, one credible upside bet, and an expendable streaming candidate. Bench players should enter the daily lineup on core-player off days. Treat IR as injury storage, not productive active depth. Avoid filling all IR capacity with long-term stashes by default.

## Draft decisions

1. Recalculate each player's projected FP under these exact rules.
2. Separate per-game production, expected availability, and starts that fit the daily lineup.
3. Compare contribution above realistic positional replacements, not just raw season totals.
4. Compare our valuation to ESPN draft-room order/ADP to decide when to select a player.
5. Use tiers: small projection differences rarely justify false precision.

Early rounds: highest custom value with role and availability considered. Middle rounds: protect F coverage and center backup without reaching. Late rounds: credible minutes/role growth and replaceable picks. Do not let a rigid position quota override a substantial value gap. Near snake turns, account for the long interval before the next pick and remaining tier depth.

## Information inputs

- Official NBA injury reports and team announcements: availability, minutes restrictions, return plans, rest.
- Reliable beat reporting and confirmed lineups: role changes and replacement opportunities.
- Minutes, starts, closing appearances, rotation competition: sustained opportunity.
- Scoring primitives plus turnovers/fouls: actual custom production.
- Attempts, touches/passing opportunities and rebound opportunities: supporting evidence of changing role; context matters.
- Season baseline and recent samples: distinguish role changes from shooting or defensive-stat noise.
- Daily schedules, back-to-backs and postponed games: usable starts.
- ESPN eligibility, roster state, lock times, acquisition count and waiver dates: execution constraints.

## ESPN projections

Use ESPN's underlying stat projections as a baseline, independently recompute custom scoring, and compare with another current projection set. Distinguish raw stat projections, fantasy totals, editorial ranks, draft-room ranks and ADP; they are different objects.

No convincing independent accuracy benchmark for ESPN under this league's exact points system has been established. A vendor backtest located during research excludes points leagues, rookies, and players with fewer than 20 games; it does not validate this use case. Do not label any provider objectively best from anecdotes or marketing.

Investigate disagreements in minutes, role, efficiency and games played. A blend is an initial heuristic, not proven superior. Save dated preseason and pre-game predictions before outcomes and evaluate per-game error, availability error, and usable season contribution separately. Missing PF/DD/TD or other scoring fields must be disclosed and sensitivity-tested, not silently assigned fabricated real-player values.

## Mock-draft validation plan

- Local simulations only; no live ESPN mock-room enrollment or real league actions.
- **14 teams, 13 opponents, 10 rounds, 140 unique picks per full mock.** Run all 14 starting draft seats.
- Reproducible seeds, diverse opponent behaviors and late-draft roster-feasibility checks.
- Compare market-order baseline, custom points, flexibility/coverage, big-heavy, availability-aware and upside approaches.
- Evaluate exact daily lineup allocation, usable starts, roster congestion, position coverage and expected points.
- Use paired seeds/common scenarios and held-out seeds plus structural scenario shifts; avoid tuning and declaring victory on the same assumptions.
- Label synthetic players, market rankings, schedules and injuries explicitly. Synthetic findings test mechanics and sensitivity; they cannot establish a real 2026-27 player draft board or calibrated fantasy win probability.
- Preserve source provenance and timestamps for real data. Do not invent real projections, bypass paywalls, or conceal incomplete data.
- Report failure cases and uncertainty; recommend a strategy only to the degree the experiments support it.

## Sources consulted

- League settings supplied by the user and verified in the signed-in ESPN UI: https://fantasy.espn.com/basketball/league/settings?leagueId=1000000000&view=summary
- ESPN Lineup Protection: https://support.espn.com/hc/en-us/articles/46679515596692-Lineup-Protection
- ESPN default scoring: https://www.espn.com/fantasy/basketball/story/_/id/30296896/espn-fantasy-default-points-league-scoring-explained
- ESPN waiver timing: https://support.espn.com/hc/en-us/articles/360012531592-Waiver-Period
- Hashtag custom points projections: https://hashtagbasketball.com/fantasy-basketball-points-league-projections
- Hashtag user guide: https://hashtagbasketball.com/user-guide
- NBA official reports: https://official.nba.com/
- RotoWire injuries: https://www.rotowire.com/basketball/injury-report.php
- RotoWire lineups: https://www.rotowire.com/basketball/nba-lineups.php
- Ball Ranks backtest and exclusions: https://ballranks.com/blog/basketball/nba/espn-yahoo-fantasy-basketball-rankings

## Delegated work

Requested model/effort: GPT-5.6 Luna, XHIGH. Three agents dispatched:

1. `mock_fixture`: fixture, configuration, exact lineup optimizer and reproducibility.
2. `mock_experiments`: invariants, experiments, held-out/scenario evaluation and results.
3. `player_data`: accessible projection/eligibility/market data, provenance and limitations.

The reusable fixture is documented in [mock_draft/README.md](mock_draft/README.md). Eight fixture tests pass, including 14-team/140-pick invariants, seeded reproducibility, all strategy/seat combinations, and exact lineup allocation checked against brute force.

### Captured projection input

The October 10 ESPN snapshot supplies 195 usable real-player records. See [data provenance and limitations](mock_draft/DATA_NOTES.md). Displayed fantasy points agree with the recomputed known scoring components within rounding tolerance, but this does **not** validate projection accuracy against future performance.

- Rendered row order is a market proxy based on roster order, not measured ADP or draft-room rank.
- Availability is a rough implied-games proxy, not a calibrated injury forecast.
- PF, TF, EJ and DD/TD/QD projections are unmodeled in the captured view. Real-player runs cannot settle the complete effect of these penalties and bonuses.
- Team schedules, health draws, opponent choices, and held-out performance perturbations are generated assumptions.

### Initial completed simulations

See the [comparison report](mock_draft/RESULTS.md), [synthetic matrix](mock_draft/results/synthetic/RESULTS.md), and [ESPN-input matrix](mock_draft/results/real_verified/RESULTS.md).

Two datasets each ran 15 scenarios × seven policies × all 14 seats: **2,940 complete drafts, 411,600 picks, and 20,580 daily lineup allocations**. Every draft used 14 total teams, 13 opponents, ten rounds, and 140 unique picks. Both matrices passed their completeness and legal-roster checks. Full example league drafts and raw CSV/JSON results are retained alongside each report.

| Policy | Synthetic held-out weekly delta | ESPN-input held-out weekly delta |
|---|---:|---:|
| Custom points | +239.4 | +51.3 |
| Availability-aware | +199.6 | +80.8 |
| Coverage-aware | +244.1 | +92.7 |
| Big-heavy | +170.8 | -2.1 |

Deltas compare modeled weekly points against each dataset's market-order baseline with paired scenarios and seats. They are not forecasts of real gains. Synthetic market order is generated; ESPN's baseline uses its rendered roster-order proxy, not ADP. Development scenarios deliberately include market agreement and mismatch; held-out scenarios include forecast perturbations and availability stress. There are only three seed sets per condition, and overlap variants reuse seeds, so observations are not independent season samples.

Development selects the coverage-aware policy for the synthetic dataset and availability-aware policy for the ESPN dataset. Both remain above their baselines in holdout. ESPN coverage-aware performance is an exploratory held-out comparison, not an independently validated replacement chosen before seeing that holdout.

**Current refinement:** rank by custom production with availability considered, then use positional coverage and flexibility to resolve close decisions. Retain the soft roster shape and exact daily assignment optimizer. Do not adopt a blanket big-heavy rule: its advantage was not robust across these inputs, and the real-player foul/bonus inputs are incomplete. The coverage-aware heuristic models marginal slot demand, not a complete future NBA schedule optimizer. Fixed roster quotas, streaming outcomes, playoff success, and projection-provider accuracy were not established by these runs.

