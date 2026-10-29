# Challenge R4 — History of the asset master data (SCD type 2)

⏱ 2–3 h · Extends **module 3** · Services: `make up-db up-lake` · Check: `make challenge-4`
**Tools:** dbt (snapshots), DuckDB, PostgreSQL. **What you practice:** dimensional modeling, *slowly changing dimensions*, idempotency.

## What you will build
In your module 3 dbt project: a **snapshot** `assets_snapshot` that keeps the history of the Postgres `assets` table (every time the criticality,
the model or the power of a pump changes, a new version is born and the old one is closed) and a `dim_assets_current` model with the current version.

## Why this challenge exists
In March pump P103 had criticality C; in September they raised it to A. If your assets table only keeps today's value, every question
about the past comes out wrong: *“how many failures did the critical pumps have in the first quarter?”* will count P103 as critical when it wasn't.
Keeping the history of a dimension (**SCD type 2**: one row per version, with a start and an end date) is one of the most asked topics in data engineering
interviews and one of the most forgotten in practice.

## Concepts
- **SCD type 1 / type 2**: type 1 overwrites (you lose the history); type 2 adds a new version and closes the previous one.
- **`dbt snapshot`**: on every run it compares the source with the last stored version. Strategy **`timestamp`** (if the source has `updated_at`) or **`check`** (compares columns).
- **`dbt_valid_from` / `dbt_valid_to`**: the validity interval of each version; the current one has a null `dbt_valid_to`.
- **As-of query**: `where date >= dbt_valid_from and (date < dbt_valid_to or dbt_valid_to is null)`.
- **Deletes**: `hard_deletes` (`ignore`, `invalidate`, `new_record`) decides what happens if an asset disappears from the source.

## The contract
- A snapshot called **`assets_snapshot`** in `pipelines/dbt` (in `snapshots/*.yml` or `*.sql`), with `unique_key: asset_id`, that reads `assets` from Postgres (your profile's `pg` `ATTACH`).
- It versions changes in `plant`, `model`, `criticality`, `install_date`, `rated_flow_m3h` and `rated_kw`. An `UPDATE` that doesn't change values does **not** create a version.
- A **`dim_assets_current`** model: one row per asset with its current values, with at least one dbt test (e.g. `unique` and `not_null` on `asset_id`).
- Everything with the usual variables (`WAREHOUSE_PATH`, `DATABASE_URL`…) and no credentials in the files.

## Your task
1. Ask yourself first: does the `assets` table have a column that says when each row changed? That decides the strategy.
2. Write the snapshot and run it: `uv run dbt snapshot --project-dir pipelines/dbt --profiles-dir pipelines/dbt`. Look at the resulting table in DuckDB.
3. Change the criticality of a pump by hand in Postgres (`make up-db`, `psql` on port 5433), run the snapshot again and **look at the two rows**.
4. Run the snapshot once more without changing anything: how many rows are there?
5. Write `dim_assets_current` and its tests. Bonus: write (without handing it in) the query that joins `asset_daily_features` with the criticality **in force on each date**.

## How you will know you are done
`make challenge-4` creates a temporary Postgres database with your migrations and seeds (module 1), runs your snapshot, changes the criticality of one pump and the power of another,
“touches” a third without changing anything, runs it again (and once more without changes) and checks versions, intervals without gaps or overlaps and the current dimension.

## Comprehension questions
1. Why can't the snapshot rebuild the history **before** its first run? What would you do if they asked you for the 2024 criticality?
2. With the `check` strategy, which date ends up in `dbt_valid_from`: the real change date or the run date? What does running the snapshot once a day imply?
3. What happens if someone deletes a pump from the master data? Which `hard_deletes` did you choose and why?
4. When would you prefer SCD type 1? Give an example of a column whose history doesn't matter.
5. Why do the `dim_assets_current` tests matter even though the snapshot “is dbt's”?

## Hints
<details><summary>Snapshot in YAML (dbt ≥ 1.9)</summary>

```yaml
snapshots:
  - name: …
    relation: source('…', '…')
    config: {unique_key: …, strategy: …, check_cols: […]}
```
You need to declare Postgres as a `source` (with `database: pg`, `schema: public`).
</details>
<details><summary>The current version</summary>

It is the one with `dbt_valid_to is null`. If you configured `dbt_valid_to_current`, it is the one with that “infinite” date.
</details>

## Common traps
- `check_cols: all` with a column that changes by itself (a technical `updated_at`, a counter): new versions on every run.
- Snapshotting an already transformed model instead of the source: if you change the model, you change the history.
- Joining facts with the **current** dimension when the question is historical.

## On a real job
Snapshots are scheduled in the orchestrator (your module 5 Dagster), fact tables are joined with the version in force **on each date**, and in Databricks the same thing is done with
`APPLY CHANGES … STORED AS SCD TYPE 2` in Lakeflow Spark Declarative Pipelines (formerly Delta Live Tables).

→ Next: [`R5_lgbm_business.md`](R5_lgbm_business.md) · Back to the [challenges index](README.md)
