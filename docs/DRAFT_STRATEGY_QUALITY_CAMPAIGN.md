# Draft strategy quality and pick regret

This research campaign evaluates the frozen draft strategy. It never changes
the observer, production recommendations, promotion artifacts, or actual picks.

Run from the repository root:

```powershell
python -m draft_assistant.campaign --budget-minutes 120 --workers 3
```

The output directory defaults to `runtime/strategy-quality-campaign`. It contains
the manifest, normalized input datasets, balanced-job checkpoints, compressed
draft traces, a counterfactual checkpoint, `summary.json`, and `REPORT.md`.
Runtime artifacts are ignored by Git. Copy the portable report and summary into
the validation directory when preserving a reviewed campaign result.

## What the experiments measure

The primary metric is realized season fantasy points from legal C/G/G/F/F/UTIL
daily lineups. Lineups are selected using forecasts and the known schedule;
their realized scores are revealed only afterward. A separate hindsight lineup
score is an upper bound, not the score assigned to an ordinary strategy.
Rosters remain fixed for the season: no waivers, trades, streaming, or IR
replacement. Weekly wins compare each roster with the thirteen opponents in
its own simulated league; they are opponent-pair win fractions, not playoff or
championship probabilities.

Six policies compete under the same roster-completion constraints: marginal,
foundation, custom points per game, custom season total, positional value over
replacement, and market order. Market order can be a source-order proxy rather
than measured ADP; the input provenance states which was available. Production
rankings and any fallback/feasibility intervention remain visible in the traces.

An additional ESPN rank-only comparison can be run against an existing frozen
campaign without modifying its six-policy results:

```powershell
python -m draft_assistant.campaign_espn --source runtime/strategy-quality-2026-10-14-final
```

This baseline always takes the lowest explicit ESPN `RK` among legal remaining
players, breaking ties by player ID. It does not combine RK with ADP, custom
points, availability, or positional bonuses. The adapter requires complete
one-to-one player joins and refuses missing ratings rather than substituting
market order. The October 13 attended capture contains RK for all 195 players
in the October 10 benchmark, so the comparison is a dated rank overlay, not an
exact reconstruction of a single room. All fourteen marginal roster outcomes
are reproduced and checked in each reused world/scenario cell. Only complete
six-cell worlds enter the new comparison; jobs and full draft traces are resumable.

1. Historical backtesting targets 2022–23, 2023–24, and 2024–25. Prior-season
   statistics and games played provide forecasts; historical positions define
   a veteran cohort without selecting players by their future participation.
   Actual game logs supply realized scoring, and team schedules retain DNPs as
   potential zero-point starts. Missing data is a gap, not a synthetic result.
2. Monte Carlo tournaments use a dated complete local projection catalog and
   independently generated full seasons. All fourteen seats compete against
   nominal, market-focused, and custom-focused opponent populations in moderate
   and injury-heavy worlds. Forecast error persists across a player's season;
   daily variation and injury blocks are separate effects. Without development
   data, uncertainty parameters are explicitly assumed rather than calibrated.
3. Counterfactuals branch on the marginal top five and each baseline's first
   choice. Later opponents react to the changed pool. Discovery seeds select
   an alternative; held-out seeds estimate its advantage. Positive regret means
   the tested alternative beats the recommendation. Joint picks 13/16 include
   a fallback when the intended second player is selected by an opponent.

The latest attended trial's diagnostics do not contain its full projection
catalog. Unless an exact dated scan is supplied and verified, counterfactuals
use a frozen simulated seat-13 draft and are not presented as exact explanations
of the user's Coward or Filipowski selections.

For the initial campaign, automatic preflight rejected the older attended
capture: 94 of its 348 nonempty projection rows lacked required primitive
statistics. The audited 195-player October 10 CSV is therefore the benchmark.
Its market order is a source-order proxy, not measured ADP. Availability is
derived from rounded displayed fantasy totals and averages, not an exact or
calibrated games-played forecast. Those limitations apply to all six policies.

## Budget and statistical interpretation

The initial execution budget is at most two hours, divided into preparation
(15 minutes), history (35), Monte Carlo (40), counterfactuals (25), and reporting
(5). Phases finish early when targets are met or their required data is absent.
At most three worker processes run concurrently. Deadlines are checked inside
drafts; a final in-flight calculation may take a small amount of shutdown time.

A balanced job contains every requested seat and all six policies for one seed,
dataset, environment, and opponent population. Partial/error jobs are retained
for diagnostics but do not enter comparisons. A resumed job is reused only when
its dataset contents, requested seats, and research/production code fingerprints
match. Completed jobs are checkpointed separately from large compressed traces.

Paired comparisons share external outcome worlds and stable opponent noise keyed
by seed, seat, pick, and player. Correlated seats and opponent populations are
clustered under their shared outcome seed. Historical opponent seeds do not
create independent historical seasons: pooled historical comparisons therefore
do not receive a simulation-world confidence interval.

Monte Carlo comparisons use 4,000 cluster-bootstrap resamples. Directional
classification requires at least eight independent clusters and a confidence
interval entirely above or below zero; fewer than thirty worlds is explicitly
exploratory. A mean difference of at least 1% is flagged as practically meaningful,
but does not substitute for uncertainty analysis. These are research summaries,
not production promotion gates or proofs of a globally optimal strategy.

Missing actual scoring fields are disclosed. DD/TD/QD can be calculated from
complete actual primitive box scores. Unobserved PF/TF/EJ are never reported as
known zero truth; comparisons lacking them concern common scoring components,
not the full custom league scoring system.

Use `--seats 1 13 --historical-seeds 0 --monte-carlo-seeds 1 --continuations 2`
only for a smoke run in a separate output directory. Such a run cannot support
strategy-quality conclusions. The default targets are eight historical opponent
seeds per season, sixteen Monte Carlo outcome seeds, and thirty-two decision
continuations. Reports state actual completed samples and unmet targets.
