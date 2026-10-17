# Draft utilization release review

This release closes the roster quality review for the completed practice draft
`uninterrupted-1000000007`. The roster is legal, but its shape is
center-heavy: C=7, G=2, F=3, and six C-only players. The review therefore
measures usable starts with an exact `C, G, G, F, F, UTIL` assignment and keeps
the schedule-aware balance adjustment advisory.

The reproducible artifact is in
`draft_assistant/validation/draft-utilization-release/`. It contains the
analysis JSON, the official schedule capture, and the top candidate comparison
CSV. Re-run it from the worktree with:

```text
python -m draft_assistant.validation_utilization_release
```

The schedule window is October 20–26, 2026, taken from the NBA's official
schedule-by-date PDF released August 13, 2026. The PDF itself says the slate
is subject to change. The captured week gives this roster 31 of a possible 70
player-days with a team game. Exact assignment produces 25 active starter
slots out of 42 available lineup slots (59.5%) and 25 usable starts out of 31
scheduled player-games (80.6%), with 2.43 inactive starter slots per day and
0.29 average busy collisions. The same seven-day pattern repeated four times
has the same mechanics rate; that repetition is explicitly synthetic and is
not a season forecast. The exact daily assignments are retained in
`analysis.json`. The captured text excerpt, PDF page/line provenance, and
SHA-256 are retained in `nba_schedule_excerpt.txt` and `schedule_capture.json`.

The team-game matrix is schedule-only. No player health probability was
supplied, so these are upper-bound schedule mechanics rather than expected
usable starts. The October 10 ESPN capture records DTD for Jalen Duren and
Walker Kessler; those labels are capture-time evidence and do not establish
availability on the October 18 draft date. The matrix also does not turn
missing PF, TF, EJ, DD, TD, or QD projections into zeros. Every candidate row
keeps a missing-field warning and a draft-date-health-unknown warning.

For a concrete balance check, the review compares G/F-eligible players from
the same captured ESPN catalog (rendered market-proxy rank 80–180) as a
replacement for the C-only `Kel'el Ware`. The schedule-only advisory leaders
are Jimmy Butler III, Alex Sarr, Ty Jerome, Caleb Wilson, and Kristaps
Porzingis by the configured production plus marginal usable value score. The
result is evidence that guard/forward depth can create two additional active
starter slots in this bounded week, while also showing why an automatic
recommendation would be unsound: several leaders have capture-time injury
labels, and every row is missing part of the custom scoring forecast.

The promotion gate is intentionally false. This bounded review does not run
the required separate 100-seed development and 100-seed held-out matrices
across all 14 seats, and it has no draft-date health matrix or complete custom
scoring forecast. The release recommendation is to retain the frozen
foundation scorer and lookahead benchmark, and use this module only as an
advisory review column after dated schedules, current status, and complete
forecast inputs are available.

Sources captured for reproducibility:

- [NBA 2026–27 schedule release and by-team index](https://www.nba.com/news/2026-27-schedule-team-by-team-index)
- [NBA official schedule-by-date PDF](https://cdn.nba.com/manage/2026/08/2026-27-NBA-Regular-Season-Schedule-By-Date.pdf)
- [NBA Karl-Anthony Towns profile](https://www.nba.com/player/1626157/karl-anthony_towns)
- [NBA Jalen Duren profile/news](https://www.nba.com/pistons/player/1631105/jalen-duren)
- [NBA Walker Kessler profile](https://www.nba.com/player/1631117/walker-kessler)
- [NBA Kyshawn George profile](https://www.nba.com/wizards/player/1642273/kyshawn-george)
- [NBA Jrue Holiday profile](https://www.nba.com/player/201950/jrue_holiday)
- [ESPN rendered projection snapshot and limitations](../mock_draft/DATA_NOTES.md)

No frozen validation artifact, live valuation rule, or coordinator binding was
changed.
