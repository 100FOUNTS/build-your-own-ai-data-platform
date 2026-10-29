"""Module 1 — Databases: PostgreSQL (schema, constraints, indexes, views) and Redis/Valkey (cache and rate limiting)."""

from __future__ import annotations

import concurrent.futures as cf
import os
import time

import psycopg
import pandas as pd
import pytest

from conftest import ROOT, need, ok, py, tail, unique


@pytest.fixture()
def tempdb():
    """A NEW, empty database: your migrations are tested from scratch."""
    need("postgres")
    base = os.environ["DATABASE_URL"]
    admin = psycopg.connect(base.rsplit("/", 1)[0] + "/postgres", autocommit=True)
    name = unique("chk")
    admin.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + f"/{name}"
    yield url
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


def con(url):
    return psycopg.connect(url, autocommit=True)


def migrate(url):
    f = ROOT / "scripts" / "migrate.py"
    ok(f.exists(), "Missing scripts/migrate.py (the migration runner). See guide/01_databases.md.")
    r = py(["scripts/migrate.py"], env={"DATABASE_URL": url})
    ok(r.returncode == 0, "scripts/migrate.py failed:\n" + tail(r))


def test_migrations_from_scratch_and_idempotent(tempdb):
    ok((ROOT / "db" / "migrations").glob("*.sql") and list((ROOT / "db" / "migrations").glob("*.sql")), "There are no .sql files in db/migrations/ (numbered: 001_..., 002_...).")
    migrate(tempdb)
    c = con(tempdb)
    tables = {r[0] for r in c.execute("select table_name from information_schema.tables where table_schema='public'").fetchall()}
    for t in ("assets", "work_orders", "risk_scores", "alerts"):
        ok(t in tables, f"After migrating, the table `{t}` does not exist.")
    n1 = c.execute("select count(*) from schema_migrations").fetchone()[0] if "schema_migrations" in tables else None
    ok(n1 is not None, "Keep a record of what was applied in a table (e.g. `schema_migrations`): that is how you know which migrations already ran.")
    migrate(tempdb)  # second run: must not fail or repeat anything
    n2 = c.execute("select count(*) from schema_migrations").fetchone()[0]
    ok(n1 == n2, f"Applying the migrations twice changed the record ({n1} → {n2}). The runner must be idempotent.")


def test_seeds_load_and_are_idempotent(tempdb):
    migrate(tempdb)
    f = ROOT / "scripts" / "seed.py"
    ok(f.exists(), "Missing scripts/seed.py (loads platform/seeds/*.csv into your tables).")
    counts = []
    for _ in range(2):
        r = py(["scripts/seed.py"], env={"DATABASE_URL": tempdb})
        ok(r.returncode == 0, "scripts/seed.py failed:\n" + tail(r))
        c = con(tempdb)
        counts.append((c.execute("select count(*) from assets").fetchone()[0], c.execute("select count(*) from work_orders").fetchone()[0]))
    ok(counts[0] == (12, len(pd.read_csv(ROOT / "platform/seeds/work_orders.csv"))), f"After loading the seeds there are (assets, work_orders) = {counts[0]}; expected (12, 76).")
    ok(counts[0] == counts[1], f"Loading the seeds twice duplicated data: {counts[0]} → {counts[1]}.")


def expect_error(c, sql, why, params=None):
    try:
        c.execute(sql, params)
    except psycopg.Error:
        return
    ok(False, f"The database ACCEPTED something it shouldn't: {why}.\nImportant business rules are enforced in the database (constraints), not only in the code.")


