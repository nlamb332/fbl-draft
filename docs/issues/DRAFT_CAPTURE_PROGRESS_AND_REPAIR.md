# Draft ticket: Reduce paused catalog capture time and show progress

Observed in ESPN practice league 1556211058 on October 14, 2026, using bundle 17596ee349046a5cc56f35fcca8fc55d8433c86bbcfc959c3df3c7a7bc996327.

The full player scan now advances correctly and verifies the custom scrollbar boundary. However, capture can traverse the entire catalog three times: one initial scan plus two projection repair passes. Every unresolved window can trigger four horizontal attempts, including windows containing players with missing or zero GP that cannot become eligible scoring candidates. The panel only says Capturing, leaving the user unable to distinguish initial traversal, repair, and restoration.

The completed live capture took 12 minutes 47.289 seconds (13:26:26.033–13:39:13.322 UTC), covered 1,095 player IDs across 276 scan steps, verified the actual bottom, and found 348 complete eligible projections. A recommendation subsequently passed against the complete remaining universe after the separate backend subset-warning bug was fixed.

Proposed change:

- Show scan phase, current player count, verified scroll progress and number of repairable players.
- Skip repeated horizontal attempts when the remaining rows in a window are unscorable.
- Retry projection reads only for windows containing unresolved, positive-GP players.
- Stop repair when a full repair pass produces no improvement; preserve verified catalog completeness separately from projection quality.
- Keep all capture bounds and the requirement for actual bottom evidence.

Acceptance:

- A fixture with a long zero-GP tail makes one bounded horizontal attempt per tail window.
- A fixture whose missing projections never hydrate stops repair after one pass with no improvement.
- A delayed projection fixture still repairs transiently missing fields.
- The UI displays progress during scanning and repair without enabling the actor.
- Re-run a paused native ESPN capture and record elapsed time, total IDs, verified boundary and scoring coverage.

Status: implemented with fixture coverage. Live elapsed-time comparison remains outstanding. See [the usability change record](../DRAFT_ADVISOR_USABILITY_2026-10-14.md).
