# Draft utilization review

This review evaluates the final rosters from the completed October 11 trial
(`trial-1000000013`) and the later uninterrupted timing room
(`uninterrupted-1000000007`)
against the verified league shape: 14 total teams, 13 opponents, ten rounds,
140 total picks, daily `C, G, G, F, F, UTIL`, four active bench spots, three IR
slots, unlimited games, and four acquisitions per matchup.

Both rosters are legal. The interrupted trial's eligibility counts are:

| measure | result |
|---|---:|
| players | 10 |
| C-eligible | 7 |
| G-eligible | 2 |
| F-eligible | 4 |
| C-only | 5 |
| daily starting capacity | 6 |
| daily bench capacity | 4 |

An all-active day can fill all six slots and leave four players on the bench.
That is a capacity check. It does not establish that six players will have
games on a real day. The final roster is more center-concentrated than the
earlier practice roster: five players are C-only and only two are G-eligible.

The later uninterrupted room finished with a different but more center-heavy
shape:

| measure | `uninterrupted-1000000007` |
|---|---:|
| players | 10 |
| C-eligible | 7 |
| G-eligible | 2 |
| F-eligible | 3 |
| C-only | 6 |
| daily starting capacity | 6 |
| daily bench capacity | 4 |

This is a structural risk signal for close draft decisions: six players can
fill only the center slot and UTIL when they are C-only, while the roster has
only two G-eligible players and three F-eligible players. It is not a forecast
of lost starts. The clean room's final observation has no dated schedule,
game-count matrix, or complete health probabilities, so actual daily starts,
weekly utilization, and schedule-avoidance value remain unmeasured.

The final observations contain no dated NBA schedule or player health matrix,
so a real daily start rate cannot be calculated from it. The captured records
also mark missing scoring fields for all ten players. Any partial stat fields
are retained for provenance only and must not be treated as complete custom-FP
projections or numeric evidence of actual performance.

Two explicit one-day mechanics tests show why legality alone is insufficient:

| synthetic active set | active roster players | active starts | start utilization |
|---|---:|---:|---:|
| five C-only players | 5 | 2 | 33.3% |
| three non-center players | 3 | 3 | 50.0% |

Both cases use the same legal ten-player roster. The first can fill only C and
UTIL with active players. The second has only three distinct G/F-eligible
players; no player can be reused across two slots. These are adversarial
mechanics scenarios, not forecasts of the actual 2026-27 schedule. The
scenarios are deliberately excluded from any claim about the trial's actual
weekly performance because no real schedule was captured.

The frozen foundation policy remains the production rule: custom points with
availability, a small coverage tie adjustment, and exact legal roster
feasibility. The frozen lookahead validation and the previous synthetic and
ESPN-input matrices remain unchanged. The new module is deliberately isolated
from `valuation.py`, the coordinator, and the live browser path.

The advisory candidate policy in `draft_assistant/utilization_review.py` is
appropriate for research review when a dated schedule matrix is available. For
each candidate it:

1. solves the exact six-slot assignment for every supplied day;
2. computes the candidate's marginal usable lineup score and active-start delta
   relative to the current roster or an explicitly named drop;
3. applies a soft balance adjustment for guard/forward shortfalls and for
   center eligibility or C-only congestion; and
4. keeps missing-stat warnings visible and leaves the result advisory.

The adjustment is intentionally soft. It does not impose a fixed position
quota and it cannot promote itself into the live policy. A high-production
center can still win when its marginal usable value is materially higher. A
lower raw-value G/F candidate can win when it supplies an otherwise missing
active start, as shown in the focused policy test.

The recommendation is to preserve the current live foundation and use this
policy only as a review column after obtaining dated schedules, health
probabilities, and complete scoring inputs. The captured practice roster gives
the policy a clear reason to scrutinize further C-only additions and to favor
G/F bench depth in close decisions. It does not support a retroactive claim
that the completed roster lost a known number of weekly starts, nor does it
support automatic adds, drops, or a production-policy change.

Validation evidence:

- `python -m pytest draft_assistant/test_utilization_review.py -q` — 8 passed.
- The earlier
  `draft_assistant/validation/utilization-review-v1/analysis.json` artifact is
  retained unchanged as a prior practice snapshot; the final-trial structural
  source is the durable observation below.
- The source observation for the interrupted trial is the final frame in
  `draft_assistant/validation/browser-recovery-2026-10-11/trial-1000000013.json`.
- The uninterrupted room's sanitized terminal projection is
  `draft_assistant/validation/browser-recovery-2026-10-11/uninterrupted-1000000007.json`;
  it preserves the legal roster and C7/G2/F3/C-only6 structure while omitting
  the full player catalog.
- The two synthetic scenarios were rerun on this exact final roster in
  `draft_assistant/validation/browser-recovery-2026-10-11/trial-1000000013-utilization.json`.
  Unit values isolate active-start mechanics; they are not player forecasts.
- The focused module uses exact matching and deterministic tie-breaking; it
  rejects missing or unequal-length schedules instead of filling them with
  guessed availability.
