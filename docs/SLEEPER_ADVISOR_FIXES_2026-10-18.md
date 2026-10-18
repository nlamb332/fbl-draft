# Sleeper advisor fixes — 2026-10-18 practice-draft findings

Branch `sleeper-advisor` (isolated from main; test with `python -m pytest sleeper_advisor -q`).

## Findings

1. **Silent identity failure.** In a practice mock the panel ran with a username
   that did not match the drafter. `our_slot` was `null`, the roster was treated
   as empty, and the advisor kept recommending the best raw value (mostly pure
   centers). The only signal was a small warning line.
2. **Policy still leans to centers with the correct identity.** Replaying the
   advisor at each of our picks: it filled both UTIL slots with pure C players
   (picks 35/38: Kessler, Clingan) over wings/guards of similar value. In a
   24-draft simulation (6 slots × 4 seeds, ADP-noise opponents), the
   current `forecast-lock-in-lineup-v1` drafts 5.1 pure-C players of 14
   (ADP drafting: 1.7; earlier lookahead policy: 4.4) and 4.8 guards (ADP: 7.6).
   Cause: ranking is only *immediate* lineup gain. UTIL is the most flexible
   slot and gets filled first by whoever has the highest raw value (bigs score
   well under Sleeper points), and bench ties are broken by raw value, so bench
   spots fill with redundant pure Cs that can only cover C/UTIL.
3. **Not embedded.** The panel is a separate localhost tab; the ESPN advisor
   mounts inside the draft page. sleeper.com sends no Content-Security-Policy
   (only `x-frame-options: SAMEORIGIN`), so a bookmarklet can inject an overlay
   script from `http://127.0.0.1`. `api.sleeper.app` sends
   `access-control-allow-origin: *`.

Owner rule: **never fix positional over-drafting with caps, quotas or fixed
positional bonuses.** Position balance must come from scoring.

## Fix A — identity (agent 1)

- `model.build_state`: expose identity resolution in `DraftState` via a new
  field `identity: str` with values `"matched"` (user found in `draft_order` or
  in picks), `"slot_override"` (from `--slot` only), `"unknown_user"` (username
  lookup failed), `"not_in_draft"` (user resolved but not in `draft_order` and
  has no picks), `"none"` (no username or slot given). Keep existing behavior
  otherwise; add tests.
