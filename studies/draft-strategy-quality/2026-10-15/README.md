# Draft strategy quality study

Publication privacy: identifying league/team labels and local paths in the
evidence and archived sources have been anonymized. Numerical results are
retained; `ARTIFACTS.json` records the sanitized artifact hashes.

Published October 15, 2026. Initial campaign completed October 14; ESPN rank-only
extension completed October 15. This study evaluates the frozen FBL drafting
strategy in a 14-team, ten-round snake draft with C/G/G/F/F/UTIL daily starters.

**Status: exploratory synthetic evidence. Historical backtests and empirical
outcome-model calibration are incomplete. No production policy was changed.**

## Read the study

- [Methods and experimental design](METHODS.md)
- [Initial results, seat/scenario breakdowns, and ten-pick regret table](RESULTS.md)
- [ESPN rank-only extension](ESPN_RANK_RESULTS.md)
- [Research implementation](../../../draft_assistant/campaign.py),
  [season evaluator](../../../draft_assistant/campaign_engine.py),
  [counterfactuals](../../../draft_assistant/campaign_counterfactual.py), and
  [historical adapter](../../../draft_assistant/campaign_history.py)

![Outcomes for all seven policies](strategy_outcomes_with_espn_rank.png)

## Main findings

| Draft policy | Mean usable season points | Weekly opponent-pair win rate |
|---|---:|---:|
| Current marginal | 29,731 | 66.0% |
| Custom season-total ranking | 28,392 | 58.8% |
| Foundation | 28,295 | 58.3% |
| ESPN rank only | 27,115 | 50.5% |
| Market-order proxy | 26,409 | 45.6% |
| Positional value over replacement | 25,081 | 37.5% |
| Custom points-per-game ranking | 23,528 | 29.2% |

Marginal gains 5.1% over foundation and 4.7% over custom season-total ranking
overall. Those advantages become inconclusive against custom-scoring-aware
opponents. Marginal gains 9.7% over ESPN rank-only overall (paired 95% interval:
+2,030 to +3,136 points), and 7.2% against custom-aware opponents.

These confidence intervals quantify uncertainty **inside the assumed model**,
not confidence in real-season performance. The strategy loses in some paired
worlds. None of the ten tested decision alternatives showed a statistically
clear held-out improvement over the recommendation. Joint picks 13/16 remain
descriptive; no best joint plan is claimed.

## What was tested

1. **Historical veteran-cohort backtests:** implementation delivered for
   2022–23, 2023–24, and 2024–25, but usable historical inputs could not be
   acquired. There are zero completed historical backtests and no historical
   performance claims.
2. **Monte Carlo tournaments:** 51 complete scenario jobs / 4,284 drafts across
   six policies. The pooled result includes eight complete worlds, all fourteen
   seats, three opponent populations, and two uncertainty environments: 672
   own-team drafts per policy. Three extra completed cells contribute to
   descriptive breakdowns, not the balanced pooled estimate. The separate ESPN
   extension adds 48 scenario jobs / 672 drafts on the same eight worlds.
3. **Decision counterfactuals:** all ten turns of a frozen simulated seat-13
   draft, with discovery-selected alternatives evaluated on twelve separate
   holdout seeds per candidate, below the target of sixteen. Joint picks 13/16
   have 34 of 56 plans with complete reduced batches, under an eight-continuation
   cap versus 32 requested. This does not exactly reconstruct the manual trial.

Foundation is custom-scoring production value adjusted for availability with a
small positional-coverage bonus. ESPN rank-only takes the smallest explicit RK;
it does not use custom scoring, availability, ADP, or coverage bonuses to rank
picks. All policies use the same hard roster-feasibility rules and common
forecast-based daily lineup allocator.

## Inputs and assumptions

- The projection benchmark is an audited 195-player October 10 ESPN snapshot.
  An older attended capture was rejected because 94 nonempty records lacked
  required primitive stats. Its ranks could still be recovered independently:
  the ESPN-only extension overlays explicit October 13 RK values after auditing
  unique joins for all 195 players. It is a mixed-date benchmark.
