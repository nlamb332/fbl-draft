# Automated draft fixture finalization — October 13, 2026

Implementation reviewed through `8755b0d`. The fixes were delegated to GPT-5.6 Luna at xhigh reasoning, then integrated and reviewed in `codex/draft-release-integration`. Main and the real ESPN draft remain outside this trial.

## Addressed findings

- Fresh page attachment restores a durable checkpoint from confirmed, claimed and dispatched coordinator draft actions. Native history alone never increases the guarded pick count. The attachment checkpoint is consumed once; explicit recovery checkpoints remain supported.
- The page adapter refuses mutating clicks when the document is hidden. Native Resume checks visibility at the click boundary and attempts a coordinator pause on refusal.
- The served-bundle fixture now exercises default `captureInputs()` without supplied rules/catalog or an injected history scroller. It covers Rules, Projected/Averages, Show Drafted and restoration, horizontal/vertical Players wheels, drafted catalog identities, and virtualized history.
- A controlled advancing-clock adapter covers synthetic deadline expiry, opponent progress during reads/confirmation, uncertainty and no duplicate click. A real localhost coordinator plus the canonical runner test refuses a delayed own-turn frame with an expired clock and records zero clicks or dispatched actions.
- Visible in-page controls support the attended connector. Loading them only attaches the room; preflight must pass before activation. Activation and batch execution share one control handler. Local Pause explicitly does not pause ESPN's native clock.

## Evidence boundaries

The standalone advancing-clock driver's 140/10 result is simulator-only direct public API evidence. It is explicitly labeled `canonical_runner: false` and `served_coordinator: false`. The separate deadline guard test uses the actual canonical runner and coordinator, but its DOM, clock and opponents remain synthetic. Its timestamp skew is test plumbing, not latency evidence.

Fixtures do not establish ESPN CSP/local-network permission compatibility, real network delays, background throttling, native timeout/autopick behavior, or sustained real-time endurance. These remain attended browser gates. Queue fallback stays disabled.

## Validation and trial

- Final JavaScript suite: 156 passed.
- Served page capture plus existing page-runner HTTP suite: 8 passed.
- Fresh attachment restart HTTP regression: 1 passed.
- Canonical advancing-clock HTTP regression: 1 passed.
- Python unittest discovery: 164 passed; this excludes module-level pytest functions.
- Final draft-assistant pytest suite: 195 passed in 308.16 seconds.
- Remaining repository pytest checks (`--ignore=draft_assistant`): 407 passed in 88.21 seconds. Together the non-overlapping suites cover 602 passing Python tests, with no failures. The separate unittest run is overlapping verification and is not added to this total.

Attended practice session `attended-723024336` is connected on `http://127.0.0.1:8765/`. Its observed room heading identifies a practice draft for Example League, source league `1000000000`, team `15`, season `2027`, seat 13 and native timer 30 seconds. The room was paused at pick 2 with no own selections during setup. The reviewed loader was successfully mounted with bundle hash `f1433461c322ba4e70a4e44d51850baea3711cfa41c309cc4d9c635faccdd885`. Default input capture found 356 players, 234 with complete core stats. Preflight passed with 14 available visible players, one history pick, zero roster players, Autopick off and one coordinator listener. Explicit Start remains pending. This setup is not a completed live draft.
