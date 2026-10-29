# Module 3 — Lakehouse: Spark, Iceberg, local S3, DuckDB and dbt

⏱ 8–10 h · Services: `make up-db up-stream up-lake` · Check: `make check-3`
**Tools:** Spark, Apache Iceberg, DuckDB, dbt, local S3 storage (SeaweedFS; MinIO no longer gets patches), SQL.

## What you will build
- **Two Spark jobs** that write to an **Iceberg** table: `backfill_history.py` (loads the 90-day history) and `stream_to_bronze.py`
  (consumes `plant.readings.clean` from Kafka with Structured Streaming).
- A **dbt project** that, using **DuckDB**, reads the Iceberg table (and the work orders from Postgres) and produces:
  `readings_hourly` (silver) and `asset_daily_features` (gold, the table the model will use).

## Why this module exists
So far data went through Kafka and was stored in Postgres. But the history grows without end, is analyzed in bulk and must not
compete with the system of record. A *lakehouse* stores data as cheap files in object storage (S3) **with
database guarantees**: transactions, schema, time travel. That is **Iceberg**: an open table format on top of
Parquet. Spark, DuckDB, Trino, Snowflake and Databricks all understand it: you are not tied to one vendor.

We pick different tools **for each size of problem** (*“DuckDB for single-machine data”*):
- **Spark** to move large volumes and for *streaming*: it scales to a cluster.
- **DuckDB + dbt** for analytical transformation of a volume that fits on one machine: simpler, faster, cheaper.

## Concepts
- **Iceberg**: table = *metadata* (snapshots, manifests) + Parquet files. Every write creates a **snapshot** (you can go back). The **REST catalog** stores where the latest one is.
- **Hidden partitioning** (`days(ts)`): Iceberg partitions the table by day without you writing the partition column; date queries skip entire files.
- **`MERGE INTO`**: “insert if it doesn't exist” in a single operation → **idempotency** (repeating doesn't duplicate).
- **Structured Streaming**: treats a Kafka topic as an infinite table. The **checkpoint** stores the processed offsets (so a restart neither repeats nor skips).
- **`foreachBatch`**: applies your own code (a `MERGE`) to each micro-batch. `trigger(availableNow=True)` processes what is pending and exits (ideal for tests and scheduled jobs).
- **dbt + DuckDB + extensions**: DuckDB can `ATTACH` an Iceberg REST catalog and a Postgres database, and mix them in one `SELECT`.
- **Time zone**: DuckDB reads `TIMESTAMPTZ` values in **your system's** time zone, not UTC. If you don't set it, your “days” come out shifted.

## The contract
**Spark.** The image already has the `lake` catalog configured (Iceberg REST + S3): use `SparkSession.builder.getOrCreate()` and write `lake.<ns>.readings`.
Environment variables your job receives: `LAKE_NS` (namespace, default `bronze`), `HISTORY_DIR` (`/data/history`), `KAFKA_BOOTSTRAP`, `KAFKA_TOPIC_CLEAN`,
`CHECKPOINT_BASE`, `AVAILABLE_NOW` (`1` = process and exit). They run with `make spark JOB=pipelines/spark/<job>.py`.

