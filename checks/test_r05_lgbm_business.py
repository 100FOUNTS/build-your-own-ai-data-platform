"""Challenge R5 (extends module 4) — LightGBM with the metric the business cares about: false alerts, early warning and the why of each alert."""

from __future__ import annotations

import io
import os

import duckdb
import httpx
import mlflow
import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from conftest import ROOT, need, ok, py, tail, unique
from expected import expected_gold

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
        arts[name] = pd.read_csv(io.StringIO(r.text))
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