- Market-order proxy means rostered-percentage row order, not measured ADP.
  Availability is inferred from rounded fantasy total/average displays.
- Schedules and outcomes are synthetic; forecast uncertainty was assumed,
  not calibrated using pre-2022 development data. PF/TF/EJ outcomes are missing.
  DD/TD/QD are derived from generated primitive box scores. Interpret scores as
  modeled/common-component results rather than complete league scoring truth.
- Lineups use forecasts and known schedules; realized daily scores are revealed
  only for evaluation. Hindsight lineups are separately labeled upper bounds.
- Rosters remain fixed: no waivers, trades, streaming, or IR replacements.
  Weekly win fractions are opponent-pair results, not championship probabilities.
- Comparisons pair identical external worlds and keyed opponent randomness.
  Intervals bootstrap independent worlds; seats and weeks are correlated.
  Eight worlds is exploratory. Multiple-comparison intervals are unadjusted.

## Published evidence

The [artifact index](ARTIFACTS.json) records SHA-256 digests and file sizes
(text files normalized to LF for portable verification across operating systems).
The [data directory](data) contains portable summaries, normalized forecasts,
the explicit ESPN rank overlay and join audit, acquisition failures, frozen
reference draft, counterfactual checkpoints, and per-draft outcome rows.

`initial_drafts.jsonl.gz` and `espn_drafts.jsonl.gz` retain every completed
140-pick draft history and the candidate IDs/scores recorded on our turns.
They are compact exports, not the full repetitive runtime traces: opponent
candidate lists and per-candidate component breakdowns are omitted. Source
archives retain the exact initial and ESPN-extension research/production code
snapshots. Detailed counterfactual continuation trace sidecars remain local;
the published checkpoint retains branch histories, outcomes, seeds, and metrics.
Absolute workstation paths are replaced by `<workspace>`; browser session state
and account credentials are excluded.

## Reproduce or inspect

Requires Python 3.10+ for the research code. From the repository root:

```powershell
# Verify archive hashes, sample counts, paired reference means, and outcomes.
python studies/draft-strategy-quality/2026-10-15/verify.py

# Run a new bounded campaign against the same frozen projection inputs.
python -m draft_assistant.campaign --budget-minutes 120 --workers 3 --projection-capture studies/draft-strategy-quality/2026-10-15/data/projection.json --output-dir runtime/strategy-quality-reproduction

# Add the actual ESPN RK baseline to that new campaign's balanced worlds.
python -m draft_assistant.campaign_espn --source runtime/strategy-quality-reproduction --capture studies/draft-strategy-quality/2026-10-15/data/espn-ranks.json --output runtime/strategy-quality-espn-reproduction

# Relevant research checks; pytest is a test-only dependency.
python -m pytest draft_assistant/test_campaign.py draft_assistant/test_campaign_data.py draft_assistant/test_campaign_engine.py draft_assistant/test_campaign_history.py draft_assistant/test_campaign_counterfactual.py draft_assistant/test_campaign_espn.py -q
```

The first command audits the published evidence without rerunning simulation.
New budgeted runs may complete different balanced sample counts depending on
machine speed. The fixed seeds and original coverage are in the saved manifests
and summaries. Bootstrap interval endpoints can vary slightly with row ordering;
the verifier checks means exactly and the recorded intervals/classifications
remain preserved. Historical backtesting requires importing the missing data
described in the methods; the new-run commands do not resolve that gap.

Initial validation: 43 research tests and 27 existing strategy/fixture checks.
ESPN extension: 24 focused checks. In addition, all 672 reused marginal roster
outcomes were reproduced exactly, and 240 recommendation-forced branches had
zero regret against their paired reference. All 46 research tests passed together
again before publication, along with the portable evidence verifier.

## Highest-value follow-up

Acquire historical outcomes, schedules, and position evidence; calibrate the
outcome model independently; increase independent-world and continuation counts;
then retest the exploratory Sengun/Bam and Porter Jr./Daniels decision leads.
Keep tuning worlds separate from final evaluation worlds before changing policy.
