# Public Sleeper projection audit for Lock-In research

Observed October 17, 2026 (America/Chicago), through read-only public HTTPS requests by the Luna 5.6 XHIGH input-audit agent. No private league captures or user identifiers were used. These counts describe the responses observed during this task, not a permanent endpoint contract.

Endpoint: `https://api.sleeper.app/projections/nba/{season}?season_type=regular&position[]=PG&position[]=SG&position[]=SF&position[]=PF&position[]=C`.

| Observation | Season key 2026 | Season key 2027 |
| --- | ---: | ---: |
| HTTP status | 200 | 200 |
| Response bytes | 1,489,870 | 909,350 |
| Unique player rows | 2,132 | 1,856 |
| Rows with non-ADP forecast statistics | 529 | 0 |
| Complete PTS/REB/AST/STL/BLK/TO/3PM forecasts | 487 | 0 |
| ADP-only rows | 1,336 | 0 |

The 2026 records identify NBA, regular season, category `proj`, company `rotowire`, and `game_id=season`. All carry `last_modified` and `updated_at`; game date and week are null. The 2027 response is metadata only: every stats object is empty. Do not substitute one season key for another silently.

2026 field coverage: PTS/REB/AST each 529; STL 527; BLK 519; TO 528; 3PM 495; DD 209; TD 26; flagrant foul 1; technical foul, 40+ and 50+ point events 0. Sparse fields may have provider-specific omission conventions; those conventions were not independently verified. Absence is not an observed zero event rate for the purpose of this audit.

Native positions are PG/SG/SF/PF/C. Non-null injury status occurs in 115 rows, injury notes in 48, injury body part in 115, injury start date in zero, and news update metadata in 1,521. Team metadata is non-null in 613 rows. These labels do not supply calibrated game-availability probabilities.

## Decision

The responses can anchor season-level player means, but they do not establish a real Lock-In outcome model. They lack game schedules, opponents, game-level variance, reliable availability probabilities and complete threshold-event forecasts. The current study therefore remains synthetic, with generated schedules, game distributions and health assumptions disclosed. It does not claim that its players, event rates or outcome probabilities are real 2026 forecasts.

A future real-player study should freeze the correct season catalog and source timestamps, verify sparse-stat conventions, acquire dated schedules and game-level histories, and estimate distributions using development data independent of evaluation. Adding real names to assumed variance and health draws would not by itself validate the model.

Rules reference: [Sleeper Lock-In details](https://support.sleeper.com/en/articles/6522833-lock-in-mode-details). The endpoint is public but not a substitute for verifying this individual league's game mode and settings.
