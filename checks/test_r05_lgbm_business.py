"""Challenge R5 (extends module 4) — LightGBM with the metric the business cares about: false alerts, early warning, the why of each alert,
the cost-optimal threshold and a comparison of two alert policies with a block-bootstrap confidence interval."""

from __future__ import annotations

import io
import itertools
import json
import os

import duckdb
import httpx
import mlflow
import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from conftest import ROOT, need, ok, py, tail, unique
from expected import R5_CI_LEVEL, R5_COST_FALSE_ALERT, R5_COST_MISSED_DAY, R5_MIN_BOOT, expected_gold

pytestmark = pytest.mark.challenge

KEYS = {"asset_id", "feature_date", "label_failure_7d"}


@pytest.fixture(scope="module")
def r5_run(tmp_path_factory):
    need("mlflow", "s3")
    ok((ROOT / "ml/lgbm.py").exists(), "Missing ml/lgbm.py (`python -m ml.lgbm`, see guide/challenges/R5_lgbm_business.md).")
    g = expected_gold()
    wh = tmp_path_factory.mktemp("lgbm") / "wh.duckdb"
    con = duckdb.connect(str(wh))
    con.register("g", g)
    con.execute("create table asset_daily_features as select * from g")
    con.close()
    exp = unique("pump-risk-lgbm")
    r = py(["-m", "ml.lgbm"], env={"WAREHOUSE_PATH": str(wh), "MLFLOW_EXPERIMENT": exp}, timeout=900)
    ok(r.returncode == 0, "`python -m ml.lgbm` failed:\n" + tail(r, 16))
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    e = mlflow.get_experiment_by_name(exp)
    ok(e, f"The experiment `{exp}` doesn't exist (use the MLFLOW_EXPERIMENT variable).")
    runs = mlflow.search_runs([e.experiment_id], output_format="pandas")
    ok("params.model_type" in runs, "The runs don't log the `model_type` param (it must be `lightgbm`).")
    runs = runs[(runs["status"] == "FINISHED") & (runs["params.model_type"] == "lightgbm")]
    ok(len(runs), "There is no finished run with `model_type = lightgbm` in the experiment.")
    run = runs.iloc[0]
    arts = {}
    for name in ("val_predictions.csv", "test_predictions.csv", "alert_explanations.csv"):
        # through the server proxy: MLflow 3 presigned URLs point to “seaweedfs:8333”, which only exists inside Docker
        path = run["artifact_uri"].replace("mlflow-artifacts:/", "").strip("/")
        r = httpx.get(f"{os.environ['MLFLOW_TRACKING_URI']}/api/2.0/mlflow-artifacts/artifacts/{path}/{name}", timeout=30)
        ok(r.status_code == 200, f"The run doesn't have the artifact `{name}` at the root of its artifacts (HTTP {r.status_code}). Without the predictions nobody can audit your metrics.")
        arts[name] = pd.read_csv(io.StringIO(r.text), float_precision="round_trip")  # exact floats: `alert` must equal `score >= threshold` to the last digit
    for df in arts.values():
        if "feature_date" in df:
            df["feature_date"] = pd.to_datetime(df["feature_date"])
    return {"run": run, "gold": g, **arts}


def p(run, k):
    v = run.get(f"params.{k}")
    ok(v is not None and v == v, f"The run is missing the param `{k}`.")
    return v


def m(run, k):
    v = run.get(f"metrics.{k}")
    ok(v is not None and v == v, f"The run is missing the metric `{k}`.")
    return float(v)


def fa_per_100(df):
    healthy = df[df["label_failure_7d"] == 0]
    return 100 * healthy["alert"].astype(bool).sum() / max(len(healthy), 1)


def early(df, test_start, test_end):
    wo = pd.read_csv(ROOT / "platform/seeds/work_orders.csv", parse_dates=["opened_at"])
    wo = wo[wo.wo_type == "CORRECTIVE"]
    total = hit = 0
    for _, w in wo.iterrows():
        f = w["opened_at"].tz_convert("UTC").tz_localize(None).normalize()
        lo, hi = f - pd.Timedelta(days=7), f - pd.Timedelta(days=3)
        if lo < test_start or hi > test_end:
            continue
        total += 1
        win = df[(df.asset_id == w.asset_id) & df.feature_date.between(lo, hi)]
        hit += bool(win["alert"].astype(bool).any())
    return hit, total


