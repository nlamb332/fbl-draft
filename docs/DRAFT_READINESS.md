# Draft readiness - October 14, 2026

## Current decision

**Merge approved by the user on October 14.** The observation-only advisor
completed its manual practice trial: 140 picks and all ten own-turn
recommendations matched. The last complete scan was saved, and tested reloads
can start observation without rescanning. See [the trial review](DRAFT_MANUAL_TRIAL_2026-10-14.md).
Automatic drafting issues #7 and #9 remain open, and queue fallback remains
disabled. This new trial validates observation and manual recommendations.

Final October 14 pre-merge verification passed 209 draft-assistant Python tests
and 204 JavaScript tests. The suite counts below are historical October 13
checkpoints.

The current integration implements the guarded queue lifecycle, direct/queue
exclusion, durable dispatch provenance, exact-frame operator retries, terminal
observation publication, and foreground checkpoint validation. Review corrected
response-schema, stale read-back, restart, cancellation, and claim-only
confirmation defects. The final merged Python suite passed **591 tests**,
including the canonical-factory HTTP/export check, with no expected failures
or errors. Node passed **128 tests**. Commands and artifacts are recorded in
[the October 13 validation report](DRAFT_FIX_VALIDATION_2026-10-13.md). These
are offline checks; browser/live gates remain pending. Repository skill source is version
1.3.3; the installed skill and all three reference files were synchronized and
hash-checked on October 13, and skill validation passed.

A Python-to-JavaScript replay feeds actual operator responses through fourteen
teams, thirteen opponents, 140 unique selections, and ten guarded direct picks.
It checks repeated-frame idempotence and rejects changed same-revision frames.
The endurance fixture represents 210 simulated minutes; it does not establish
that duration of live browser continuity. Frozen benchmark artifacts remain
unchanged.

## Owner decisions (2026-10-12)

`marginal-lineup-v1` is accepted as the automatic policy. The coordinator
loads `validation/marginal-lineup-v1/promotion_artifact.json` by default;
`--foundation-only` opts out. Requests cannot supply a promotion artifact.
Lookahead remains advisory. The localhost panel is
`http://127.0.0.1:8765`; the executable start/check path and attended-practice
handoff are in [LOCALHOST_HANDOFF.md](LOCALHOST_HANDOFF.md). It needs a
separate owner-provided ESPN practice-room link and does not start a room.

