# FBL Draft

Independent repository snapshot for the FBL fantasy basketball draft assistant
and its draft-strategy research. The package is self-contained: the live draft
control plane and browser observer use only `draft_assistant` and `mock_draft`
modules in this repository, with no dependency on the season-management code.

## Download and run

Choose **Code → Download ZIP** on GitHub, extract the archive, and double-click
**Launch Advisor.cmd** on Windows. Python 3.10 or newer must be installed and
available on PATH. Keep the launcher window open while using the advisor.

On first launch, enter your ESPN league ID, season ID, team ID, and exact team
name. These details are saved only in ignored `runtime/league.json`. Public
defaults are examples. CLI users can copy `league.example.json` to
`runtime/league.json`, edit it locally, and pass `--league-config runtime/league.json`
before `serve`. See the [privacy notes](docs/PUBLIC_RELEASE_PRIVACY.md).

The ZIP contains the application code, launcher, projection data, required
policy configuration, and operating guides. Research studies, tests, fixtures,
saved validation results, and development-only documentation are excluded by
the repository's archive rules. These files remain available in GitHub and
in a full `git clone` for development or strategy research.

See [bookmarklet installation](docs/DRAFT_ADVISOR_BOOKMARKLET.md) after opening
the local panel.

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
node --test draft_assistant/test_browser_observer.test.js draft_assistant/test_operator_runtime.test.js
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
current ESPN basketball draft room and click the bookmark after the practice
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