def test_r5_three_windows_without_leakage(r5_run):
    run = r5_run["run"]
    te, vs, ve, ts = (pd.Timestamp(p(run, k)) for k in ("train_end", "val_start", "val_end", "test_start"))
    ok((vs - te).days >= 7 and (ts - ve).days >= 7, f"Windows: train until {te.date()}, validation {vs.date()}→{ve.date()}, test from {ts.date()}. "
       "Each pair needs a gap of ≥ 7 days between them (the label looks 7 days ahead).")
    for name, lo, hi in (("val_predictions.csv", vs, ve), ("test_predictions.csv", ts, None)):
        df = r5_run[name]
        ok({"asset_id", "feature_date", "score", "label_failure_7d", "alert"} <= set(df), f"{name} must have the columns asset_id, feature_date, score, label_failure_7d and alert.")
        ok(df.feature_date.min() >= lo and (hi is None or df.feature_date.max() <= hi), f"{name} has dates outside its window ({df.feature_date.min().date()} → {df.feature_date.max().date()}).")
        ok(df["label_failure_7d"].notna().all(), f"{name} includes rows without a label: they can't be evaluated (the last 7 days are scored, but not evaluated).")
        ref = df.merge(r5_run["gold"][["asset_id", "feature_date", "label_failure_7d"]], on=["asset_id", "feature_date"], suffixes=("", "_g"))
        ok(len(ref) == len(df) and (ref.label_failure_7d == ref.label_failure_7d_g).all(), f"The labels in {name} don't match the ones in the gold table.")


def test_r5_threshold_chosen_on_validation(r5_run):
    run = r5_run["run"]
    thr = float(p(run, "threshold"))
    ok(str(p(run, "threshold_selected_on")).lower() == "validation", "The threshold is chosen on VALIDATION (`threshold_selected_on = validation`). If you choose it by looking at the test, the test stops measuring anything.")
    for name in ("val_predictions.csv", "test_predictions.csv"):
        df = r5_run[name]
        ok(((df.score >= thr) == df.alert.astype(bool)).all(), f"In {name}, `alert` is not exactly `score >= threshold` ({thr}).")
    val = fa_per_100(r5_run["val_predictions.csv"])
    ok(val <= 5 + 1e-9, f"With your threshold there are {val:.1f} false alerts per 100 healthy days on validation; the business asks for ≤ 5. The threshold must meet the target where you choose it.")
    ok(abs(m(run, "val_false_alerts_per_100_healthy") - val) < 0.01, f"The logged `val_false_alerts_per_100_healthy` ({m(run, 'val_false_alerts_per_100_healthy'):.2f}) doesn't match the one computed from val_predictions.csv ({val:.2f}).")


def test_r5_business_metrics_on_test(r5_run):
    run, df = r5_run["run"], r5_run["test_predictions.csv"]
    fa = fa_per_100(df)
    ok(abs(m(run, "false_alerts_per_100_healthy") - fa) < 0.01, f"The logged `false_alerts_per_100_healthy` ({m(run, 'false_alerts_per_100_healthy'):.2f}) doesn't match the one from test_predictions.csv ({fa:.2f}). Check the definition: alerts on rows with label 0, per 100 of those rows.")
    hit, total = early(df, df.feature_date.min(), df.feature_date.max())
    ok(int(m(run, "early_total")) == total and int(m(run, "early_detected")) == hit,
       f"Early detection: you logged {int(m(run, 'early_detected'))}/{int(m(run, 'early_total'))} and your predictions give {hit}/{total}.\n"
       "A failure counts if its window [failure day − 7, day − 3] falls entirely inside the test; it is detected if there is any alert for that asset in that window.")
    ap = average_precision_score(df.label_failure_7d, df.score)
    base = df.label_failure_7d.mean()
    ok(abs(m(run, "average_precision") - ap) < 1e-3, f"The logged `average_precision` ({m(run, 'average_precision'):.3f}) ≠ the one computed from test_predictions.csv ({ap:.3f}).")
    ok(ap >= max(0.25, 3 * base), f"AP on test = {ap:.2f} (chance = {base:.2f}). ≥ {max(0.25, 3 * base):.2f} expected.")
    print(f"\n  test: AP={ap:.2f} · false alerts/100 healthy days={fa:.1f} · failures warned ≥3 days ahead={hit}/{total}")


