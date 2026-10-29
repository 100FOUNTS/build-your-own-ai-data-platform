"""Challenge R2 (extends module 3) — Delta and Iceberg side by side: publish bronze to Delta Lake for a team that works with Databricks."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pytest

from conftest import ROOT, drop_namespace, iceberg_catalog, ok, py, tail, unique

pytestmark = pytest.mark.challenge

DAYS = ["2026-08-01", "2026-08-02", "2026-08-03"]
EXTRA_DAY = "2026-08-04"


def _day(d: str) -> pa.Table:
    df = pd.read_parquet(ROOT / f"data/history/date={d}/readings.parquet")
    return pa.Table.from_pandas(df, preserve_index=False)


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    """A small Iceberg table (3 days) created right here: this challenge doesn't depend on your Spark jobs."""
    from pyiceberg.transforms import DayTransform

    ok((ROOT / "pipelines/delta/export.py").exists(), "Missing pipelines/delta/export.py (`python -m pipelines.delta.export`, see guide/challenges/R2_delta_iceberg.md).")
    cat = iceberg_catalog()
    ns = unique("bronze")
    cat.create_namespace(ns)
    data = pa.concat_tables([_day(d) for d in DAYS])
    t = cat.create_table(f"{ns}.readings", schema=data.schema)
    with t.update_spec() as u:
        u.add_field("ts", DayTransform(), "ts_day")
    t = cat.load_table(f"{ns}.readings")
    t.append(data)
    out = tmp_path_factory.mktemp("delta") / "readings"
    env = {"LAKE_NS": ns, "DELTA_PATH": str(out)}
    yield {"ns": ns, "path": out, "env": env, "n": data.num_rows, "table": t}
    drop_namespace(ns)


def _export(lake):
    r = py(["-m", "pipelines.delta.export"], env=lake["env"], timeout=600)
    ok(r.returncode == 0, "`python -m pipelines.delta.export` failed:\n" + tail(r, 14))
    return r


def _delta(lake):
    from deltalake import DeltaTable

    try:
        return DeltaTable(str(lake["path"]))
    except Exception as exc:  # noqa: BLE001
        ok(False, f"There is no valid Delta table at DELTA_PATH={lake['path']} ({exc}). Do you write with `deltalake` (delta-rs) to the path the variable gives?")


@pytest.fixture(scope="module")
def first_export(lake):
    _export(lake)
    dt = _delta(lake)
    return {"version": dt.version(), "count": dt.to_pyarrow_table(columns=["msg_id"]).num_rows}


def test_r2_exports_everything_with_the_right_schema(lake, first_export):
    ok(first_export["count"] == lake["n"], f"The Delta table has {first_export['count']:,} rows and the Iceberg one {lake['n']:,}.")
    dt = _delta(lake)
    cols = {f.name: str(f.type) for f in dt.schema().fields}
    missing = {"msg_id", "asset_id", "plant", "ts", "sensor", "value", "unit", "quality"} - set(cols)
    ok(not missing, f"The Delta table is missing columns: {sorted(missing)}.")
    ok("timestamp" in cols["ts"].lower(), f"`ts` must still be a timestamp in Delta (it is {cols['ts']}). If you store it as text, every consumer parses it its own way.")


def test_r2_partitioned_by_day(lake, first_export):
    dt = _delta(lake)
    parts = dt.metadata().partition_columns
    ok(parts == ["date"], f"The Delta table must be partitioned by a `date` column (UTC day of `ts`); it has {parts or 'no partitions'}.\n"
       "Delta has no hidden partitioning like Iceberg: the partition column is explicit. That difference is exactly what this challenge wants you to notice.")
    df = dt.to_pandas(columns=["ts", "date"])
    utc = pd.to_datetime(df["ts"], utc=True).dt.strftime("%Y-%m-%d")
    ok((utc == df["date"].astype(str).str[:10]).all(), "The `date` column doesn't match the UTC day of `ts` in every row (time zone?).")


def test_r2_reexporting_is_idempotent(lake, first_export):
    _export(lake)
    dt = _delta(lake)
    n = dt.to_pyarrow_table(columns=["msg_id"]).num_rows
    ok(n == first_export["count"], f"Exporting twice changed the Delta table from {first_export['count']:,} to {n:,} rows. Use a MERGE on msg_id (or read only what is new), never a blind append.")


def test_r2_only_new_data_and_time_travel(lake, first_export):
    from deltalake import DeltaTable

    extra = _day(EXTRA_DAY)
    lake["table"].append(extra)
    _export(lake)
    dt = _delta(lake)
    n = dt.to_pyarrow_table(columns=["msg_id"]).num_rows
    ok(n == lake["n"] + extra.num_rows, f"After adding a day ({extra.num_rows:,} rows) to Iceberg, Delta should have {lake['n'] + extra.num_rows:,} rows and it has {n:,}.")
    ok(dt.version() > first_export["version"], "The Delta table version didn't move forward after writing new data.")
    old = DeltaTable(str(lake["path"]), version=first_export["version"])
    n_old = old.to_pyarrow_table(columns=["msg_id"]).num_rows
    ok(n_old == first_export["count"], f"Time travel to version {first_export['version']} returns {n_old:,} rows and should return {first_export['count']:,}.")


def test_r2_another_engine_reads_the_same(lake, first_export):
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("install delta; load delta")
        n = con.execute(f"select count(*) from delta_scan('{lake['path']}')").fetchone()[0]
        per_day = con.execute(f"select count(distinct date) from delta_scan('{lake['path']}')").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        ok(False, f"DuckDB could not read your Delta table ({exc}). An open format only counts if ANOTHER engine reads it.")
    finally:
        con.close()
    df = _delta(lake).to_pandas(columns=["date"])
    ok(n == len(df), f"DuckDB sees {n:,} rows in the Delta table and delta-rs sees {len(df):,}.")
    ok(per_day == df["date"].nunique(), f"DuckDB sees {per_day} distinct days and delta-rs {df['date'].nunique()}.")
