# Benchmark: the gold layer with pandas, Polars and DuckDB (5× the plant)

## Methodology
- Machine: Apple M4 Max, 36 GB of RAM, Python 3.12 (pandas 3, Polars 2.1, DuckDB 1.5), no other heavy processes.
- Data: 60 pumps × 90 days × 144 instants × 5 sensors = 3.9 M readings in 90 Parquet files (5 perturbed copies of PLT01, same as the check).
- What is measured: wall time from reading the Parquet files until the gold table is in memory (without writing it). Memory: *maximum resident set size* from `/usr/bin/time -l`, one process per engine.
- Repetitions: 3 runs in a row in the same process; the best one is reported (the first pays for the disk cache and library loading).
- Equality: pandas and Polars match on every column (tolerance 1e-6, checked by `make challenge-3`). The DuckDB query computes the same 13 features **without the label**.
- What is NOT measured: writing, concurrency, data that doesn't fit in memory, or interpreter start-up time.

## Results

| Engine | Best time (s) | Runs (s) | Process peak memory (GB) |
|---|---|---|---|
| pandas (the check's reference) | 1.03 | 1.29 · 1.03 · 1.03 | 2.45 |
| Polars lazy (`pipelines/polars_gold.py`) | 0.20 | 0.42 · 0.21 · 0.20 | 2.10 |
| DuckDB (equivalent SQL, no label) | 0.12 | 0.15 · 0.12 · 0.13 | 1.12 |

## EXPLAIN
The Polars plan for the read (`scan_parquet(...).filter(quality == "GOOD").select(...)`, with `.explain()`):

```
simple π 5/5 ["msg_id", "asset_id", "ts", ... 2 other columns]
  Parquet SCAN [.../date=2026-07-03/readings.parquet, ... 89 other sources]
  PROJECT 6/8 COLUMNS
  SELECTION: col("quality") == "GOOD"
```

Polars reads only 6 of the 8 columns (*projection pushdown*) and applies the quality filter inside the scan (*predicate pushdown*): the bad rows are never materialized.
In *eager* mode (`read_parquet`) it would read everything first. In DuckDB, `EXPLAIN ANALYZE` shows the same in the `READ_PARQUET` operator (projection and filter).

## Conclusion
At 3.9 M readings all three fit comfortably on one machine: Polars is 5× faster than pandas and DuckDB 8×. I would keep DuckDB/dbt for the gold layer (SQL, tests, a single writer)
and Polars for Python *pipelines*. pandas is still fine for one-off analysis, but its per-asset loop is the first thing that doesn't scale.
