# Canonical draft rehearsal evidence — October 11–12, 2026

## Latest recovery checkpoint

Practice room 1000000012 has one guarded direct pick, Towns at pick 13,
confirmed from full history plus roster after an incomplete fast read-back.
Its action retains durable claim/dispatch provenance. The room is paused at
pick 16 with no unresolved actions. See the sanitized
[recovery record](../draft_assistant/validation/canonical-release-2026-10-11/recovery-townsp13.json)
and [activation diagnostics](../draft_assistant/validation/canonical-release-2026-10-11/activation-diagnostic-2026-10-12.json).

This is recovery evidence only. The canonical ten-pick run and Q1–Q4 queue
pilot remain pending. Two competing Windows listeners caused an outdated
panel response; the server now binds exclusively and a real-socket regression
rejects duplicate listeners. Current decisions are in
[draft readiness](DRAFT_READINESS.md).

## Historical room 1000000016

This report records the release-browser rehearsal for issue #9. The browser
room was opened and controlled in a dedicated Chrome tab. The room is paused
after an early native fallback consumed two owned turns; those turns are
excluded from direct runtime evidence, and this room is not eligible for the
10-pick direct gate.

## Room and handoff evidence

| field | observed value |
|---|---|
| source team page | `leagueId=1000000000`, team 15, season 2027 |
| fresh practice room | ESPN league `1000000016` |
| control-plane session | `2936b166d79949a29c6ad7b2c3a5023c` |
| attested heading | `ESPN Fantasy Basketball Draft - Practice Draft for Example League` |
| format | Head-to-Head Points, 14 teams, snake, 10 rounds |
| our team | Example Team, pick 13/14 first turn |
| native timer | 30 seconds configured in Rules/League Manager; running read-back exposed a live positive clock |
| Autopick | off on our sidebar; the separate League Manager control remains distinct |
| stopped state | explicit native pause at pick 30 after fallback recovery |
| evidence timestamp | `2026-10-11T21:01:17Z` (corrected UTC label) |

The active practice link was opened in my own Chrome tab. The heading and
14-team setup were verified, and the native room was paused before catalog
capture. I did not click `Start Practice Draft`. The local control plane records
the practice attestation and safe room URL. The canonical observer captured a
fresh revision 5 paused frame with 11 history events and 138 catalog entries.

The visible Rules view confirmed C=1, G=2, F=2, UTIL=1, BE=4, and IR=3. The
scoring read-back was FGM 2, FGA -1, FTM 1, FTA -1, TPM 1, REB 2, AST 2,
STL 4, BLK 4, TO -2, E -5, PF -1, TF -2, DD 3, TD 5, QD 100, and PTS 1.

## Catalog provenance

The paused Players view exposed 14 enabled `button[data-player-id]` rows. The
captured IDs are preserved in
[`catalog_snapshot.json`](../draft_assistant/validation/canonical-release-2026-10-11/catalog_snapshot.json).
They are a visible, paused-room catalog only. The snapshot records the
projected-average columns ESPN exposed and explicitly leaves TF, DD, TD, and
QD unavailable; it does not infer health, availability, or missing bonuses.
Current availability for a live pick must still come from the fresh enabled
button rows at the owned turn.

## Runtime changes and offline endurance

`operator_runtime.js` supports a durable, explicit foreground batch boundary.
A checkpoint requires a fresh complete observation, a fresh coordinator status
read with a measured receipt timestamp, an explicitly paused state, matching
room and revision identity, and confirmed direct draft actions whose IDs,
sessions, action types, claimed/dispatched flags, picks, teams, and players
match both history and roster. Actual `unresolved_actions` and
`unresolved_queue_mutations` reject the boundary. A resumed batch requires a
fresh explicitly running status after Resume and preserves the exact action
evidence. The panel adapter stamps the measured status receipt locally because
the coordinator status schema timestamps the nested observation, not the HTTP
response. The runner does not turn history alone into an action confirmation.

The owned observer/runtime Node suite passes 44/44 tests (including the
25-test runtime set). Its endurance fixture uses three
bounded foreground batches, crosses explicit 90-minute simulated-clock
boundaries, carries durable checkpoints between batches, and reaches a
terminal 140-history/10-roster result with ten confirmed own picks. A second
test rejects a checkpoint containing a pending click or a claimed count beyond
authoritative history. The simulated duration is test evidence only; no claim
of 90 minutes of live wall-clock browser continuity is made.

## Release status

The visible ESPN setup, attestation, pause/resume controls, rules, timer,
Autopick state, and catalog provenance are captured. Canonical definitions were
loaded in isolated CUA namespaces with the 138-player catalog and the local
panel session. A fresh running frame reached pick 13 with 14 enabled rows and
readiness passed. Before the runner could dispatch a direct click, the native
timer elapsed and ESPN selected Domantas Sabonis at pick 13 and Jalen Brunson
at pick 16 through native fallback. The room was then explicitly paused at pick
30. The sanitized readback is in
[`contaminated-practice-readback.json`](../draft_assistant/validation/canonical-release-2026-10-11/contaminated-practice-readback.json)
and the pause proof is in
the private browser screenshot, omitted from the publication snapshot.

This rehearsal has no canonical 140-pick history, ten direct-action journal, or
live latency result. The queue pilot was not started. A new clean practice room
or user-directed relaunch is required before browser execution continues; the
current room must remain paused and its native fallback picks must stay outside
the direct gate.

The remaining live gate is a clean room, followed by the exact reviewed
plain-JavaScript definitions in isolated IIFEs, fresh visible rows,
history-plus-roster confirmation for all ten owned picks, and a durable
sanitized export. Any >10 second observation gap must pause and require an
explicit validated resume. The release decision remains with the root
integrator after that evidence is reviewed.
