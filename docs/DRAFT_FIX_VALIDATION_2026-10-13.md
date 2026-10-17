# Draft fixes and localhost handoff — October 13, 2026

The next supported trial is an attended ESPN practice: the owner supplies the localhost panel URL and the practice-room URL, then gives the verified room its explicit Start handoff. The reusable factory is `draft_assistant/canonical_runner.js`; the start/check instructions are in [LOCALHOST_HANDOFF.md](LOCALHOST_HANDOFF.md).

## Tickets and changes

- [#51](https://github.com/nlamb332/fbl-automation/issues/51): ownership transfer now pauses both running and degraded sessions atomically with owner-token rotation. The shared pause implementation cancels only unsent work and preserves dispatched evidence. Fresh heartbeat cannot resume the transferred session; the successor must explicitly Resume.
- [#52](https://github.com/nlamb332/fbl-automation/issues/52): a separate clean offline rehearsal combines the canonical observer/runtime, real local HTTP, the accepted marginal-lineup artifact, and heartbeat. It completes 140 unique selections and ten guarded confirmations without recovery, including an 11-second gap in agent calls.
- [#53](https://github.com/nlamb332/fbl-automation/issues/53): subprocess report validation now preserves exit code/stdout/stderr before parsing, and setup registers cleanup immediately. One prior system-Node 24.15.0 exit with code 3221226505 and empty streams remains unexplained. Final validation uses bundled Node 24.19.0; do not claim the older runtime's failure was diagnosed or silently retry failed drafts.
- [#54](https://github.com/nlamb332/fbl-automation/issues/54): added a localhost launcher that verifies the actual service's identity, startup source hashes and configured policy; owner-gated operator-form Pause; a reusable practice runner with explicit activation latch, fresh unchanged activation evidence, guarded exact-row picks, history/roster confirmation, and terminal evidence preservation; and a sanitized exporter. Strategy guidance and skill source now match the accepted policy.

The release branch also includes current main's roster system. Main itself has not been advanced by these fixes; PR #39 remains the integration review boundary.

## Validation

Implementation revision: `f2e0927` (documentation/evidence commits may follow).

- First full merged Python suite: **590 passed**, 210.27 seconds, no expected failures or errors.
- After adding the actual reusable-factory HTTP test: **one additional test passed**, including the sanitized export CLI. It uses the real factory, observer, activation, view switching, runtime, and coordinator; only ESPN's DOM/opponents are simulated.
- Final JavaScript suite: **128 passed**. It includes activation safety and runner-assembly tests.
- Localhost launcher smoke: accepted matching source/policy for marginal-lineup and foundation; refused the opposite requested policy. No persistent test service was left running.
- Final complete Python rerun including the factory test: **591 passed**, 227.03 seconds, zero failures, errors or expected failures. This also repeated both complete offline rehearsals on the final integrated implementation.

Python: installed 3.13. Node: bundled 24.19.0, selected first on PATH. Windows sandbox limitations required the local tests to run outside the sandbox; initial sandbox database/network errors are not product failures.

The final combined artifact records ten promoted recommendations, a maximum publication gap of 2.017 seconds, and zero gaps over the ten-second limit. Both retained offline artifacts show 140 unique picks, ten owned confirmations, and a legal ten-player roster:

- [Combined heartbeat rehearsal](../draft_assistant/validation/release-review-2026-10-13/combined-offline.json).
- [Reusable factory and sanitized exporter](../draft_assistant/validation/release-review-2026-10-13/canonical-factory-offline.json).

These are offline artifacts. They do not establish real ESPN DOM compatibility, permission/CSP behavior, or live timing.

## Live boundary

The new factory is scoped to one continuously awaited foreground practice run. It does not wire the independent in-page heartbeat or establish survival across long agent/tool gaps. That longer-session integration remains part of #41/#9 live readiness. Queue fallback and page dispatch remain disabled by default. No ESPN room was opened or started during this work.

For the next trial, launch/check the panel from the integration checkout with `scripts/local-panel.ps1`, supply both URLs, run the read-only locator/connectivity checks in the actual signed-in practice browser, verify room/rules/order/seat/roster/timer/Autopick, then give Start. Run `prepareActivation()`, `activate()`, and `runBatch()` in the bounded canonical flow. If any guard refuses, inspect and reconcile before explicit recovery. A practice authorization does not authorize the real draft.