def test_integrity_constraints(tempdb):
    migrate(tempdb)
    py(["scripts/seed.py"], env={"DATABASE_URL": tempdb})
    c = con(tempdb)
    A = "PLT01-P101"
    ins = "insert into work_orders (wo_id, asset_id, wo_type, opened_at, closed_at, failure_mode, cost_usd, downtime_hours) values (%s,%s,%s,%s,%s,%s,%s,%s)"
    expect_error(c, "insert into assets (asset_id, plant, model, criticality, install_date) values ('PLT01-P101','PLT01','CP-200','A','2020-01-01')", "a duplicate asset_id")
    expect_error(c, "insert into assets (asset_id, plant, model, criticality, install_date) values ('X1','PLT01','CP-200','Z','2020-01-01')", "a criticality outside A/B/C")
    expect_error(c, ins, "a work order for an asset that doesn't exist", ("T1", "NO-SUCH-ASSET", "PREVENTIVE", "2026-01-01", None, None, 1, 0))
    expect_error(c, ins, "an invalid work order type", ("T2", A, "FOO", "2026-01-01", None, None, 1, 0))
    expect_error(c, ins, "a negative cost", ("T3", A, "PREVENTIVE", "2026-01-01", None, None, -5, 0))
    expect_error(c, ins, "a work order closed BEFORE it was opened", ("T4", A, "PREVENTIVE", "2026-01-02", "2026-01-01", None, 1, 0))
    expect_error(c, ins, "a CORRECTIVE work order without a failure mode", ("T5", A, "CORRECTIVE", "2026-01-01", None, None, 1, 0))
    c.execute(ins, ("OK1", A, "CORRECTIVE", "2026-10-02", "2026-10-03", "seal", 10, 5))  # the valid case does get in
    expect_error(c, "insert into risk_scores (asset_id, feature_date, risk_score, model_version) values (%s,'2026-10-01',1.5,'v1')", "a risk outside [0,1]", (A,))
    c.execute("insert into risk_scores (asset_id, feature_date, risk_score, model_version) values (%s,'2026-10-01',0.4,'v1')", (A,))
    expect_error(c, "insert into risk_scores (asset_id, feature_date, risk_score, model_version) values (%s,'2026-10-01',0.5,'v2')", "two scores for the same asset and day", (A,))
    expect_error(c, "insert into alerts (asset_id, severity, risk_score) values (%s,'EXTREME',0.9)", "an invalid severity", (A,))
    c.execute("insert into alerts (asset_id, severity, risk_score, payload) values (%s,'HIGH',0.9,'{\"why\":\"test\"}')", (A,))
    st = c.execute("select status from alerts order by alert_id desc limit 1").fetchone()[0]
    ok(st == "OPEN", f"A new alert should get status 'OPEN' by default and it got `{st}`.")


def test_index_for_the_typical_query(tempdb):
    migrate(tempdb)
    py(["scripts/seed.py"], env={"DATABASE_URL": tempdb})
    c = con(tempdb)
    c.execute("""insert into work_orders (wo_id, asset_id, wo_type, opened_at, closed_at, cost_usd, downtime_hours)
                 select 'SYN-' || g, (select asset_id from assets order by asset_id offset (g % 12) limit 1), 'INSPECTION',
                        timestamptz '2025-01-01' + (g || ' minutes')::interval, null, 1, 0
                 from generate_series(1, 120000) g""")
    c.execute("analyze work_orders")
    plan = c.execute("explain (format json) select * from work_orders where asset_id = 'PLT01-P101' and opened_at >= '2025-02-01' and opened_at < '2025-02-02'").fetchone()[0]
    text = str(plan)
    ok("Index" in text and "Seq Scan" not in text,
       "With 120,000 work orders, the query “work orders of one asset on one day” does a full table scan (Seq Scan).\n"
       "Design an index that serves exactly that query (which columns, and in which order?). Use `EXPLAIN` to check it.")


