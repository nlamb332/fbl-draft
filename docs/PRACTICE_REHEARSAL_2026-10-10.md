# Automated practice rehearsal — October 10, 2026

**Functional result: complete. Real-draft release: withheld.**

The user supplied practice room 1000000005, season 2027, team 15. The visible
heading identified a practice draft for Example League. Rules matched
14 teams, ten rounds, C/G/G/F/F/UTIL, four bench, three IR, and custom scoring.
Example Team was seat 13; all thirteen other teams were AUTO. The timer was
set and read back as 90 seconds. The agent executed ten direct Draft clicks
under the user's existing automatic-picking authorization; no per-pick approval
was requested. Foundation policy remained automatic; lookahead stayed disabled.

## Authoritative result

The completed browser room, full Pick History, and our roster agree. The local
coordinator reached `completed`; all ten journaled actions are `confirmed`.
There are 140 contiguous, unique player selections, ten per team.

| Pick | Player | ESPN final slot |
|---:|---|---|
| 13 | Domantas Sabonis | C |
| 16 | Jalen Duren | UTIL |
| 41 | Jarrett Allen | BE |
| 44 | Donovan Clingan | BE |
| 69 | Jaren Jackson Jr. | F |
| 72 | Ivica Zubac | BE |
| 97 | Josh Hart | F |
| 100 | Jabari Smith Jr. | BE |
| 125 | Grayson Allen | G |
| 128 | Kyshawn George | G |

Evidence: [final observation](../draft_assistant/validation/browser-practice-2026-10-10/final_observation.json),
[action journal](../draft_assistant/validation/browser-practice-2026-10-10/action_journal.json),
and [timing CSV](../draft_assistant/validation/browser-practice-2026-10-10/latency.csv).
Dates in these files are UTC; the run occurred on October 10 in Chicago.
Member identifiers, browser credentials, claim tokens, and the runtime database
are excluded from the exported evidence.

## Findings and limits

- Actual direct submission, exact player-ID targeting, snake turns, history plus
  roster confirmation, ten-player legality, and terminal completion succeeded.
- Initial helper integration needed two ESPN pauses. Pick History has one grid
  per round, roster names use full DOM titles, and native scrolling needs ESPN
  focus after panel commands. These details are now in the installed skill.
- An operator result visibility wait timed out after a successful prepare.
  The agent read the existing result instead of preparing again. **The first
  command then aged about 20 seconds before its click. This exceeded the
  ten-second freshness requirement and was an operator error.** The skill now
  requires prepare/deadline-check/click in one batch and forbids executing an
  aged command recovered after a response timeout. This run cannot pass the
  execution freshness release gate.
- Once the loop stabilized, claim-to-submission acknowledgment was 0.7–0.9
  seconds; those six selections were submitted with roughly 78–86 seconds left.
  Complete observation-to-confirmation took 7.3–14.3 seconds for five of those
  six; the final confirmation took 39.2 seconds after Players stopped rendering
  a grid. The first three confirmations took 11.9–62.4 seconds. CSV timestamps
  measure durable acknowledgment/confirmation, not instantaneous ESPN render
  latency. Do not report the full run as uniformly meeting the 30-second goal.
- Fast opponent selections can outrun a history/table capture. Guards paused
  the coordinator on stale or incomplete frames; the agent explicitly resumed
  only on a fresh consistent own-turn frame under the existing authorization.
- This was a partial visible-window foundation draft. Bonus/foul forecasts were
  explicitly missing; live injury adjustments were not fully captured. The
  resulting roster is legal but has six primary centers and a center-heavy
  bench. Legality alone does not prove optimal daily start utilization or player
  forecast quality. Evaluate position bottlenecks and injury inputs separately
  without changing the frozen benchmark artifacts.
- Queue consumption/timeout fallback and deliberate human takeover/reconnect
  were not tested in this full run. Earlier queue insertion/removal evidence
  remains narrower than fallback verification.

## Next release gate

Run a fresh uninterrupted practice with the corrected capture/dispatch loop,
immediate history-and-roster confirmation, and complete timestamp logging.
Verify queue fallback and takeover/reconnect separately. Keep automatic
lookahead off. Final real-room activation still requires fresh identity/rules
checks and the user's explicit Start handoff.
