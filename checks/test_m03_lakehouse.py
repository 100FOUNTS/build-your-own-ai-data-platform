"""Module 3 — Lakehouse: Spark (batch and streaming) → Iceberg on local S3 → DuckDB + dbt."""

from __future__ import annotations

import json
import os
import re

import duckdb
import numpy as np
import pandas as pd
import pytest

from conftest import ROOT, drop_namespace, iceberg_catalog, kafka_topics, need, ok, seeded_db, sh, spark_job, table_count, tail, unique

N_HISTORY = 90 * 144 * 12 * 5


@pytest.fixture(scope="module")
def ns_hist():
    ns = unique("bronze")
    yield ns
    drop_namespace(ns)


@pytest.fixture(scope="module")
def ns_stream():
    ns = unique("bronze")
    yield ns
    drop_namespace(ns)


# ------------------------------------------------------------------ Spark batch
def test_backfill_loads_the_history_and_is_idempotent(ns_hist):
    r = spark_job("backfill_history.py", {"LAKE_NS": ns_hist})
    ok(r.returncode == 0, "backfill_history.py failed:\n" + tail(r, 16))
    n1 = table_count(ns_hist)
    ok(n1 == N_HISTORY, f"The table {ns_hist}.readings has {n1:,} rows and the history has {N_HISTORY:,}.")
    t = iceberg_catalog().load_table(f"{ns_hist}.readings")
    cols = {f.name for f in t.schema().fields}
    missing = {"msg_id", "asset_id", "plant", "ts", "sensor", "value", "unit", "quality"} - cols
    ok(not missing, f"The table is missing columns: {sorted(missing)}.")
    transforms = [str(f.transform) for f in t.spec().fields]
    ok(any("day" in x.lower() for x in transforms), f"The table is not partitioned by day of `ts` (current partitioning: {transforms or 'none'}).\nWithout partitions, a one-day query reads the whole history; with them, Iceberg skips entire files.")
    r = spark_job("backfill_history.py", {"LAKE_NS": ns_hist})
    ok(r.returncode == 0, "The second backfill run failed:\n" + tail(r, 10))
    n2 = table_count(ns_hist)
    ok(n2 == n1, f"IDEMPOTENCY: running the backfill twice changed the count from {n1:,} to {n2:,}.")


# ------------------------------------------------------------------ Spark streaming
def _produce_clean(topic, ids, ts0_hours=0):
    from confluent_kafka import Producer

    p = Producer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
    for i in ids:
        m = {"msg_id": f"CHK|{i}", "asset_id": "PLT01-P101", "plant": "PLT01", "ts": f"2026-11-{1 + (i // 144) % 28:02d}T{(i % 144) // 6:02d}:{(i % 6) * 10:02d}:00Z",
             "sensor": "VIB_MM_S", "value": 1.0 + (i % 7) / 10, "unit": "mm/s", "quality": "GOOD"}
        p.produce(topic, key="PLT01-P101", value=json.dumps(m).encode())
    p.flush()


def test_streaming_kafka_to_iceberg_without_duplicates(ns_stream):
    topic = unique("chk.clean")
    kafka_topics(topic)
    env = {"LAKE_NS": ns_stream, "KAFKA_TOPIC_CLEAN": topic, "AVAILABLE_NOW": "1"}
    _produce_clean(topic, list(range(0, 3000)) + list(range(0, 120)))  # 3000 unique + 120 repeated
    r = spark_job("stream_to_bronze.py", env)
    ok(r.returncode == 0, "stream_to_bronze.py failed:\n" + tail(r, 16))
    n1 = table_count(ns_stream)
    ok(n1 == 3000, f"After reading 3,120 messages (3,000 unique) the table has {n1}. Repeated ones must not get in (MERGE on msg_id).")
    r = spark_job("stream_to_bronze.py", env)
    ok(r.returncode == 0, "The second streaming run failed:\n" + tail(r, 10))
    n2 = table_count(ns_stream)
    ok(n2 == n1, f"Rerunning the job with no new messages changed the count from {n1} to {n2}: use a checkpoint to remember which offsets you already processed.")
    _produce_clean(topic, list(range(3000, 4000)) + list(range(100, 200)))
    spark_job("stream_to_bronze.py", env)
    n3 = table_count(ns_stream)
    ok(n3 == 4000, f"With 1,000 new messages and 100 repeated old ones, 4,000 rows were expected and there are {n3}.")


# ------------------------------------------------------------------ DuckDB + dbt
from expected import FEATURES, expected_gold  # noqa: E402


