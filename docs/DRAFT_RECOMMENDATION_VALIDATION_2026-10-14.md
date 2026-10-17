# Observation-only draft advisor — October 14, 2026

Implementation through `23bbb84`, on `codex/draft-release-integration`. GPT-5.6 Luna agents at xhigh implemented the universe, recommendation backend and UI. Parent review corrected capture repair boundaries, atomic history updates, coverage labels, stale handling and terminal read-back verification.

## Behavior

- Attach with `mode: "recommendation"`. Activation, batch execution and actor recovery reject in this mode. The panel offers capture, observation start/stop and recommendation refresh only. The human makes all selections in ESPN.
- A stable-ID catalog and a validated history ledger define the full remaining universe. Scoring uses the eligible subset with complete core projections and GP; the panel distinguishes that coverage from the remaining-player count.
- Current feed, turn controls and roster are refreshed independently of the Players viewport. Confirmed past picks remain in the ledger after the generic observer's retained-history window expires. Missing or conflicting history blocks a fresh recommendation while preserving the last validated universe.
- An owned, CSRF-protected `recommend-preview` endpoint evaluates the configured selection policy in memory. It validates room identity, fresh timestamps, exact rules, snake order, history and own-roster agreement. It never persists the expanded pool as an actionable observation or creates a draft action.
- The panel shows the proposed player and alternatives, score and reason, turn distance, clock, universe and projection coverage, feed/roster health and update time. Stale, blocked or erroneous choices are suppressed. Collapsed diagnostics retain recommendation snapshots for all ten own turns.

## Validation

- JavaScript suite after catalog traversal and capture diagnostics fixes: 188 passed.
- Repository pytest checks outside `draft_assistant`: 407 passed.
- Focused capture and served-bundle recommendation HTTP tests after the capture fix: three passed, including the strengthened terminal read-back check.
- Final full draft-assistant pytest rerun: 197 passed in 336.46 seconds. Together with the non-overlapping repository checks, 604 Python tests passed.

The served-bundle fixture runs the real localhost preview endpoint and in-page recommendation lifecycle against a synthetic ESPN DOM. External simulated manual selections advance all 140 unique picks. The observer reads back the complete 140-pick history and ten-player own roster after the final selection, and records recommendations before all ten own turns. It records zero FBL draft clicks, zero native Resume clicks, no persisted actionable observation and no action lifecycle entries.

This fixture establishes observation/recommendation integration, not live ESPN completion or actor readiness. It does not establish real network timing or sustained native-browser behavior.

## Attended handoff

The localhost service was restarted from the updated integration checkout on October 14. The prior room link for practice league `723024336` reopened as a blank page, so its live state could not be verified.

The user supplied fresh practice league `1556211058`, season `2027`, team `15`. The exact visible practice heading was verified against source league `1000000000`; the room was paused at pick 5 with Example Team in seat 13 and an empty own roster. Session `observe-1556211058` is observation-only. The first loaded advisor blocked recommendations because catalog completeness was unverified; observation was stopped. The captured count was not exposed by that version, so truncation at its 460-player limit is a hypothesis, not confirmed live evidence.

The revised capture traverses up to 1,500 players and 300 steps, verifies actual end-of-list evidence, and reports termination reason and captured count. Reaching either bound remains incomplete and blocks recommendations. Unscorable zero-GP rows stay in the universe without repeated projection repair passes. The revised UI displays capture progress and diagnostics. The tested replacement loader pins bundle hash `5cd3b16e28440224b8fc9fef8580bbb8d8ed1e40172a374a66d6495bad708af9`; a fresh live capture is pending user installation of this loader. The user will make all picks manually. See [the trial procedure](DRAFT_OBSERVATION_TRIAL.md).

### Follow-up live defect

The user installed the replacement loader. Its diagnostics exposed the previous capture count as 464 players. A fresh capture then incorrectly returned 14 players, `catalog_complete: true`, `end_of_grid`, and one scan step. Both boundary signatures were identical, but the actual vertical scrollbar thumb was at the top (22 px thumb within a 519 px track, translated 4 px). This is false completeness; recommendations were not started. A native browser scroll advanced the row window and thumb normally. The capture's synthetic wheel target and its unchanged-window end inference require correction before the live trial proceeds. This live result supersedes any earlier claim that unchanged-window evidence verifies the end of the list.

The follow-up fix dispatches inside the rows container, reads custom scrollbar geometry, rejects non-bottom stalls, filters off-viewport buffered rows, and verifies row identity across horizontal projection reads. The simulator now places its wheel listener inside the grid, matching the observed ESPN structure; its HTTP test explicitly asserts complete capture and end-of-grid termination. Validation: 190 JavaScript tests and three HTTP tests passed, followed by 19 focused capture tests including an additional regression for the live four-pixel scrollbar inset. Native inspection verified the real bottom at thumb translation 493 px within a 519 px track with a 22 px thumb. Corrected bundle `17596ee349046a5cc56f35fcca8fc55d8433c86bbcfc959c3df3c7a7bc996327` is awaiting installation and fresh capture. No live recommendation or completed manual draft is claimed yet.

### Live capture and recommendation passed

The corrected bundle loaded successfully. Capture ran from 13:26:26.033 to 13:39:13.322 UTC (12 minutes 47.289 seconds), returned 1,095 unique player IDs with verified bottom evidence, and identified 348 complete eligible projections. It traversed 276 steps including two repair passes and restored the Players view to the top. Capture speed and progress reporting remain limitations; see [draft follow-up ticket](issues/DRAFT_CAPTURE_PROGRESS_AND_REPAIR.md).

The first preview then exposed a separate backend defect: the explicit `recommendation_catalog_incomplete` scoring-subset warning was rejected by a blanket feed-warning check. The backend now exempts only that exact marker while continuing to reject other incomplete/conflict/gap warnings. All three recommendation HTTP tests passed, including a partial-projection preview with no action or state writes and rejection of an additional unknown incomplete warning. Localhost restarted with the existing attended credential; the browser capture was preserved.

Live preview subsequently passed in the paused room: current pick 5, own turn eight picks away, 1,091 remaining players, healthy feed, matched empty own roster, and Autopick OFF. The displayed priority list was Nikola Jokic (169.39), Luka Doncic (102.52), Victor Wembanyama (97.99). This validates the initial live observation/recommendation, not a completed manual draft. The advisor remains observing while the user resumes ESPN and makes actual selections. The card still shows Team unknown despite the visible player team; this display limitation does not affect the stable-ID recommendation or feed validation. Runtime evidence is saved in ignored `runtime/advisor-complete-capture.txt`, `runtime/advisor-live-ready.txt`, and `runtime/advisor-live-ready.png`.