def test_r5_each_alert_explains_why(r5_run):
    df, ex = r5_run["test_predictions.csv"], r5_run["alert_explanations.csv"]
    ok({"asset_id", "feature_date", "rank", "feature", "contribution"} <= set(ex), "alert_explanations.csv must have asset_id, feature_date, rank, feature and contribution.")
    alerts = df[df.alert.astype(bool)][["asset_id", "feature_date"]]
    ok(len(alerts), "There is no alert on test: with that threshold the model is of no use to maintenance.")
    feats = set(r5_run["gold"].columns) - KEYS
    bad = []
    for (a, d), grp in ex.groupby(["asset_id", "feature_date"]):
        grp = grp.sort_values("rank")
        if list(grp["rank"]) != [1, 2, 3] or not set(grp.feature) <= feats or not (np.diff(np.abs(grp.contribution.to_numpy())) <= 1e-12).all():
            bad.append((a, str(d.date())))
    got = set(map(tuple, ex[["asset_id", "feature_date"]].drop_duplicates().astype(str).values))
    want = set(map(tuple, alerts.astype(str).values))
    ok(want <= got, f"There are {len(want - got)} test alerts without an explanation (e.g. {sorted(want - got)[:2]}). The technician needs to know WHY each alert fires.")
    ok(not bad, f"Malformed explanations in {bad[:3]}: each alert has exactly 3 rows (rank 1, 2, 3), with real gold features, ordered by descending |contribution|.")


# ------------------------------------------------------------------ cost-optimal threshold
def cost(df, thr):
    """Business cost of the policy `score >= thr`: wasted visits + asset-days with a coming failure and no alert."""
    alert = df["score"] >= thr
    fa = int((alert & (df["label_failure_7d"] == 0)).sum())
    missed = int((~alert & (df["label_failure_7d"] == 1)).sum())
    return R5_COST_FALSE_ALERT * fa + R5_COST_MISSED_DAY * missed


def test_r5_threshold_is_cost_optimal_on_validation(r5_run):
    df, thr = r5_run["val_predictions.csv"], float(p(r5_run["run"], "threshold"))
    candidates = [*np.unique(df.score), df.score.max() + 1e-9]
    feasible = [t for t in candidates if fa_per_100(df.assign(alert=df.score >= t)) <= 5 + 1e-9]
    best = min(cost(df, t) for t in feasible)
    mine = cost(df, thr)
    ok(mine <= best + 1e-9, f"On validation your threshold costs {mine:.0f} units (1 per false alert, {R5_COST_MISSED_DAY:.0f} per asset-day with a coming failure and no alert), "
       f"but another threshold that also meets the ≤ 5 target costs {best:.0f}.\n"
       "The business asks for the CHEAPEST policy that respects the false-alert limit, not just any policy that respects it: look at the cost of every candidate threshold.")


# ------------------------------------------------------------------ policy comparison: default 0.5 vs cost-optimal, with block bootstrap
MODULE = ROOT / "ml" / "policy_compare.py"
BLOCKS = ("asset_week", "day")


