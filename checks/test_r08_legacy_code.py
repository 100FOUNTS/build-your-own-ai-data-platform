"""Challenge R8 (extends module 14) — Legacy code: the monthly report Marta uses is wrong and nobody knows why."""

from __future__ import annotations

import re
import shutil
import sys

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT, ok, py, sh, tail

pytestmark = pytest.mark.challenge

PKG = ROOT / "legacy" / "report"
ORIGINAL = ROOT / "checks" / "_legacy_original"
MONTHS = ["2026-08", "2026-09"]


def expected_report(month: str) -> pd.DataFrame:
    """The ticket's definition, computed independently."""
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    hours = (end - start).total_seconds() / 3600
    h = pd.concat([pd.read_parquet(p) for p in sorted((ROOT / "data/history").glob("date=*/readings.parquet"))], ignore_index=True)
    h = h[(h.quality == "GOOD") & (h.ts >= start) & (h.ts < end)].drop_duplicates("msg_id")
    w = h.pivot_table(index=["asset_id", "ts"], columns="sensor", values="value", aggfunc="first").reset_index()
    wo = pd.read_csv(ROOT / "platform/seeds/work_orders.csv", parse_dates=["opened_at", "closed_at"])
    wo = wo[wo.wo_type == "CORRECTIVE"]
    rows = []
    for a, d in w.groupby("asset_id"):
        f = wo[wo.asset_id == a]
        lo, hi = f.opened_at.clip(lower=start, upper=end), f.closed_at.clip(lower=start, upper=end)
        down = (hi - lo).dt.total_seconds().clip(lower=0).sum() / 3600
        n = int(((f.opened_at >= start) & (f.opened_at < end)).sum())
        run = d[d.RUN_STATE == 1]
        op = len(run) * 10 / 60
        rows.append({"month": month, "asset_id": a, "availability_pct": round(100 * (1 - down / hours), 2), "n_failures": n, "operating_hours": round(op, 2),
                     "mtbf_hours": round(op / n, 2) if n else np.nan, "vib_p95": round(float(run.VIB_MM_S.quantile(0.95)), 4)})
    return pd.DataFrame(rows)


def run_report(month: str, tmp_path):
    out = tmp_path / f"report_{month}.csv"
    r = py(["-m", "legacy.report", "--month", month, "--out", str(out)], timeout=300)
    ok(r.returncode == 0, f"`python -m legacy.report --month {month}` failed:\n" + tail(r))
    return pd.read_csv(out)


@pytest.mark.parametrize("month", MONTHS)
def test_r8_the_report_adds_up(month, tmp_path):
    got, exp = run_report(month, tmp_path), expected_report(month)
    ok(list(got.columns) == list(exp.columns), f"You changed the report format: columns {list(got.columns)}; Marta expects {list(exp.columns)}. Fix the computation, not the contract.")
    m = got.merge(exp, on=["month", "asset_id"], suffixes=("", "_e"), how="outer", indicator=True)
    ok((m._merge == "both").all() and len(got) == 12, f"The {month} report must have one row per pump (12).")
    for c in ["availability_pct", "n_failures", "operating_hours", "mtbf_hours", "vib_p95"]:
        a, b = m[c].astype(float), m[f"{c}_e"].astype(float)
        same = (a.isna() & b.isna()) | (np.abs(a - b) <= 0.011)
        ok(same.all(), f"[{month}] `{c}` doesn't add up for {int((~same).sum())} pumps, e.g. {m.loc[~same, ['asset_id', c, f'{c}_e']].head(2).values.tolist()} (yours, correct).\n"
           "Reread the ticket: the definition of each column, which time zone the month is cut in, what happens with a stoppage that crosses from one month to the next.")


def _run_tests_against(code_root, tmp_path):
    """Copies `legacy/` from code_root + YOUR tests to a separate folder (with the same data) and runs them."""
    work = tmp_path / "work"
    work.mkdir(parents=True)
    shutil.copytree(code_root / "legacy", work / "legacy", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    shutil.copytree(PKG / "tests", work / "legacy" / "report" / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    for d in ("data", "platform"):
        (work / d).symlink_to(ROOT / d)
    r = sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", str(work / "legacy" / "report" / "tests")],
           env={"PYTHONPATH": str(work)}, cwd=work, timeout=600)
    failed = re.search(r"(\d+) failed", r.stdout)
    passed = re.search(r"(\d+) passed", r.stdout)
    return r, int(failed.group(1)) if failed else 0, int(passed.group(1)) if passed else 0


def test_r8_regression_tests_that_catch_the_bugs(tmp_path):
    ok(PKG.joinpath("tests").exists(), "Missing the tests in legacy/report/tests/.")
    new = [p for p in (PKG / "tests").glob("test_*.py") if p.name != "test_report.py"]
    ok(new, "Write your regression tests in a new file in legacy/report/tests/ (e.g. test_regressions.py): one for each bug you found.")
    r, failed, passed = _run_tests_against(ROOT, tmp_path / "now")
    ok(r.returncode == 0, "With your fixed code, all the tests in legacy/report/tests must pass:\n" + tail(r))
    r, failed, passed = _run_tests_against(ORIGINAL, tmp_path / "orig")
    ok(failed >= 3, f"I ran your tests against the ORIGINAL code (with the bugs) and only {failed} fail. A regression test must fail with the bug and pass without it;\n"
       "if it doesn't fail against the original, it protects against nothing. Write at least one per bug (there are more than three).")


def test_r8_postmortem():
    f = ROOT / "docs" / "postmortem-report.md"
    ok(f.exists(), "Missing docs/postmortem-report.md (a blameless post-incident review).")
    t = f.read_text()
    for sec in ("Summary", "Impact", "Cause", "Fix", "Prevention"):
        ok(re.search(rf"(?im)^#+\s*(?:root\s+)?{sec}", t), f"The postmortem needs a “{sec}” section.")
    ok(len(re.findall(r"(?m)^\s*([-*]|\d+\.)\s+\S", t)) >= 6 and len(t) > 1500, "The postmortem is too short: explain each bug (what it did, why nobody saw it, how wrong the report was) and what changes so it doesn't come back.")