Table `lake.<ns>.readings`: `msg_id string, asset_id string, plant string, ts timestamp, sensor string, value double, unit string, quality string`,
**partitioned by day of `ts`**. Both jobs are **idempotent** (repeating doesn't duplicate; the streaming job also uses a checkpoint).

**dbt** in `pipelines/dbt/` (`dbt_project.yml`, `profiles.yml`). It reads from the environment: `LAKE_NS`, `WAREHOUSE_PATH` (default `data/warehouse.duckdb`), `DATABASE_URL`,
`ICEBERG_REST_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`. **No default credentials in the file** (module 8 will check it).

`readings_hourly` — grain (`asset_id, sensor, hour_start`): `value_mean, value_min, value_max, value_std, n_readings`, only `GOOD` readings, no duplicates, hour in UTC.

`asset_daily_features` — grain (`asset_id, feature_date`) with these **exact** definitions (the check recomputes them with pandas):

| Column | Definition |
|---|---|
| `feature_date` | UTC day of `ts` |
| `run_share` | mean of `RUN_STATE` during the day (all readings) |
| `vib_mean`, `vib_max`, `vib_std` | mean, max and **sample** standard deviation of vibration, **only at instants with `RUN_STATE = 1`** |
| `temp_mean`, `temp_max` | same for `TEMP_BEARING` |
| `press_mean`, `press_std` | same for `PRESS_DISCH` (sample standard deviation) |
| `curr_mean` | mean of `CURRENT_A` while running |
| `vib_mean_7d` | mean of `vib_mean` over the asset's last 7 rows (the current day + the 6 before) |
| `vib_delta_28d`, `temp_delta_28d`, `press_delta_28d` | today's `*_mean` minus the average of the **previous 28 days (not including today)**; `NULL` if there are no previous days |
| `label_failure_7d` | `1` if the asset had a **CORRECTIVE** work order opened in `(feature_date, feature_date + 7 days]` (read from Postgres); `0` otherwise; **`NULL` on the last 7 days of data** |

`dbt build` is expected to pass ≥ 8 items (models + tests: unique grain, `not_null`, `accepted_values`…).

## Your task
1. Look at the history: `data/history/date=…/readings.parquet`. What is the grain? What does `msg_id` look like?
2. **`backfill_history.py`**: create the namespace and the table (if they don't exist) and load with `MERGE INTO … WHEN NOT MATCHED THEN INSERT *`.
   Run it twice and count rows with the Spark console or with PyIceberg.
3. **`stream_to_bronze.py`**: Kafka → JSON parsing → `foreachBatch` with `MERGE`. Test it with `AVAILABLE_NOW=1` on a topic with repeats.
4. **Explore Iceberg**: how many snapshots does the table have? which Parquet files are in the `warehouse` bucket? (SeaweedFS console or `aws s3 ls`).
5. **dbt**: write `dbt_project.yml` and `profiles.yml` (extensions `httpfs`, `iceberg`, `postgres`; `ATTACH` both), the two models, `schema.yml` with tests and a singular test of the grain.
6. Compare your features with your own pandas computation for **one** asset and **one** week. If they don't match, that is where the learning is.

## How you will know you are done
`make check-3` runs your Spark jobs in a temporary namespace (backfill twice; streaming with 3,120 messages of which 3,000 are unique, again, and then with more messages), counts rows with PyIceberg and checks the partitioning. Then it runs your `dbt build`
against that namespace and a temporary Postgres database and compares **all** your gold columns with the independent computation.

## Comprehension questions
1. Why `MERGE` and not a plain `INSERT` in the streaming job? What role does the checkpoint play and what role the `MERGE`? Could you do without either?
2. What is an Iceberg snapshot and what real problem does it solve (hint: a job fails halfway through a write)?
3. Why does `vib_delta_28d` exclude the current day? What leakage would happen if you included it?
4. When would you prefer DuckDB over Spark? And the other way round? Give a reasonable volume threshold.
5. If the history grows to 5 years, what maintenance does the Iceberg table need (compaction, snapshot expiry)?

## Hints
<details><summary>dbt profile (the configuration is not what you need to learn here)</summary>

```yaml
plant:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "{{ env_var('WAREHOUSE_PATH', 'data/warehouse.duckdb') }}"
      extensions: [httpfs, iceberg, postgres]
      settings: {TimeZone: UTC}
      secrets:
        - {type: s3, key_id: "{{ env_var('S3_ACCESS_KEY') }}", secret: "{{ env_var('S3_SECRET_KEY') }}", endpoint: "localhost:8333", url_style: path, use_ssl: false, region: us-east-1}
      attach:
        - {path: warehouse, alias: lake, type: iceberg, options: {endpoint: "{{ env_var('ICEBERG_REST_URL', 'http://localhost:8181') }}", authorization_type: none}}
        - {path: "{{ env_var('DATABASE_URL') }}", alias: pg, type: postgres, read_only: true}
```
Then you read `lake.<ns>.readings` and `pg.public.work_orders` from your models.
</details>
<details><summary>Pivoting sensors into columns</summary>

`max(value) filter (where sensor = 'VIB_MM_S') as vib` grouping by `(asset_id, ts)`. That way `RUN_STATE` and the rest end up on the same row and you can filter “only while running”.
</details>
<details><summary>Windows by rows</summary>

`avg(vib_mean) over (partition by asset_id order by feature_date rows between 6 preceding and current row)` and `rows between 28 preceding and 1 preceding` for the baseline.
</details>
<details><summary>The streaming job doesn't run / keeps waiting</summary>

With `foreachBatch`, the `MERGE` needs a temporary view of the micro-batch (`createOrReplaceTempView`) and the function body uses `batch.sparkSession.sql(...)`. And remember `awaitTermination()`.
</details>

## Common traps
- `cast(ts as date)` on a `TIMESTAMPTZ` **without setting the UTC time zone**: you get a day more/less depending on your machine.
- Deduplicating with `DISTINCT *` (if two rows differ in `ingested_at`, they are not deduplicated). Use the natural key `msg_id`.
- `MERGE` with duplicates in the micro-batch *source*: deduplicate the batch first (`dropDuplicates(["msg_id"])`).
- Default credentials written in `profiles.yml`.
- Computing the baseline including the current day (information leakage).

## On a real job
You would add scheduled *compaction* (`rewrite_data_files`) and snapshot expiry, a managed catalog (Glue, Polaris, Unity), data contracts and *freshness* tests. In Databricks/Snowflake the same concepts use Delta/Iceberg and Unity Catalog/Polaris.
**Courses:** *Data Engineering Zoomcamp* (dbt, Spark, Kafka); the official Iceberg documentation (the Zoomcamp doesn't cover it).

**Industry challenges (optional, once this module is green):** [R2 · Delta + Iceberg](challenges/R2_delta_iceberg.md), [R3 · Polars at scale](challenges/R3_polars_scale.md), [R4 · SCD type 2](challenges/R4_scd2.md) and [R9 · Databricks](challenges/R9_databricks.md). Index: [`challenges/README.md`](challenges/README.md).

→ Next: [`04_ml.md`](04_ml.md)
