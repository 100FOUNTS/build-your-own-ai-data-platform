"""Module 5 — Orchestration with Dagster: the whole pipeline as an asset graph, tested end to end."""

from __future__ import annotations

import ast
import importlib
import os
import sys

import duckdb
import psycopg
import pytest

from conftest import ROOT, drop_namespace, need, ok, seeded_db, sh, tail, unique

MODULE = "pipelines.dagster.definitions"


def load_defs():
    err, mod = None, None
    try:
        mod = importlib.import_module(MODULE)
    except Exception as exc:  # noqa: BLE001
        err = exc
    ok(err is None, f"Cannot import pipelines/dagster/definitions.py ({err!r}).\nIt must expose `defs = Definitions(assets=[...])` (guide/05_orchestration.md).")
    ok(hasattr(mod, "defs"), "definitions.py must define `defs = Definitions(...)`.")
    return mod.defs


def chain_depth(graph) -> int:
    keys = list(graph.get_all_asset_keys())
    parents = {k: list(graph.get(k).parent_keys) for k in keys}
    memo: dict = {}

    def depth(k) -> int:
        if k not in memo:
            memo[k] = 1 + max([depth(p) for p in parents[k] if p in parents] or [0])
        return memo[k]

    return max(depth(k) for k in keys)


def test_the_graph_chains_the_layers():
    g = load_defs().resolve_asset_graph()
    n = len(list(g.get_all_asset_keys()))
    ok(n >= 4, f"Your graph has {n} asset(s); at least 4 are expected (bronze → dbt → model → scores).")
    d = chain_depth(g)
    ok(d >= 4, f"The longest dependency chain has length {d} and must be ≥ 4. Without declared dependencies (deps=[...]) Dagster doesn't know in what order to run, or what to rerun when something changes.")
    descs = [g.get(k).description for k in g.get_all_asset_keys()]
    ok(all(d and len(d) > 15 for d in descs), "Every asset must have a description (what it produces and from where): the data catalog starts there.")


def test_has_checks_and_a_schedule():
    defs = load_defs()
    g = defs.resolve_asset_graph()
    ok(list(g.asset_check_keys), "Add at least one Dagster *asset check* (data quality: rows, duplicates, ranges…). A green pipeline with bad data is worse than a red one.")
    ok(list(defs.schedules or []), "Define a schedule (ScheduleDefinition) that runs the pipeline every day at 06:00 UTC.")


def test_end_to_end_run_in_isolation(tmp_path):
    """Materializes the WHOLE graph with Dagster, in a new namespace, warehouse and database: your real data is not touched."""
    need("iceberg", "s3", "postgres", "mlflow", "redpanda")
    ns = unique("bronze")
    wh = tmp_path / "warehouse.duckdb"
    home = tmp_path / "dagster_home"
    home.mkdir()
    try:
        with seeded_db() as url:
            env = {"DAGSTER_HOME": str(home), "LAKE_NS": ns, "WAREHOUSE_PATH": str(wh), "DATABASE_URL": url,
                   "MLFLOW_EXPERIMENT": unique("dagster"), "MODEL_NAME": unique("pump-risk-dg"), "PYTHONPATH": str(ROOT)}
            r = sh([sys.executable, "-m", "dagster", "asset", "materialize", "-m", MODULE, "--select", "*"], env=env, timeout=1500)
            ok(r.returncode == 0, "`dagster asset materialize --select '*'` failed. Last lines:\n" + tail(r, 14)
               + "\n\nIf it was because of a hard-coded path or name in your code: use the variables LAKE_NS, WAREHOUSE_PATH, DATABASE_URL…")
            ok(wh.exists(), "The run finished but didn't create the warehouse given by WAREHOUSE_PATH (does your dbt read it from the environment?).")
            con = duckdb.connect(str(wh), read_only=True)
            n = con.execute("select count(*) from asset_daily_features").fetchone()[0]
            con.close()
            ok(n == 1080, f"The gold produced by Dagster has {n} rows (expected 1,080).")
            with psycopg.connect(url) as c:
                scored = c.execute("select count(*) from risk_scores").fetchone()[0]
            ok(scored == 1080, f"risk_scores has {scored} rows after the run (expected 1,080).")
    finally:
        drop_namespace(ns)


@pytest.mark.bonus
def test_bonus_airflow_dag():
    files = list((ROOT / "deploy" / "airflow" / "dags").glob("*.py"))
    ok(files, "BONUS: express the same pipeline as an Airflow DAG in deploy/airflow/dags/*.py (what changes when you think in tasks instead of assets?).")
    src = files[0].read_text()
    tree = ast.parse(src)  # valid syntax
    ok("DAG" in src and ">>" in src, "The DAG must declare `DAG(...)` and dependencies between tasks with `>>`.")
    ok(sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "").endswith("Operator")) >= 3, "At least 3 tasks (operators) are expected.")
