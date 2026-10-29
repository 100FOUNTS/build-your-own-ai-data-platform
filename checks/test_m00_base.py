"""Module 0 — Engineering foundations: Git, Python with uv/ruff/pytest, shell and SQL."""

from __future__ import annotations

import re
import stat

import duckdb
import pandas as pd
import pytest

from conftest import ROOT, ok, sh, tail


def git(*args: str):
    return sh(["git", *args])


# ------------------------------------------------------------------ Git
def test_git_professional_history():
    ok((ROOT / ".git").exists(), "This folder is not a Git repository. Run `git init -b main` and start committing.")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    ok(branch in {"main", "master"} or git("rev-parse", "--verify", "main").returncode == 0,
       f"Cannot find the `main` branch (you are on `{branch}`). Integrated work lives on main.")
    log = git("log", "main", "--pretty=%H|%P|%s").stdout.strip().splitlines()
    ok(len(log) >= 6, f"There are {len(log)} commits on main and at least 6 are expected. Small, frequent commits = a history that can be reviewed.")
    merges = [ln for ln in log if len(ln.split("|")[1].split()) > 1]
    ok(merges, "There is no *merge* commit on main. Build a feature on a branch (`git switch -c feat/…`) and join it with `git merge --no-ff`.")
    rx = re.compile(r"^(feat|fix|docs|chore|test|refactor|ci|build|perf|style)(\([\w\-/.]+\))?!?: .{5,}")
    bad = [ln.split("|", 2)[2] for ln in log if len(ln.split("|")[1].split()) <= 1 and not rx.match(ln.split("|", 2)[2])]
    ok(not bad, f"{len(bad)} commit(s) don't follow Conventional Commits (feat:, fix:, docs:, chore:…). Example to fix: “{bad[0] if bad else ''}”.\n"
       "Why it matters: standard messages make automatic changelogs and versioning possible.")


def test_gitignore_protects_what_matters():
    f = ROOT / ".gitignore"
    ok(f.exists(), "Missing .gitignore.")
    text = f.read_text()
    for must in (".venv", "node_modules", "data/", ".env"):
        ok(must in text, f"Your .gitignore doesn't include `{must}`: you would end up pushing dependencies, heavy data or secrets to the repository.")


def test_pre_commit_hook_with_ruff():
    out = git("config", "--get", "core.hooksPath")
    ok(out.returncode == 0 and out.stdout.strip(), "Set up a versioned hooks folder: `git config core.hooksPath .githooks`.")
    hook = ROOT / out.stdout.strip() / "pre-commit"
    ok(hook.exists(), f"Missing the hook {hook.relative_to(ROOT)}.")
    ok(hook.stat().st_mode & stat.S_IXUSR, "The pre-commit hook is not executable (`chmod +x`). Git silently ignores it if it isn't.")
    ok("ruff" in hook.read_text(), "The pre-commit hook must run ruff (format and lint) before allowing the commit.")


# ------------------------------------------------------------------ Python
def test_ruff_clean():
    r = sh(["uv", "run", "ruff", "check", "."])
    ok(r.returncode == 0, "`ruff check .` finds problems:\n" + tail(r))
    r = sh(["uv", "run", "ruff", "format", "--check", "."])
    ok(r.returncode == 0, "`ruff format --check .` says some files are not formatted: run `uv run ruff format .`\n" + tail(r, 6))


