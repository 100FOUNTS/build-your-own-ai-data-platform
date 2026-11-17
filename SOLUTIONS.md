# Reference solutions for the industry challenges (R1–R9)

**Don't open them until you have finished the challenge.** Each one is *one* solution that passes `make challenge-N`, not the only one.

| Challenge | Files |
|---|---|
| R1 | `pipelines/stream/models.py`, `pipelines/stream/consumer.py`, `pipelines/stream/contract.py` (⚠️ a complete module 2 consumer + the v2 translation: it also spoils module 2. Part 2: the v1 JSON Schema registered under `<topic>-value` with `BACKWARD` pinned on the subject (consumers are upgraded first and the raw topic is replayed, so new code must read old messages); the consumer reads the contracts from the registry by declared version, `<topic>-v2-value` for the vendor's v2, and sends anything non-conforming to the DLQ as `schema:<detail>`; a topic without a subject is ungoverned (a warning is printed). Needs the registry published on `localhost:18081`) |
| R2 | `pipelines/delta/export.py` |
| R3 | `pipelines/polars_gold.py`, `docs/benchmark.md` (figures measured on an M4 Max: yours will differ) |
| R4 | `pipelines/dbt/snapshots/assets_snapshot.yml`, `pipelines/dbt/models/scd/` (uses its own `source` called `master_data` so it doesn't clash with yours; it needs your module 3 dbt project) |
| R5 | `ml/lgbm.py` (cheapest threshold on validation under the ≤ 5 limit), `ml/policy_compare.py` (default 0.5 vs optimal: paired block bootstrap by asset-week, seeded; on the test period the interval contains 0) |
| R6 | `ml/drift.py` (an empirical threshold per asset: ~4.5% of pairs flagged on clean data) |
| R7 | `genai/text2sql.py` (imports `check_input` from your module 6 `genai/guardrails.py`) |
| R8 | `legacy/report/report.py` (fixed), `legacy/report/tests/test_regressions.py`, `docs/postmortem-report.md`, `notes/R8_agent_review.md` (part 2: a worked example whose agent diffs were written by hand to show the shape of a review; yours will differ) |
| R9 | `docs/databricks.md` (Jobs, pipelines, managed Iceberg and Lakebase names checked in October 2026) |

The module solutions are not published: the modules are the exercise.