def block_bootstrap(df, thr_opt, block, thr_def=0.5, n_boot=4000, seed=123):
    """The check's own computation: point estimates and a percentile CI resampling whole blocks (default minus optimal, per 100 asset-days)."""
    df = df.reset_index(drop=True)
    pos = df.label_failure_7d == 1
    cols = pd.DataFrame({"n": 1.0}, index=df.index)
    for tag, t in (("d", thr_def), ("o", thr_opt)):
        a = df.score >= t
        cols["fa_" + tag], cols["mi_" + tag] = (a & ~pos).astype(float), (~a & pos).astype(float)
    key = df.feature_date.astype(str) if block == "day" else df.asset_id + "|" + ((df.feature_date - df.feature_date.min()).dt.days // 7).astype(str)
    blk = cols.groupby(key.to_numpy()).sum()
    arr = blk[["fa_d", "mi_d", "fa_o", "mi_o", "n"]].to_numpy()

    def stats(s):
        n = s[..., 4]
        c = 100 * ((R5_COST_FALSE_ALERT * s[..., 0] + R5_COST_MISSED_DAY * s[..., 1]) - (R5_COST_FALSE_ALERT * s[..., 2] + R5_COST_MISSED_DAY * s[..., 3])) / n
        return c, 100 * (s[..., 1] - s[..., 3]) / n

    draws = arr[np.random.default_rng(seed).integers(0, len(arr), size=(n_boot, len(arr)))].sum(axis=1)
    out = {"n_blocks": len(arr), "counts": arr.sum(axis=0)}
    for name, est, dist in zip(("cost", "missed"), stats(arr.sum(axis=0)), stats(draws)):
        lo, hi = np.percentile(dist, [2.5, 97.5])
        out[name] = {"estimate": float(est), "lo": float(lo), "hi": float(hi), "width": float(hi - lo)}
    return out


def synthetic_predictions(thr=0.2):
    """12 pumps x 4 weeks (starting on a Monday). In week 2 EVERY pump drifts: score 0.3 and a failure ahead. The default policy (0.5) misses all 84 rows, the
    optimal one (thr=0.2) catches them. Elsewhere pumps are healthy, with some borderline scores. The big difference lives in ONE week: the rows are not independent."""
    rng = np.random.default_rng(2026)
    rows = []
    for a, (i, d) in itertools.product([f"PLT01-P{100 + k}" for k in range(1, 13)], enumerate(pd.date_range("2026-08-31", periods=28))):
        drift = i // 7 == 1
        score = 0.3 if drift else (0.3 if rng.random() < 0.06 else rng.uniform(0, 0.1))
        rows.append({"asset_id": a, "feature_date": d, "score": round(float(score), 4), "label_failure_7d": 1.0 if drift else 0.0})
    df = pd.DataFrame(rows)
    df["alert"] = df.score >= thr
    return df


def run_policy_cli(pred_csv, thr, seed, n_boot, out):
    ok(MODULE.exists(), "Missing ml/policy_compare.py (`python -m ml.policy_compare`, see the policy comparison in guide/challenges/R5_lgbm_business.md).")
    r = py(["-m", "ml.policy_compare", "--predictions", str(pred_csv), "--threshold", repr(float(thr)), "--seed", str(seed), "--n-boot", str(n_boot), "--out", str(out)], timeout=300)
    ok(r.returncode == 0 and out.exists(), "`python -m ml.policy_compare --predictions … --threshold … --seed … --n-boot … --out …` failed:\n" + tail(r))
    return json.loads(out.read_text())


def field(res, *path):
    cur = res
    for k in path:
        ok(isinstance(cur, dict) and k in cur, f"The policy comparison JSON is missing `{'.'.join(path)}`.")
        cur = cur[k]
    return cur


def number(res, *path):
    v = field(res, *path)
    ok(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v, f"`{'.'.join(path)}` in the JSON must be a number (it is {v!r}).")
    return float(v)


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    d = tmp_path_factory.mktemp("r5synth")
    df = synthetic_predictions()
    df.to_csv(d / "pred.csv", index=False)
    a, a2, b = (run_policy_cli(d / "pred.csv", 0.2, seed, 2000, d / f"out_{i}.json") for i, seed in enumerate((11, 11, 12)))
    return {"df": df, "a": a, "a2": a2, "b": b}


def test_r5_policy_comparison_is_reproducible_with_the_seed(synth):
    a, a2, b = synth["a"], synth["a2"], synth["b"]
    ok(a == a2, "Two runs with the same seed give different JSON. A result that changes every time you run it can't be audited or reviewed: seed your random number generator and pass it through.")
    ok((int(number(a, "seed")), int(number(a, "n_boot"))) == (11, 2000), f"The JSON must record the seed and n_boot it was run with (11 and 2000); it says seed={a.get('seed')}, n_boot={a.get('n_boot')}.")
    ok(a["cost_diff_per_100_days"] != b["cost_diff_per_100_days"] or a["missed_diff_per_100_days"] != b["missed_diff_per_100_days"],
       "Seeds 11 and 12 give exactly the same interval: the seed is not being used for the resampling.")
    for k in ("cost_diff_per_100_days", "missed_diff_per_100_days"):
        ok(a[k]["estimate"] == b[k]["estimate"], f"The point estimate of `{k}` depends on the seed. The estimate is computed on the real data; only the interval comes from resampling.")


def test_r5_confidence_interval_resamples_blocks_not_rows(synth):
    a, df = synth["a"], synth["df"]
    block = field(a, "block")
    ok(block in BLOCKS, f"`block` = {block!r}: the resampling unit must be a whole `day` or an `asset_week` (7 consecutive days of one pump), never a single row. Rows of one pump on consecutive days share the same future failure.")
    ref = block_bootstrap(df, 0.2, block)
    ok(int(number(a, "n_blocks")) == ref["n_blocks"], f"With {block} blocks this data has {ref['n_blocks']} blocks and your JSON says n_blocks={a['n_blocks']}. Is each draw really made of whole blocks?")
    for key, name in (("cost", "cost_diff_per_100_days"), ("missed", "missed_diff_per_100_days")):
        width = number(a, name, "ci_high") - number(a, name, "ci_low")
        ratio = width / ref[key]["width"]
        ok(ratio >= 0.75, f"The 95% interval of `{name}` is {width:.1f} wide; resampling {block} blocks gives ≈ {ref[key]['width']:.1f} (yours is {ratio:.0%} of that).\n"
           "In this data ALL the difference between the policies comes from one week of strongly correlated rows. An interval this narrow is what resampling individual rows produces: "
           "it treats 336 correlated rows as 336 independent observations and promises certainty you don't have.")
        ok(ratio <= 1.33, f"The 95% interval of `{name}` is {width:.1f} wide, much wider than the ≈ {ref[key]['width']:.1f} I get with {block} blocks. Check the percentiles and that every draw has as many blocks as there are in the data.")


def test_r5_policy_comparison_on_the_test_period(r5_run, tmp_path):
    run, df = r5_run["run"], r5_run["test_predictions.csv"]
    thr = float(p(run, "threshold"))
    pred = tmp_path / "test_predictions.csv"
    df.to_csv(pred, index=False)
    res = run_policy_cli(pred, thr, 7, 2000, tmp_path / "policy_comparison.json")
    ok(number(res, "n_boot") >= R5_MIN_BOOT, f"n_boot = {res['n_boot']}: with fewer than {R5_MIN_BOOT} resamples the interval's bounds move noticeably between seeds.")
    ok(abs(number(res, "ci_level") - R5_CI_LEVEL) < 1e-9, f"`ci_level` must be {R5_CI_LEVEL} (a 95% interval).")
    ok(number(res, "unit_costs", "false_alert") == R5_COST_FALSE_ALERT and number(res, "unit_costs", "missed_failure_day") == R5_COST_MISSED_DAY,
       f"`unit_costs` must echo the agreed costs: {R5_COST_FALSE_ALERT:g} per false alert and {R5_COST_MISSED_DAY:g} per missed failure-day.")
    ok(int(number(res, "n_rows")) == len(df), f"`n_rows` = {res['n_rows']} but the test period has {len(df)} labeled asset-days.")
    block = field(res, "block")
    ok(block in BLOCKS, f"`block` = {block!r}: it must be `day` or `asset_week`.")
    ref = block_bootstrap(df, thr, block)
    # point estimates, recomputed from the predictions
    fa_d, mi_d, fa_o, mi_o, n = ref["counts"]
    for pol, thr_p, fa, mi in (("default", 0.5, fa_d, mi_d), ("optimal", thr, fa_o, mi_o)):
        got = field(res, "policies", pol)
        ok(abs(number(got, "threshold") - thr_p) < 1e-9, f"The `{pol}` policy must use threshold {thr_p:g} (the optimal one is the `threshold` of your MLflow run); the JSON says {got['threshold']}.")
        ok((int(number(got, "false_alerts")), int(number(got, "missed_failure_days"))) == (int(fa), int(mi)),
           f"Policy `{pol}` (threshold {thr_p:.4g}): the JSON says {got['false_alerts']} false alerts and {got['missed_failure_days']} missed failure-days; from the predictions I count {int(fa)} and {int(mi)}.\n"
           "False alert: alert on a row with label 0. Missed failure-day: label 1 and no alert.")
        want = 100 * (R5_COST_FALSE_ALERT * fa + R5_COST_MISSED_DAY * mi) / n
        ok(abs(number(got, "cost_per_100_days") - want) < 1e-3, f"Policy `{pol}`: cost_per_100_days = {got['cost_per_100_days']:.3f} but the agreed costs give {want:.3f} per 100 asset-days.")
    for key, name in (("cost", "cost_diff_per_100_days"), ("missed", "missed_diff_per_100_days")):
        est, lo, hi = number(res, name, "estimate"), number(res, name, "ci_low"), number(res, name, "ci_high")
        ok(abs(est - ref[key]["estimate"]) < 1e-3, f"`{name}.estimate` = {est:.3f}; recomputed from your predictions (default minus optimal, per 100 asset-days) it is {ref[key]['estimate']:.3f}.")
        ok(lo < hi, f"`{name}`: ci_low ({lo:.3f}) must be below ci_high ({hi:.3f}).")
        ok(lo <= est <= hi, f"`{name}`: the interval [{lo:.2f}, {hi:.2f}] doesn't contain its own point estimate ({est:.2f}). A bootstrap interval is built around the estimate on the real data.")
        ok(0.4 * ref[key]["width"] <= hi - lo <= 2.5 * ref[key]["width"],
           f"`{name}`: your interval is {hi - lo:.2f} wide and resampling {block} blocks of this test period gives ≈ {ref[key]['width']:.2f}. The width is not plausible.")
    c = res["cost_diff_per_100_days"]
    verdict = "the interval contains 0: not conclusive" if c["ci_low"] <= 0 <= c["ci_high"] else "the interval excludes 0"
    print(f"\n  default vs optimal on test: cost saved per 100 asset-days = {c['estimate']:.2f}  95% CI [{c['ci_low']:.2f}, {c['ci_high']:.2f}] ({block} blocks, {verdict})")
