# Draft implementation roadmap

This repository owns the FBL draft assistant, its strategy models, and the
reproducible draft studies. Existing simulation code is research infrastructure;
it does not by itself execute live ESPN actions.

## Foundation

- [x] Capture league rules, scoring, roster slots, and the transaction approval
  boundary relevant to draft actions.
- [x] Build reproducible 14-team mocks and validate unique picks, roster
  feasibility, and lineup allocation.
- [x] Preserve strategy results and data provenance beside the source studies.
- [ ] Create a versioned league configuration and timestamped player/state
  contract shared by the live adapter and simulations.
- [ ] Ingest current projections, actual draft order/ADP, eligibility, and
  official draft-room inputs while marking missing or stale values.
- [ ] Keep execution audit logs, dry-run mode, state reconciliation, and
  duplicate-action prevention covered by the release checks.

## Automated drafting

- [ ] Upgrade valuation using complete custom scoring inputs, availability,
  replacement value, and roster fit.
- [ ] Replay draft events with 13 opponents and measure choices, latency,
  feasibility, and stale-board recovery.
- [ ] Read the visible draft room, our seat, remaining players, timer, and
  completed selections.
- [ ] Submit and verify picks; handle unavailable players, retries,
  disconnects, and manual takeover.

Acceptance: in a verified 14-team test, complete ten legal selections within
deadlines, with no duplicate submissions and a history-plus-roster read-back
for every pick.

## Open validation needs

Credible foul and double/triple-double forecasts; ESPN bonus overlap behavior;
actual market rankings; live integration reliability; and historical projection
accuracy. Synthetic simulation gains alone do not resolve these questions.
Imputation constants were re-estimated from public data and backtested in
`docs/IMPUTATION_CALIBRATION.md` (flag-gated, default off; double-doubles and
real preseason projections remain open).
