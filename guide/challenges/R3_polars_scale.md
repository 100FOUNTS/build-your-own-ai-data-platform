# Challenge R3 — Scale 5×: the gold layer with Polars

⏱ 4–5 h · Extends **module 3** · Services: none · Check: `make challenge-3`
**Tools:** Polars, DuckDB, pandas. **What you practice:** expressions and *lazy* mode, window functions, measuring performance with method.

## What you will build
`pipelines/polars_gold.py` with `build_gold(history_dir, work_orders_csv) -> polars.DataFrame`, which computes **exactly** the module 3
`asset_daily_features` table from the history Parquet files and the work orders CSV, in Polars and in *lazy* mode.
And a `docs/benchmark.md` report that compares pandas, Polars and DuckDB **with method**.

## Why this challenge exists
*“Tomorrow they connect 4 more plants.”* Code that works with 12 pumps may not work with 60, and the only way to know is to **measure**.
Polars is the fastest-growing alternative to pandas today: it is columnar, multi-threaded and, in *lazy* mode, sees the whole query before running it
(it reads only the columns you use, filters while reading). But “rewriting it in Polars” is not enough: you have to get **the same result**
(windows, nulls and sample standard deviations are where it breaks) and prove the improvement with a method someone else can repeat.

## Concepts
- **Lazy vs. eager**: `pl.scan_parquet` builds a plan; `.collect()` runs it. `.explain()` shows the optimized plan (*projection/predicate pushdown*).
- **Expressions**: `pl.col("vib").filter(pl.col("run") == 1).mean()` inside a `group_by().agg(...)`; no per-row loops.
- **Windows per group**: `expr.over("asset_id")`, `rolling_mean(7, min_samples=1)`, `shift(1)`: the same exact definitions as module 3.
- **Numeric equality**: compare with a tolerance and treat `NaN`/`null` the same on both sides.
- **Honest benchmark**: warm-up, several repetitions, same hardware, memory as well as time, and **check that the result is identical**.

## The contract
- `build_gold(history_dir, work_orders_csv)` accepts paths (`str` or `Path`). `history_dir` has the structure of `data/history` (`date=YYYY-MM-DD/readings.parquet`).
- It returns an already materialized `polars.DataFrame` with `asset_id`, `feature_date` and the 13 features + `label_failure_7d`, with the **definitions from the module 3 table**
  (the label comes from the work orders CSV, not from Postgres).
- The computation is 100% Polars, reads with `scan_parquet`, and has no `apply`/`map_elements`/`iter_rows` and no detour through pandas.
- `docs/benchmark.md` with a **Methodology** section, a results table (≥ 3 rows: pandas, Polars, DuckDB; time and memory) and a commented **`EXPLAIN`**.

## Your task
1. Read the `asset_daily_features` section of [`../03_lakehouse.md`](../03_lakehouse.md) and write the Polars version **step by step**: read → pivot sensors → daily aggregates → windows → label.
2. Compare each step with your DuckDB gold (or with pandas) for **one** asset before moving on.
3. Go lazy end to end and look at `.explain()`: does the `quality` filter show up inside the scan? how many columns does it read?
4. Generate the big plant to measure (the check uses 5 plants copied from PLT01; you can do the same in a script of your own).
5. Measure pandas, Polars and DuckDB (your dbt model) with the same method and write `docs/benchmark.md`. End with a one-line conclusion: when would you switch tools?

## How you will know you are done
`make challenge-3` builds a plant of **60 pumps** (3.9 M readings) in a temporary folder, computes the reference gold with pandas and yours with Polars,
compares **every** column (tolerance 1e-6), checks that your version is lazy and 100% Polars, that it is **faster** than the reference (it prints both times)
and that your report has a methodology, a table and an `EXPLAIN`.

## Comprehension questions
1. Which optimizations did Polars apply in your plan? Which one would have been impossible in *eager* mode?
2. Why aren't `rolling_mean(7)` and a “7-day” window the same if a day is missing? Which one does the contract ask for?
3. Your first measurement came out 3× slower than the second. Why? Which one do you report?
4. In which case would DuckDB beat Polars? And in which is neither enough so you need Spark?
5. If the Polars gold and the dbt gold differ in the 7th decimal digit, is it a bug? How do you decide?

## Hints
<details><summary>Pivoting without `pivot`</summary>

Group by `(asset_id, ts)` and pull out each sensor with `pl.col("value").filter(pl.col("sensor") == "VIB_MM_S").first().alias("vib")`. It works in lazy mode, unlike `pivot`.
</details>
<details><summary>UTC day</summary>

`pl.col("ts").dt.convert_time_zone("UTC").dt.replace_time_zone(None).dt.truncate("1d")`.
</details>
<details><summary>The 28-day baseline</summary>

`pl.col("vib_mean") - pl.col("vib_mean").shift(1).rolling_mean(28, min_samples=1).over("asset_id")`, with the data **sorted** by asset and date first.
</details>
<details><summary>Measuring memory</summary>

`/usr/bin/time -l uv run python my_bench.py` (macOS) or `/usr/bin/time -v …` (Linux) shows the *maximum resident set size*. Measure each engine in a separate process.
</details>

## Common traps
- A `.collect()` halfway through “to see what happens”, left there: the rest is no longer lazy.
- Polars' and pandas' `std()` use `ddof=1` by default, but `numpy.std` uses `ddof=0`.
- Windows without sorting first: the result depends on the order in which the files are read.
- Measuring only once, or measuring each engine with different data.

## On a real job
Polars is used more and more instead of pandas in single-machine *pipelines*; DuckDB for local analytical SQL; Spark when the data doesn't fit on one machine.
A performance report with method is what decides a migration (and what you get asked for in a design review).

→ Next: [`R4_scd2.md`](R4_scd2.md) · Back to the [challenges index](README.md)
