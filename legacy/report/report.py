"""Monthly reliability report per pump (Marta uses it in the Monday meeting).

Written by the previous team in 2025, fixed in ticket INF-2026-10.
Columns: month, asset_id, availability_pct, n_failures, operating_hours, mtbf_hours, vib_p95
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BUSINESS_TZ = "UTC"  # the whole platform cuts days and months in UTC (before: Europe/Madrid, the server time)
SLOT_HOURS = 10 / 60  # the historian stores one reading every 10 minutes


def load_readings(history_dir: Path) -> pd.DataFrame:
    files = sorted(history_dir.glob("date=*/readings.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df[df["quality"] == "GOOD"].drop_duplicates("msg_id")
    df["month"] = df["ts"].dt.tz_convert(BUSINESS_TZ).dt.strftime("%Y-%m")
    return df


def load_work_orders(csv: Path) -> pd.DataFrame:
    wo = pd.read_csv(csv, parse_dates=["opened_at", "closed_at"])
    return wo[wo["wo_type"] == "CORRECTIVE"].copy()


def month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{month}-01", tz=BUSINESS_TZ)
    return start, start + pd.offsets.MonthBegin(1)


def downtime_hours(failures: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Failure downtime hours WITHIN the month: a stoppage that crosses months is split between the two."""
    lo = failures["opened_at"].clip(lower=start, upper=end)
    hi = failures["closed_at"].clip(lower=start, upper=end)
    return float((hi - lo).dt.total_seconds().clip(lower=0).sum() / 3600)  # total_seconds, not .seconds (which ignores the days)


def build_report(month: str, history_dir: Path | None = None, work_orders_csv: Path | None = None) -> pd.DataFrame:
    history_dir = history_dir or ROOT / "data" / "history"
    work_orders_csv = work_orders_csv or ROOT / "platform" / "seeds" / "work_orders.csv"
    readings = load_readings(history_dir)
    readings = readings[readings["month"] == month]
    wo = load_work_orders(work_orders_csv)
    start, end = month_bounds(month)
    hours = (end - start).total_seconds() / 3600

    rows = []
    for asset_id, r in readings.groupby("asset_id"):
        asset_wo = wo[wo["asset_id"] == asset_id]
        failures = asset_wo[(asset_wo["opened_at"] >= start) & (asset_wo["opened_at"] < end)]
        downtime = downtime_hours(asset_wo, start, end)
        running = r[(r["sensor"] == "RUN_STATE") & (r["value"] == 1)]
        operating_hours = len(running) * SLOT_HOURS  # one RUN_STATE row per instant (before it counted the 5 of every sensor)
        vib = r[(r["sensor"] == "VIB_MM_S") & r["ts"].isin(running["ts"])]["value"]  # only while running
        rows.append({
            "month": month,
            "asset_id": asset_id,
            "availability_pct": round(100 * (1 - downtime / hours), 2),
            "n_failures": len(failures),
            "operating_hours": round(operating_hours, 2),
            "mtbf_hours": round(operating_hours / len(failures), 2) if len(failures) else None,
            "vib_p95": round(float(vib.quantile(0.95)), 4),
        })
    return pd.DataFrame(rows).sort_values("asset_id").reset_index(drop=True)
