"""Independent computation (pandas) of what the lakehouse layers MUST produce, from the history and the seeds."""

from __future__ import annotations

import numpy as np
import pandas as pd

from conftest import ROOT

FEATURES = ["run_share", "vib_mean", "vib_max", "vib_std", "temp_mean", "temp_max", "press_mean", "press_std", "curr_mean",
            "vib_mean_7d", "vib_delta_28d", "temp_delta_28d", "press_delta_28d"]


def expected_gold(history_dir=None, work_orders_csv=None) -> pd.DataFrame:
    history_dir = history_dir or ROOT / "data" / "history"
    h = pd.concat([pd.read_parquet(p) for p in sorted(history_dir.glob("date=*/readings.parquet"))], ignore_index=True)
    h = h[h.quality == "GOOD"].drop_duplicates("msg_id")
    w = h.pivot_table(index=["asset_id", "ts"], columns="sensor", values="value", aggfunc="first").reset_index()
    w["feature_date"] = w["ts"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    run = w[w["RUN_STATE"] == 1]
    g = w.groupby(["asset_id", "feature_date"]).agg(run_share=("RUN_STATE", "mean")).join(
        run.groupby(["asset_id", "feature_date"]).agg(
            vib_mean=("VIB_MM_S", "mean"), vib_max=("VIB_MM_S", "max"), vib_std=("VIB_MM_S", "std"), temp_mean=("TEMP_BEARING", "mean"),
            temp_max=("TEMP_BEARING", "max"), press_mean=("PRESS_DISCH", "mean"), press_std=("PRESS_DISCH", "std"), curr_mean=("CURRENT_A", "mean")),
        how="left").reset_index().sort_values(["asset_id", "feature_date"])
    parts = []
    for _, d in g.groupby("asset_id"):
        d = d.copy()
        d["vib_mean_7d"] = d["vib_mean"].rolling(7, min_periods=1).mean()
        for c in ("vib", "temp", "press"):
            d[f"{c}_delta_28d"] = d[f"{c}_mean"] - d[f"{c}_mean"].shift(1).rolling(28, min_periods=1).mean()
        parts.append(d)
    g = pd.concat(parts)
    wo = pd.read_csv(work_orders_csv or ROOT / "platform/seeds/work_orders.csv", parse_dates=["opened_at"])
    wo = wo[wo.wo_type == "CORRECTIVE"]
    fails = {a: [x.tz_convert("UTC").tz_localize(None).normalize() for x in grp.opened_at] for a, grp in wo.groupby("asset_id")}
    last = g["feature_date"].max()
    g["label_failure_7d"] = [np.nan if d > last - pd.Timedelta(days=7) else float(any(d < f <= d + pd.Timedelta(days=7) for f in fails.get(a, [])))
                             for a, d in zip(g.asset_id, g.feature_date)]
    return g.reset_index(drop=True)
