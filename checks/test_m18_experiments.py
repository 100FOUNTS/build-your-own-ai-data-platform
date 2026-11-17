"""Module 18 — Prove it works: a staggered rollout of the new alert policy across the company's simulated fleet.

The check needs NO services. It runs YOUR `experiments.design` and `experiments.analyze` as commands, generates the panels itself with the
simulator extension (`platform/sim/rollout.py`) and computes the TRUE effect by counterfactual simulation (same seeds, policy on vs off):
no estimator is involved in the truth. Then it asks four things of your work:
  1. was the rollout randomized as you declared (and is there a power plan)?
  2. is the estimator right? On a noise-free panel it must recover the truth to rounding error, and naive two-way fixed effects must not;
  3. on noisy panels, does your pump-level bootstrap interval cover the truth, and are the placebo leads quiet?
  4. do the pre-registered plan and the decision memo exist, with the required sections and the numbers of your own result?
"""

from __future__ import annotations

import json
import re
import sys
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT, ok, py, tail

sys.path.insert(0, str(ROOT / "platform" / "sim"))
import rollout  # noqa: E402  (the simulator extension: "the plant", don't modify it)

# ------------------------------------------------------------------ named constants. Every threshold below: calibrate in final test pass.
M18_DESIGN_SEED = 2026               # the seed the check gives your design command
M18_OTHER_DESIGN_SEED = 2027         # a second seed: a randomization must change with it
M18_PANEL_SEEDS = (1801, 1802, 1803)  # the check's noisy panels (same design, other luck)
M18_CHECK_N_BOOT = 300               # bootstrap draws the check asks for (your CLI default must be >= M18_MIN_BOOT)
M18_MIN_BOOT = 1000
M18_NOISEFREE_BOOT = 20
M18_MIN_ADOPT_WEEK = 10              # at least 10 weeks of history before the first line adopts
M18_MAX_ADOPT_WEEK = rollout.N_WEEKS - 8  # and at least 8 weeks of follow-up after the last adoption
M18_MIN_COHORTS = 3                  # staggered: at least 3 different adoption weeks
M18_MIN_LINES_PER_COHORT = 2
M18_MAX_COHORT_SPREAD = 1            # adopting cohorts differ by at most this many lines ("randomized as declared": balanced cohorts)
M18_MAX_BALANCE_SMD = 1.0            # early adopters vs the rest, baseline downtime of the lines (standardized mean difference)
M18_MDE_FACTOR = 2.5                 # declared MDE within [1/F, F] x the check's own back-of-the-envelope MDE
M18_COVERAGE_MIN = 2                 # the 95 % interval must cover the truth in at least 2 of the 3 noisy panels
M18_CI_WIDTH_H = (0.2, 4.0)          # plausible width of the interval on the overall ATT (hours per pump-week)
M18_MAX_REL_ERROR = 0.9              # |estimate - truth| / |truth| on the noisy panels
M18_EVENT_RMSE_H = 2.0               # RMSE of the event-study lags (e = 0..7) against the truth, noisy panels
M18_MEAN_LEAD_FRACTION = 1.0         # |average of the 4 placebo leads| must stay below this share of |true ATT| (a lead is noisy: single event times have SE ~ 1 h)
M18_DYNAMIC_SLACK_H = 0.75           # average(e=5..7) must be below average(e=0..2) + this (noise allowance)
M18_PLACEBO_MIN_COVERED = 2          # of the 4 required placebo leads (e = -5..-2) whose interval contains 0
M18_NOISEFREE_TOL_H = 1e-6           # noise-free panel: estimates must match the truth to this many hours
M18_TWFE_TOL_H = 1e-6                # your TWFE must equal the check's TWFE (same OLS, same panel)
M18_TWFE_MIN_REL_BIAS = 0.10         # noise-free staggered fixture: |TWFE - truth| >= 10 % of the truth
M18_MEMO_WORDS = (120, 700)
M18_PLAN_MIN_WORDS = 200
LEADS = (-5, -4, -3, -2)
LAGS = tuple(range(0, 8))

DESIGN_CMD = ["-m", "experiments.design"]
ANALYZE_CMD = ["-m", "experiments.analyze"]
RESULT_JSON = ROOT / "data/rollout/result.json"
PLAN_MD = ROOT / "notes/m18_analysis_plan.md"
MEMO_MD = ROOT / "notes/m18_decision_memo.md"


