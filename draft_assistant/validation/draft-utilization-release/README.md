# Draft utilization release artifact

`analysis.json` is the dated, schedule-aware review of the final practice
roster from `uninterrupted-1000000007`. `schedule_capture.json` retains the
official NBA opening-week game rows used to build the player matrix, and
`candidate_comparison.csv` contains the highest advisory G/F replacement
rows from the captured ESPN catalog.

The source schedule is the NBA's 2026–27 by-date PDF, released August 13,
2026 and marked subject to change. The forecast catalog is the rendered ESPN
projection snapshot captured October 10, 2026. The review uses known scoring
components only and preserves missing-field warnings. It applies no player
health forecast; capture-time DTD/O labels are retained as warnings and are
not treated as draft-date availability.

The seven-day matrix is exact for the captured dates. The four-cycle matrix
repeats the seven-day pattern only to test mechanics and is labeled synthetic.
The policy status is `advisory_only`; no promotion or live policy binding is
present in this artifact.

For the final roster, the opening-week result is 25 active starter slots out
of 42 lineup slots and 25 usable starts out of 31 scheduled player-games.
Those denominators are reported separately because the first measures lineup
slot fill while the second measures conversion of scheduled player-games into
usable starts.
