"""The readings contract: v1 (canonical) and the translation of v2 (the new gateway, challenge R1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

RANGES = {
    "VIB_MM_S": (0.0, 30.0),
    "TEMP_BEARING": (-20.0, 150.0),
    "PRESS_DISCH": (0.0, 1500.0),
    "CURRENT_A": (0.0, 300.0),
    "RUN_STATE": (0.0, 1.0),
}
CANON_UNIT = {
    "VIB_MM_S": "mm/s",
    "TEMP_BEARING": "degC",
    "PRESS_DISCH": "kPa",
    "CURRENT_A": "A",
    "RUN_STATE": "bool",
}
# (sensor, v2 unit) → function that takes it to the canonical unit
CONVERT = {
    ("VIB_MM_S", "in/s"): lambda v: v * 25.4,
    ("VIB_MM_S", "mm/s"): lambda v: v,
    ("TEMP_BEARING", "degF"): lambda v: (v - 32) * 5 / 9,
    ("TEMP_BEARING", "degC"): lambda v: v,
    ("PRESS_DISCH", "kPa"): lambda v: v,
    ("CURRENT_A", "A"): lambda v: v,
    ("RUN_STATE", "bool"): lambda v: v,
}
STATUS = {"OK": "GOOD", "GOOD": "GOOD", "BAD": "BAD", "UNCERTAIN": "UNCERTAIN"}


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msg_id: str
    asset_id: str
    plant: str
    ts: str
    sensor: Literal["VIB_MM_S", "TEMP_BEARING", "PRESS_DISCH", "CURRENT_A", "RUN_STATE"]
    value: float
    unit: str
    quality: str

    @model_validator(mode="after")
    def in_range(self) -> Reading:
        lo, hi = RANGES[self.sensor]
        if not lo <= self.value <= hi:
            raise ValueError(f"{self.sensor}={self.value} outside [{lo}, {hi}]")
        return self


class Rejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def from_v2(m: dict) -> dict:
    """Translates a v2 message to the v1 contract. Raises Rejected with the reason if it can't."""
    try:
        dev, meas = m["device"], m["measurement"]
        sensor, val, uom = meas["tag"], meas["val"], meas["uom"]
        ts = datetime.fromtimestamp(int(m["event_time_ms"]) / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg_id, asset, site, status = m["id"], dev["asset"], dev["site"], m["status"]
    except (KeyError, TypeError) as exc:
        raise Rejected(f"missing_field:v2:{exc}") from None
    conv = CONVERT.get((sensor, uom))
    if conv is None:
        raise Rejected(f"unknown_unit:{sensor} in {uom!r}")
    try:
        value = round(conv(float(val)), 4)
    except (TypeError, ValueError):
        raise Rejected("bad_type:v2:val") from None
    return {
        "msg_id": msg_id,
        "asset_id": asset,
        "plant": site,
        "ts": ts,
        "sensor": sensor,
        "value": value,
        "unit": CANON_UNIT.get(sensor, uom),
        "quality": STATUS.get(status, status),
    }