# ------------------------------------------------------------------ the check's own (independent) computations
def fixture_adoption() -> dict:
    """A fixed staggered rollout where everybody ends up treated: the textbook case where two-way fixed effects goes wrong."""
    weeks = {0: 12, 1: 20, 2: 28, 3: 36}
    return {g: weeks[i * len(weeks) // rollout.N_LINES] for i, g in enumerate(rollout.GROUPS)}


def matrices(panel: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    p = panel.sort_values(["pump_id", "week"])
    n = p["pump_id"].nunique()
    return p["downtime_hours"].to_numpy().reshape(n, -1), p["policy_on"].to_numpy().reshape(n, -1).astype(float)


def twfe(panel: pd.DataFrame) -> tuple[float, float]:
    """Plain two-way fixed effects (pump and week) on the treatment dummy, and the share of weight it puts on negative weights."""
    y, d = matrices(panel)
    dd = d - d.mean(1, keepdims=True) - d.mean(0, keepdims=True) + d.mean()
    beta = float((dd * y).sum() / (dd * dd).sum())
    w = dd / (dd * dd).sum()  # beta = sum over treated cells of w * (effect of that cell) + 0
    return beta, float(-w[(d == 1) & (w < 0)].sum())


def reference_mde(adoption: dict, seed: int, alpha: float, power: float) -> float:
    """Back-of-the-envelope MDE for a pump-level DiD with this schedule: (z_a + z_b) x SE, with the simulated fleet's own pump-week variance."""
    w = rollout._world(seed)
    p = w["p_i"].mean() + w["season"].mean()
    sigma = float(np.sqrt(p * (rollout.MEAN_REPAIR_H**2 + (rollout.REPAIR_MAX_H - rollout.REPAIR_MIN_H) ** 2 / 12) - (p * rollout.MEAN_REPAIR_H) ** 2))
    weeks = [v for v in adoption.values() if v is not None]
    never = sum(v is None for v in adoption.values())
    last = max(weeks)
    n_t = len(weeks) * rollout.PUMPS_PER_LINE
    n_c = (never if never else sum(v == last for v in weeks)) * rollout.PUMPS_PER_LINE
    end = rollout.N_WEEKS if never else last
    w_post = float(np.mean([end - v for v in weeks if v < end] or [1]))
    w_pre = float(np.mean(weeks))
    se = sigma * np.sqrt(1 / n_t + 1 / n_c) * np.sqrt(1 / w_post + 1 / w_pre)
    nd = NormalDist()
    return float((nd.inv_cdf(1 - alpha / 2) + nd.inv_cdf(power)) * se)


def write_panel(path, adoption: dict, seed: int, expected: bool = False) -> pd.DataFrame:
    panel = rollout.simulate(adoption, seed, expected=expected)
    panel.to_csv(path, index=False)
    return panel


def run_analyze(tmp, tag: str, panel_csv, design_json, seed: int, n_boot: int) -> dict:
    out = tmp / f"result_{tag}.json"
    r = py([*ANALYZE_CMD, "--panel", str(panel_csv), "--design", str(design_json), "--seed", str(seed), "--n-boot", str(n_boot), "--out", str(out)], timeout=900)
    ok(r.returncode == 0, f"`python -m experiments.analyze` failed on the {tag} panel. Last lines:\n" + tail(r, 14))
    ok(out.exists(), f"`experiments.analyze` finished but did not write --out ({out.name}).")
    res = json.loads(out.read_text())
    check_result_shape(res, tag)
    return res


def check_result_shape(res: dict, tag: str) -> None:
    ok(isinstance(res.get("att"), dict) and {"estimate", "ci_low", "ci_high"} <= set(res["att"]),
       f"[{tag}] result.json needs \"att\": {{\"estimate\", \"ci_low\", \"ci_high\"}} (guide/18_experiments.md, The contract).")
    ok(isinstance(res.get("event_study"), list), f"[{tag}] result.json needs an \"event_study\" list of {{event_time, estimate, ci_low, ci_high}}.")
    es = {int(r["event_time"]): r for r in res["event_study"]}
    miss = [e for e in (*LEADS, *LAGS) if e not in es]
    ok(not miss, f"[{tag}] the event study must cover event times {LEADS[0]}..{LEADS[-1]} and {LAGS[0]}..{LAGS[-1]}; missing {miss}. "
                 "(The reference week -1 is optional.)")
    ok(isinstance(res.get("twfe"), dict) and {"estimate", "negative_weight_mass"} <= set(res["twfe"]),
       f"[{tag}] result.json needs \"twfe\": {{\"estimate\", \"negative_weight_mass\"}}: the naive estimate must be reported, not hidden.")


def event_map(res: dict) -> dict[int, dict]:
    return {int(r["event_time"]): r for r in res["event_study"]}


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def design(tmp_path_factory):
    ok((ROOT / "experiments/design.py").exists(), "Missing experiments/design.py (`python -m experiments.design --seed S --out design.json`, see guide/18_experiments.md).")
    tmp = tmp_path_factory.mktemp("m18_design")
    outs = {}
    for tag, seed in (("a", M18_DESIGN_SEED), ("a2", M18_DESIGN_SEED), ("b", M18_OTHER_DESIGN_SEED)):
        out = tmp / f"design_{tag}.json"
        r = py([*DESIGN_CMD, "--seed", str(seed), "--out", str(out)])
        ok(r.returncode == 0, f"`python -m experiments.design --seed {seed} --out …` failed. Last lines:\n" + tail(r, 12))
        ok(out.exists(), "`experiments.design` finished but did not write --out.")
        outs[tag] = json.loads(out.read_text())
    outs["path"] = tmp / "design_a.json"
    ok("adoption" in outs["a"], "design.json needs an \"adoption\" object: {\"FLEET-L01\": <week or null>, …} (it is what the simulator reads).")
    return outs


@pytest.fixture(scope="module")
def truth_panels(design, tmp_path_factory):
    """Noisy panels from the learner's design, the results of their analysis and the true effects."""
    ok((ROOT / "experiments/analyze.py").exists(), "Missing experiments/analyze.py (`python -m experiments.analyze`, see guide/18_experiments.md).")
    tmp = tmp_path_factory.mktemp("m18_noisy")
    adoption = rollout.validate_adoption(design["a"]["adoption"])
    runs = []
    for s in M18_PANEL_SEEDS:
        csv = tmp / f"panel_{s}.csv"
        panel = write_panel(csv, adoption, s)
        res = run_analyze(tmp, str(s), csv, design["path"], s, M18_CHECK_N_BOOT)
        runs.append({"seed": s, "panel": panel, "res": res, "truth": rollout.true_att(adoption, s)})
    return runs


@pytest.fixture(scope="module")
def noise_free(tmp_path_factory):
    ok((ROOT / "experiments/analyze.py").exists(), "Missing experiments/analyze.py (`python -m experiments.analyze`, see guide/18_experiments.md).")
    tmp = tmp_path_factory.mktemp("m18_exact")
    adoption = fixture_adoption()
    dj = tmp / "design.json"
    dj.write_text(json.dumps({"seed": 0, "adoption": adoption}))
    csv = tmp / "panel.csv"
    panel = write_panel(csv, adoption, 7, expected=True)
    res = run_analyze(tmp, "noise-free", csv, dj, 7, M18_NOISEFREE_BOOT)
    return {"panel": panel, "res": res, "truth": rollout.true_att(adoption, 7, expected=True)}


# ------------------------------------------------------------------ 1. the design
def test_cohorts_were_randomized_as_declared(design):
    a, a2, b = design["a"], design["a2"], design["b"]
    adoption = a["adoption"]
    try:
        adoption = rollout.validate_adoption(adoption)
    except ValueError as exc:
        ok(False, f"design.json: {exc}")
    weeks = [w for w in adoption.values() if w is not None]
    ok(all(M18_MIN_ADOPT_WEEK <= w <= M18_MAX_ADOPT_WEEK for w in weeks),
       f"Adoption weeks must lie in {M18_MIN_ADOPT_WEEK}..{M18_MAX_ADOPT_WEEK}: you need a pre-period to learn from and a follow-up to see the effect (found {sorted(set(weeks))}).")
    sizes = pd.Series(weeks).value_counts()
    ok(len(sizes) >= M18_MIN_COHORTS, f"Staggered means at least {M18_MIN_COHORTS} different adoption weeks; yours has {len(sizes)}.")
    ok(sizes.min() >= M18_MIN_LINES_PER_COHORT, f"Every cohort needs at least {M18_MIN_LINES_PER_COHORT} lines (smallest: {sizes.min()}): one line is an anecdote, not a cohort.")
    ok(sizes.max() - sizes.min() <= M18_MAX_COHORT_SPREAD,
       f"Cohorts must be balanced (sizes differ by at most {M18_MAX_COHORT_SPREAD} line); yours: {sizes.sort_index().to_dict()}.")
    ok(sum(w is None for w in adoption.values()) + len(weeks) == rollout.N_LINES, "Every line must appear exactly once.")
    follow = rollout.true_att(adoption, M18_PANEL_SEEDS[0])["by_event_time"]
    ok(all(e in follow for e in LAGS),
       f"With this schedule some event times 0..{LAGS[-1]} have no comparison lines left (everybody is already treated). Keep some never-treated lines "
       "or leave more weeks between the first and the last adoption: without a comparison group there is no estimate.")
    # the declaration: seed, unit and cohort sizes
    rz = a.get("randomization")
    ok(isinstance(rz, dict) and {"unit", "seed", "cohort_sizes"} <= set(rz),
       "design.json needs \"randomization\": {\"unit\", \"seed\", \"cohort_sizes\"}: what you declare is what the check verifies.")
    ok(rz["seed"] == M18_DESIGN_SEED, f"randomization.seed is {rz['seed']} but the command was run with --seed {M18_DESIGN_SEED}.")
    ok(str(rz["unit"]).lower() in {"line", "group", "pump_group"}, "The unit of randomization is the LINE (the group of pumps), not the pump: write \"line\".")
    declared = {str(k): int(v) for k, v in rz["cohort_sizes"].items()}
    actual = {str(k): int(v) for k, v in sizes.items()}
    if any(w is None for w in adoption.values()):
        actual["never"] = sum(w is None for w in adoption.values())
    ok(declared == actual, f"randomization.cohort_sizes says {declared} but the adoption plan has {actual}.")
    # reproducible and really random
    ok(a2["adoption"] == a["adoption"], "The same seed must give the same assignment (a reproducible randomization is an auditable one).")
    ok(b["adoption"] != a["adoption"], "Another seed gave the SAME assignment: this is not randomized, it is a fixed list.")
    # balance on something the randomizer could not see
    base = rollout.baseline_by_group(M18_PANEL_SEEDS[0]).set_index("group_id")["baseline_downtime_h"]
    median = float(np.median(weeks))
    early = [g for g, w in adoption.items() if w is not None and w <= median]
    late = [g for g in adoption if g not in early]
    smd = abs(base[early].mean() - base[late].mean()) / np.sqrt((base[early].var() + base[late].var()) / 2)
    ok(smd <= M18_MAX_BALANCE_SMD,
       f"Early adopters are not comparable to the rest: standardized difference in baseline downtime is {smd:.2f} (limit {M18_MAX_BALANCE_SMD}). "
       "Older lines fail more in this fleet. Did the oldest lines go first?")


def test_the_power_plan_is_there_and_plausible(design):
    a = design["a"]
    pw = a.get("power")
    ok(isinstance(pw, dict) and {"alpha", "power", "mde_hours", "sd_pump_week_hours"} <= set(pw),
       "design.json needs \"power\": {\"alpha\", \"power\", \"mde_hours\", \"sd_pump_week_hours\"}: the pre-registered power calculation.")
    ok(0 < pw["alpha"] <= 0.1 and 0.7 <= pw["power"] < 1, f"alpha={pw['alpha']}, power={pw['power']}: use alpha <= 0.10 and power between 0.7 and 1.")
    ok(pw["sd_pump_week_hours"] > 0 and pw["mde_hours"] > 0, "sd_pump_week_hours and mde_hours must be positive numbers (hours of downtime per pump-week).")
    ref = reference_mde(rollout.validate_adoption(a["adoption"]), M18_PANEL_SEEDS[0], pw["alpha"], pw["power"])
    ok(ref / M18_MDE_FACTOR <= pw["mde_hours"] <= ref * M18_MDE_FACTOR,
       f"Your MDE ({pw['mde_hours']:.2f} h per pump-week) is far from a back-of-the-envelope calculation for this schedule ({ref:.2f} h; accepted: "
       f"{ref / M18_MDE_FACTOR:.2f}..{ref * M18_MDE_FACTOR:.2f}). Check: the unit of the standard deviation, how many pumps are treated vs control, and "
       "how many weeks go into each side of the comparison.")


# ------------------------------------------------------------------ 2. the estimator
def test_estimator_recovers_the_truth_without_noise(noise_free):
    res, truth = noise_free["res"], noise_free["truth"]
    est = res["att"]["estimate"]
    ok(abs(est - truth["overall"]) <= M18_NOISEFREE_TOL_H,
       f"On a panel WITHOUT noise (parallel trends hold exactly) your overall ATT is {est:.6f} h and the truth is {truth['overall']:.6f} h. "
       "A correct staggered estimator is exact here; the gap is a bug or a different estimand (cells it averages, controls it uses, weights). "
       "Re-read 'The estimand' in the guide.")
    es = event_map(res)
    for e in LAGS:
        ok(abs(es[e]["estimate"] - truth["by_event_time"][e]) <= M18_NOISEFREE_TOL_H,
           f"Event time {e}: your estimate is {es[e]['estimate']:.6f} h, the truth is {truth['by_event_time'][e]:.6f} h (noise-free panel).")
    for e in LEADS:
        ok(abs(es[e]["estimate"]) <= M18_NOISEFREE_TOL_H,
           f"Placebo lead {e}: {es[e]['estimate']:.6f} h on a panel where nothing happens before adoption. A lead must be exactly 0 here: "
           "check that the controls are not-yet-treated at that week and that your baseline window ends before the leads start.")


def test_naive_twfe_is_reported_and_biased(noise_free):
    res, truth, panel = noise_free["res"], noise_free["truth"], noise_free["panel"]
    ref_beta, ref_neg = twfe(panel)
    ok(abs(res["twfe"]["estimate"] - ref_beta) <= M18_TWFE_TOL_H,
       f"Your TWFE estimate is {res['twfe']['estimate']:.6f} but plain two-way fixed effects (pump and week dummies, one treatment dummy) gives {ref_beta:.6f} on this panel.")
    ok(abs(res["twfe"]["negative_weight_mass"] - ref_neg) <= M18_TWFE_TOL_H,
       f"negative_weight_mass is {res['twfe']['negative_weight_mass']:.6f}; the check gets {ref_neg:.6f} (minus the sum of the negative weights TWFE puts on treated cells; "
       "weights = treatment residual after removing pump and week means, divided by the sum of its squares).")
    ok(ref_neg > 0.01, "The fixture should give TWFE negative weights; the check's own computation found none (report this).")
    gap = abs(ref_beta - truth["overall"])
    ok(gap >= M18_TWFE_MIN_REL_BIAS * abs(truth["overall"]),
       f"Calibration problem, not yours: TWFE is only {gap:.4f} h from the truth ({truth['overall']:.4f}) on the fixture.")
    print(f"\n[m18] fixture: truth {truth['overall']:.3f} h, TWFE {ref_beta:.3f} h, negative weight mass {ref_neg:.3f}")


# ------------------------------------------------------------------ 3. uncertainty and validity on noisy panels
def test_the_interval_covers_the_true_att(truth_panels):
    covered, lines = 0, []
    for r in truth_panels:
        att, t = r["res"]["att"], r["truth"]["overall"]
        ok(att["ci_low"] < att["estimate"] < att["ci_high"], f"[seed {r['seed']}] the estimate must lie strictly inside its interval ({att}).")
        width = att["ci_high"] - att["ci_low"]
        ok(M18_CI_WIDTH_H[0] <= width <= M18_CI_WIDTH_H[1],
           f"[seed {r['seed']}] interval width {width:.2f} h is outside the plausible {M18_CI_WIDTH_H} for this panel. Too narrow: you resampled pump-weeks, not pumps. "
           "Too wide: you resampled something coarser or forgot to keep each cohort's size.")
        ok(abs(att["estimate"] - t) <= M18_MAX_REL_ERROR * abs(t),
           f"[seed {r['seed']}] estimate {att['estimate']:.2f} h vs truth {t:.2f} h: further off than {M18_MAX_REL_ERROR:.0%}.")
        inside = att["ci_low"] <= t <= att["ci_high"]
        covered += inside
        lines.append(f"seed {r['seed']}: truth {t:.2f}, estimate {att['estimate']:.2f} [{att['ci_low']:.2f}, {att['ci_high']:.2f}] {'covers' if inside else 'MISSES'}")
    print("\n[m18] " + "\n[m18] ".join(lines))
    ok(covered >= M18_COVERAGE_MIN, f"The 95 % interval covered the truth in {covered} of {len(truth_panels)} panels (needs {M18_COVERAGE_MIN}).\n" + "\n".join(lines))


def test_placebo_leads_are_quiet(truth_panels):
    for r in truth_panels:
        es = event_map(r["res"])
        quiet = sum(es[e]["ci_low"] <= 0 <= es[e]["ci_high"] for e in LEADS)
        ok(quiet >= M18_PLACEBO_MIN_COVERED,
           f"[seed {r['seed']}] only {quiet} of the {len(LEADS)} placebo leads have an interval containing 0 (needs {M18_PLACEBO_MIN_COVERED}). "
           "Before adoption nothing differs between the cohorts, so leads far from 0 mean the comparison is contaminated (controls already treated?) or the baseline overlaps the leads.")
        ok(abs(np.mean([es[e]["estimate"] for e in LEADS])) <= M18_MEAN_LEAD_FRACTION * abs(r["truth"]["overall"]) + 1e-9,
           f"[seed {r['seed']}] the average placebo lead is as large as the true effect: a pre-trend that size would make any estimate of the effect meaningless.")


def test_event_study_tracks_the_truth(truth_panels):
    for r in truth_panels:
        es, tr = event_map(r["res"]), r["truth"]["by_event_time"]
        rmse = float(np.sqrt(np.mean([(es[e]["estimate"] - tr[e]) ** 2 for e in LAGS])))
        ok(rmse <= M18_EVENT_RMSE_H, f"[seed {r['seed']}] the event-study lags are {rmse:.2f} h (RMSE) from the true dynamic effect (limit {M18_EVENT_RMSE_H}).")
        early, late = np.mean([es[e]["estimate"] for e in (0, 1, 2)]), np.mean([es[e]["estimate"] for e in (5, 6, 7)])
        ok(late < early + M18_DYNAMIC_SLACK_H,
           f"[seed {r['seed']}] the effect should grow with exposure (crews learn); your average of e=5..7 ({late:.2f}) is not below your average of e=0..2 ({early:.2f}) (slack {M18_DYNAMIC_SLACK_H} h for noise).")


def test_naive_twfe_on_the_noisy_panel(truth_panels):
    for r in truth_panels:
        beta, neg = twfe(r["panel"])
        got = r["res"]["twfe"]
        ok(abs(got["estimate"] - beta) <= M18_TWFE_TOL_H, f"[seed {r['seed']}] your TWFE is {got['estimate']:.6f}, the check's is {beta:.6f}.")
        ok(abs(got["negative_weight_mass"] - neg) <= M18_TWFE_TOL_H, f"[seed {r['seed']}] negative_weight_mass {got['negative_weight_mass']:.6f} vs {neg:.6f}.")
    r = truth_panels[0]
    print(f"\n[m18] your design, seed {r['seed']}: truth {r['truth']['overall']:.2f} h, CS-style {r['res']['att']['estimate']:.2f} h, TWFE {r['res']['twfe']['estimate']:.2f} h")


def test_the_analysis_does_not_peek_at_the_truth():
    files = sorted((ROOT / "experiments").glob("*.py"))
    ok(files, "No experiments/*.py yet: write experiments/design.py and experiments/analyze.py (guide/18_experiments.md).")
    src = "\n".join(p.read_text() for p in files)
    for bad in ("true_effects", "true_att", "import rollout", "from rollout", "policy=False"):
        ok(bad not in src, f"experiments/ uses `{bad}`: the analysis may only read the panel and the design. The truth is for the check (and for you, AFTER the memo).")


# ------------------------------------------------------------------ 4. the plan and the memo
def headings(text: str) -> list[str]:
    return [h.lower() for h in re.findall(r"^#{1,4}\s+(.+)$", text, flags=re.M)]


def has_section(text: str, *keys: str) -> bool:
    return any(any(k in h for k in keys) for h in headings(text))


def words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def test_the_analysis_plan_was_pre_registered(design):
    ok(PLAN_MD.exists(), "Missing notes/m18_analysis_plan.md: the pre-registered plan, written BEFORE you generate the panel.")
    text = PLAN_MD.read_text()
    ok(words(text) >= M18_PLAN_MIN_WORDS, f"The plan has {words(text)} words; at least {M18_PLAN_MIN_WORDS} are needed to say what you will do and why.")
    need = {"estimand": ("estimand",), "primary outcome": ("outcome",), "randomization": ("randomi", "design"), "estimator": ("estimator", "method"),
            "inference": ("inference", "uncertainty", "bootstrap"), "power / MDE": ("power", "mde"), "validity checks": ("pre-trend", "placebo", "validity"),
            "decision rule": ("decision rule", "decision")}
    miss = [k for k, keys in need.items() if not has_section(text, *keys)]
    ok(not miss, f"notes/m18_analysis_plan.md needs a heading for: {', '.join(miss)}.")
    mde = design["a"]["power"]["mde_hours"]
    ok(any(f"{mde:.{n}f}" in text for n in (1, 2)), f"The plan must state the same MDE as design.json ({mde:.2f} h per pump-week): the number you pre-registered.")
    ok(re.search(r"\b(not[- ]yet[- ]treated|never[- ]treated)\b", text, flags=re.I) and re.search(r"bootstrap", text, flags=re.I),
       "The plan must name the comparison group (not-yet-treated / never-treated) and the bootstrap it will use.")


def test_the_decision_memo():
    ok(RESULT_JSON.exists(), "Missing data/rollout/result.json: run `python -m experiments.analyze` on YOUR panel (data/rollout/panel.csv) with --out data/rollout/result.json.")
    res = json.loads(RESULT_JSON.read_text())
    check_result_shape(res, "your own result")
    ok(res.get("n_boot", 0) >= M18_MIN_BOOT, f"data/rollout/result.json used n_boot={res.get('n_boot')}; your own final run needs at least {M18_MIN_BOOT} bootstrap draws.")
    ok(MEMO_MD.exists(), "Missing notes/m18_decision_memo.md: one page for the maintenance director.")
    text = MEMO_MD.read_text()
    n = words(text)
    ok(M18_MEMO_WORDS[0] <= n <= M18_MEMO_WORDS[1], f"The memo has {n} words; a decision memo is {M18_MEMO_WORDS[0]}-{M18_MEMO_WORDS[1]} (the manager reads it standing up).")
    need = {"Decision": ("decision",), "Evidence": ("evidence", "result"), "Uncertainty": ("uncertainty", "interval", "confidence"),
            "Validity": ("validity", "pre-trend", "placebo", "trust"), "Limits / next steps": ("limit", "next step", "risk", "caveat")}
    miss = [k for k, keys in need.items() if not has_section(text, *keys)]
    ok(not miss, f"The memo needs a heading for: {', '.join(miss)}.")
    att = res["att"]
    for label, v in (("estimate", att["estimate"]), ("interval low", att["ci_low"]), ("interval high", att["ci_high"])):
        shown = {f"{abs(v):.{k}f}" for k in (1, 2)}
        ok(any(s in text for s in shown), f"The memo does not quote your result ({label} = {v:.2f} h per pump-week, from data/rollout/result.json). A memo that disagrees with the analysis is worse than none.")
    m = re.search(r"decision\s*:\s*\**\s*(roll out|extend|stop)", text, flags=re.I)
    ok(m, "Write one line `Decision: ROLL OUT`, `Decision: EXTEND` (keep the pilot running / widen it) or `Decision: STOP` under the Decision heading.")
    d = m.group(1).lower()
    helps = att["ci_high"] < 0   # the whole interval says downtime goes down
    hurts = att["ci_low"] > 0
    ok(not (d == "stop" and helps), "Your interval says downtime FELL (it lies entirely below 0) and the memo says STOP.")
    ok(not (d == "roll out" and not helps), "Your interval contains 0 (or is positive) and the memo says ROLL OUT: the evidence does not support it. EXTEND the pilot, or say what else justifies it.")
    ok(not (d != "stop" and hurts), "Your interval says downtime ROSE and the memo does not say STOP.")
    low = text.lower()
    for k, alts in {"two-way fixed effects (TWFE)": ("twfe", "two-way"), "placebo / pre-trend": ("placebo", "pre-trend", "pretrend"), "bootstrap": ("bootstrap",),
                    "the unit (hours)": ("hours", " h "), "the MDE": ("mde", "minimum detectable")}.items():
        ok(any(a in low for a in alts), f"The memo never mentions {k}. Say what it means for the decision, in plain words.")
