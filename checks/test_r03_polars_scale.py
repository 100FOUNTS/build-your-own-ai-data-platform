"""Challenge R3 (extends module 3) — Scale 5×: the same gold layer with Polars on a plant five times bigger, measured with method."""

from __future__ import annotations

import importlib
import re
import time

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT, ok
from expected import FEATURES, expected_gold

pytestmark = pytest.mark.challenge

SCALE = 5


@pytest.fixture(scope="module")
def big_plant(tmp_path_factory):
    """60 pumps in 5 plants (perturbed copies of PLT01), same format as data/history."""
    base = tmp_path_factory.mktemp("big")
    hist = base / "history"
    for f in sorted((ROOT / "data/history").glob("date=*/readings.parquet")):
        df = pd.read_parquet(f)
        parts = []
        for k in range(SCALE):
            c = df.copy()
            plant = f"PLT0{k + 1}"
            c["asset_id"] = c["asset_id"].str.replace("PLT01", plant, regex=False)
            c["plant"] = plant
            c["msg_id"] = c["msg_id"].str.replace("PLT01", plant, regex=False)
            if k:
                c.loc[c["sensor"] != "RUN_STATE", "value"] = (c.loc[c["sensor"] != "RUN_STATE", "value"] * (1 + 0.013 * k)).round(4)
            parts.append(c)
        (hist / f.parent.name).mkdir(parents=True)
        pd.concat(parts, ignore_index=True).to_parquet(hist / f.parent.name / "readings.parquet", index=False)
    wo = pd.read_csv(ROOT / "platform/seeds/work_orders.csv")
    wos = pd.concat([wo.assign(asset_id=wo["asset_id"].str.replace("PLT01", f"PLT0{k + 1}", regex=False), wo_id=wo["wo_id"] + f"-{k}") for k in range(SCALE)])
    wo_csv = base / "work_orders.csv"
    wos.to_csv(wo_csv, index=False)
    t0 = time.perf_counter()
    exp = expected_gold(hist, wo_csv)
    return {"hist": hist, "wo": wo_csv, "exp": exp, "pandas_s": time.perf_counter() - t0}


def _module():
    f = ROOT / "pipelines/polars_gold.py"
    ok(f.exists(), "Missing pipelines/polars_gold.py with `build_gold(history_dir, work_orders_csv) -> polars.DataFrame` (see guide/challenges/R3_polars_scale.md).")
    return importlib.import_module("pipelines.polars_gold"), f.read_text()


@pytest.fixture(scope="module")
def r3_run(big_plant):
    import polars as pl

    mod, src = _module()
    times, out = [], None
    for _ in range(2):
        t0 = time.perf_counter()
        out = mod.build_gold(big_plant["hist"], big_plant["wo"])
        times.append(time.perf_counter() - t0)
    ok(isinstance(out, pl.DataFrame), f"build_gold must return an already materialized polars.DataFrame (it returned {type(out).__name__}). If you work lazily, finish with .collect().")
    return {"df": out, "src": src, "polars_s": min(times)}


def test_r3_is_really_lazy(r3_run):
    src = r3_run["src"]
    ok("scan_parquet" in src, "Read the history with `pl.scan_parquet` (lazy mode): that way Polars sees the WHOLE query before running it and can push filters and columns down to the read.\n"
       "With `read_parquet` you load everything into memory first, which is exactly what doesn't scale.")
    ok(not re.search(r"\bimport pandas\b|\bfrom pandas\b|\.to_pandas\(", src), "The computation must be 100% Polars: no going through pandas halfway (then you aren't measuring Polars).")
    ok(not re.search(r"\.apply\(|map_elements\(|iter_rows\(", src), "There is `apply`/`map_elements`/`iter_rows`: that runs Python row by row and cancels Polars' advantage. Write it with expressions (`over`, `rolling`, `shift`, `join_asof`…).")


def test_r3_same_result_as_the_reference(big_plant, r3_run):
    exp = big_plant["exp"]
    got = r3_run["df"].to_pandas()
    missing = [c for c in ["asset_id", "feature_date", *FEATURES, "label_failure_7d"] if c not in got.columns]
    ok(not missing, f"Your Polars gold is missing columns: {missing} (same definitions as guide/03_lakehouse.md).")
    got["feature_date"] = pd.to_datetime(got["feature_date"])
    ok(len(got) == len(exp), f"Your gold has {len(got):,} rows and must have {len(exp):,} (60 pumps × 90 days).")
    m = got.merge(exp, on=["asset_id", "feature_date"], suffixes=("", "_e"), how="outer", indicator=True)
    ok((m["_merge"] == "both").all(), f"The keys (asset_id, feature_date) don't match: {m.loc[m._merge != 'both', ['asset_id', 'feature_date', '_merge']].head(3).values.tolist()}.")
    for c in [*FEATURES, "label_failure_7d"]:
        a, b = m[c].astype(float), m[f"{c}_e"].astype(float)
        same = (a.isna() & b.isna()) | (np.abs(a - b) < 1e-6)
        ok(same.all(), f"`{c}` differs from the reference in {int((~same).sum())} rows, e.g. {m.loc[~same, ['asset_id', 'feature_date', c, f'{c}_e']].head(2).values.tolist()}.\n"
           "Watch out for: windows by rows (not by dates), sample standard deviation (ddof=1), nulls in `shift`, and UTC days.")


def test_r3_faster_than_pandas(big_plant, r3_run):
    p, q = big_plant["pandas_s"], r3_run["polars_s"]
    print(f"\n  pandas (reference): {p:.2f} s · your Polars: {q:.2f} s · {p / q:.1f}× ")
    ok(q < p, f"Your Polars version takes {q:.2f} s and the pandas one {p:.2f} s on the same 3.9 M readings. Polars should win comfortably:\n"
       "do you read only the columns you use? do you filter before pivoting? is there an intermediate `collect()`?")


def test_r3_benchmark_documented():
    f = ROOT / "docs/benchmark.md"
    ok(f.exists(), "Missing docs/benchmark.md: the performance report (methodology, results table and conclusion). A number without a method convinces nobody.")
    t = f.read_text()
    ok(re.search(r"(?im)^#+\s*methodolog", t), "docs/benchmark.md needs a “Methodology” section (machine, data size, number of repetitions, what was measured and what wasn't).")
    rows = [ln for ln in t.splitlines() if ln.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", ln)]
    ok(len(rows) >= 4, "docs/benchmark.md must include a results table with at least 3 data rows (e.g. pandas, Polars and DuckDB) and time and memory columns.")
    ok(re.search(r"(?i)explain", t), "Include an `EXPLAIN` (from DuckDB, or the Polars plan with `.explain()`) and explain which optimization the engine applied.")
