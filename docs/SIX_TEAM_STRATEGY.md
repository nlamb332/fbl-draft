# Six-team points strategy

Created October 17, 2026 (America/Chicago). Manual strategy and local research profile. Live advisor support is not enabled. Settings come from the user in this chat, including the final correction that both bonus pairs stack.

Machine-readable settings: [six_team_points.json](../profiles/six_team_points.json).

## League settings

Six teams; fourteen ordinary roster spots: PG, SG, G, SF, PF, F, C, UTIL, UTIL and five bench spots. A full non-keeper snake draft therefore has fourteen rounds and **84 unique picks**, with five opponents and nine starting slots per team. Snake format and no keepers are research assumptions, not newly verified settings.

| Scoring event | Points |
| --- | ---: |
| NBA points scored | 0.50 |
| Rebound / assist | 1 each |
| Steal / block | 2 each |
| Turnover | -1 |
| Double-double | 1 |
| Triple-double | 2 |
| Technical / flagrant foul | -2 each |
| Three-pointer made | 0.50 |
| 40+ points / 50+ points | 2 each |

Bonuses stack: a triple-double receives DD and TD bonuses, totaling 3. A 50-point game receives both scoring bonuses, totaling 4. A made three contributes 1.5 through NBA points and 0.5 through the made three: 2 fantasy points before other events. Field-goal attempts, misses, free-throw attempts and personal fouls have no separate weights in the supplied rules. Flagrant fouls are distinct from ejections.

For per-game forecasts, use event probabilities on a consistent basis:

`FP = 0.5*PTS + REB + AST + 2*STL + 2*BLK - TO - 2*TF - 2*FF + 0.5*3PM + P(DD) + 2*P(TD) + 2*P(PTS>=40) + 2*P(PTS>=50)`.

DD probability includes triple-doubles; 40+ probability includes 50+ games. Do not infer these probabilities from a player's averages. Missing event forecasts remain unknown for real players; synthetic rates in the research fixture are assumptions.

## Decision

Keep custom scoring, availability, usable lineup contribution and exact roster feasibility as principles. **Do not reuse the old numerical strategy unchanged.** Its scoring, roster shape and fourteen-team validation do not apply to these rules.

The old scoring used different relative weights and separately adjusted makes, attempts and personal fouls. There is no basis to preserve an automatic big-player preference. Scoring bonuses add value to high-scoring games, but their effect requires event-rate forecasts. The reduced weights on rebounds, assists, stocks and turnovers do not by themselves establish a new ordering among those player types; points also have a reduced weight.

| Quantity | Existing profile | New profile |
| --- | ---: | ---: |
| Teams / opponents | 14 / 13 | 6 / 5 |
| Roster size / draft rounds (no keepers) | 10 / 10 | 14 / 14 |
| League drafted players | 140 | 84 |
| League starting slots | 84 | 54 |
| Maximum intervening snake picks | 26 | 10 |

Under a common player catalog, fewer league-wide picks leave more undrafted players. However, each team now starts nine and carries five bench players. Simple scaling of the old C/G/F replacement formula is inappropriate. League-wide demand is six each at PG, SG, SF, PF and C, plus six G, six F and twelve UTIL slots. Overlapping eligibility requires matching; these counts are not positional replacement ranks.

## Draft decisions

1. Recalculate forecasts under the new scoring. Evaluate early picks by custom production and availability. Treat market ranks as timing information, not custom value.
2. Preserve native PG/SG/SF/PF/C eligibility. G accepts PG or SG; F accepts SF or PF; UTIL accepts any position. Exact matching must fill all nine slots simultaneously. Collapsing eligibility into C/G/F can conceal an unfillable SG or PF slot.
3. Reassess positional supply and legal lineup contribution each round. Prefer flexibility when it creates useful starts at little production cost. Avoid fixed big-player, guard or forward quotas. Repeated C-only picks can compete for C and the two UTIL starts.
4. Use six-seat snake timing: zero through ten intervening opponent selections. Check the actual tier and board before reaching. A shorter wait does not guarantee a player survives.
5. Late picks should exceed realistic undrafted alternatives or offer credible role growth. Five bench spots can support rotation and upside, but their value depends on lineup rules and the actual replacement pool.
6. Retain availability. Injury stashes and streaming require actual transaction, lock and IR rules. Those rules were not supplied, so this profile does not assume three IR slots, unlimited acquisitions or a particular lock cadence.

## Evidence and limits

The [production marginal strategy](DRAFT_STRATEGY_MARGINAL.md) and [prior campaign](../studies/draft-strategy-quality/2026-10-15/RESULTS.md) used different scoring, fourteen teams and C/G/G/F/F/UTIL. Their gains and confidence intervals are not evidence of superiority here. The old tuned replacement weight of 0.5 is not carried into this profile as an established optimum.

The separate six-team experiment tests synthetic strategies under the supplied scoring and native positions. It does not execute production `marginal-lineup-v1`. Results test mechanics and conditional preferences; they cannot establish current player values, calibrated injury forecasts or championship probabilities.

Research assumes fixed rosters and daily lineup selection with synthetic schedules. Waivers, streaming, trades, IR and late injury information are omitted. Daily selection is an experiment assumption awaiting confirmation of this league's rules. Transaction guidance remains conditional.

## Reproduce and review

The [completed study and scoring plots](../studies/six-team/2026-10-17/README.md) cover 360 drafts. The development-selected lineup-coverage heuristic scored 1,491.0 usable points per modeled week on held-out pools, versus 1,435.6 for custom points per game (+3.9%). Its improvement over availability alone was inconclusive. These results favor retaining availability and lineup fit, while rejecting a fixed big preference in this synthetic model; they do not establish a real-player optimum.

From a full clone of the draft repository:

```powershell
python -m unittest mock_draft.test_six_team -v
python -m mock_draft.six_team --output-dir runtime/six_team_research
```

The runner reads the JSON profile, checks its settings and compares custom per-game, availability, market, lineup-aware and big-preference heuristics across all six seats. Development seeds select one fixed heuristic; independent held-out pool seeds audit that choice. Draft choices and lineup selection use forecasts; a separate synthetic realized-stat copy supplies evaluation scores. The lineup-aware heuristic is a slot-coverage adjustment, not a rollout or production marginal policy.

Three GPT-5.6 Luna agents at XHIGH were assigned assessment, implementation and independent challenge/integration review. The assessment and challenge both rejected transferring the old strategy unchanged. Review focused on the corrected bonus semantics, native position matching, legal 84-pick drafts and separation of forecast decisions from realized evaluation. Generated research is excluded from the minimal download archive.

## Before live advisor use

The ESPN advisor's `LeagueConfig` still requires the original fourteen-team shape. This research profile cannot be passed as its `--league-config`. The separate Sleeper advisor is being developed on `sleeper-advisor`; this study does not validate that advisor's recommendation algorithm. Live support requires native-position observations, new scoring events, nine-slot matching and policy-specific validation. Reusing the old promotion artifact or merely relaxing its team-count guard would not supply that evidence.
