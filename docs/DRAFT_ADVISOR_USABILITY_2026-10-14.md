# Advisor movement, faster capture, and saved scans

Implemented after the user could not reach ESPN's Resume control behind the fixed advisor.

## Interface

The advisor has a draggable handle, arrow-key movement, viewport clamping, Dock left/right, and a collapsible compact bar retaining the current recommendation and turn distance. Moving or collapsing it does not stop observation. Remounting it recognizes an already-running observer. Capture displays scan/repair phase, captured count and progress. Loaded inputs show their source and capture time; the capture button becomes Rescan players.

Scan/Rescan now owns the room lifecycle needed for a complete capture. When the
verified room is a practice draft, it waits for the draft to start, pauses the
native clock once, and then scans the full rules/player catalog. A verified real
room is accepted when its league ID matches the configured live league; it
begins scanning immediately and is never natively paused by the advisor.

## Capture

Capture reads the rendered DOM rather than screenshots. ESPN virtualizes rows, so scrolling remains necessary. A cheap identity/GP read covers the player universe; expensive horizontal projection reads are skipped for no-data windows and resume if a later positive-GP window appears. Repair skips complete or unscorable windows and stops when missing-field signatures stop improving. The initial verified catalog boundary is preserved separately from best-effort repair outcomes.

The prior native scan covered 1,095 players, with 348 complete projections, in 12 minutes 47 seconds. The optimized implementation has fixture coverage; its new native elapsed time has not yet been measured. No-data players remain identifiable to the draft feed and are excluded from the scoring subset.

## Saved scans

Recommendation mode automatically loads a versioned localStorage scan scoped to season, team ID and own team. Reuse across fresh practice league IDs is permitted within that scope. Before using cached projections, the runner reads the current room's Rules tab; it does not rescan the Players table. Current feed/history and roster remain fresh and are never restored from the scan.

Only an explicit complete capture replaces the saved scan. Partial/corrupt/mismatched scans are rejected; storage failures are non-fatal. The saved record contains catalog, rules and capture metadata, not credentials, actions, roster or draft history. No automatic rescan is performed.

The currently loaded pre-cache runner cannot export its captured catalog. A reviewed UI-only upgrade therefore preserves that existing runner and catalog in the current tab. Installing the full updated loader enables the new capture/cache engine; its first explicit capture creates the reusable saved scan. This migration distinction must be stated when handing off.

## Validation

- Final pre-merge draft-assistant Python suite: 209 passed in 328.64 seconds.
- 204 JavaScript tests passed.
- Four capture/recommendation HTTP integration tests passed.
- Browser preview verified dock, compact/expanded views and keyboard movement with illustrative data only.
- The subsequent live manual draft completed in room 200082403: 140 selections, all ten own-turn recommendations matched, and a healthy terminal feed. See [the trial review](DRAFT_MANUAL_TRIAL_2026-10-14.md).

## Reusable room connection

The old runtime loader was pinned to practice league 1556211058 and refused the next room, 10000000140. `recommendation_loader_snippet` and the `advisor-loader` CLI now produce a room-agnostic loader. It reads the numeric room IDs and practice heading from the current page and the entire visible fourteen-team roster option order, independent of the selected roster. The server derives our seat from its configured team name and binds a new observation-only session. Member IDs are omitted from the connection URL.

The returned observer capability permits only status and recommendation preview; actor, queue, ownership and actionable publication operations are rejected. The panel defaults to compact and left-docked without starting capture, observation or ESPN's clock. Same-room reattachment can reuse an existing captured recommendation runner. An active actor batch is refused before replacing its bundle.

Validation: 22 targeted Python tests passed across the generic loader, advisory connection, existing page-runner HTTP lifecycle, server binding and recommendation preview. The loader fixture exercises two different transient practice IDs and different seats using one snippet. The user then loaded it successfully in fresh practice room 200082403 and completed the manual draft. The localhost backend ran with page runner enabled and page dispatch disabled.

The user's final Diagnostics report `cache_status: "saved"`, 1,095 catalog IDs and 348 complete projections. Capture took 171.657 seconds, 77.6% less time than the previous scan. Reload coverage confirms that the saved catalog loads in a different practice league within the same browser profile, season, team ID and own-team scope, without starting a new player scan. Select Start observing to use it; only Rescan players triggers a new scan. Current-room rules, feed and roster are still verified freshly. Clearing site storage, changing browser profiles or changing the scope can require an explicit new capture.
