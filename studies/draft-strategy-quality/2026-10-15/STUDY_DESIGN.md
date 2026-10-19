# Study design and actual execution status

Condensed companion to [METHODS.md](METHODS.md): the policy definitions, the
target-versus-actual sample counts, and the statistical conventions for the
October 15, 2026 study. Numbers match [README.md](README.md) and
[RESULTS.md](RESULTS.md).

## Question and primary outcome

Does the frozen FBL marginal draft strategy produce more usable season-long
fantasy points than simpler selection policies, and which picks leave value
behind within the tested alternatives?

Use a 14-team, ten-round snake draft and six daily starters: C/G/G/F/F/UTIL,
with four bench players. Drafted rosters remain fixed: no trades, waivers,
streaming, or IR replacement. The primary outcome is realized season points
from legal daily starting lineups. Secondary outcomes are weekly opponent-pair
win rates, usable starts, benched points, and downside performance.

Daily starters are selected using forecasts and known schedules before that
day's outcomes are revealed. A separately labeled hindsight lineup is an upper
bound, never the ordinary strategy score. All policies share roster-completion
feasibility rules and the same daily lineup allocator.

## Policies

| Policy | Pick-ranking rule |
|---|---|
| Current marginal | Existing production marginal daily-lineup valuation, with frozen parameters, replacement-value signals, and existing missing-forecast estimation |
| Foundation | Custom projected points per game times expected availability, plus a small positional-coverage tie adjustment |
| Custom points per game | Highest projected per-game points under league scoring |
| Custom season total | Highest projected custom per-game points times expected games |
| Positional VOR | Custom points per game above a position-specific replacement level |
| Market-order proxy | Captured market-order/ADP-compatible proxy; this dataset's source is rostered-percentage row order, not measured ADP |
| ESPN rank only | Lowest explicit ESPN RK among legal available players; no custom points, availability, ADP, or positional bonuses in ranking |

Foundation does not estimate missing forecasts in the same way as marginal, so
their comparison is not a pure isolation of lineup optimization. ESPN-only
ties use player ID. Hard roster legality still applies to every baseline.

## 1. Historical veteran-cohort backtesting

Target the 2022–23, 2023–24, and 2024–25 regular seasons. Forecast each season
only from the preceding season's per-game statistics and games played, with
historically supported position eligibility. Exclude players lacking prior NBA
data. This is a veteran-cohort strategy test, not an ESPN forecast backtest.

Use public historical game logs, schedules including scheduled absences, and
position evidence. Audit player joins. Derive DD/TD from actual primitive box
scores. Missing actual scoring components must be disclosed; never silently
treat unavailable fields as known zero truth. Evaluate every draft seat against
nominal, market-focused, and custom-focused opponents, targeting eight opponent
seeds per season. Report season/seat comparisons and forecast-selected versus
hindsight lineups.

**Actual status:** the importer/evaluator was implemented and tested locally,
but official NBA requests timed out or failed DNS. Zero historical backtests
completed. There are no historical season results in this study.

## 2. Paired Monte Carlo tournaments

The benchmark is the audited 195-player October 10 local ESPN projection
snapshot. An attended capture with 94 incomplete nonempty projection records
was rejected for forecast use. Its explicit RK column was still recoverable:
the ESPN extension audited unique joins for all 195 benchmark players and
overlaid October 13 ranks. It is a mixed-date benchmark, not an exact room replay.

Three opponent populations are used: mixed nominal, market-focused, and
custom-scoring/roster-need-focused. Test moderate forecast error and larger
forecast error with clustered injury absences. Generate synthetic full seasons
independently of the strategy's representative-week and bonus-estimation
functions. Draft policies never receive future outcomes.

Each synthetic team has 82 scheduled games within a 182-day horizon. Persistent
player forecast errors, daily variation, team shocks, and injury blocks create
outcome variation. Availability is inferred from rounded displayed fantasy
total/average values. Uncertainty parameters are **assumed**, because the
planned pre-2022 historical calibration data was unavailable. DD/TD/QD are
derived from generated primitive box scores; PF/TF/EJ outcomes are unavailable.
Reported scores therefore concern modeled/common components rather than full
league scoring truth.

Pair policies on identical outcome worlds and stable opponent randomness keyed
by opponent seat, pick, and player. Opponents react to the remaining pool. A
balanced scenario job includes all fourteen seats and all requested policies.
Incomplete jobs are excluded. Pooled estimates additionally require all six
opponent/environment cells for a world.

**Targets:** sixteen independent outcome worlds. **Actual:** 51 complete
six-policy scenario jobs / 4,284 drafts, of which 48 jobs comprise eight complete
worlds for pooled inference. The additional three cells appear only in
descriptive breakdowns. The ESPN extension completed 48 jobs / 672 drafts on
exactly those eight worlds. Every reused marginal roster's points, weekly
scores, usable starts, bench points, and hindsight bound reproduced exactly.

## 3. Decision counterfactuals

The exact latest practice catalog was not recoverable, so freeze a reproducible
simulated seat-13 draft. At each of ten turns, branch on the union of marginal's
top five candidates and each baseline's first choice. Force the candidate, then
continue with reactive opponents and unchanged marginal decisions for our later
turns. Already selected players cannot be drafted again.

Use separate discovery seeds to choose an alternative and holdout seeds to
estimate its advantage, across all three opponent populations. Positive regret
means final usable points after the tested alternative minus points after the
recommendation. It is conditional on the tested candidates and continuation
policy, not a globally optimal draft claim.

**Target:** 32 continuations, split sixteen discovery/sixteen holdout.
**Actual:** all ten decisions reached twelve holdout seeds per tested candidate.
Joint picks 13/16 evaluate ordered plans with intervening opponent selections
and recorded fallbacks if a second player becomes unavailable. Under a reduced
eight-continuation cap, 34 of 56 plans had complete batches; joint results remain
descriptive without an independently selected/held-out best plan.

Coward is included at pick 97 in the fallback draft; Filipowski is not selected,
so the study does not estimate regret for his actual practice-room selection.
Center concentration remains a hypothesis, not a demonstrated causal defect.

## Statistics, execution, and validation

Bootstrap 4,000 times at the independent outcome-world level, not by correlated
seats or weeks. Classify a directional advantage/disadvantage only with at least
eight worlds and a paired interval wholly above/below zero; otherwise classify
as inconclusive. Flag mean differences of at least 1% as practically meaningful.
Fewer than thirty worlds is exploratory; multiple-comparison intervals are
unadjusted. Historical opponent seeds would not create independent seasons.

The initial local execution budget was capped at two hours: fifteen minutes
preparation, 35 historical, 40 tournaments, 25 counterfactuals, and five reporting,
with at most three worker processes. Interrupted source-verified samples were
resumed within remaining phase allowances. Invalid adapter/model pilots were
excluded. The later ESPN-only extension completed in about six minutes.

Validation included hand-calculated scoring examples, exhaustive small-roster
lineup allocation, absence of future-score selection, paired randomness,
drafted-player exclusion, and an exhaustive tiny draft with a known best branch.
Forty-six research tests passed together before publication; earlier validation
also passed 27 existing strategy/fixture checks. The portable evidence verifier (`verify.py`)
checked 4,956 complete draft histories and paired means. Two hundred forty
recommendation-forced branches reproduced their reference with zero regret.

The production strategy, recommendation service, and observer were unchanged.
Portable summaries, per-draft outcome rows, and hashes are in `data/` and
`ARTIFACTS.json`; detailed continuation trace sidecars remain outside this repository.
