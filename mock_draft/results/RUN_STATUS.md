Matrix runs were launched from the repository root with:

```powershell
python -m mock_draft.experiments --output-dir mock_draft/results/synthetic
python -m mock_draft.experiments --players mock_draft/data/players_2026-27_espn_projection.csv --output-dir mock_draft/results/real_verified
```

Both canonical matrices completed successfully. Each covers 15 scenario conditions, 7 strategies, and all 14 seats; outputs include run-level JSON/CSV, aggregate summaries, policy selection, a representative complete draft log, and `RESULTS.md`.

The postprocessor includes the baseline in tested-policy selection, allowing it to remain recommended when every heuristic has a negative development delta.
