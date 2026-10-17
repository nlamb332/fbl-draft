# Recovery and attended-practice rehearsal — October 11, 2026

**Functional result: completed trial with a legal final roster; one uninterrupted timing gate passed, while the production release remains withheld.**

The durable validation exports now include a completed trial with 140 unique
history selections, ten direct picks confirmed against both history and roster,
and a legal ten-player final roster. That trial still had multiple pauses and
resumes, one cancelled unclicked command, an empty-Players post-read pause, a
low-clock pick, and a roughly 22-second final confirmation. A separate room,
`uninterrupted-1000000007`, completed one single-foreground-invocation run
without a recovery pause or resume after Start; its clean timing gate passed.
The clean timing result is bounded to that run. Earlier rooms add
manual-takeover and queue timeout evidence. A follow-up room, `1000000004`,
completed the same ten-pick shape with one pause before pick 125; room
`1000000002` remains paused with its outcome pending.

## Durable session evidence

| session | durable outcome | confirmed actions | terminal state |
|---|---|---|---|
| `trial-1000000013` | 140 unique history picks; ten direct selections confirmed; one unclicked command cancelled and retried | Sabonis 13, Duren 16, Allen 41, Randle 44, Gobert 69, Clingan 72, Vucevic 97, Collins 100, Kyshawn George 125, Jrue Holiday 128 | completed; final observation complete and roster legal, but multiple pauses/resumes prevent a clean release pass |
| `clean-1000000004` | 140 unique history picks; ten direct selections confirmed; one pause before pick 125 | Tatum 13, Duren 16, Allen 41, Randle 44, Gobert 69, Clingan 72, Vucevic 97, Collins 100, Kyshawn George 125, Jrue Holiday 128 | completed; final observation complete and roster legal, but the pause prevents a no-pause clean pass |
| `uninterrupted-1000000007` | 140 unique history picks; ten direct selections confirmed; one foreground invocation, no recovery pause/resume after Start | Towns 13, Duren 16, Allen 41, Randle 44, Kessler 69, Ware 72, Vucevic 97, Collins 100, Kyshawn George 125, Jrue Holiday 128 | completed; final observation complete and roster legal; clean timing gate passed, with production limits still open |
| `recovery-1000000008` | stale pause, manual takeover, explicit resume, then recovery pause | Pascal Siakam at pick 28; Bam Adebayo at pick 29 | paused; history/roster continuity and snake-order mismatch required recovery |
| `clean-1000000009` | two direct submissions confirmed; later stale pauses | Jalen Duren at pick 13; Karl-Anthony Towns at pick 16 | paused; explicit resume was followed by another stale pause |

The durable files are [trial-1000000013](../draft_assistant/validation/browser-recovery-2026-10-11/trial-1000000013.json), [clean-1000000004](../draft_assistant/validation/browser-recovery-2026-10-11/clean-1000000004.json), [uninterrupted-1000000007](../draft_assistant/validation/browser-recovery-2026-10-11/uninterrupted-1000000007.json), [recovery-1000000008](../draft_assistant/validation/browser-recovery-2026-10-11/recovery-1000000008.json), and [clean-1000000009](../draft_assistant/validation/browser-recovery-2026-10-11/clean-1000000009.json). The structured latency and provenance companion is [browser-events.json](../draft_assistant/validation/browser-recovery-2026-10-11/browser-events.json). The exported rooms were explicitly attested as practice rooms for Example League, season 2027, team 15, with the configured live league recorded as the source identity.

For `trial-1000000013`, the first Sabonis pick was confirmed, the room paused
when current candidates disappeared, and the Duren command was later cancelled
without a browser click after an operator form timeout. Duren was then retried
and confirmed. The remaining eight picks were confirmed from history and roster
read-back. The final observation reports 140 unique history player IDs,
`phase=complete`, `history_complete=true`, ten roster players, and legal roster
shape. The final roster has C=7, G=2, F=4, and five C-only players; its
structural utilization implications are covered in [the utilization review](DRAFT_UTILIZATION_REVIEW.md).

The trial's per-pick latency facts are preserved in milliseconds in
`browser-events.json`: observation-to-click values range from 1,221 to 2,156
ms, and click-to-confirm values range from 1,651 to 22,004 ms. Pick 72 had 15
seconds remaining and pick 128's final confirmation took 22,004 ms. These
figures document the run; they do not establish a clean latency gate because
the room paused repeatedly and the final confirmation was materially delayed.

For `clean-1000000004`, all ten direct picks were confirmed against history
and roster, the final observation contains 140 unique history IDs, and the
roster is legal with the same C=7, G=2, F=4 shape. Its latency rows are also
preserved in `browser-events.json`; observation-to-click values range from
1,085 to 2,177 ms and click-to-confirm values from 1,406 to 5,143 ms. The
durable journal records one stale pause, and the operator summary attributes
the pause before pick 125 to a transient prototype Feed identity-incomplete
row. That single pause keeps the room outside the no-pause clean gate even
though its ten-pick functional outcome is complete.

