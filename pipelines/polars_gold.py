"""Challenge R3 — the gold layer (same definitions as module 3) with Polars in lazy mode."""

from __future__ import annotations

from pathlib import Path

import polars as pl

SENSORS = {
    "VIB_MM_S": "vib",
    "TEMP_BEARING": "temp",
    "PRESS_DISCH": "press",
    "CURRENT_A": "curr",
    "RUN_STATE": "run",
}


def build_gold(history_dir: str | Path, work_orders_csv: str | Path) -> pl.DataFrame:
    readings = (
        pl.scan_parquet(str(Path(history_dir) / "date=*" / "readings.parquet"), hive_partitioning=False)
        .filter(pl.col("quality") == "GOOD")
        .select("msg_id", "asset_id", "ts", "sensor", "value")
        .unique("msg_id", keep="first")
    )
    # one row per (asset, instant) with the 5 sensors as columns
    wide = (
        readings.group_by("asset_id", "ts")
        .agg([pl.col("value").filter(pl.col("sensor") == s).first().alias(c) for s, c in SENSORS.items()])
        .with_columns(
            feature_date=pl.col("ts").dt.convert_time_zone("UTC").dt.replace_time_zone(None).dt.truncate("1d")
        )
    )
    on = pl.col("run") == 1
    daily = (
        wide.group_by("asset_id", "feature_date")
        .agg(
            run_share=pl.col("run").mean(),
            vib_mean=pl.col("vib").filter(on).mean(),
            vib_max=pl.col("vib").filter(on).max(),
            vib_std=pl.col("vib").filter(on).std(ddof=1),
            temp_mean=pl.col("temp").filter(on).mean(),
            temp_max=pl.col("temp").filter(on).max(),
            press_mean=pl.col("press").filter(on).mean(),
            press_std=pl.col("press").filter(on).std(ddof=1),
            curr_mean=pl.col("curr").filter(on).mean(),
        )
        .sort("asset_id", "feature_date")
    )
    daily = daily.with_columns(
        vib_mean_7d=pl.col("vib_mean").rolling_mean(7, min_samples=1).over("asset_id"),
        **{
            f"{c}_delta_28d": pl.col(f"{c}_mean")
            - pl.col(f"{c}_mean").shift(1).rolling_mean(28, min_samples=1).over("asset_id")
            for c in ("vib", "temp", "press")
        },
    )
    wo = (
        pl.scan_csv(str(work_orders_csv))
        .filter(pl.col("wo_type") == "CORRECTIVE")
        .select(
            "asset_id",
            fail_date=pl.col("opened_at")
            .str.to_datetime(time_zone="UTC")
            .dt.replace_time_zone(None)
            .dt.truncate("1d"),
        )
    )
    last = pl.col("feature_date").max()
    labelled = (
        daily.join(wo, on="asset_id", how="left")
        .with_columns(
            hit=(pl.col("fail_date") > pl.col("feature_date"))
            & (pl.col("fail_date") <= pl.col("feature_date") + pl.duration(days=7))
        )
        .group_by("asset_id", "feature_date")
        .agg(pl.col("hit").fill_null(False).any().cast(pl.Float64).alias("label"))
    )
    out = (
        daily.join(labelled, on=["asset_id", "feature_date"])
        .with_columns(
            label_failure_7d=pl.when(pl.col("feature_date") > last - pl.duration(days=7))
            .then(None)
            .otherwise(pl.col("label"))
        )
        .drop("label")
    )
    return out.sort("asset_id", "feature_date").collect()