def test_own_tests():
    t = ROOT / "tests"
    files = list(t.glob("test_*.py")) if t.exists() else []
    ok(files, "There are no tests in tests/. Create tests/test_smoke.py with at least 3 tests that check real things in the project.")
    r = sh(["uv", "run", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-o", "addopts="], env={"PYTHONPATH": str(ROOT)})
    ok(r.returncode == 0, "Your tests in tests/ fail:\n" + tail(r))
    m = re.search(r"(\d+) passed", r.stdout)
    ok(m and int(m.group(1)) >= 3, "At least 3 passing tests are expected in tests/ (and they must test something, not `assert True`).")


# ------------------------------------------------------------------ Linux / shell
def test_doctor_sh_works_and_detects_problems():
    f = ROOT / "scripts" / "doctor.sh"
    ok(f.exists(), "Missing scripts/doctor.sh.")
    text = f.read_text()
    ok(text.startswith("#!"), "doctor.sh must start with a shebang (#!/usr/bin/env bash).")
    ok("set -e" in text and "pipefail" in text, "Use `set -euo pipefail` in your scripts: without it a failed command is silently ignored.")
    good = sh(["/bin/bash", str(f)])
    ok(good.returncode == 0, "doctor.sh should exit with code 0 on a complete machine (which tool can't it find?):\n" + tail(good))
    for tool in ("git", "uv", "docker", "node", "ollama"):
        ok(tool in good.stdout, f"doctor.sh doesn't mention `{tool}` in its output.")
    bad = sh(["/bin/bash", str(f)], env={"PATH": "/nonexistent"})
    ok(bad.returncode != 0, "With an empty PATH doctor.sh should FAIL (code ≠ 0). A diagnostic script that always says “all good” is useless.")


# ------------------------------------------------------------------ SQL
@pytest.fixture(scope="module")
def tables():
    con = duckdb.connect()
    con.execute("set TimeZone='UTC'")  # TIMESTAMPTZ values are read in UTC (watch out for this in your queries)
    con.execute(f"create table assets as select * from read_csv_auto('{ROOT}/platform/seeds/assets.csv')")
    con.execute(f"create table work_orders as select * from read_csv_auto('{ROOT}/platform/seeds/work_orders.csv')")
    wo = pd.read_csv(ROOT / "platform/seeds/work_orders.csv", parse_dates=["opened_at"])
    assets = pd.read_csv(ROOT / "platform/seeds/assets.csv")
    return con, wo, assets


def run_sql(con, n: int):
    f = ROOT / "sql" / f"q{n}.sql"
    ok(f.exists(), f"Missing sql/q{n}.sql (see the questions in guide/00_foundations.md).")
    try:
        return con.execute(f.read_text()).fetchall()
    except duckdb.Error as exc:
        ok(False, f"sql/q{n}.sql raises an error in DuckDB: {exc}")


def norm(rows):
    return [tuple(round(x, 2) if isinstance(x, float) else x for x in r) for r in rows]


def test_q1_failures_per_asset(tables):
    con, wo, _ = tables
    c = wo[wo.wo_type == "CORRECTIVE"].groupby("asset_id").size().reset_index(name="n").sort_values(["n", "asset_id"], ascending=[False, True])
    ok(norm(run_sql(con, 1)) == [tuple(r) for r in c.itertuples(index=False)], "q1: (asset_id, n_failures) per asset, from most to fewest corrective failures (ties broken by asset_id).")


def test_q2_cost_by_criticality(tables):
    con, wo, assets = tables
    m = wo[wo.wo_type == "CORRECTIVE"].merge(assets, on="asset_id").groupby("criticality")["cost_usd"].sum().round(2).reset_index().sort_values("criticality")
    ok(norm(run_sql(con, 2)) == [tuple(r) for r in m.itertuples(index=False)], "q2: (criticality, total_cost) with the total cost of corrective failures per criticality, ordered by criticality.")


def test_q3_days_between_failures(tables):
    con, wo, _ = tables
    rows = []
    for a, g in wo[wo.wo_type == "CORRECTIVE"].sort_values("opened_at").groupby("asset_id", sort=True):
        d = g["opened_at"].dt.tz_localize(None).dt.normalize().tolist()
        rows += [(a, (d[i] - d[i - 1]).days) for i in range(1, len(d))]
    ok(norm(run_sql(con, 3)) == rows, "q3: (asset_id, days_since_prev): for every failure that has a previous one on the same asset, the difference in calendar days between the two. Order by asset and date. Hint: the LAG window function.")


def test_q4_mean_time_to_repair(tables):
    con, wo, _ = tables
    m = wo[wo.wo_type == "CORRECTIVE"].groupby("failure_mode")["downtime_hours"].mean().round(2).reset_index().sort_values("failure_mode")
    ok(norm(run_sql(con, 4)) == [tuple(r) for r in m.itertuples(index=False)], "q4: (failure_mode, avg_downtime_hours) with the average downtime hours per failure mode (rounded to 2 decimals), ordered by mode.")


def test_q5_assets_without_failures(tables):
    con, wo, assets = tables
    none = sorted(set(assets.asset_id) - set(wo[wo.wo_type == "CORRECTIVE"].asset_id))
    ok(norm(run_sql(con, 5)) == [(a,) for a in none], "q5: the asset_ids that have NEVER had a corrective failure, ordered. Hint: anti-join (LEFT JOIN … IS NULL) or NOT EXISTS.")