For `uninterrupted-1000000007`, the durable session reached revision 680 with
140 unique history selections and ten direct actions confirmed against both
history and roster. The single foreground invocation ran for 240.5786 seconds,
from `2026-10-11T15:12:04.885Z` through `2026-10-11T15:16:05.351Z`, and no
recovery pause or resume occurred after Start. Root CUA summary provenance
records observation-to-click latencies of 1,445–1,867 ms and click-to-confirm
latencies of 1,485–2,607 ms, with 81–86 seconds on the clock at each prepare
observation. This is a passed clean timing gate for this foreground run. It
does not establish canonical-module execution, ninety-minute foreground
continuity, deterministic queue fallback, or a production strategy decision.
The final roster is legal but center-heavy at C=7, G=2, F=3, with six C-only
players; the structural utilization review remains advisory.

The latest room `1000000002` has only root-summary/DB-export evidence in this
report. Towns at pick 13 and Duren at pick 16 were confirmed with the listed
latencies, then the coordinator stale-paused at 10.569 seconds after a latest
publish at `15:07:05.947Z` and next frame at `15:07:16.480Z`. Native Autopick
was off, the room was paused at pick 41 with only two roster entries, and no
native autopicks were observed. Its stored snapshot may end around history 17,
so it is labeled paused and non-terminal pending the root agent's outcome.
User updates after the agent handoff also failed the tab claim; the root agent
remains the sole owner of that pending outcome.

For `recovery-1000000008`, the coordinator paused on a stale observation, accepted a manual override, staged Siakam at pick 28 while automation remained paused, and confirmed it from authoritative history and roster data. After an explicit resume, Bam at pick 29 was submitted and confirmed; the next observation caused a recovery pause because the observed history was not contiguous and did not match the expected snake order. This is positive evidence for takeover, explicit resume, and conservative recovery behavior. It is not a clean autonomous pass.

For `clean-1000000009`, Duren at pick 13 and Towns at pick 16 were both submitted from fresh recommendations and confirmed by history plus roster. The journal then recorded a stale pause, an explicit resume, and a second stale pause. The operator summary reports that native Autopick subsequently selected De'Aaron Fox at pick 41 and LeBron James at pick 44 before the room paused at pick 58. Those native selections are reported as operator observations; the durable export contains the two direct confirmed actions and the pause/restart journal, not a complete native-autopick action journal.

## Queue and Autopick narrow facts

The native queue exercise began with a 15-second timer and Autopick off. Shai
Gilgeous-Alexander was queued first and Nikola Jokic second; the insertion
order was captured at `2026-10-11T14:01:05.866Z`. After a timeout at
`2026-10-11T14:01:46.274Z`, the read-back showed Shai in history and on our
roster, Autopick on, an opponent's Jokic selection at pick 6, and an empty
queue.

This is narrow evidence that queue insertion order, timeout observation, and
post-timeout read-back were exercised. It does not establish a general queue
fallback guarantee across rooms, browser timing, or Autopick states. The
fallback capability remains a release item until a clean, durable end-to-end
run demonstrates it with complete action and roster evidence.

## Takeover and restart facts

The operator summary reports a later current-room attempt in which Sabonis at
pick 13 and Duren at pick 16 were recovered and confirmed, then the coordinator
paused at pick 41 after an operator SQLite API error. This is evidence that
recovery preserved confirmed picks and stopped on an operator fault. It is not
a clean pass, and that later attempt has no durable export supporting a claim
of terminal completion; the separate `trial-1000000013` room is the completed
trial documented above.

The rehearsal therefore supports these bounded conclusions:

- stale observations pause automation and require an explicit resume;
- manual takeover can stage and reconcile a pick while automation remains paused;
- direct actions can be confirmed against both history and roster;
- a malformed or incomplete board state causes a recovery pause;
- operator and browser faults stop automation rather than silently advancing it.

It does not support claims that every production release gate is complete,
deterministic queue fallback is verified, ninety-minute foreground continuity
is rehearsed, or the strategy should change automatically. The completed trial
and the uninterrupted room support bounded claims of terminal completion and
final roster legality; the uninterrupted room also supports a clean timing
gate for its single foreground run.

## Frozen strategy gate

The lookahead gate remains failed. The frozen held-out report records a mean
modeled gain of 2.674 points, a clustered paired 95% lower bound of -3.094
points, and a worst-seat loss of -1.13%. Foundation remains the automatic
selection policy; lookahead remains advisory and is not promoted. No browser
rehearsal result changes that frozen decision.

## Remaining release work

The latest room `1000000002` remains paused and its outcome is pending with the
root agent. The uninterrupted room clears the clean timing gate for one
foreground run, but exact canonical-module execution, real ninety-minute
foreground continuity, deterministic queue fallback, and the center-heavy
strategy review remain open. Until those checks are resolved, keep the
real-draft release withheld and require a fresh explicit Start handoff for any
future practice or real room.
