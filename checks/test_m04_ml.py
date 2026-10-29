"""Module 4 — ML: pandas + scikit-learn + PyTorch, experiments in MLflow, champion registration and scoring into Postgres."""

from __future__ import annotations

import os

import duckdb
import mlflow
import pandas as pd
import psycopg
import pytest
from mlflow import MlflowClient

from conftest import ROOT, need, ok, py, seeded_db, tail, unique
from expected import expected_gold


@pytest.fixture(scope="module")
def training(tmp_path_factory):
    need("mlflow", "s3", "postgres")
    wh = tmp_path_factory.mktemp("ml") / "wh.duckdb"
    g = expected_gold()  # gold table computed independently: this module doesn't depend on your module 3 being perfect
    con = duckdb.connect(str(wh))
    con.register("g", g)
    con.execute("create table asset_daily_features as select * from g")
    con.close()
    ok((ROOT / "ml" / "train.py").exists(), "Missing ml/train.py (see guide/04_ml.md).")
    exp, model = unique("pump-risk"), unique("pump-risk-model")
    with seeded_db() as url:
        env = {"WAREHOUSE_PATH": str(wh), "MLFLOW_EXPERIMENT": exp, "MODEL_NAME": model, "DATABASE_URL": url}
        r = py(["-m", "ml.train"], env=env, timeout=900)
        ok(r.returncode == 0, "`python -m ml.train` failed:\n" + tail(r, 16))
        yield {"exp": exp, "model": model, "url": url, "env": env, "gold_rows": len(g)}


def runs(exp):
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    e = mlflow.get_experiment_by_name(exp)
    ok(e, f"The experiment `{exp}` doesn't exist in MLflow: use the name from the MLFLOW_EXPERIMENT variable (default “pump-risk”).")
    return mlflow.search_runs([e.experiment_id], output_format="pandas")


def test_two_models_logged_as_experiments(training):
    df = runs(training["exp"])
    df = df[df["status"] == "FINISHED"]
    ok(len(df) >= 2, f"At least 2 finished runs are expected in MLflow (there are {len(df)}): a classic model and a deep learning one, to compare them.")
    types = set(df["params.model_type"].dropna())
    ok({"sklearn", "autoencoder"} <= types, f"The logged `model_type` params are {sorted(types)}; `sklearn` and `autoencoder` (PyTorch) are expected.")
    for col in ("metrics.average_precision", "params.train_end", "params.test_start"):
        ok(col in df.columns and df[col].notna().all(), f"Every run must log `{col.split('.')[1]}` (reproducibility: without params you can't explain a result).")


def test_honest_time_split(training):
    df = runs(training["exp"])
    for _, r in df.iterrows():
        gap = (pd.Timestamp(r["params.test_start"]) - pd.Timestamp(r["params.train_end"])).days
        ok(gap >= 7, f"There are {gap} days between the end of training and the start of test (< 7). The label looks 7 days ahead: without a gap, you train on answers from the test period.")


def test_autoencoder_loss_curve(training):
    df = runs(training["exp"])
    ae = df[df["params.model_type"] == "autoencoder"].iloc[0]
    hist = MlflowClient().get_metric_history(ae["run_id"], "train_loss")
    ok(len(hist) >= 5, f"The autoencoder must log `train_loss` per epoch (there are {len(hist)} points; ≥ 5 expected).")
    hist.sort(key=lambda m: m.step)
    ok(hist[-1].value < hist[0].value, f"The loss doesn't go down ({hist[0].value:.3f} → {hist[-1].value:.3f}): the autoencoder is not learning.")


def test_champion_in_the_model_registry(training):
    c = MlflowClient()
    name = training["model"]
    try:
        mv = c.get_model_version_by_alias(name, "champion")
    except Exception as exc:  # noqa: BLE001
        ok(False, f"There is no registered model `{name}` with the alias `champion` ({exc}).\nRegister the best model in the Model Registry and mark it as champion: it is what a production service would load.")
    df = runs(training["exp"])
    best = df.sort_values("metrics.average_precision", ascending=False).iloc[0]
    ok(mv.run_id == best["run_id"], "The champion is NOT the model with the highest average_precision on test. The promotion criterion must be measurable and explicit.")
    ap, base = float(best["metrics.average_precision"]), float(best["metrics.baseline_average_precision"])
    ok(ap >= max(0.25, 3 * base), f"The champion has AP={ap:.2f}; chance would give {base:.2f}. At least {max(0.25, 3 * base):.2f} is expected. Review how you prepare the features (nulls, scaling) and the split.")


def test_scores_published_in_postgres(training):
    with psycopg.connect(training["url"]) as con:
        n, mn, mx, versions = con.execute("select count(*), min(risk_score), max(risk_score), array_agg(distinct model_version) from risk_scores").fetchone()
        last = con.execute("select count(distinct asset_id) from risk_scores where feature_date = (select max(feature_date) from risk_scores)").fetchone()[0]
    ok(n == training["gold_rows"], f"risk_scores has {n} rows and gold has {training['gold_rows']}: score ALL rows, including the last 7 days without a label (they are the ones that matter “today”).")
    ok(0 <= mn and mx <= 1, f"risk_score outside [0,1]: min={mn}, max={mx}.")
    ok(last == 12, f"On the last day there are scores for {last} assets and there must be 12.")
    ok(len(versions) == 1 and ":" in versions[0] or len(versions) == 1, f"model_version should identify the champion (name and version); there is {versions}.")


def test_rerunning_is_safe(training):
    r = py(["-m", "ml.train"], env=training["env"], timeout=900)
    ok(r.returncode == 0, "A second run of ml.train failed (key conflict in risk_scores? use an UPSERT):\n" + tail(r))
    with psycopg.connect(training["url"]) as con:
        n = con.execute("select count(*) from risk_scores").fetchone()[0]
    ok(n == training["gold_rows"], f"Rerunning duplicated/changed rows in risk_scores ({n}).")
