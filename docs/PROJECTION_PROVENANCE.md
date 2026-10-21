# Projection provenance and freshness schema

Schema id: `projection-provenance/1`. Implemented in
`draft_assistant/projection_refresh.py`. This is the shared contract for the
ADP, news/injury, and readiness work: new layers should add rows in the same
shape (`source`, `captured_at`, `age_hours`) rather than invent their own.

Everything here is **off by default**. The `marginal-lineup-v1` promotion
artifact was validated on synthetic data, so none of this changes
recommendations; it only records where inputs came from and how old they are.

## Row stamp

Stored in `dataset.provenance["projection_provenance"]["rows"][player_id]`
(a side map, because `PlayerRecord` has no extra fields):

| field | meaning |
| --- | --- |
| `source` | `espn`, an external source name, `espn+name` for a blend, or `checked_in_projection_csv` |
| `captured_at` | UTC `YYYY-MM-DDTHH:MM:SSZ`. For a blend, the **oldest** component, so a row is only as fresh as its stalest input |
| `age_hours` | age at `built_at`. Persisted for audit; freshness checks recompute it from `captured_at` at evaluation time |
| `gp_source` | `source_direct` (a source supplied projected GP; availability = GP/82) or `proxy_total_over_average` (the old derived proxy, kept only as a tagged fallback) |
| `components` | list of `{source, captured_at}` that fed the row |

Block-level fields: `schema`, `built_at`, `offline_fallback`, `sources`
(`source, role, captured_at, weight, rows`), `skipped_sources`, `warnings`,
`gp_direct_rows`, `gp_proxy_rows`.

## Freshness evaluation

`evaluate_freshness(provenance, now, max_age_hours=72)` returns
`{status, ok, age_hours, oldest_captured_at, max_age_hours, offline_fallback, warnings}`.
`status` is `fresh`, `stale`, or `unknown`. `ok` is true only for `fresh`.
Missing provenance, a row without `source` or `captured_at`, or a capture time
in the future is `unknown` and not ok (the gate fails closed). The checked-in
CSV is tagged `offline_fallback` and carries its own 2026-10-10 capture date, so
it reads as stale once past the threshold.

## Refreshing

There is no network client in this repo. ESPN projections are captured in an
attended browser session; `refresh-inputs` re-reads that capture.

```
python -m draft_assistant refresh-inputs --espn runtime/capture.json ^
    --external hashtag=runtime/hashtag.csv:0.5 --out runtime/projection_dataset.json
```

- `--espn` takes an attended capture JSON or normalized CSV. If omitted or
  unusable, the checked-in CSV is used and tagged `offline_fallback`.
- `--external NAME=PATH[:WEIGHT]`, at most two. Each file must carry its own
  `captured_at` (top-level JSON key, a per-row `captured_at`, or a dated source
  label); otherwise it is skipped and recorded in `skipped_sources`. A missing
  or unreadable source is also skipped, never fatal.
- Blending is a weighted per-field mean over the sources that supply the field.
  The ESPN base defines the candidate pool; external rows that match no base
  player (by `player_id`, then normalized name) are ignored.

## Staleness gate (default off)

```
python -m draft_assistant --projection-dataset runtime/projection_dataset.json ^
    --projection-max-age-hours 72 [--projection-staleness-gate] serve
```

- With only `--projection-dataset`: the panel shows a projection-data warning
  line and `status.projection_freshness` is reported. Nothing is blocked.
- With `--projection-staleness-gate` as well: readiness gains a
  `projection_fresh` check, so start/resume, prepare, claim, and the final
  action preflight are refused while data is stale or unverified.
- The policy is server-side configuration, like `promotion_path`; HTTP callers
  cannot set it.

Without these flags, behavior and `projection_dataset()` output are unchanged.