@pytest.fixture(scope="module")
def dbt_run(ns_hist, tmp_path_factory):
    need("iceberg", "s3", "postgres")
    ok(table_count(ns_hist) > 0, "This part needs the backfill (first test) to have worked.")
    wh = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    ok((ROOT / "pipelines/dbt/dbt_project.yml").exists() and (ROOT / "pipelines/dbt/profiles.yml").exists(), "Missing pipelines/dbt/dbt_project.yml and/or profiles.yml (see guide/03_lakehouse.md).")
    with seeded_db() as url:
        r = sh(["uv", "run", "dbt", "build", "--project-dir", "pipelines/dbt", "--profiles-dir", "pipelines/dbt"],
               env={"LAKE_NS": ns_hist, "WAREHOUSE_PATH": str(wh), "DATABASE_URL": url, "PYTHONPATH": str(ROOT)}, timeout=900)
        yield r, wh


def test_dbt_build_passes_with_tests(dbt_run):
    r, _ = dbt_run
    ok(r.returncode == 0, "`dbt build` failed:\n" + tail(r, 18))
    m = re.search(r"PASS=(\d+)", r.stdout)
    ok(m and int(m.group(1)) >= 8, f"Only {m.group(1) if m else 0} items passed in dbt build (models + tests). Add tests (not_null, accepted_values, unique grain…): a layer without tests can't be trusted.")


def test_hourly_silver(dbt_run):
    _, wh = dbt_run
    con = duckdb.connect(str(wh), read_only=True)
    con.execute("set TimeZone='UTC'")
    try:
        n = con.execute("select count(*) from readings_hourly").fetchone()[0]
        ok(n == 90 * 24 * 12 * 5, f"readings_hourly has {n:,} rows; the grain is (asset_id, sensor, hour_start): 90 days × 24 h × 12 assets × 5 sensors = {90 * 24 * 12 * 5:,}.")
        cols = {r[0] for r in con.execute("describe readings_hourly").fetchall()}
        ok({"asset_id", "sensor", "hour_start", "value_mean", "value_min", "value_max", "value_std", "n_readings"} <= cols, f"readings_hourly needs the columns asset_id, sensor, hour_start, value_mean, value_min, value_max, value_std, n_readings (you have {sorted(cols)}).")
        v = con.execute("select value_mean from readings_hourly where asset_id='PLT01-P101' and sensor='VIB_MM_S' and hour_start = timestamp '2026-08-01 05:00:00'").fetchone()
    finally:
        con.close()
    h = pd.read_parquet(ROOT / "data/history/date=2026-08-01/readings.parquet")
    e = h[(h.asset_id == "PLT01-P101") & (h.sensor == "VIB_MM_S") & (h.ts.dt.hour == 5)].value.mean()
    ok(v and abs(v[0] - e) < 1e-6, f"The mean vibration of PLT01-P101 on 2026-08-01 at 05:00 UTC should be {e:.6f} and your silver gives {v[0] if v else None}. Are your hours in UTC?")


def test_gold_matches_the_independent_computation(dbt_run):
    _, wh = dbt_run
    con = duckdb.connect(str(wh), read_only=True)
    try:
        got = con.execute("select * from asset_daily_features").df()
    finally:
        con.close()
    exp = expected_gold()
    missing = [c for c in ["asset_id", "feature_date", *FEATURES, "label_failure_7d"] if c not in got.columns]
    ok(not missing, f"asset_daily_features is missing columns: {missing} (exact definitions in guide/03_lakehouse.md).")
    got["feature_date"] = pd.to_datetime(got["feature_date"])
    ok(len(got) == len(exp), f"asset_daily_features has {len(got)} rows and must have {len(exp)} (12 assets × 90 UTC days).\n"
       f"Dates: {got.feature_date.min().date()} → {got.feature_date.max().date()}; expected {exp.feature_date.min().date()} → {exp.feature_date.max().date()}.\n"
       "Hint: DuckDB reads TIMESTAMPTZ in YOUR SYSTEM's time zone, not in UTC. The “days” come out shifted.")
    m = got.merge(exp, on=["asset_id", "feature_date"], suffixes=("", "_e"), how="outer", indicator=True)
    ok((m["_merge"] == "both").all(), "The keys (asset_id, feature_date) don't match the expected ones.")
    for c in [*FEATURES, "label_failure_7d"]:
        a, b = m[c].astype(float), m[f"{c}_e"].astype(float)
        same = (a.isna() & b.isna()) | ((a - b).abs() < 1e-6)
        ok(same.all(), f"The column `{c}` differs from the reference computation in {int((~same).sum())} rows (e.g. {m.loc[~same, ['asset_id', 'feature_date', c, f'{c}_e']].head(2).values.tolist()}).\nCheck the exact definition in the guide: only readings while running? a backward-looking window? is the current day excluded from the baseline?")
