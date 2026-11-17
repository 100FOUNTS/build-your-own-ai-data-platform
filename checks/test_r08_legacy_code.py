"""Challenge R8 (extends module 14) — Legacy code: the monthly report Marta uses is wrong and nobody knows why.
Part 2: supervise a coding agent (review notes + a regression test written first)."""

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


def _run_tests_against(code_root, tmp_path, select=None):
    """Copies `legacy/` from code_root + YOUR tests to a separate folder (with the same data) and runs them (or only `select`: "test_file.py::test_name")."""
    work = tmp_path / "work"
    work.mkdir(parents=True)
    shutil.copytree(code_root / "legacy", work / "legacy", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    shutil.copytree(PKG / "tests", work / "legacy" / "report" / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    for d in ("data", "platform"):
        (work / d).symlink_to(ROOT / d)
    target = str(work / "legacy" / "report" / "tests" / select) if select else str(work / "legacy" / "report" / "tests")
    r = sh([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", target],
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


# ------------------------------------------------------------------ part 2: supervise a coding agent
REVIEW = ROOT / "notes" / "R8_agent_review.md"
# section -> (pattern the heading must match, minimum words, minimum distinct words). Order matters: the first pattern that matches a heading wins.
SECTIONS = {
    "Failing test": (r"failing test", 0, 0),
    "Diff summary": (r"diff", 30, 18),
    "What the agent got right": (r"right", 25, 15),
    "What the agent got wrong": (r"wrong", 25, 15),
    "Decision": (r"decision|verdict", 25, 15),
    "Prompt": (r"prompt", 15, 10),
}
TEST_REF = re.compile(r"(legacy/report/tests/(test_\w+\.py))::(test_\w+)")


def review_sections() -> dict[str, str]:
    ok(REVIEW.exists(), "Missing notes/R8_agent_review.md. Part 2 of R8: ask a coding agent to fix ONE bug, write the regression test FIRST, and review its diff critically (see guide/challenges/R8_legacy_code.md).")
    parts: dict[str, str] = {}
    current, level = None, 0
    in_code = False
    for line in REVIEW.read_text().splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        h = None if in_code else re.match(r"(#+)\s*(.+?)\s*$", line)  # a `# comment` inside a code block is not a heading
        if h:
            name = next((n for n, (pat, _, _) in SECTIONS.items() if re.search(pat, h.group(2), re.I)), None)
            if name:
                current, level = name, len(h.group(1))
                parts[current] = parts.get(current, "")
            elif len(h.group(1)) <= level:  # a heading of the same or higher level that is not ours closes the section; a sub-heading stays inside it
                current = None
            continue
        if current:
            parts[current] += line + "\n"
    return parts


def test_r8_agent_review_has_its_sections():
    parts = review_sections()
    missing = [n for n in SECTIONS if n not in parts]
    ok(not missing, f"notes/R8_agent_review.md is missing the sections: {', '.join(missing)}.\n"
       "It needs a heading for each of: the prompt you gave, the failing test written first, a diff summary, what the agent got right, what the agent got wrong and the decision (accept or reject).")
    for name, (_, min_words, min_distinct) in SECTIONS.items():
        words = re.findall(r"[\w'-]+", re.sub(r"```.*?```", " ", parts[name], flags=re.S))
        ok(len(words) >= min_words and len({w.lower() for w in words}) >= min_distinct,
           f"The section “{name}” is too thin ({len(words)} words outside code blocks, at least {min_words} expected). "
           "A review that says “looks good” is not a review: say what you checked, what you found and why it matters.")
    ok(re.search(r"\b(accept|reject)", parts["Decision"], re.I), "The Decision section must say whether you ACCEPT or REJECT the agent's change (or accept with changes), and why.")
    ok(re.search(r"\w+\.py", parts["Diff summary"]), "The Diff summary must name the files the agent touched (for example `report.py`): you can't review a diff you haven't read.")
    ok(re.search(r"(?i)\bfailed\b|assertionerror|\bE\s+assert", parts["Failing test"]),
       "The Failing test section must include the red pytest output (the test FAILING before the agent touched anything). Without that evidence nobody knows the test was written first.")


def test_r8_agent_review_test_fails_on_the_original_and_passes_on_the_fix(tmp_path):
    parts = review_sections()
    ok("Failing test" in parts, "notes/R8_agent_review.md needs a “Failing test” section.")
    ref = TEST_REF.search(parts["Failing test"])
    ok(ref, "The Failing test section must name the test as `legacy/report/tests/<file>.py::<test_name>` so I can run exactly that test.")
    rel, file, name = ref.group(1), ref.group(2), ref.group(3)
    ok((ROOT / rel).exists() and re.search(rf"(?m)^def {name}\b", (ROOT / rel).read_text()), f"{rel}::{name} doesn't exist. Name a test that is in your repository.")
    ok(file != "test_report.py", "That test is in the team's original file, which passes with the bugs. Write a NEW test for the bug the agent fixes.")
    r, failed, _ = _run_tests_against(ORIGINAL, tmp_path / "orig", select=f"{file}::{name}")
    ok(r.returncode == 1 and failed >= 1,
       f"`{name}` doesn't fail on the ORIGINAL code (pytest exit code {r.returncode}). A test written before the fix must be red on the bug: if it is green it protects against nothing, "
       "and it can't tell you whether the agent's change fixed anything.\n" + tail(r, 6))
    r, failed, passed = _run_tests_against(ROOT, tmp_path / "now", select=f"{file}::{name}")
    ok(r.returncode == 0 and passed >= 1, f"`{name}` fails on your current code. Whatever you decided about the agent's diff, the code that stays in the repository must pass the test that motivated it:\n" + tail(r, 8))
