"""Monthly reliability report per pump (Marta uses it in the Monday meeting).

Written by the previous team in 2025. It has worked "forever".
Columns: month, asset_id, availability_pct, n_failures, operating_hours, mtbf_hours, vib_p95
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PLANT_TZ = "Europe/Madrid"  # historian server time
SLOT_HOURS = 10 / 60  # the historian stores one reading every 10 minutes


def load_readings(history_dir: Path) -> pd.DataFrame:
    files = sorted(history_dir.glob("date=*/readings.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df[df["quality"] == "GOOD"].drop_duplicates("msg_id")
    df["month"] = df["ts"].dt.tz_convert(PLANT_TZ).dt.strftime("%Y-%m")
    return df


def load_work_orders(csv: Path) -> pd.DataFrame:
    wo = pd.read_csv(csv, parse_dates=["opened_at", "closed_at"])
    wo = wo[wo["wo_type"] == "CORRECTIVE"].copy()
    wo["month"] = wo["opened_at"].dt.tz_convert(PLANT_TZ).dt.strftime("%Y-%m")
    return wo


def month_hours(month: str) -> float:
    start = pd.Timestamp(f"{month}-01")
    return ((start + pd.offsets.MonthBegin(1)) - start).days * 24.0


def build_report(month: str, history_dir: Path | None = None, work_orders_csv: Path | None = None) -> pd.DataFrame:
    history_dir = history_dir or ROOT / "data" / "history"
    work_orders_csv = work_orders_csv or ROOT / "platform" / "seeds" / "work_orders.csv"
    readings = load_readings(history_dir)
    readings = readings[readings["month"] == month]
    wo = load_work_orders(work_orders_csv)
    wo = wo[wo["month"] == month]

    rows = []
    for asset_id, r in readings.groupby("asset_id"):
        failures = wo[wo["asset_id"] == asset_id]
        downtime = (failures["closed_at"] - failures["opened_at"]).dt.seconds.sum() / 3600
        running = r[(r["sensor"] == "RUN_STATE") & (r["value"] == 1)]
        operating_hours = len(r[r["ts"].isin(running["ts"])]) * SLOT_HOURS
        vib = r[r["sensor"] == "VIB_MM_S"]["value"]
        rows.append({
            "month": month,
            "asset_id": asset_id,
            "availability_pct": round(100 * (1 - downtime / month_hours(month)), 2),
            "n_failures": len(failures),
            "operating_hours": round(operating_hours, 2),
            "mtbf_hours": round(operating_hours / len(failures), 2) if len(failures) else None,
            "vib_p95": round(float(vib.quantile(0.95)), 4),
        })
    return pd.DataFrame(rows).sort_values("asset_id").reset_index(drop=True)