def test_asset_summary_view(tempdb):
    migrate(tempdb)
    py(["scripts/seed.py"], env={"DATABASE_URL": tempdb})
    c = con(tempdb)
    try:
        rows = c.execute("select asset_id, n_failures, total_corrective_cost, last_failure_at from v_asset_summary order by asset_id").fetchall()
    except psycopg.Error as exc:
        ok(False, f"Cannot read the view v_asset_summary(asset_id, n_failures, total_corrective_cost, last_failure_at): {exc}")
    wo = pd.read_csv(ROOT / "platform/seeds/work_orders.csv", parse_dates=["opened_at"])
    assets = pd.read_csv(ROOT / "platform/seeds/assets.csv")
    cor = wo[wo.wo_type == "CORRECTIVE"].groupby("asset_id").agg(n=("wo_id", "count"), cost=("cost_usd", "sum"), last=("opened_at", "max"))
    ok(len(rows) == len(assets), f"The view returns {len(rows)} rows and must have ONE per asset ({len(assets)}), including the ones that never failed (LEFT JOIN).")
    for a, n, cost, last in rows:
        e = cor.loc[a] if a in cor.index else None
        en, ec = (int(e.n), float(e.cost)) if e is not None else (0, 0.0)
        ok(n == en and abs(float(cost) - ec) < 0.01, f"{a}: the view says n_failures={n}, cost={cost}; the right values are {en}, {ec:.2f}. Do you count only CORRECTIVE work orders? Assets without failures must give 0, not NULL.")
        ok((last is None) == (e is None), f"{a}: last_failure_at must be NULL only if the asset never failed.")


# ------------------------------------------------------------------ Redis / Valkey
@pytest.fixture()
def cache_mod():
    need("valkey")
    f = ROOT / "services" / "common" / "cache.py"
    ok(f.exists(), "Missing services/common/cache.py (cache and rate limiting). See the module 1 guide.")
    import importlib

    import redis

    r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    mod = importlib.import_module("services.common.cache")
    importlib.reload(mod)
    for k in r.scan_iter("chk:*"):
        r.delete(k)
    for k in r.scan_iter("rl:chk*"):
        r.delete(k)
    return mod, r


def test_cache_get_or_set(cache_mod):
    mod, r = cache_mod
    calls = []

    def compute():
        calls.append(1)
        return {"risk": 0.42, "n": len(calls)}

    key = unique("chk:risk")
    a = mod.cache_get_or_set(key, 30, compute)
    b = mod.cache_get_or_set(key, 30, compute)
    ok(a == b and len(calls) == 1, f"The second call should come from the cache (compute ran {len(calls)} times).")
    ttl = r.ttl(key)
    ok(0 < ttl <= 30, f"The cached key must expire (TTL between 1 and 30 s) and it has TTL={ttl}. Without a TTL the cache serves stale data forever.")
    mod.invalidate(key)
    mod.cache_get_or_set(key, 30, compute)
    ok(len(calls) == 2, "After invalidate(key) the value must be recomputed.")


def test_rate_limit(cache_mod):
    mod, r = cache_mod
    s = unique("chk")
    res = [mod.allow_request(s, 3, 2) for _ in range(5)]
    ok(res == [True, True, True, False, False], f"allow_request(subject, limit=3, window=2) returned {res}; expected [True, True, True, False, False].")
    ok(r.ttl(f"rl:{s}") > 0 or r.ttl(f"rl:{s}") == -2, "The rate-limit counter must expire.")
    time.sleep(2.3)
    ok(mod.allow_request(s, 3, 2), "Once the window (2 s) has passed, the subject should be able to make requests again.")
    other = unique("chk")
    ok(mod.allow_request(other, 3, 2), "Each subject has its own counter.")


def test_rate_limit_under_concurrency(cache_mod):
    mod, _ = cache_mod
    s = unique("chk")
    with cf.ThreadPoolExecutor(32) as ex:
        results = list(ex.map(lambda _: mod.allow_request(s, 10, 30), range(60)))
    ok(sum(results) == 10, f"With 60 simultaneous requests and a limit of 10, {sum(results)} were allowed. Reading the counter and then writing it is not safe with several clients at once: use atomic Redis operations (INCR, SET NX EX…).")
