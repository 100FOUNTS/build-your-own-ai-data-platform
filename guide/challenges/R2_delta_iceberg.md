# Challenge R2 — Delta and Iceberg side by side

⏱ 3–4 h · Extends **module 3** · Services: `make up-lake` · Check: `make challenge-2`
**Tools:** Delta Lake (with `deltalake`, the Rust/Python library without Spark), Iceberg (PyIceberg), DuckDB.

## What you will build
`python -m pipelines.delta.export`: reads your bronze table `lake.<LAKE_NS>.readings` (Iceberg) and publishes it as a **Delta table** at `DELTA_PATH`,
partitioned by day, **idempotent** (repeating doesn't duplicate) and **incremental** (a second run only adds what is new). Another engine (DuckDB) must be able to read it.

## Why this challenge exists
The two open table formats live side by side in the industry: **Iceberg** (Snowflake, AWS, Trino, the one you use here) and **Delta** (Databricks').
The usual thing is not to pick one forever: a data science team on Databricks asks you for the data “in Delta”, another team wants it in Iceberg.
Making them coexist forces you to understand how they are alike (Parquet + a transaction log + snapshots) and how they are not (hidden partitioning
versus an explicit column, a catalog versus a `_delta_log` folder). And it is a common interview question: *“Delta or Iceberg?”*.

## Concepts
- **Delta Lake**: table = Parquet files + `_delta_log/` (one JSON per transaction). Each write creates a **version**; `DeltaTable(path, version=n)` is time travel.
- **Explicit partitioning**: in Delta the partition is a **column** (`date`); in Iceberg it is a hidden transform (`days(ts)`). Compare it with what you did in module 3.
- **MERGE in delta-rs**: `dt.merge(source, predicate="t.msg_id = s.msg_id", source_alias="s", target_alias="t").when_not_matched_insert_all().execute()`.
- **Incremental**: reading only what is new (by Iceberg snapshot, by date or by comparing keys) versus rewriting everything.
- **Interoperability**: an open format only counts if **another engine** reads it the same way (DuckDB's `delta_scan`). There is also **UniForm** (Delta that reads as Iceberg) and **XTable**.

## The contract
- **Input**: `lake.<LAKE_NS>.readings` (the module 3 schema), with the variables `LAKE_NS`, `ICEBERG_REST_URL`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (in `platform.env`).
- **Output**: a Delta table at `DELTA_PATH` (default `data/delta/readings`, a local folder) with the 8 module 3 columns (`ts` is still a *timestamp*) **plus** `date` = UTC day of `ts` (text `YYYY-MM-DD` or a date), **partitioned only by `date`**.
- **Idempotent**: running twice with no changes doesn't change the number of rows.
- **Incremental**: if Iceberg gets a new day, the next run adds exactly those rows and the Delta table version moves forward.
- The previous version is still available (time travel) and DuckDB (`delta_scan`) sees the same rows.

## Your task
1. Write 1,000 toy rows with `write_deltalake` into a temporary folder and **open `_delta_log/`**: what is in the first JSON? Write again and compare.
2. Read your Iceberg table with PyIceberg (`catalog.load_table(...).scan().to_arrow()`) and add the `date` column in UTC.
3. First run: create the partitioned table. Next ones: **MERGE on `msg_id`** (or filter out first what already exists, and explain why you still use MERGE).
4. Try time travel: `DeltaTable(path, version=0)`. And read it from DuckDB: `install delta; load delta; select count(*) from delta_scan('...')`.
5. Write down in `notes/decisions.md` an “Iceberg vs. Delta” table with what you saw (partitioning, metadata, catalog, maintenance).

## How you will know you are done
`make challenge-2` creates **its own** small Iceberg table (3 days of history) in a temporary namespace, runs your export, checks rows, schema and partitioning,
runs it again (idempotency), adds a fourth day to Iceberg and checks that only what is new gets in, tests time travel and reads the result with DuckDB.

## Comprehension questions
1. What is easier to change later: the partitioning of an Iceberg table or of a Delta table? Why?
2. Two exports run at the same time. What stops a row from getting in twice? What would happen with a blind *append*?
3. What grows without end in `_delta_log/` and in the data files if you export every 5 minutes for a year? What would you do (`optimize`, `vacuum`, *checkpoints*)?
4. If one team only has Snowflake and another only Databricks, would you keep two copies or one table with UniForm? What do you risk in each case?
5. Why does your `date` have to be computed in UTC and not in the plant's local time?

## Hints
<details><summary>The `date` column in UTC without pandas</summary>

`pyarrow.compute.strftime(table["ts"], format="%Y-%m-%d")` on a `timestamp[us, tz=UTC]` gives the UTC day.
</details>
<details><summary>First run versus the next ones</summary>

`DeltaTable.is_deltatable(path)` tells you whether it already exists. The first time: `write_deltalake(path, data, partition_by=["date"])`.
</details>
<details><summary>Writing to S3 instead of a folder</summary>

It works with `storage_options` (endpoint, credentials, `AWS_ALLOW_HTTP`), but on S3 delta-rs needs conditional writes or a *locking provider* so two writers don't step on each other. A local folder is enough for this challenge; if you try S3, document which option you used and why.
</details>

## Common traps
- Partitioning by `ts` (one partition per instant: thousands of tiny folders).
- Computing `date` with the system's time zone.
- Turning `ts` into text “so it doesn't cause problems”: every consumer will parse it differently.
- `mode="overwrite"` on every run: it works, but it rewrites everything and you lose the useful version history.

## On a real job
Databricks writes Delta natively and can expose it as Iceberg (**UniForm**); Unity Catalog already manages Iceberg tables; Apache XTable translates metadata between formats.
Teams schedule `OPTIMIZE` (compaction) and `VACUUM` (deleting files no retained version references anymore).

→ Next: [`R3_polars_scale.md`](R3_polars_scale.md) · Back to the [challenges index](README.md)
