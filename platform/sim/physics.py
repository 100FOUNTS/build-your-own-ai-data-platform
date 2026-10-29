"""Simplified physics of a centrifugal pump (shared by the live simulator and the history generator).

You don't need to read this file for the exercise: it is part of "the plant".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SENSORS = {"VIB_MM_S": "mm/s", "TEMP_BEARING": "degC", "PRESS_DISCH": "kPa", "CURRENT_A": "A", "RUN_STATE": "bool"}
MODELS = {"CP-200": (420.0, 45.0, 1.1), "CP-350": (520.0, 80.0, 1.5), "HX-90": (300.0, 22.0, 0.8)}
PLANT = "PLT01"
N_ASSETS = 12
ASSET_IDS = [f"{PLANT}-P{101 + i}" for i in range(N_ASSETS)]
MODEL_OF = {a: list(MODELS)[i % 3] for i, a in enumerate(ASSET_IDS)}
CRITICALITY = {a: "ABC"[i % 3] for i, a in enumerate(ASSET_IDS)}


@dataclass
class Degradation:
    """An ongoing failure: health worsens from `start_h` to `fail_h` (hours since T0) and the pump stops at `fail_h`."""

    asset_id: str
    mode: str  # bearing | seal | cavitation
    start_h: float
    fail_h: float
    down_h: float = 36.0

    def level(self, t: np.ndarray) -> np.ndarray:
        prog = np.clip((t - self.start_h) / (self.fail_h - self.start_h), 0, 1)
        prog = np.where(t > self.fail_h, 0.0, prog)
        return prog**2


def signals(asset_id: str, t_h: np.ndarray, rng: np.random.Generator, degs: list[Degradation], stops: list[tuple[float, float]] | None = None) -> dict[str, np.ndarray]:
    """Values of a pump's 5 sensors at the instants `t_h` (hours since T0)."""
    p_nom, i_nom, base = MODELS[MODEL_OF[asset_id]]
    base *= 1.0 + 0.03 * (int(asset_id[-2:]) % 7 - 3)
    hour = t_h % 24.0
    load = np.clip(0.72 + 0.14 * np.sin(2 * np.pi * (hour - 6) / 24) + rng.normal(0, 0.03, t_h.shape), 0.4, 1.0)
    ambient = 24 + 3 * np.sin(2 * np.pi * (hour - 9) / 24)
    d = {"bearing": np.zeros_like(t_h), "seal": np.zeros_like(t_h), "cavitation": np.zeros_like(t_h)}
    run = np.ones_like(t_h, dtype=bool)
    for g in degs:
        if g.asset_id == asset_id:
            d[g.mode] = np.maximum(d[g.mode], g.level(t_h))
            run &= ~((t_h >= g.fail_h) & (t_h < g.fail_h + g.down_h))
    for a, b in stops or []:
        run &= ~((t_h >= a) & (t_h < b))
    vib = base * (0.9 + 0.2 * load) + base * (2.5 * d["bearing"] + 0.3 * d["seal"] + d["cavitation"]) + rng.normal(0, 0.05 * base, t_h.shape) * (1 + 5 * d["cavitation"])
    temp = ambient + 28 + 12 * load + 15 * d["bearing"] + 2 * d["seal"] + 3 * d["cavitation"] + rng.normal(0, 0.4, t_h.shape)
    press = p_nom * (0.97 + 0.03 * load) * (1 - 0.18 * d["seal"] - 0.06 * d["cavitation"]) + rng.normal(0, 3, t_h.shape) + rng.normal(0, 1, t_h.shape) * 15 * d["cavitation"]
    curr = i_nom * load * (1 + 0.05 * d["bearing"] + 0.08 * d["seal"]) + rng.normal(0, 0.01 * i_nom, t_h.shape)
    vib = np.where(run, vib, np.abs(rng.normal(0.02, 0.01, t_h.shape)))
    temp = np.where(run, temp, ambient + 2)
    press = np.where(run, press, np.abs(rng.normal(2, 1, t_h.shape)))
    curr = np.where(run, curr, 0.0)
    return {"VIB_MM_S": vib, "TEMP_BEARING": temp, "PRESS_DISCH": press, "CURRENT_A": curr, "RUN_STATE": run.astype(float)}
