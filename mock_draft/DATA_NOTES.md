# 2026–27 player-data snapshot

Updated: 2026-10-10 (America/Chicago). This snapshot is for the 14-team, 10-round mock-draft sensitivity runs. It contains **195 complete player rows** from 200 rows transcribed from the rendered ESPN projections UI. The five omitted rows were four free agents and one non-free-agent row with `--` placeholders; no values were filled in for them.

## Files

- `data/espn_ui_raw_2026-10-10.txt` is the source transcription from the signed-in, rendered ESPN page. It is retained so every normalized value can be audited against what was visible in the UI.
- `data/players_2026-27_espn_projection.csv` is the normalized input. It loads with `fixture.load_player_records()` and has enough C/F/G depth for all 140 picks.
- `data/normalize_espn_projection.py` is the deterministic, standard-library-only normalizer used to produce the CSV.

The normalizer performs a deterministic audit after writing: 195/195 complete raw rows matched normalized names, IDs, and primitive values; the known-component score (`PTS + 2*FGM - FGA + FTM - FTA + 3PM + 2*REB + 2*AST + 4*STL + 4*BLK - 2*TO`) differed from the displayed ESPN fantasy average by at most 0.2 points, with all 195 rows within the 0.25 propagated-rounding tolerance. PF/TF/EJ/DD/TD/QD remain intentionally excluded from that check because the source did not provide credible forecasts.

## Sources and provenance

The primary source is the rendered ESPN projections page for the league (`https://fantasy.espn.com/basketball/players/projections?leagueId=1000000000`), read on 2026-10-10. The page visibly supplied 2027 projected per-game `MIN`, `FGM`, `FGA`, `FTM`, `FTA`, `3PM`, `REB`, `AST`, `STL`, `BLK`, `TO`, and `PTS`, ESPN position tags, a displayed fantasy total, and a displayed fantasy average. No private API, hidden endpoint, or cookie extraction was used.

As an independent public cross-check, FantasyPros' current 2026–27 overall projections page was readable on 2026-10-10 (`https://www.fantasypros.com/nba/projections/overall.php`; page says consensus last updated October 9, 2026). It exposes a separate public projection/ranking view, but does not replace the ESPN rows here because the requested ESPN eligibility and primitive makes/attempts are available in the rendered ESPN table.

## Field contract

The CSV keeps the displayed primitive columns in both lower-case evidence columns (`fgm`, `fga`, `ftm`, `fta`, `3pm`, `reb`, `ast`, `stl`, `blk`, `to`, `pts`, `mpg`) and a JSON `stats` object with the uppercase names expected by `fixture.py`. `espn_positions` preserves ESPN's source tags. `eligibility` maps PG/SG to `G`, SF/PF to `F`, and C to `C` for the mock engine.

ESPN did not display a projected games-played field. `gp_projected` is therefore blank. `gp_implied_from_total_avg` is only the quotient of the displayed rounded season total and rounded average, retained as an explicitly labelled audit aid; it is not an exact games projection and should not be treated as one. `mpg` is the displayed projected minutes per game. The dataset never reconstructs makes or attempts from percentages; ESPN displayed the makes and attempts directly.

The page order appeared to be percent-rostered order, not a true ADP table. `source_row_rank` and `market_rank` retain that visible order as a **market-order proxy**. `adp` repeats the proxy only because the current engine requires a numeric ADP field; `adp_source` explicitly says that an independent ADP was unavailable. It must not be reported as measured ADP or used to claim an ADP finding. There is no ESPN market-rank value in this snapshot beyond the visible row order.

`projected_availability` is a rough derived proxy: `(displayed fantasy total / displayed fantasy average) / 82`, clipped to `[0, 1]`. ESPN did not provide an availability probability; this quotient is only a season-length signal for sensitivity testing, not a calibrated health forecast. The original `injury_status` remains in the CSV so current `O`, `OUT`, and `DTD` labels can be audited without treating them as season-long exclusions. `upside` is `0.0` because no source ceiling projection was available.

The source UI rendered `EJ`, `PF`, `TF`, `DD`, `TD`, and `QD` as `0.0` throughout the inspected pages. Those values are omitted from `stats` and left blank in the evidence columns rather than being asserted as real zero forecasts. Consequently, any simulation using the supplied rows scores those unmodeled components as zero through the engine's missing-stat default. This is a known limitation, not evidence that players commit no fouls or record no double/triple doubles.

## DD/TD and leakage notes

The DD/TD stack convention remains unresolved by the source. `config.json` defaults to `double_double_includes_triple_double: true`, which awards both the DD and TD bonuses when a TD is supplied. Since this snapshot has no DD or TD projections, that switch has no direct effect on these rows; it remains a sensitivity assumption for any later enriched data.

The normalized values are pre-draft projections and a visible market-order proxy captured on 2026-10-10. They are appropriate as frozen inputs for paired mock-draft experiments. Do not evaluate a strategy with a later realized season result as though it were available at draft time. Do not call the roster-order proxy ADP, and do not infer causal conclusions from this single snapshot. Conclusions supported here are modeled sensitivity results conditional on the supplied projections, ESPN eligibility, the missing-stat convention, the rough implied-GP availability proxy, and the shared schedule seed.