- **Page trust:** the owner accepts that the in-page heartbeat (#41) requires
  the coordinator CSRF token inside the ESPN page, so scripts on espn.com could
  post board observations. They cannot select the promotion artifact, and page
  dispatch stays off unless `serve --page-dispatch` is set.
- **Unclicked commands:** a prepared command refused before click is released
  as `failed` with a `not clicked:` detail.
- **Live locator check:** before the canonical run, use the read-only
  `live_probe.js` procedure in
  `skills/fbl-attended-draft/references/canonical-rehearsal-runbook.md`.

## Live gates

The real draft has no native Pause. Native pauses used in these practice
diagnostics do not demonstrate real-draft recovery. Acceptance requires a
complete canonical run with the clock advancing after activation, plus
recovery/takeover behavior that continues observing the room. Local Pause
stops FBL writes only; it cannot stop ESPN's timer or opponents.

| Gate | Current evidence / remaining work |
|---|---|
| Canonical browser execution | Pending: current observer/runtime must complete a clean practice room with 140 unique picks, ten guarded direct confirmations, and a legal roster. |
| Timing | Prior prototype completed within the first nine seconds of verified ninety-second turns, with 3.137–4.474 seconds observation-to-confirmation. Repeat with canonical modules. |
| Foreground recovery | Durable checkpoint and explicit resume contracts pass offline tests; retain distinct live evidence. |
| Q1–Q4 queue behavior | Pending ordered insertion/read-back, duplicate no-op/cleanup, timeout and nearby snake consumption, opponent disappearance, and Pause/Autopick-off/Resume behavior. |
| Final integration review | Pending inspection of live artifacts and journals; native or manual picks cannot count as guarded direct picks. |

At the October 11 checkpoint, room 1000000012 was paused at pick 16 with fifteen history picks and one owned
selection. Its ninety-second timer, practice identity,
and callable canonical observer factory were verified. Towns at pick 13 was
submitted once, marked uncertain after an incomplete fast read-back, then
confirmed from full history plus roster. No unresolved actions remain. This is
recovery evidence, not a completed ten-pick rehearsal.

The trial exposed two Windows coordinator processes listening on port 8765,
which caused stale response schemas. Both were stopped; one current server now
returns claim/dispatch provenance. Exclusive listener binding and a real-socket
duplicate-bind rejection test prevent recurrence. Empty ESPN roster slots no
longer invalidate an otherwise hydrated roster read-back. The runtime now
defers our turn's publication to guarded prepare and includes fresh pre-click
state with submitted acknowledgements, avoiding redundant panel roundtrips.
Acknowledgements still cannot confirm a pick; full post-click evidence is
required.

The retained catalog contains 230 unique players; 208 have complete primitive
scoring forecasts and positive GP. Its original forecast capture time is
unknown. The retained snapshot was exported at 2026-10-11T15:05:08.576Z.
Current visible numeric fields may enrich retained records, while PTS and
source labels remain retained. Record that hybrid provenance. Current enabled
rows alone establish availability; the strict filter excludes incomplete
candidates without treating them as drafted.

## Historical browser evidence

The root REPL prototype in room 1000000007 completed 140 unique selections and
ten direct history-and-roster confirmations in one 240.5786-second foreground
invocation, with a legal roster and no recovery pauses. This supports the
prototype's timing result, not the current reusable runner's release gate.
See [browser events](../draft_assistant/validation/browser-recovery-2026-10-11/browser-events.json)
and [sanitized session](../draft_assistant/validation/browser-recovery-2026-10-11/uninterrupted-1000000007.json).

Room 1000000016 elapsed its thirty-second timer before canonical dispatch;
Sabonis and Brunson were native fallback selections. It has zero guarded
direct confirmations and is excluded from the ten-pick gate. Other interrupted
rooms supply recovery evidence only. See [canonical trial report](CANONICAL_DRAFT_REHEARSAL.md),
[October 11 rehearsal](PRACTICE_REHEARSAL_2026-10-11.md), and
[prior practice evidence](PRACTICE_REHEARSAL_2026-10-10.md).

Earlier queue observations established narrow insertion-order timeout behavior
and a native Autopick transition. They do not clear the complete Q1–Q4 gate in
[queue release procedures](DRAFT_QUEUE_RELEASE.md).

## Schedule utilization review

The completed center-heavy roster has been reviewed against the captured NBA
opening-week schedule using exact C/G/G/F/F/UTIL assignments. Its 31 scheduled
player-games yield 25 usable starts: 80.6% of scheduled games and 59.5% of the
42 lineup slots. Guard/forward replacements can add two usable starts in this
bounded week, but health and several scoring forecasts remain unknown.
The review is complete and advisory only; `marginal-lineup-v1` remains the
automatic default, with foundation available through `--foundation-only`.
See [utilization release review](DRAFT_UTILIZATION_RELEASE.md).

## Frozen strategy evaluation


The nominal matrix uses 100 independent seeds across all 14 seats: 1,400 paired comparisons, 2,800 complete drafts, 13 opponents per draft, and 392,000 unique-within-draft selections. All 14 rosters in every draft pass legality checks. The live policy's first choice is executed without hidden opponent-roster repairs.

Both live and validation use eight candidate rollouts and a two-second budget. Scoring uses custom per-game points, GP/82 or supplied availability, a small coverage adjustment, and exact future roster feasibility. A retained catalog supplies opponent eligibility. Incomplete inputs or unfinished rollouts fall back to foundation.

| Metric | Result |
|---|---:|
| Mean modeled weekly gain, lookahead minus foundation | 2.674 points (0.23%) |
| Paired 95% bootstrap lower bound, clustered by seed | -3.094 points |
| Worst seat's mean gain/loss | -1.13% |
| Lookahead promotion gate | FAIL |

Lookahead is not promoted and may be displayed as advisory. The accepted
`marginal-lineup-v1` artifact is the automatic selection policy; foundation is
selected only with `--foundation-only`.

Artifacts: [summary](../draft_assistant/validation/heldout-v2/validation_summary.json), [paired rows](../draft_assistant/validation/heldout-v2/heldout_runs.csv), and [full report](../draft_assistant/validation/heldout-v2/heldout_validation.json).
Algorithm: draft-strategy-v2. Fingerprint: dc4c94b93b4219e4c317dd54660886f9b84c2f36437a5cc83c56dd8cab0fcd2d.
Matrix elapsed time: 1473.1 seconds.

These are synthetic forecasts, schedules, and modeled opponents. The matrix measures usable fantasy points under those assumptions, not real-player forecast accuracy or calibrated league win probability. Market/custom opponent-misspecification scenarios are supported but are not part of this nominal promotion result. No tuning follows this held-out result.

## Operating requirements

Real activation requires fresh room/team/rules/order/roster/Autopick checks and
the user's explicit Start handoff. Practice authorization does not transfer to
the real room. The active agent operates the signed-in browser; the local
server does not independently control Chrome. An awake computer and active
agent session are required. Preserve the ten-second freshness guard.

## Connectivity: in-page heartbeat (issue #41)

In the real room there is no native Pause, so a stale pause during our turn
becomes an ESPN autopick. Frames therefore must not depend on the agent's
tool-call cadence.

**Heartbeat.** `draft_assistant/heartbeat_publisher.js` runs inside the ESPN
page: a MutationObserver on the Feed/clock (150 ms debounce) plus an interval
of at most 2 s capture a frame with the unchanged browser observer
(`createPageTab` gives it the live DOM) and POST it to `/api/heartbeat`. Only
complete frames are sent; an incomplete frame sends nothing and never
refreshes freshness. The coordinator treats a frame whose visible board equals
the latest observation as a liveness touch (same revision, so claims stay
valid); a changed board becomes a new revision numbered by the coordinator, so
the page and the agent never share a counter. An agent frame that repeats a
board the heartbeat already numbered is an idempotent retry. Before each
agent batch, seed the observer counter above `latest_revision` from status.

**Freshness by state.** The 10 s action guard is unchanged: no recommendation,
claim, final guard, or dispatch is possible on a stale frame, in any state.
When the board goes stale (detected on any status/ingest/heartbeat or action
call) the session becomes:

- `degraded` when nothing is pending, claimed, submitted or uncertain (actions
  or queue mutations) and the last frame showed an opponent on the clock. It
  returns to `running` on the next complete, fresh, readiness-clean frame
  whose history extends the last confirmed history, and journals
  `degraded_recovered` with `gap_seconds`.
- `paused` (explicit resume required) when an action is unresolved, the last
  frame showed our own turn or an unknown clock team, the frame after the gap
  is non-contiguous or contradicts the journal, or the room identity changed.
  A refused recovery journals `degraded_pause`. A restart pauses `degraded`
  sessions like `running` ones.

**Telemetry.** Every published frame records its gap from the previous frame.
`status` and `/api/export-session` include p50/p95/max, a histogram
(`<=0.5s ... >60s`), the count of gaps over the freshness limit, and the
number of frames posted while the tab was not visible.

**Browser constraints (investigated, not yet confirmed live).**

- Mixed content: Chrome and Firefox treat `http://127.0.0.1` as potentially
  trustworthy, so an HTTPS espn.com page may fetch it. Safari is not
  supported. ESPN's Content-Security-Policy `connect-src` could still block
  the request; that cannot be settled offline and is the first live check.
- CORS: the response must carry `Access-Control-Allow-Origin` for the exact
  page origin. The server allows only `https://fantasy.espn.com` and
  `https://www.espn.com`, only for POST to `/api/heartbeat` (and
  `/api/operator` prepare/ack/reconcile when `--page-dispatch` is on), and only
  with the `X-CSRF-Token` header. `/api/csrf` and every GET remain unreadable
  cross-origin; the operator injects the token into the page.
- Private Network Access: a public-site to loopback request sends a preflight
  with `Access-Control-Request-Private-Network: true`; the server answers
  `Access-Control-Allow-Private-Network: true` for the ESPN origins only. Newer
  Chrome releases replace this with a Local Network Access permission prompt
  for the site; accept it once in the attended browser before the draft, then
  re-check with a practice room.
- Background tabs: Chrome throttles timers in hidden tabs (about one run per
  second, then once per minute after several minutes, and intensive throttling
  of chained timers). The draft tab must stay visible and in the foreground,
  the computer awake, and the window unoccluded. Each post reports
  `document.visibilityState`; the coordinator counts non-visible frames
  (`hidden_tab_frames`) and the in-page dispatch never runs from a hidden tab.

**In-page guarded dispatch (phase 2, off by default).** Enabled only when both
the coordinator is started with `--page-dispatch` and the publisher is given
`guardedDispatch: {enabled: true, runtime, sessionId, ownTeam}`. After a post
that the coordinator acknowledges as `running`, on our turn, autopick off, tab
visible and frame under 10 s old, the publisher calls the supplied operator
runtime's `runTurn` once per pick. That is the unchanged claim, dispatch,
confirm contract (prepare, final guard, one click, `submitted` ack, post-click
confirmation, reconcile). The runtime must be created with its own
`postClickConfirmation` (issue #42). A failed or not-attempted turn is not
retried by the publisher.

**Queue pre-arm (design only; implement under #7).** Before each owned turn,
keep the top N feasible recommendations in the ESPN queue so a timeout takes
our choice. Proposed shape: (1) at the pick before ours, the strategy returns
the ranked list filtered by feasibility (still-available in the latest frame,
roster-legal); (2) the existing queue lifecycle (`prepare` / `claim` /
`final-guard` / `ack` / `reconcile`, one unresolved mutation at a time) writes
the queue in rank order, reconciling each mutation against the visible queue;
(3) the heartbeat re-verifies the queue on every board change and re-prepares
only the changed entries; (4) the queue is a fallback only: the direct
Draft click remains primary and a queue mutation never overlaps an
unresolved direct action; (5) known risks: ESPN queue may reorder or drop
drafted players, and queue writes cost clock time, so pre-arm must complete
before our turn starts and stop once we are on the clock.

The original release gate requires measured latency on verified ninety-second
turns and safe recovery. A ninety-minute wall-clock rehearsal is additional
endurance evidence; accelerated logical-time tests remain labeled separately.

This standalone checkout contains the draft integration snapshot; it does not
depend on a parent integration worktree. See the [operator runbook](DRAFT_RUNBOOK.md)
and the versioned [attended draft skill](../skills/fbl-attended-draft/SKILL.md).
