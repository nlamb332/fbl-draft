# Observation and recommendation trial

This trial exercises turn tracking, the player universe and the existing selection policy. The FBL actor is disconnected. The human makes every Example Team selection manually in ESPN. Native Autopick remains off; the embedded panel must never resume ESPN or click Draft/Queue.

## Component boundaries

1. Turn tracking reads the visible current pick, clock and verified snake order. It reports picks until our next turn and highlights an own turn.
2. The player universe retains the captured catalog by stable player ID. Only confirmed, consistent draft history removes a player. Feed overlap and duplicates do not remove twice. Missing history, conflicting events and incomplete projection coverage remain explicit.
3. Strategy ranks eligible players from the remaining catalog using the configured existing policy and current own roster. The rendered player window does not restrict ranking. An incomplete or contradictory observation blocks a fresh recommendation instead of presenting it as current.
4. The actor is absent from this workflow. Recommendation requests cannot reserve, claim, dispatch, acknowledge or submit a player selection, and the UI has no activation or recovery action controls.

## Attended sequence

- Generate the reusable loader from the integration checkout with `python -m draft_assistant advisor-loader`. Save its output to ignored `runtime/fbl-advisor-loader.js` and paste it into the current ESPN practice room's Console. The same loader detects each room's URL and visible fourteen-team order; no transient league ID, fixed seat or per-room owner token is embedded. Regenerate only when the installed code or localhost credential changes.
- The localhost `connect-advisor` operation binds a new observation-only session to the visible practice heading and configured source season/team. Its returned token permits only status and recommendation preview. It cannot start, resume, publish an actionable observation, prepare a pick, claim ownership or mutate a queue.
- The panel opens compact and docked left. Expand, drag, use arrow keys or dock it elsewhere to reach native ESPN controls. A valid saved scan loads automatically; capture/rescan occurs only through its explicit button.
- Keep the practice room paused while capturing rules, player projections and the initial complete draft history. Verify the current team, room, pick order, roster and projection coverage.
- Load the reviewed recommendation-only bundle and start observation. Confirm that the panel displays a proposed player, alternatives, turn distance, feed health and last update, while ESPN remains paused.
- The human resumes the native practice draft when ready. FBL continues observing opponent selections and recomputing recommendations without tool-call dependence.
- On each own turn, record the proposed player before the human chooses a player in ESPN. The next validated history and roster remove that exact selection from the universe and update the priority list. Choosing a different player is allowed and should be reflected correctly.
- A gap or conflict must display a blocked/reconciling status. It must not silently invent history, erase the retained catalog or repeat an own selection.
- Stop observing at completion and retain sanitized evidence. Require 140 unique draft selections, ten own selections matching the roster, and zero FBL actor actions. Report any gaps, recovery, partial projections or missed own-turn recommendations separately.

## Historical room

The October 13 trial uses practice league `723024336`, season `2027`, team `15`, Example Team at seat 13. It was paused at pick 13 with twelve opponent selections and no own picks after the earlier actor activation failed. That earlier attempt is recovery evidence, not a clean automatic-draft pass. Observation-only acceptance is a separate gate and does not establish actor readiness.

On October 14 the user opened fresh practice league `10000000140`. Before browser access was interrupted, it was visibly paused at pick 4 with Example Team in seat 13. The reusable loader replaces the old room-pinned loader that rejected this room. Live connection of the new loader remains pending; fixtures establish its room-agnostic behavior, not a completed native trial.

The subsequent practice room `200082403` completed the observation-only trial. User-supplied full history and Diagnostics establish 140 unique selections, ten own selections matching all ten recorded recommendations, and a healthy terminal feed. The scan was saved, and reload tests support starting observation without rescanning. See [the completed trial review](DRAFT_MANUAL_TRIAL_2026-10-14.md). Historical pending statements above describe earlier checkpoints.
