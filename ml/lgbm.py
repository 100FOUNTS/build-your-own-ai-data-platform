"""Challenge R5 — LightGBM with a threshold chosen on validation and the metrics maintenance asks for."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb
import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

KEYS = ["asset_id", "feature_date", "label_failure_7d"]
GAP = pd.Timedelta(days=7)
TARGET_FA = 5.0  # false alerts per 100 healthy asset-days
PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 7,
    "min_child_samples": 10,
    "n_estimators": 300,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "random_state": 7,
    "verbose": -1,
}


def load() -> pd.DataFrame:
    con = duckdb.connect(os.environ.get("WAREHOUSE_PATH", "data/warehouse.duckdb"), read_only=True)
    g = con.execute("select * from asset_daily_features").df()
    con.close()
    g["feature_date"] = pd.to_datetime(g["feature_date"])
    return g.sort_values(["feature_date", "asset_id"]).reset_index(drop=True)


def fa_per_100(labels: pd.Series, alerts: pd.Series) -> float:
    healthy = labels == 0
    return 100 * float((alerts & healthy).sum()) / max(int(healthy.sum()), 1)


def pick_threshold(val: pd.DataFrame) -> float:
    """The lowest (most sensitive) threshold that meets the false-alert target on validation."""
    for thr in np.unique(val["score"]):
        if fa_per_100(val["label_failure_7d"], val["score"] >= thr) <= TARGET_FA:
            return float(thr)
    return float(val["score"].max()) + 1e-9


def early_detection(df: pd.DataFrame) -> tuple[int, int]:
    wo = pd.read_csv(
        Path(__file__).resolve().parents[1] / "platform/seeds/work_orders.csv", parse_dates=["opened_at"]
    )
    wo = wo[wo["wo_type"] == "CORRECTIVE"]
    lo_t, hi_t = df["feature_date"].min(), df["feature_date"].max()
    hit = total = 0
    for _, w in wo.iterrows():
        f = w["opened_at"].tz_convert("UTC").tz_localize(None).normalize()
        lo, hi = f - pd.Timedelta(days=7), f - pd.Timedelta(days=3)
        if lo < lo_t or hi > hi_t:
            continue
        total += 1
        hit += bool(df[(df["asset_id"] == w["asset_id"]) & df["feature_date"].between(lo, hi)]["alert"].any())
    return hit, total


def main() -> None:
    g = load()
    feats = [c for c in g.columns if c not in KEYS]
    labelled = g[g["label_failure_7d"].notna()]
    last = labelled["feature_date"].max()
    test_start = last - pd.Timedelta(days=24)
    val_end = test_start - GAP
    val_start = val_end - pd.Timedelta(days=20)
    train_end = val_start - GAP
    train = labelled[labelled["feature_date"] <= train_end]
    val = labelled[labelled["feature_date"].between(val_start, val_end)].copy()
    test = labelled[labelled["feature_date"] >= test_start].copy()

    mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "pump-risk"))
    with mlflow.start_run(run_name="lightgbm"):
        model = lgb.LGBMClassifier(**PARAMS).fit(train[feats], train["label_failure_7d"])
        val["score"] = model.predict_proba(val[feats])[:, 1]
        thr = pick_threshold(val)
        # the threshold holds for THIS model: if you retrained on train+val, its scores change and it would need recalibrating
        test["score"] = model.predict_proba(test[feats])[:, 1]
        for df in (val, test):
            df["alert"] = df["score"] >= thr
        hit, total = early_detection(test)
        mlflow.log_params(
            {
                "model_type": "lightgbm",
                "train_end": train_end.date(),
                "val_start": val_start.date(),
                "val_end": val_end.date(),
                "test_start": test_start.date(),
                "threshold": thr,
                "threshold_selected_on": "validation",
                **PARAMS,
            }
        )
        mlflow.log_metrics(
            {
                "average_precision": average_precision_score(test["label_failure_7d"], test["score"]),
                "baseline_average_precision": float(test["label_failure_7d"].mean()),
                "false_alerts_per_100_healthy": fa_per_100(test["label_failure_7d"], test["alert"]),
                "val_false_alerts_per_100_healthy": fa_per_100(val["label_failure_7d"], val["alert"]),
                "early_detected": hit,
                "early_total": total,
            }
        )
        # why each alert fires: LightGBM SHAP contributions (pred_contrib), the 3 with the largest |value|
        alerts = test[test["alert"]]
        contrib = (
            model.predict(alerts[feats], pred_contrib=True)[:, :-1]
            if len(alerts)
            else np.empty((0, len(feats)))
        )
        expl = []
        for (_, row), c in zip(alerts.iterrows(), contrib):
            for rank, i in enumerate(np.argsort(-np.abs(c))[:3], start=1):
                expl.append(
                    {
                        "asset_id": row["asset_id"],
                        "feature_date": row["feature_date"].date(),
                        "rank": rank,
                        "feature": feats[i],
                        "contribution": float(c[i]),
                    }
                )
        with tempfile.TemporaryDirectory() as d:
            cols = ["asset_id", "feature_date", "score", "label_failure_7d", "alert"]
            val[cols].to_csv(Path(d) / "val_predictions.csv", index=False)
            test[cols].to_csv(Path(d) / "test_predictions.csv", index=False)
            pd.DataFrame(
                expl, columns=["asset_id", "feature_date", "rank", "feature", "contribution"]
            ).to_csv(Path(d) / "alert_explanations.csv", index=False)
            mlflow.log_artifacts(d)
        mlflow.lightgbm.log_model(model, name="model", input_example=test[feats].head(2))
        print(
            f"threshold={thr:.3f} · test: FA/100={fa_per_100(test['label_failure_7d'], test['alert']):.1f} · warned {hit}/{total}"
        )


if __name__ == "__main__":
    main()
