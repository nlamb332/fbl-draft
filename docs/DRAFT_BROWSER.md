# Visible ESPN draft-room adapter

`draft_assistant.browser_state` consumes a visible text or accessibility/DOM
snapshot and produces the version-one `DraftObservation`. The caller must pass
an explicit `session_id`, `RoomIdentity`, and `LeagueConfig`; the parser does
not infer league, season, team, practice status, or credentials from a page.
The module has no browser, cookie, network, or private ESPN API access.

The parser scopes player rows to the `Draft`/`QUEUE` player table after its
`RK Player FPTS GP ...` header. Duplicate `Players`, `Pick History`, `Board`,
and `Rules` labels in nested controls are ignored as labels. `RK` becomes
`LivePlayer.draft_rank`; it is never treated as ADP. `GP` is stored as the
seasonal `projected_gp` value, `FPTS` is the displayed total, and credible
stat columns are retained as per-game values. `PF`, `TF`, `EJ`, `DD`, `TD`, and
`QD` stay in `missing_stats` even when the UI renders zero. Rules aliases are
recorded as `TPM -> 3PM` and `E -> EJ`.

Visible table scope is intentionally conservative. `No Players Available` is
a transient empty table, and `available_pool_complete` remains false unless
the page explicitly says that all players are shown. A player missing from a
virtualized window is never removed from `known_players` and is never inferred
to be drafted. `reconcile_observations` merges known history and the catalog
of seen players while replacing the current visible availability window.
Observed teams, slots, and scoring are emitted only when rendered in the
supplied Rules view or structured snapshot; configured values are never copied
into the observation as proof. A generic `Autopick` tab label is ignored; only
an explicit checked/unchecked toggle or on/off status changes `autopick`.

## Operator procedure

`BrowserOperator` is a guard and audit object. The caller must inject the
coordinator's final guard callback; the operator refuses to invoke a browser
submitter without that durable control-plane check. A controller should:

1. Attach a fresh observation whose session and room identity match exactly.
2. Start the lifecycle only after the caller has verified the visible practice
   room shape: 14 teams, ten rounds, 30-second clock, C/G/G/F/F/UTIL plus four
   bench slots, custom scoring, and the practice league identity.
3. Plan a pick or a reviewable queue fallback. Planning does not mutate the
   page. Direct actions require the observed pick, on-clock team, visible clock,
   revision, and visible player row to remain unchanged.
4. Require explicit confirmation before invoking the caller-owned visible UI
   submitter callback. A callback is never created by this package and must
   not be an API client.
5. Read the new pick history **and** roster, then call `confirm_action`. A
   history-only match is `uncertain`; it is not a successful confirmation.

Pause transitions stop all submits. Autopick on our team blocks a direct or
queue plan until a human takes over. A stale expected turn, session mismatch,
unknown clock, missing player row, or revision change blocks the action. If a
submitter raises, the result is `uncertain` because the click may have reached
the room. `retry` refuses to act until `inspect_unknown` has performed a fresh
post-submit read-back; there is no automatic retry loop.

## Fixtures and validation

Files under `draft_assistant/fixtures/` are hand-authored synthetic snapshots.
They are marked as such and make no claim to be captured or source-verified
ESPN data. They cover reordered/newline-shaped sections, duplicate controls,
the transient empty table, and a post-pick history/roster read-back.

Run the focused tests with:

```powershell
python -m unittest draft_assistant.test_browser_state -v
```
