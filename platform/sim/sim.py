"""Simulator of plant PLT01: publishes sensor readings to Kafka (Redpanda).

You don't need to read this file: it is "the plant". What you DO need to know is in the module 2 guide:
  - JSON messages with msg_id, asset_id, plant, ts, sensor, value, unit, quality;
  - the message key is asset_id;
  - as in real life, not every message that arrives is good.

Modes:
  live                 accelerated continuous stream (1 simulated day ≈ 10 s)  [--inject PLT01-P107:bearing:12]
  burst --n 5000       N messages as fast as possible, deterministic; prints a JSON manifest (for the checks)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import UTC, datetime, timedelta

import numpy as np
from confluent_kafka import Producer

from physics import ASSET_IDS, PLANT, SENSORS, Degradation, signals

T0 = datetime(2026, 10, 1, tzinfo=UTC)  # the history (data/history) ends on 2026-09-30
RANGES = {"VIB_MM_S": (0.0, 30.0), "TEMP_BEARING": (-20.0, 150.0), "PRESS_DISCH": (0.0, 1500.0), "CURRENT_A": (0.0, 300.0), "RUN_STATE": (0.0, 1.0)}


def msg_id(asset: str, sensor: str, ts: datetime) -> str:
    return f"{asset}|{sensor}|{int(ts.timestamp())}"


def good_message(asset: str, sensor: str, ts: datetime, value: float) -> dict:
    return {"msg_id": msg_id(asset, sensor, ts), "asset_id": asset, "plant": PLANT, "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sensor": sensor, "value": round(float(value), 4), "unit": SENSORS[sensor], "quality": "GOOD"}


def corrupt(msg: dict, kind: str, rnd: random.Random) -> bytes:
    if kind == "malformed_json":
        s = json.dumps(msg)
        return s[: rnd.randint(10, len(s) - 5)].encode()
    m = dict(msg)
    if kind == "missing_field":
        m.pop(rnd.choice(["value", "asset_id", "ts", "sensor"]))
    elif kind == "bad_type":
        m["value"] = rnd.choice(["N/A", "null", "--"])
    elif kind == "out_of_range":
        lo, hi = RANGES[m["sensor"]]
        m["value"] = round(hi * 5 + 100, 2) if rnd.random() < 0.5 else -round(abs(hi) + 7, 2)
    elif kind == "unknown_asset":
        m["asset_id"] = "PLT01-P999"
    return json.dumps(m).encode()


DEFECT_KINDS = ["malformed_json", "missing_field", "bad_type", "out_of_range", "unknown_asset"]


class Plant:
    def __init__(self, seed: int, degs: list[Degradation]):
        self.rngs = {a: np.random.default_rng(seed * 1000 + i) for i, a in enumerate(ASSET_IDS)}
        self.degs = degs

    def tick(self, t_h: float) -> list[tuple[str, str, float]]:
        out = []
        for a in ASSET_IDS:
            vals = signals(a, np.array([t_h]), self.rngs[a], self.degs)
            out += [(a, s, float(v[0])) for s, v in vals.items()]
        return out


def parse_inject(spec: str | None) -> list[Degradation]:
    if not spec:
        return []
    asset, mode, days = spec.split(":")
    return [Degradation(asset, mode, 0.0, float(days) * 24.0)]


def burst(n: int, topic: str, seed: int, bootstrap: str, inject: str | None, start_hours: float = 0.0) -> None:
    rnd = random.Random(seed)
    plant = Plant(seed, parse_inject(inject))
    prod = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True, "linger.ms": 5})
    manifest = {"total_sent": 0, "valid_unique": 0, "duplicates": 0, "invalid": {k: 0 for k in DEFECT_KINDS}, "seed": seed, "topic": topic}
    seen: set[str] = set()
    t_h = start_hours
    while manifest["total_sent"] < n:
        ts = T0 + timedelta(hours=t_h)
        for asset, sensor, value in plant.tick(t_h):
            if manifest["total_sent"] >= n:
                break
            msg = good_message(asset, sensor, ts, value)
            r = rnd.random()
            if r < 0.05:  # 5% defective (1% of each kind)
                kind = DEFECT_KINDS[int(r / 0.01)]
                prod.produce(topic, key=asset, value=corrupt(msg, kind, rnd))
                manifest["invalid"][kind] += 1
                manifest["total_sent"] += 1
                continue
            prod.produce(topic, key=asset, value=json.dumps(msg).encode())
            manifest["total_sent"] += 1
            if msg["msg_id"] not in seen:
                seen.add(msg["msg_id"])
                manifest["valid_unique"] += 1
            if rnd.random() < 0.02 and manifest["total_sent"] < n:  # 2% identical resends
                prod.produce(topic, key=asset, value=json.dumps(msg).encode())
                manifest["total_sent"] += 1
                manifest["duplicates"] += 1
        t_h += 10 / 60
        prod.poll(0)
    prod.flush()
    print(json.dumps(manifest))


def live(topic: str, seed: int, bootstrap: str, speed: float, inject: str | None) -> None:
    rnd = random.Random(seed)
    plant = Plant(seed, parse_inject(inject))
    prod = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True, "linger.ms": 20})
    t_h, step = 0.0, 10 / 60
    wait = (step * 3600.0) / speed  # real seconds per tick (speed = simulated seconds per real second)
    print(f"[sim] live: 1 tick = 10 simulated min every {wait:.2f} real s → topic {topic}", flush=True)
    while True:
        ts = T0 + timedelta(hours=t_h)
        for asset, sensor, value in plant.tick(t_h):
            msg = good_message(asset, sensor, ts, value)
            r = rnd.random()
            if r < 0.03:
                prod.produce(topic, key=asset, value=corrupt(msg, DEFECT_KINDS[int(r / 0.006)], rnd))
                continue
            prod.produce(topic, key=asset, value=json.dumps(msg).encode())
            if rnd.random() < 0.02:
                prod.produce(topic, key=asset, value=json.dumps(msg).encode())
        prod.poll(0)
        t_h += step
        time.sleep(wait)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["live", "burst"])
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--topic", default=os.environ.get("KAFKA_TOPIC_RAW", "plant.readings.raw"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092"))
    ap.add_argument("--speed", type=float, default=8640.0, help="simulated seconds per real second (8640 = 1 day every 10 s)")
    ap.add_argument("--start-hours", type=float, default=0.0, help="shifts the simulated clock (so two bursts don't share msg_ids)")
    ap.add_argument("--inject", default=None, help="ASSET:MODE:DAYS, e.g. PLT01-P107:bearing:12 (fails in 12 simulated days)")
    a = ap.parse_args()
    try:
        burst(a.n, a.topic, a.seed, a.bootstrap, a.inject, a.start_hours) if a.mode == "burst" else live(a.topic, a.seed, a.bootstrap, a.speed, a.inject)
    except KeyboardInterrupt:
        sys.exit(0)
