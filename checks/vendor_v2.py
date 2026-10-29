"""The new sensor gateway vendor (challenge R1). It is "the plant": you don't need to read it.

During the migration two formats live together on the same raw topic, as happens in real life:
  - v1 (the usual one, module 2);
  - v2 (new gateway): another schema and other units, documented in guide/challenges/R1_new_vendor.md;
  - messages from a version nobody announced;
  - readings that arrive late (communication gaps recovered days later);
  - and, during the cutover, the same reading sent by both gateways (same msg_id).

To look at the messages:  uv run python checks/vendor_v2.py --n 3000   (publishes to KAFKA_TOPIC_RAW)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "sim"))
from physics import ASSET_IDS, PLANT, SENSORS, signals  # noqa: E402

T0 = datetime(2026, 10, 20, tzinfo=UTC)
RANGES = {"VIB_MM_S": (0.0, 30.0), "TEMP_BEARING": (-20.0, 150.0), "PRESS_DISCH": (0.0, 1500.0), "CURRENT_A": (0.0, 300.0), "RUN_STATE": (0.0, 1.0)}
V2_UNIT = {"VIB_MM_S": "in/s", "TEMP_BEARING": "degF", "PRESS_DISCH": "kPa", "CURRENT_A": "A", "RUN_STATE": "bool"}
V2_DEFECTS = ["unknown_unit", "out_of_range_v2", "missing_measurement"]


def to_v2_value(sensor: str, value: float) -> float:
    if sensor == "VIB_MM_S":
        return value / 25.4
    if sensor == "TEMP_BEARING":
        return value * 9 / 5 + 32
    return value


def v1(asset: str, sensor: str, ts: datetime, value: float) -> dict:
    return {"msg_id": f"{asset}|{sensor}|{int(ts.timestamp())}", "asset_id": asset, "plant": PLANT, "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sensor": sensor, "value": round(value, 4), "unit": SENSORS[sensor], "quality": "GOOD"}


def v2(asset: str, sensor: str, ts: datetime, value: float, version: int = 2) -> dict:
    return {"schema_version": version, "id": f"{asset}|{sensor}|{int(ts.timestamp())}", "device": {"asset": asset, "site": PLANT},
            "event_time_ms": int(ts.timestamp()) * 1000, "measurement": {"tag": sensor, "val": round(to_v2_value(sensor, value), 6), "uom": V2_UNIT[sensor]},
            "status": "OK"}


def burst(topic: str, n: int = 3000, seed: int = 21, bootstrap: str | None = None) -> dict:
    """Publishes n messages and returns the manifest: what must end up in clean (with its canonical value) and what in the DLQ."""
    from confluent_kafka import Producer

    rnd = random.Random(seed)
    rngs = {a: np.random.default_rng(seed * 100 + i) for i, a in enumerate(ASSET_IDS)}
    prod = Producer({"bootstrap.servers": bootstrap or os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092"), "enable.idempotence": True, "linger.ms": 5})
    man: dict = {"total_sent": 0, "clean": {}, "dlq": {"unknown_schema": 0, **{k: 0 for k in V2_DEFECTS}}, "v1": 0, "v2": 0, "cutover_duplicates": 0, "late": 0}
    late: list[tuple[str, str, datetime, float]] = []

    def send(asset: str, payload: dict | bytes) -> None:
        prod.produce(topic, key=asset, value=payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        man["total_sent"] += 1

    def emit(asset: str, sensor: str, ts: datetime, value: float) -> None:
        canon = v1(asset, sensor, ts, value)
        r = rnd.random()
        if r < 0.01:  # a version nobody announced
            send(asset, v2(asset, sensor, ts, value, version=3))
            man["dlq"]["unknown_schema"] += 1
            return
        if r < 0.03:  # defects specific to the new format
            kind = V2_DEFECTS[int((r - 0.01) / 0.02 * 3)]
            m = v2(asset, sensor, ts, value)
            if kind == "unknown_unit":
                m["measurement"]["uom"] = rnd.choice(["furlong/s", "mm", "%"])
            elif kind == "out_of_range_v2":
                m["measurement"]["val"] = round(to_v2_value(sensor, RANGES[sensor][1] * 4 + 50), 4)
            else:
                m.pop("measurement")
            send(asset, m)
            man["dlq"][kind] += 1
            return
        use_v2 = rnd.random() < 0.45
        send(asset, v2(asset, sensor, ts, value) if use_v2 else canon)
        man["v2" if use_v2 else "v1"] += 1
        man["clean"][canon["msg_id"]] = {"value": canon["value"], "ts": canon["ts"], "unit": canon["unit"], "asset_id": asset, "sensor": sensor}
        if rnd.random() < 0.04:  # the cutover: the other gateway sends the same reading
            send(asset, canon if use_v2 else v2(asset, sensor, ts, value))
            man["cutover_duplicates"] += 1

    t_h = 0.0
    while man["total_sent"] < n:
        ts = T0 + timedelta(hours=t_h)
        for a in ASSET_IDS:
            vals = signals(a, np.array([t_h]), rngs[a], [])
            for s, v in vals.items():
                if man["total_sent"] >= n:
                    break
                if rnd.random() < 0.03:  # communication gap: this reading will arrive late
                    late.append((a, s, ts, float(v[0])))
                    continue
                emit(a, s, ts, float(v[0]))
        t_h += 10 / 60
        prod.poll(0)
    for a, s, ts, v in late:  # the gaps are recovered at the end (days later in plant time)
        emit(a, s, ts, v)
        man["late"] += 1
    prod.flush()
    man["valid_unique"] = len(man["clean"])
    return man


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Burst from the new gateway (v1 + v2 mixed)")
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--topic", default=os.environ.get("KAFKA_TOPIC_RAW", "plant.readings.raw"))
    ap.add_argument("--seed", type=int, default=21)
    a = ap.parse_args()
    m = burst(a.topic, a.n, a.seed)
    print(json.dumps({k: v for k, v in m.items() if k != "clean"}))
