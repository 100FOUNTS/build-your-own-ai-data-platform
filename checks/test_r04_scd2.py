"""Challenge R4 (extends module 3) — History of the asset master data (SCD type 2) with a dbt snapshot."""

from __future__ import annotations

import re

import duckdb
import psycopg
import pytest

from conftest import ROOT, need, ok, seeded_db, sh, tail

pytestmark = pytest.mark.challenge

DBT = ["uv", "run", "dbt"]
ARGS = ["--project-dir", "pipelines/dbt", "--profiles-dir", "pipelines/dbt"]


def _dbt(cmd: list[str], env: dict):
    return sh([*DBT, *cmd, *ARGS], env={**env, "PYTHONPATH": str(ROOT)}, timeout=600)


def _snapshot(env):
    r = _dbt(["snapshot", "--select", "assets_snapshot"], env)
    ok(r.returncode == 0, "`dbt snapshot --select assets_snapshot` failed:\n" + tail(r, 14))


def _rows(wh, sql):
    con = duckdb.connect(str(wh), read_only=True)
    try:
        where = con.execute("select table_schema from information_schema.tables where table_name = 'assets_snapshot'").fetchall()
        ok(where, "Cannot find the table `assets_snapshot` in the DuckDB warehouse after the snapshot (is the snapshot called that?).")
        return con.execute(sql.replace("{snap}", f'"{where[0][0]}".assets_snapshot')).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def scd(tmp_path_factory):
    need("postgres", "iceberg", "s3")
    ok((ROOT / "pipelines/dbt/dbt_project.yml").exists(), "This challenge extends your dbt project from module 3 (pipelines/dbt).")
    files = [*(ROOT / "pipelines/dbt").rglob("*.sql"), *(ROOT / "pipelines/dbt").rglob("*.yml")]
    ok(any("assets_snapshot" in p.read_text() for p in files), "Cannot find the snapshot `assets_snapshot` in pipelines/dbt (see guide/challenges/R4_scd2.md).")
    wh = tmp_path_factory.mktemp("scd") / "warehouse.duckdb"
    with seeded_db() as url:
        env = {"WAREHOUSE_PATH": str(wh), "DATABASE_URL": url}
        _snapshot(env)
        first = _rows(wh, "select count(*) from {snap}")[0][0]
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("update assets set criticality = 'A' where asset_id = 'PLT01-P103'")
            c.execute("update assets set rated_kw = rated_kw + 7.5 where asset_id = 'PLT01-P105'")
            c.execute("update assets set install_date = install_date where asset_id = 'PLT01-P106'")  # touch without changing anything
        _snapshot(env)
        second = _rows(wh, "select count(*) from {snap}")[0][0]
        _snapshot(env)
        third = _rows(wh, "select count(*) from {snap}")[0][0]
        r = _dbt(["build", "--select", "dim_assets_current"], env)
        yield {"wh": wh, "env": env, "first": first, "second": second, "third": third, "build": r}


def test_r4_first_snapshot_one_row_per_asset(scd):
    ok(scd["first"] == 12, f"The first snapshot has {scd['first']} rows; with 12 pumps it must have 12 (one version per asset).")


def test_r4_a_change_creates_a_new_version(scd):
    ok(scd["second"] == 14, f"After changing the criticality of P103 and the power of P105 the snapshot has {scd['second']} rows; 14 were expected (12 + one new version per change).\n"
       "If it has 12: you don't detect changes (which columns do you watch?). If it has more than 14: you are versioning things that didn't change (P106 was “touched” without changing anything).")
    rows = _rows(scd["wh"], "select criticality, dbt_valid_from, dbt_valid_to from {snap} where asset_id = 'PLT01-P103' order by dbt_valid_from")
    ok(len(rows) == 2, f"PLT01-P103 has {len(rows)} versions in the snapshot; 2 were expected (before and after the change).")
    (c_old, f_old, t_old), (c_new, f_new, t_new) = rows
    ok(c_old == "C" and c_new == "A", f"The P103 versions have criticality {c_old} → {c_new}; C → A was expected.")
    ok(t_old is not None and t_old == f_new, "The old P103 version must close (`dbt_valid_to`) exactly when the new one starts (`dbt_valid_from`): no gaps or overlaps.")
    ok(t_new is None, "The current P103 version must have a null `dbt_valid_to` (open).")


def test_r4_repeating_without_changes_creates_no_versions(scd):
    ok(scd["third"] == scd["second"], f"A snapshot with no changes at the source went from {scd['second']} to {scd['third']} rows: it must be idempotent.")


def test_r4_current_dimension(scd):
    r = scd["build"]
    ok(r.returncode == 0, "`dbt build --select dim_assets_current` failed (the model or its tests):\n" + tail(r, 14))
    con = duckdb.connect(str(scd["wh"]), read_only=True)
    try:
        n, crit = con.execute("select count(*), max(criticality) filter (where asset_id = 'PLT01-P103') from dim_assets_current").fetchone()
    finally:
        con.close()
    ok(n == 12 and crit == "A", f"dim_assets_current must have one row per asset with its CURRENT values (it has {n} rows; P103 with criticality {crit}, A was expected).")
    m = re.search(r"PASS=(\d+)", r.stdout)
    ok(m and int(m.group(1)) >= 2, "Add at least one dbt test to dim_assets_current (e.g. `unique` on asset_id) so a broken snapshot doesn't pass silently.")