- CLI and panel: when identity is not `matched`/`slot_override`, show a
  prominent red banner ("Advisor can't see your team: <reason>. Recommendations
  ignore your roster.") above recommendations, and include `identity` in
  `/api/state`. If the draft has a `draft_order` and the user isn't in it, list
  the usernames/display names in the draft (`GET /v1/draft/{id}` →
  `draft_order` user ids → `GET /v1/user/{user_id}`; cache per draft) so the
  user can spot the right one. Do not print user ids.

## Fix B — in-page overlay (agent 1)

- New `sleeper_advisor/static/overlay.js` served by the panel at
  `GET /overlay.js`. It mounts a fixed, draggable, collapsible panel in the
  Sleeper draft page (Shadow DOM so Sleeper CSS doesn't leak; textContent
  only; no innerHTML with data). It reads the draft id from
  `location.pathname` (`/draft/nba/<id>`) and polls
  `GET http://127.0.0.1:<port>/api/state?draft=<id>` every 3 s. It shows: the
  identity banner, on-clock / picks-away, top 5–8 recommendations (name, pos,
  Added/wk, ADP), open lineup slots, and an expand toggle for the full table.
  Close button (×) removes it and stops polling. Re-clicking the bookmarklet
  while mounted toggles it instead of mounting twice.
- Bookmarklet: `javascript:` URL that injects
  `<script src="http://127.0.0.1:<port>/overlay.js?v=<ts>">` only on
  `https://sleeper.com/draft/nba/*` (alert otherwise; alert if the panel is
  not running). The panel home page gets an "Install the Sleeper bookmarklet"
  card with a draggable link plus a copy-URL fallback, mirroring the ESPN
  instructions.
- Server changes in `panel.py`:
  - `GET /api/state?draft=<numeric id>`: if it differs from the watched draft,
    switch the poller to it (validate digits only, max 25 chars; reuse
    username/slot; `--slot` override is dropped on switch unless the draft is
    the original one). Read-only public data only.
  - CORS only for origin `https://sleeper.com` on `/api/state` and
    `/overlay.js`: `Access-Control-Allow-Origin: https://sleeper.com`,
    `Vary: Origin`; answer `OPTIONS` preflight for those paths with
    `Access-Control-Allow-Methods: GET`, and
    `Access-Control-Allow-Private-Network: true` when requested. All other
    origins: no CORS headers. Still loopback-only bind, GET-only otherwise.
  - Host-header check must still pass for requests from the page (Host is
    `127.0.0.1:<port>`).
  - The page's CSP currently set by the panel applies only to the panel's own
    HTML; don't send it on `/overlay.js` in a way that breaks injection.
- Docs: update `docs/SLEEPER_ADVISOR.md` (bookmarklet install, Chrome "local
  network access" prompt: allow it; the standalone tab still works).
- Tests: overlay served with correct content type; CORS headers only for
  sleeper.com; preflight; draft switching via query; invalid draft param
  rejected (400); identity banner in CLI output and `/api/state`.
  Static check that `overlay.js` never uses `innerHTML`/`eval`.

## Fix C — scoring-based balance (agent 2)

Change only `sleeper_advisor/advisor.py` (and its tests; a small helper module
is fine). Keep `POLICY_ID` distinct: `forecast-lock-in-lookahead-v2`.

1. **Lookahead / opportunity cost** (restores the earlier mechanism on the
   Lock-In metric). Score of candidate X =
   `V(roster + X + completion_X) − V(roster + R + completion_R)` where
   completion simulates our remaining picks: opponents take players in ADP
   order (unknown ADP last, ties by weekly value) between our picks, and at
   each of our future picks we take the player that maximizes `V` (search the
   best remaining player per distinct eligibility tuple — exact because a
   higher-valued same-eligibility player dominates). R = best player expected
   to survive to our next pick. Limit completion to roster capacity. When
   `our_slot` is unknown, assume our picks are every `teams` picks.
2. **Absence-aware lineup objective `V`** (gives bench depth positional value
   from scoring, not quotas). With weekly per-player absence probability `q`
   (default 0.10; read `defaults.weekly_absence_probability` from the
   forecast config if present, else default; label it as an assumption in
   warnings), `V(roster) = L(roster) − q · Σ_s [L(roster) − L(roster − s)]`
   over starters `s` of the optimal lineup, where `L` is the optimal weekly
   Lock-In starting lineup total (`best_lineup`). A fourth pure C only helps
   when a C/UTIL starter is out; a guard backs up guard slots. No positional
   constants anywhere.
3. Keep the capacity guard, determinism, the 2 s budget (evaluate top ~25
   candidates by weekly value ∪ ADP ∪ best per eligibility tuple), and the
   existing `Recommendation` fields. Components: `lineup_gain` (immediate),
   `lookahead_value` (the score), `depth_value` (absence term contribution),
   `lock_in_weekly_points`, `survival`. Explanation: one short sentence, no
   duplicated numbers (e.g. "Fills SG now; +3.1 vs. waiting (best likely at
   pick 18: Kessler C)").
4. **Validation (required in the report):** run the simulation script at
   `%TEMP%\sim\run.py` (args: repo path, a cache dir with Sleeper
   players/projections, old advisor path) adapted to include the new policy,
   and report for adp / current (e3e11ab) / new: mean starting-lineup weekly
   value, mean absence-aware `V`, pure-C count, C-eligible count, guard count.
   Acceptance: `V` and lineup value not worse than current beyond noise, and
   pure-C count materially lower — achieved without caps/quotas/bonuses.
   Copy the adapted script into `sleeper_advisor/tests/` only if it runs on
   fixtures with no network; otherwise leave it in %TEMP%.
