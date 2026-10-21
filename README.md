# FBL Draft

> **Inactive.** The draft is complete and this advisor is no longer used.
> Season-long work lives in [fbl-season](https://github.com/nlamb332/fbl-season)
> (ESPN) and [fbl-automation-sleeper-league](https://github.com/nlamb332/fbl-automation-sleeper-league)
> (Sleeper).

Independent repository snapshot for the FBL fantasy basketball draft assistant
and its draft-strategy research. The package is self-contained: the live draft
control plane and browser observer use only `draft_assistant` and `mock_draft`
modules in this repository, with no dependency on the season-management code.

## Quick start (Windows)

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) and tick
   **Add python.exe to PATH** in the installer.
2. On GitHub choose **Code → Download ZIP**, extract it, and double-click
   **Launch Advisor.cmd**. Keep that window open while you use the advisor;
   if something is wrong it stays open and explains how to fix it.
3. The local panel opens in Chrome with a **Get started** checklist:
   1. **Add the FBL Advisor bookmark** — drag the link to the bookmarks bar.
   2. **Link your ESPN league** — paste the address of your ESPN *My Team*
      page and click **Save source team**.
   3. **Choose your team** — open a practice (or real) draft room for that
      league on ESPN and click the bookmark. Pick your team in the box that
      appears on the ESPN page and click **Save and connect**. (You can also
      pick it in the panel.)
   4. **Capture the player catalog** in a practice draft (see below).
   5. **Draft day** — open the real draft room, click the bookmark, and
      **Start observing**.

Your league, season, team IDs, and team name are saved only in the ignored
`runtime/league.json`. To change them later, click **Change** in the panel.
Operator tools (manual room connection, controls, journal) are under
**Advanced** in the panel.

CLI users can copy `league.example.json` to
`runtime/league.json`, edit it locally, and pass `--league-config runtime/league.json`
before `serve`. See the [privacy notes](docs/PUBLIC_RELEASE_PRIVACY.md).

The ZIP contains the application code, launcher, projection data, required
policy configuration, and operating guides. Research studies, tests, fixtures,
saved validation results, and development-only documentation are excluded by
the repository's archive rules. These files remain available in GitHub and
in a full `git clone` for development or strategy research.

See [bookmarklet installation](docs/DRAFT_ADVISOR_BOOKMARKLET.md) after opening
the local panel.

## Prepare the player catalog in a practice draft

Run a full capture in an ESPN practice draft **before your real draft**. This
builds a saved catalog of player IDs, eligibility, and available projections,
so the real draft can start recommending without a lengthy player scan.

1. Launch the local panel and open a practice draft for your configured ESPN
   league, season, and team. Wait for the draft heading and team list to load,
   then click your **FBL Advisor** bookmark.
2. Click **Capture inputs** (or **Rescan players** to replace an older scan).
   If the practice draft has not started, the advisor waits for it to begin.
   Once it starts, the advisor requests Pause once and waits for ESPN to
   confirm it. An already paused room can proceed directly to capture.
3. Keep the ESPN tab visible and the practice draft paused. The advisor reads
   Rules, then scrolls through the Players grid to collect the catalog. Let
   both the initial scan and any repair pass finish; reaching the full player
   count alone does not mean capture is complete. Avoid changing tabs,
   filters, or scroll position during capture.
4. Confirm the advisor has finished capturing without an error. In the
   expanded advisor, the input source should say **captured**, with a fresh
   capture time. In **Diagnostics**, check that `capture.cache_status` is
   `saved`; a storage failure means the scan is not saved for another room.
   **Start observing** becomes available. You may use it to check
   recommendations in the practice room, then close the advisor when done.
5. Capture again close to draft day if ESPN's player projections or
   eligibility have changed. Only a successfully completed catalog replaces
   the saved scan. Some cataloged players lack sufficient projections to be
   scored; projection coverage can therefore be lower than the catalog count.

### Reuse the catalog in your real draft

1. Launch the local panel again if you closed it, open your real ESPN draft
   room, and click the same **FBL Advisor** bookmark. Use the same Chrome profile and ESPN site
   origin used for the practice capture, with the same configured source
   league, season, and team ID. The saved scan identity does not depend on the
   displayed team name, so a verified rename keeps the scan available after
   you edit setup and select the renamed team from the current ESPN roster.
2. Expand the advisor and confirm **Loaded saved scan** or **Inputs source
   cache**, then check the capture time. Click **Start observing**. The
   advisor re-reads the real room's scoring rules and uses its live pick
   history and your current roster to remove drafted players and update
   recommendations. Practice-room picks and rosters are not carried into the
   real draft.
3. Make your picks manually. Observation updates recommendations as picks
   happen. If no usable saved scan is available, **Capture inputs** can scan
   the real room directly, but it takes time while picks continue. The
   real-draft workflow never requests native Pause or Resume.

The player catalog is stored in **Chrome local storage for the ESPN site**,
not in the downloaded folder. A fresh download or launcher restart does not
clear it. Different browser profiles and origins have separate storage;
incognito storage is separate and is lost when all incognito windows close.
New scans are scoped to the configured source league, season, and team ID, so
they survive display-name changes and can be reused between practice and real
rooms. Older saved scans did not record the source league ID; FBL leaves those
untouched and asks for one fresh capture before reuse.
For a dependable practice-to-real handoff, use the same regular Chrome
profile for both. Historical captures are not included in the download ZIP.

## Full repository contents

- `draft_assistant/` — local coordinator, valuation and strategy policies,
  visible ESPN observer/operator runtime, fixtures, tests, and release evidence.
- `mock_draft/` — deterministic 14-team draft fixture, projections, simulation
  runner, and recorded synthetic/ESPN-input results.
- `studies/draft-strategy-quality/` — reproducible strategy-quality methods,
  data summaries, and reports.
- `preliminary_strategy.md` — league rules, scoring assumptions, and initial
  strategy notes.
- `docs/` — draft runbooks, control and browser notes, release evidence, and
  the draft-only roadmap.
- `scripts/` — the local panel launcher and sanitized-session export helper.
- `skills/fbl-attended-draft/` — the attended visible-browser operating skill.

The repository intentionally excludes credentials, browser state, local
runtime databases, worktrees, and unrelated personal files. Runtime state is
created under the ignored `runtime/` directory.

## Requirements and checks

Python 3.10 or newer and Node.js are required. The Python code uses the
standard library; no package installation is needed to run the advisor.
Node.js and the tests below are for development in a full Git checkout;
tests and their fixtures are excluded from the ZIP download.

```powershell
python -m unittest mock_draft.test_fixture -v
python -m pytest draft_assistant mock_draft -q
node --test "draft_assistant/*.test.js"
```

The strategy study contains its own reproduction commands in
`studies/draft-strategy-quality/2026-10-15/README.md`. Generated campaign
outputs should go under `runtime/` or another explicitly ignored directory.

## Local panel

The panel is observation and coordination infrastructure; it does not open an
ESPN room or start a draft. From this repository, launch it with:

```powershell
python -m draft_assistant --db runtime/control.sqlite3 serve --host 127.0.0.1 --port 8765
```

The PowerShell helper performs source-identity checks before starting the
service:

```powershell
.\scripts\local-panel.ps1
```

On Windows, double-click `Launch Advisor.cmd` for the ordinary attended
workflow. It starts the coordinator with the in-page runner enabled, then
opens the local panel after the server is listening. Keep its console window
open while using the advisor.

For the recommendation-only ESPN advisor, open the panel after starting it
with `-PageRunner`, then drag **FBL Advisor — drag to bookmarks** from the
**ESPN advisor bookmarklet** card to the browser bookmarks bar. Save that link,
rather than bookmarking the localhost panel page with Chrome's star. Open the
current ESPN basketball draft room and click the bookmark after the draft
heading and fourteen-team roster list render. Read
`docs/DRAFT_ADVISOR_BOOKMARKLET.md` for the popup, origin, and fresh-server
connection requirements; the bookmarklet does not authorize picks or queue
actions.

Read `docs/LOCALHOST_HANDOFF.md`, `docs/DRAFT_RUNBOOK.md`, and the attended
draft skill before operating a visible ESPN room. Live operation requires the
room-specific identity and an explicit user handoff described there.

## Provenance

This is a source snapshot extracted from the original workspace repository.
The exact source revision and exclusions are recorded in
`docs/SOURCE_SNAPSHOT.md`. The original workspace remains available for
historical recovery; future draft changes belong here.
