# Reference solutions for the industry challenges (R1–R9)

**Don't open them until you have finished the challenge.** Each one is *one* solution that passes `make challenge-N`, not the only one.

| Challenge | Files |
|---|---|
| R1 | `pipelines/stream/models.py`, `pipelines/stream/consumer.py` (⚠️ a complete module 2 consumer + the v2 translation: it also spoils module 2) |
| R2 | `pipelines/delta/export.py` |
| R3 | `pipelines/polars_gold.py`, `docs/benchmark.md` (figures measured on an M4 Max: yours will differ) |
| R4 | `pipelines/dbt/snapshots/assets_snapshot.yml`, `pipelines/dbt/models/scd/` (uses its own `source` called `master_data` so it doesn't clash with yours; it needs your module 3 dbt project) |
| R5 | `ml/lgbm.py` |
| R6 | `ml/drift.py` (an empirical threshold per asset: ~4.5% of pairs flagged on clean data) |
| R7 | `genai/text2sql.py` (imports `check_input` from your module 6 `genai/guardrails.py`) |
| R8 | `legacy/report/report.py` (fixed), `legacy/report/tests/test_regressions.py`, `docs/postmortem-report.md` |
| R9 | `docs/databricks.md` (Jobs, pipelines, managed Iceberg and Lakebase names checked in October 2026) |

The module solutions are not published: the modules are the exercise.
