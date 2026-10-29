# Industry challenges

Nine challenges that extend the modules with what you see most on a real job and the modules don't cover: data that changes without warning,
**Delta Lake** next to Iceberg, **Polars**, dimension history (**SCD2**), **LightGBM** with business metrics, **drift** without alert fatigue,
safe **text-to-SQL**, **legacy code** with bugs and the map onto **Databricks**.

Just like the modules, **nothing is done for you** here: each guide gives you the what, the why and the what-for, the contract and hints; you write the code.
Only R8 hands you code, because the challenge is precisely inheriting it.

| Challenge | Extends | What you build | Tools | Hours | Services | Check |
|---|---|---|---|---|---|---|
| [R1](R1_new_vendor.md) New vendor | M2 | The consumer accepts a v2 schema with other units, rejects unknown versions, deduplicates across formats | Kafka, Pydantic, Redis | 3–4 | `up-db up-stream` | `make challenge-1` |
| [R2](R2_delta_iceberg.md) Delta and Iceberg | M3 | Publish bronze (Iceberg) as an idempotent Delta table, with time travel and read by another engine | Delta Lake (delta-rs), PyIceberg, DuckDB | 3–4 | `up-lake` | `make challenge-2` |
| [R3](R3_polars_scale.md) Scale 5× with Polars | M3 | The gold layer with Polars (lazy) on 60 pumps, plus a performance report with method | Polars, DuckDB, `EXPLAIN` | 4–5 | none | `make challenge-3` |
| [R4](R4_scd2.md) Master data history | M3 | A dbt snapshot (SCD type 2) and the current dimension | dbt snapshots, DuckDB, Postgres | 2–3 | `up-db up-lake` | `make challenge-4` |
| [R5](R5_lgbm_business.md) LightGBM and the business | M4 | A threshold chosen on validation, false alerts per 100 healthy days, warning ≥ 3 days ahead and the why of each alert | LightGBM, SHAP (`pred_contrib`), MLflow | 4–5 | `up-lake` | `make challenge-5` |
| [R6](R6_drift.md) Drift without fatigue | M4 | PSI per asset that catches a silent unit change without alerting on everything | NumPy, pandas | 3–4 | none | `make challenge-6` |
| [R7](R7_text2sql.md) Safe text-to-SQL | M6 | Natural-language questions → SQL validated with the query tree, a read-only database, a retry with the error | DuckDB, Ollama, guardrails | 4–5 | Ollama | `make challenge-7` |
| [R8](R8_legacy_code.md) Legacy code | M14 | Find the bugs in a report that “always worked”, regression tests and a postmortem | pandas, pytest | 3–4 | none | `make challenge-8` |
| [R9](R9_databricks.md) Take it to Databricks | M3, M14 | A mapping table, what doesn't carry over, costs, a migration plan and a recommendation | Databricks (Free Edition optional) | 3–4 | none | `make challenge-9` |

**Total ≈ 30–38 hours.** `make challenges` runs them all. The challenges are not part of `make check` or `make progress`: they are optional.

## In what order
- Each challenge comes **after** its module (R1 after 2, R2–R4 after 3, R5–R6 after 4, R7 after 6, R8 whenever you like, R9 at the end).
- The R2, R3, R5, R6 and R7 checks build their own input data (like the module 4 check): they don't depend on your earlier modules being perfect.
  R1 uses your module 2 consumer and R4 your module 3 dbt project, because they extend them.
- By role (see [`../role_paths.md`](../role_paths.md)): **data** R1 · R2 · R3 · R4 · R8 · R9 · **ML/AI** R5 · R6 · R7 · R8 · **backend/platform** R1 · R7 · R8.

## How to work
Same as the modules: read *Why this challenge exists*, build, `make challenge-N`, `/review` with Claude Code and answer the comprehension questions in your own words.
`/hint` and `/why` also work with the challenges (for example `/hint R5`).
The `solution` branch has a reference solution for each challenge; open it only after you finish.
