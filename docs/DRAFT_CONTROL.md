# Draft control plane

The control plane is a local, durable coordinator between the browser
observer and the draft strategy.  It does not perform HTTP requests to ESPN
and it never starts a room merely because a URL was connected.  The browser
agent publishes a `DraftObservation`; the strategy returns a recommendation;
and the coordinator emits one guarded `select_player` command at a time.

Run the panel with:

```powershell
python -m draft_assistant --db runtime/control.sqlite3 serve
```

The server binds to `127.0.0.1`.  POST requests require a local `Origin` or
the CSRF token embedded in the panel.  A connect request is always an
observing session.  Start and resume are explicit controls and readiness
requires the connected league/team, fresh state, verified rules (14 teams,
ten rounds, exact scoring and slots), our team in the observed order, a known
phase, complete history, and visible current candidates. The observer may
publish a bounded candidate window; a full league player pool is only needed
when the strategy explicitly requests it.

The lifecycle is deliberately conservative:

* observations older than ten seconds or dated in the future cannot drive an
  action;
* a turn, session, room, or revision mismatch rejects a claim;
* only one pending action may exist;
* pause cancels undispatched pending actions. A browser command already marked
  dispatched remains pending until its outcome is acknowledged, while
  submitted and uncertain actions remain in the journal for reconciliation;
* an uncertain browser outcome locks automation until fresh authoritative
  history and roster data confirm it;
* a process restart changes running sessions to paused and preserves all
  unresolved actions.

`claim-action` performs the first phase.  The browser bridge rereads the
  control intent and fresh observation immediately before invoking its visible
  operator callback.  Since a browser click cannot be cancelled after that
  callback begins, an outcome that cannot be read back is recorded as
  `uncertain`.

Manual override pauses automation.  `manual-pick` stages a fresh available
  player as a user-requested action while the session remains paused; the
  user can perform the pick in the browser and publish the resulting board
  observation for reconciliation.

The CLI accepts JSON from a file or stdin:

```powershell
python -m draft_assistant connect "https://fantasy.espn.com/basketball/draft?leagueId=1000000006&seasonId=2027&teamId=15" --practice
python -m draft_assistant verify-practice SESSION_ID --heading "ESPN Fantasy Basketball Draft - Practice Draft for Example League" --source-league-id 1000000000
python -m draft_assistant publish-observation --file observation.json
python -m draft_assistant status SESSION_ID
python -m draft_assistant control SESSION_ID pause
```

ESPN assigns a fresh league ID to each practice room. After connecting, the
browser agent must copy the exact one-line practice heading it can see and
attest the configured live league that launched the room. The attestation is
stored for that session and room in the journal; a practice flag alone cannot
authorize a room, and the configured live league cannot be labeled practice.

For a time-sensitive turn, `prepare-action` keeps observation publication,
recommendation, claim, and the one-time final guard in one short-lived
process.  The browser operator should dispatch the returned command
immediately, then acknowledge it separately:

```powershell
python -m draft_assistant --db runtime/control.sqlite3 prepare-action SESSION_ID --file observation.json
python -m draft_assistant --db runtime/control.sqlite3 ack-action ACTION_ID submitted
```

`dispatch-command ACTION_ID` is available when recommendation and claim were
already performed.  It consumes the final guard exactly once; callers must
not call it twice for the same action.

`runtime/` contains only local state and is ignored by git.  Credentials,
browser profiles, and tokens must remain outside the repository.

The browser observer should publish a complete frame at least every few
seconds while automation is active.  If no fresh frame arrives for ten
seconds, the coordinator pauses itself and reports the stale-data reason;
fresh data alone never resumes it, so the operator must explicitly resume
after checking the room.  A normal long on-clock pause is represented by a
fresh frame with a nonnegative clock, rather than by stopping observation.

