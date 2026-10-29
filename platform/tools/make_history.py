"""Generates the plant history: 90 days (2026-07-03 → 2026-09-30), 12 pumps, 5 sensors every 10 min.

Output:  data/history/date=YYYY-MM-DD/readings.parquet      (readings, same schema as the Kafka messages)
         platform/seeds/assets.csv, platform/seeds/work_orders.csv   (pump master data and work orders)
You don't need to read it: it is "the plant's history".
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "sim"))
from physics import ASSET_IDS, CRITICALITY, MODEL_OF, PLANT, SENSORS, Degradation, signals  # noqa: E402

DAYS = 90
START = datetime(2026, 7, 3, tzinfo=UTC)  # the live simulator starts on 2026-10-01 (the day after the last day of the history)


def main() -> None:
    rng = np.random.default_rng(11)
    n = DAYS * 144
    t_h = np.arange(n) / 6.0
    ts = pd.date_range(START, periods=n, freq="10min", tz="UTC")
    degs: list[Degradation] = []
    wos: list[dict] = []
    stops: dict[str, list[tuple[float, float]]] = {a: [] for a in ASSET_IDS}
    for a in ASSET_IDS:
        times: list[float] = []
        for _ in range(int(rng.choice([0, 1, 2], p=[0.15, 0.55, 0.30]))):
            for _try in range(20):
                t = float(rng.uniform(15 * 24, DAYS * 24 - 30))
                if all(abs(t - u) > 30 * 24 for u in times):
                    times.append(t)
                    break
        for tf in sorted(times):
            mode = str(rng.choice(["bearing", "seal", "cavitation"], p=[0.45, 0.30, 0.25]))
            lo, hi = {"bearing": (14, 20), "seal": (10, 16), "cavitation": (6, 10)}[mode]
            down = float(rng.uniform(12, 60))
            degs.append(Degradation(a, mode, tf - float(rng.uniform(lo, hi)) * 24, tf, down))
            cost = {"bearing": (3500, 6000), "seal": (1800, 3000), "cavitation": (2500, 4500)}[mode]
            wos.append(dict(asset_id=a, wo_type="CORRECTIVE", opened_at=START + timedelta(hours=tf), closed_at=START + timedelta(hours=tf + down),
                            failure_mode=mode, cost_usd=round(float(rng.uniform(*cost)), 2), downtime_hours=round(down, 1), description=f"{mode.capitalize()} failure"))
        t = float(rng.uniform(10 * 24, 40 * 24))
        while t < DAYS * 24 - 24:
            d = float(rng.uniform(3, 6))
            stops[a].append((t, t + d))
            wos.append(dict(asset_id=a, wo_type="PREVENTIVE", opened_at=START + timedelta(hours=t), closed_at=START + timedelta(hours=t + d),
                            failure_mode=None, cost_usd=round(float(rng.uniform(250, 600)), 2), downtime_hours=round(d, 1), description="Scheduled preventive maintenance"))
            t += float(rng.uniform(45, 60)) * 24
        t = float(rng.uniform(5 * 24, 25 * 24))
        while t < DAYS * 24 - 24:
            wos.append(dict(asset_id=a, wo_type="INSPECTION", opened_at=START + timedelta(hours=t), closed_at=START + timedelta(hours=t + 1),
                            failure_mode=None, cost_usd=round(float(rng.uniform(40, 120)), 2), downtime_hours=0.0, description="Inspection round"))
            t += float(rng.uniform(28, 34)) * 24

    frames = []
    for a in ASSET_IDS:
        vals = signals(a, t_h, np.random.default_rng(1000 + int(a[-3:])), degs, stops[a])
        for s, v in vals.items():
            frames.append(pd.DataFrame({"ts": ts, "asset_id": a, "sensor": s, "value": np.round(v, 4)}))
    df = pd.concat(frames, ignore_index=True)
    df["plant"] = PLANT
    df["unit"] = df["sensor"].map(SENSORS)
    df["quality"] = "GOOD"
    df["msg_id"] = df["asset_id"] + "|" + df["sensor"] + "|" + ((df["ts"] - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype(str)
    df = df[["msg_id", "asset_id", "plant", "ts", "sensor", "value", "unit", "quality"]].sort_values(["ts", "asset_id", "sensor"])
    out = ROOT / "data" / "history"
    for d, part in df.groupby(df["ts"].dt.strftime("%Y-%m-%d")):
        p = out / f"date={d}"
        p.mkdir(parents=True, exist_ok=True)
        part.to_parquet(p / "readings.parquet", index=False)

    seeds = ROOT / "platform" / "seeds"
    seeds.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"asset_id": ASSET_IDS, "plant": PLANT, "model": [MODEL_OF[a] for a in ASSET_IDS], "criticality": [CRITICALITY[a] for a in ASSET_IDS],
         "install_date": [str((START - timedelta(days=int(rng.integers(300, 2500)))).date()) for _ in ASSET_IDS],
         "rated_flow_m3h": [float(rng.choice([90, 180, 250, 320])) for _ in ASSET_IDS], "rated_kw": [float(rng.choice([11.0, 18.5, 30.0, 45.0])) for _ in ASSET_IDS]}
    ).to_csv(seeds / "assets.csv", index=False)
    wo = pd.DataFrame(wos).sort_values("opened_at").reset_index(drop=True)
    wo.insert(0, "wo_id", [f"WO-{k + 1:04d}" for k in range(len(wo))])
    for c in ("opened_at", "closed_at"):
        wo[c] = wo[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    wo.to_csv(seeds / "work_orders.csv", index=False)
    print(f"{len(df):,} readings over {DAYS} days · {len(ASSET_IDS)} pumps · {int((wo.wo_type == 'CORRECTIVE').sum())} failures · {len(wo)} work orders")


if __name__ == "__main__":
    main()
