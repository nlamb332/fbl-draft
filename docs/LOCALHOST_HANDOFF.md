# Localhost draft handoff

The localhost link is the FBL coordinator and control panel. It is not an ESPN
room and does not create or start a draft. The attended end-to-end path needs
both `http://127.0.0.1:8765` and an ESPN practice-room link supplied by the
owner. A real room always needs its own identity/rules checks and a separate,
explicit user Start handoff. Queue fallback remains disabled.

## Three different things

| Mode | What it is | Starts a real draft? |
|---|---|---|
| Offline simulation | `test_dress_rehearsal` and the canonical-runner HTTP tests against a local fixture room. No ESPN, no browser. | No |
| Attended ESPN practice | The panel plus an owner-provided ESPN practice-room link, a human present, foreground runner only. Live gates are tracked in fbl-season #2 and #8. | No |
| Real draft | A real room with its own identity/rules checks and a separate, explicit user Start handoff. | Only after that Start |

Providing a localhost or ESPN link, probing, claiming ownership or passing
preflight never starts drafting in any mode.

## Start and check the coordinator

Run these commands from the integration checkout (the directory containing
`draft_assistant/`):

```powershell
.\scripts\local-panel.ps1
```

The script uses Python from `PATH` (or the Windows `py -3` launcher), reuses
`runtime/control.sqlite3`, and serves only on `127.0.0.1:8765`. Keep that
PowerShell window open; press Ctrl+C to stop the service. The launcher checks
for an already-running FBL panel before starting another coordinator. It
checks the actual service identity, startup revision, source hashes, and
loaded policy through `/api/runtime-info`; it refuses to treat a different or
stale checkout as this one. To check the existing panel from a second
PowerShell window:

```powershell
.\scripts\local-panel.ps1 -CheckOnly
```

The check prints the serving process revision and loaded policy separately
from each connected session's latest observed revision and recommendation.
The configured default automatic policy is the accepted `marginal-lineup-v1`
promotion artifact; `--foundation-only` is an explicit opt-out. Lookahead
remains advisory and queue fallback remains disabled. When no session exists,
there is no current session revision or recommendation yet.

To set up a room, paste either the ESPN team URL or draft-room URL into the
panel. The setup flow extracts `leagueId`, `teamId`, and `seasonId` from the
query string automatically and stores the normalized draft-room URL. For
example:

```text
https://fantasy.espn.com/basketball/team?leagueId=1000000000&teamId=15&seasonId=2027
```

The equivalent CLI command accepts the same URL:

```powershell
python -m draft_assistant --db runtime/control.sqlite3 connect "<espn-team-or-draft-url>"
```

If Python is installed elsewhere, pass its full path using `-Python`.
`-Port` is available for isolated launcher tests only. Attended browser
helpers use the fixed loopback port `8765`. Do not expose the service beyond
loopback.

## Harness-agnostic in-page runner

Any agent harness that can evaluate JavaScript in the visible ESPN tab can run
the canonical sequence from inside the page:

```powershell
.\scripts\local-panel.ps1 -PageRunner
python -m draft_assistant page-loader
```

Evaluate the printed loader once in the room tab. Claim the session owner
through the panel or CLI (never from the page), then in the room tab:

```javascript
FBL.attach({ sessionId: "<SESSION_ID>", ownerToken: "<PRIVATE_OWNER_TOKEN>", ownTeam: "Example Team", expectedSeat: <SEAT> });
await FBL.runner().captureInputs();   // paused: Rules tab + Players catalog
await FBL.runner().preflight();       // read-only
// only after the owner's explicit Start handoff:
await FBL.runner().activate();
FBL.runner().startBatch();            // runs in the page; poll FBL.runner().summary()
```

`-PageRunner` serves `/page-runner.js` and accepts page-origin operator
requests only for an owned session with its owner token; ownership and queue
operations stay panel/CLI-only. Without the flag the page may only publish
heartbeats.

## Attended ESPN practice

1. The owner launches Practice Draft in ESPN, verifies the setup,
   and clicks **Start Practice Draft exactly once**. If the room does not open,
   stop and have the owner point to it; never click Start a second time.
2. Connect the room URL from the panel, or use the CLI below (do one, not
   both). Neither starts the draft. Record the returned session ID. Read the
   exact practice heading from the visible room and attest the connected
   session with the real configured source-league ID:

   ```powershell
   python -m draft_assistant --db runtime/control.sqlite3 connect "<practice-room-url>" --practice
   python -m draft_assistant --db runtime/control.sqlite3 verify-practice <SESSION_ID> --heading "<exact visible practice heading>" --source-league-id <CONFIGURED_LIVE_LEAGUE_ID>
   ```

   Use the actual session ID returned by Connect. Practice IDs and headings are
   read fresh for every room. Attestation does not activate automation.
