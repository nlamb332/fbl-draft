# Mock draft fixture

Run from the workspace root:

```powershell
python -m unittest mock_draft.test_fixture -v
python -m mock_draft.experiments --output-dir mock_draft/results/synthetic
python -m mock_draft.experiments --players mock_draft/data/players_2026-27_espn_projection.csv --output-dir mock_draft/results/real_verified
```

Each full matrix covers seven strategies, all 14 seats, and 15 scenarios (1,470 drafts). Results are written after the complete matrix passes its checks. See [DATA_NOTES.md](DATA_NOTES.md) for the captured ESPN input's limitations and [RESULTS.md](RESULTS.md) for the comparison. These are local simulations; they do not join ESPN draft rooms or change the live league.

`fixture.py` is a standard-library mock snake draft engine for sensitivity tests. It models exactly 14 total teams (our team plus 13 opponents), 10 rounds, and 140 unique picks. The draft seats are 1 through 14 and the order reverses each round. Each roster has six daily starting slots (`C, G, G, F, F, UTIL`), four bench spots, and three additional IR slots that are not draft spots.

The checked-in fixture is explicitly synthetic. `synthetic_fixture()` creates generated `SYNTH-*` records with coherent shot-attempt/made and points identities, generated market rank/ADP, realistic role-style eligibility, availability, and upside. These values must not be presented as real-player projections. Use `load_player_records("players.csv")` or `load_player_records("players.json")` to supply real data; the loader does not infer missing projections.

The custom points formula is:

`PTS + 2FGM - FGA + FTM - FTA + 3PM + 2REB + 2AST + 4STL + 4BLK - 2TO - PF - 2TF - 5EJ + 3DD + 5TD + 100QD`.

The platform's DD/TD overlap convention is unresolved, so the fixture exposes it as `DraftConfig.double_double_includes_triple_double` (the default is `true`, awarding both bonuses to a triple-double). Passing `false` makes the bonuses mutually exclusive.

## Small API example

```python
from mock_draft.fixture import (
    MockDraft, allocate_lineup, make_synthetic_schedule, synthetic_fixture,
)

players = synthetic_fixture(seed=7)
draft = MockDraft(players, strategy="custom_value", seat=1, seed=11).run()
roster = draft.roster()
schedule = make_synthetic_schedule(players, days=7, seed=99)
lineup = allocate_lineup(roster, day=0, schedule=schedule, method="dp")
print(len(draft.picks), lineup.score, lineup.inactive_starters)
```

The exact allocator uses dynamic programming over player-to-slot assignments. `method="greedy"` is provided as a transparent approximation for strategy comparisons. Both methods preserve the legal roster shape; if availability makes an all-active assignment impossible, unavailable starters are returned with zero score and named in `inactive_starters`.

Use the same `SyntheticSchedule` instance (or the same seed and generated matrix) for paired strategies. This keeps schedule randomness common across comparisons. Its `source_label` is `synthetic_schedule`. The schedule separates shared synthetic team game/off-day patterns (roughly 3–4 games per seven-day week) from player health draws; `availability` is the conjunction of both. Supply `schedule_group`/`team` on external records to preserve teammate correlations; records without one receive deterministic synthetic groups.

`check_roster_shape` exposes `(is_legal, errors)` and `assert_roster_shape` raises `RosterShapeError`. The draft validates all 14 final rosters, rejects duplicate picks, and checks that every seat 1–14 is represented. Opponent profiles deliberately vary market rank, custom points, positional fit/flexibility, big-player preference, availability, upside, and noise. The supported strategies are `baseline_market`, `custom_value`, `flexibility_balanced`, `big_heavy`, `availability`, `upside`, and `marginal_usable_lineup`. The market baseline ranks strictly by market rank/ADP subject to shape feasibility; availability uses expected custom points multiplied by projected availability; the lineup-aware variant adds a transparent marginal C1/G2/F2/UTIL1 coverage signal.

`check_player_pool_shape` / `assert_player_pool_shape` perform a pre-draft depth check (at least 140 records, 14 C, 28 G, and 28 F eligible records) so a malformed data file is rejected before a late-round shape failure. During a pick, the picking team's remaining slots use exact eligibility matching; the global reservation is a conservative position-count check and is intentionally documented as such because multi-position records can count toward more than one global category.