3. Read the coordinator status and latest revision, capture and publish a
   complete visible observation, and read back identity, rules, order, roster,
   native timer, and explicit Autopick OFF state. Claim one owner token through
   the panel operator form using `{"operation":"claim-owner","session_id":"<SESSION_ID>","label":"<operator>"}`.
   Keep the returned token private and send it on every operator write.
4. Ask the coordinator for the read-only practice preflight with the fresh
   observation and the values read from the paused ESPN room:

   ```json
   {
     "operation": "preflight",
     "session_id": "<SESSION_ID>",
     "owner_token": "example-9432110ed5d3c36d",
     "observation": { "...": "fresh complete visible observation" },
     "preflight": { "native_timer_seconds": 90, "expected_seat": 1 }
   }
   ```

   Replace the timer and seat with observed values. Any readable pick timer
   is accepted; the first accepted Start records it as the session's
   `pick_timer_seconds`, and later Resumes must read back the same value.
   A refused preflight means
   do not activate. Then run the read-only connection probe from the visible
   ESPN room using `node draft_assistant/live_probe.js`; it never clicks or
   publishes an observation. Browser Local Network Access prompts belong to
   the owner. See the [canonical rehearsal runbook](../skills/fbl-attended-draft/references/canonical-rehearsal-runbook.md)
   for probe verdicts and live gates.
5. With a verified practice tab still paused, load the checked-in browser
   module exports and create `createCanonicalPracticeRunner` from
   `draft_assistant/canonical_runner.js`, passing the visible draft and panel
   tabs, verified room/rules/catalog, session ID, private owner token, team,
   and seat. Run the bounded sequence `await runner.prepareActivation();
   await runner.activate();` only after the owner has clicked Start exactly
   once and the visible room is ready. The assembly moves to the unique
   anchored **League Manager** tab and uses the unchanged fresh paused frame
   for native Resume; a missing or ambiguous tab refuses. Then run
   `await runner.runBatch()` in one continuously awaited foreground call.
   This factory does not wire the independent in-page heartbeat publisher;
   it is suitable only for the bounded attended-practice foreground gate and
   does not promise survival across long agent/tool gaps. `runBatch` and
   `recover` fail closed until activation succeeds. For a stopped run, use the returned stop
   state with `runner.recover(stop)` before starting another batch. Save
   `runner.evidenceSnapshot()` as evidence input and use the sanitized exporter
   below. See the [runbook](../skills/fbl-attended-draft/references/canonical-rehearsal-runbook.md)
   and [runtime handoff reference](../skills/fbl-attended-draft/references/runtime-handoff.md)
   for exact visible gates. This foreground assembly has not yet been
   validated against a live ESPN room; the room-specific selector/probe and
   browser Local Network Access remain practice-room checks. Connecting,
   probing, claiming ownership, or passing preflight does not start the draft.
   Do not use queue fallback.

The local fixture rehearsal is an offline check of the canonical modules and
the real Python coordinator. It does not open ESPN or establish browser/CSP/
Local Network Access behavior. Run it before an attended trial:

```powershell
$env:FBL_REHEARSAL_OUTPUT_DIR = Join-Path $PWD 'runtime\combined'
python -m unittest draft_assistant.test_dress_rehearsal -v
```

This writes offline-only `clean-dress-rehearsal-export.json` and
`clean-report.json` under the selected output directory. The fixture run does
not validate live ESPN selectors, CORS, or browser Local Network Access.

The generic `/api/export-session` endpoint is an internal raw snapshot and
includes the complete journal and observations; do not use it as a sanitized
artifact. For a completed attended run, first save the runner's measured data
in an evidence JSON file with this shape:

```json
{
  "latency_rows": [],
  "foreground": {},
  "provenance": "describe the actual source",
  "release_limits": [],
  "extra": {}
}
```

This is only the JSON shape, not completed-run evidence. Fill the fields only
from observed runner/status output. Then write the
sanitized projection (which omits owner/claim tokens, full available-player
catalogs, and repeated observation/recommendation payloads) with:

```powershell
python .\scripts\export_sanitized_session.py --db runtime\control.sqlite3 --session-id <SESSION_ID> --evidence <EVIDENCE_JSON> --output draft_assistant\validation\<RUN>\sanitized-session.json
```

The exporter requires a `completed` session and validates that its output has
no credential or full-catalog keys. Review the artifact before keeping it in
version control. Report offline rehearsal, attended-practice, and real-draft
evidence separately. A local simulator rehearsal is not a completed ESPN
practice or real draft.
