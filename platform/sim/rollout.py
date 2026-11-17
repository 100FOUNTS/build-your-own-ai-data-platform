"""Staggered-rollout extension of the plant simulator (module 18). OFF by default: nothing imports it, `sim.py` does not know it exists.

The question it lets you answer: "does the NEW ALERT POLICY reduce repair time / downtime?". The company's simulated FLEET
(the pump lines of all its plants, no specific plant) has 96 pump lines of 4 pumps (384 pumps). Each LINE adopts the new policy in some week (or never). The simulator turns an `adoption` plan into a pump-week panel.

World (what is the same for every pump, line and week)
  - a pump fails in a week with probability  p_it = p_i + s_t   (a pump level that never changes + a calendar level shared by the fleet);
    an older line fails more (line 01 about twice as much as line 96), like in real fleets where nobody randomizes the age of the equipment;
  - a failure stops the pump for a repair time drawn uniformly between 12 and 60 h (as in the PLT01 history);
  - the downtime of a failure is booked in the week its work order opens (like `downtime_hours` in `platform/seeds/work_orders.csv`).
    The outcome of the experiment is therefore `downtime_hours` per pump-week: mostly 0, sometimes 12-60.

The policy (what changes after a line adopts it; k = weeks since adoption, k = 0 in the adoption week)
  - it warns earlier, so crews arrive prepared: the repair is shorter by a share  r = R_MAX[criticality] x skill(line) x ramp(k);
  - it also catches some failures before they happen: the weekly failure probability is multiplied by (1 - AVOID_MAX x ramp(k));
  - ramp(k) = 1 - exp(-(k+1) / RAMP_WEEKS): crews need weeks to learn it, so the effect is DYNAMIC (it grows with exposure);
  - R_MAX depends on the pump's criticality (A > B > C) and `skill(line)` on the line's crew: the effect is HETEROGENEOUS.
  The true effect is therefore known, different for every pump-week, and it grows with time since adoption.

Counterfactuals (how the check computes the truth, independently of any estimator)
  Every random number of a pump (does it fail this week? how long would the repair take?) is drawn BEFORE and independently of the policy,
  from a generator keyed by (seed, pump). Simulating the same seed with the policy switched on and switched off therefore gives, for every
  pump-week, both potential outcomes with the same luck. `true_effects()` is that difference. No estimator is involved.

`--expected` (noise-free): outcomes are replaced by their expectation given the pump (no Bernoulli, no random repair time). Untreated outcomes
  are then EXACTLY additive (pump level + week level), so parallel trends hold exactly and a correct estimator must recover the truth to
  rounding error. Useful to debug an estimator before blaming the noise; it is what the check uses to tell a correct estimator from a wrong one.

Usage (from the repository root):
  python platform/sim/rollout.py frame                                   # the fixed frame (lines, pumps, weeks) as JSON
  python platform/sim/rollout.py panel --design D.json --seed 18 --out panel.csv [--expected]
  python platform/sim/rollout.py truth --design D.json --seed 18 [--expected] [--out truth.json]   # only AFTER you wrote your memo
`D.json` is any JSON with an `"adoption"` object: {"FLEET-L01": 12, "FLEET-L02": null, ...} (week number of adoption, null = never).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FLEET = "FLEET"  # ids of the simulated fleet: FLEET-L01 .. FLEET-L96 (lines), FLEET-L01-P1 .. (pumps); not PLT01 or PLT02
N_LINES = 96
PUMPS_PER_LINE = 4
N_WEEKS = 48
GROUPS = [f"{FLEET}-L{i + 1:02d}" for i in range(N_LINES)]
PUMPS = [(f"{g}-P{k + 1}", g) for g in GROUPS for k in range(PUMPS_PER_LINE)]
N_PUMPS = len(PUMPS)
CRITICALITY = "ABC"  # pump j has criticality CRITICALITY[j % 3], as in physics.py

# --- baseline world
BASE_FAIL_P = 0.08  # weekly failure probability of an average pump of the newest line (PLT01 history: about 0.09 per week for the whole fleet)
FRAILTY_SD = 0.4  # spread of the pump level
AGE_GRADIENT = 1.0  # line 01 fails (1 + 1.0) = 2 times as much as line 96
SEASON_AMP, SEASON_NOISE, SEASON_CLIP = 0.012, 0.008, 0.02  # calendar level shared by all pumps (additive)
REPAIR_MIN_H, REPAIR_MAX_H = 12.0, 60.0
MEAN_REPAIR_H = (REPAIR_MIN_H + REPAIR_MAX_H) / 2

# --- the policy: true effect
R_MAX = {"A": 0.55, "B": 0.40, "C": 0.20}  # biggest share of the repair time the policy removes, by criticality
SKILL_RANGE = (0.6, 1.4)  # crew skill of a line multiplies R_MAX
AVOID_MAX = 0.20  # biggest share of failures the policy prevents
RAMP_WEEKS = 5.0
_NEVER = 10**6  # "week of adoption" of a pump that never adopts

COLUMNS = ["pump_id", "group_id", "criticality", "week", "adopt_week", "policy_on", "failures", "repair_hours", "downtime_hours"]


def validate_adoption(adoption: dict) -> dict[str, int | None]:
    """Every line exactly once; a week in 1..N_WEEKS-1 or None (never)."""
    if set(adoption) != set(GROUPS):
        missing, extra = sorted(set(GROUPS) - set(adoption)), sorted(set(adoption) - set(GROUPS))
        raise ValueError(f"adoption must list every line exactly once (missing: {missing[:3]}, unknown: {extra[:3]})")
    out: dict[str, int | None] = {}
    for g, w in adoption.items():
        if w is None:
            out[g] = None
        elif isinstance(w, int) and not isinstance(w, bool) and 1 <= w <= N_WEEKS - 1:
            out[g] = int(w)
        else:
            raise ValueError(f"{g}: adoption week must be an integer in 1..{N_WEEKS - 1} or null, got {w!r}")
    return out


def load_adoption(path: str | Path) -> dict[str, int | None]:
    return validate_adoption(json.loads(Path(path).read_text())["adoption"])


def _world(seed: int) -> dict[str, np.ndarray]:
    """Everything that does not depend on the policy and is the same in the factual and the counterfactual world."""
    rng = np.random.default_rng([seed, 0])
    line = np.repeat(np.arange(N_LINES), PUMPS_PER_LINE)
    z = rng.standard_normal(N_PUMPS)
    age = 1.0 + AGE_GRADIENT * (1.0 - line / (N_LINES - 1))
    p_i = np.clip(BASE_FAIL_P * np.exp(FRAILTY_SD * z - FRAILTY_SD**2 / 2) * age, 0.03, 0.22)
    skill_line = rng.uniform(*SKILL_RANGE, size=N_LINES)
    t = np.arange(N_WEEKS)
    season = np.clip(SEASON_AMP * np.sin(2 * np.pi * (t + rng.uniform(0, 26)) / 26) + SEASON_NOISE * rng.standard_normal(N_WEEKS),
                     -SEASON_CLIP, SEASON_CLIP)
    crit = np.array([CRITICALITY[j % 3] for j in range(N_PUMPS)])
    return {"line": line, "p_i": p_i, "skill": skill_line[line], "season": season, "crit": crit,
            "r_max": np.array([R_MAX[c] for c in crit])}


def _draws(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Per pump and week: u (does it fail?) and v (how long would the repair take?). Keyed by (seed, pump): independent of the policy."""
    u = np.empty((N_PUMPS, N_WEEKS))
    v = np.empty((N_PUMPS, N_WEEKS))
    for j in range(N_PUMPS):
        r = np.random.default_rng([seed, 1, j])
        u[j], v[j] = r.random(N_WEEKS), r.random(N_WEEKS)
    return u, v


def simulate(adoption: dict, seed: int, *, expected: bool = False, policy: bool = True) -> pd.DataFrame:
    """The pump-week panel (N_PUMPS x N_WEEKS rows). `policy=False` is the counterfactual world where nobody ever adopts."""
    adoption = validate_adoption(adoption)
    w = _world(seed)
    adopt = np.array([_NEVER if adoption[g] is None else adoption[g] for _, g in PUMPS])
    if not policy:
        adopt = np.full(N_PUMPS, _NEVER)
    k = np.arange(N_WEEKS)[None, :] - adopt[:, None]  # weeks since adoption (negative before)
    on = k >= 0
    ramp = np.where(on, 1.0 - np.exp(-(np.maximum(k, 0) + 1.0) / RAMP_WEEKS), 0.0)
    r = np.clip(w["r_max"][:, None] * w["skill"][:, None] * ramp, 0.0, 0.9)  # share of repair time removed
    a = AVOID_MAX * ramp  # share of failures prevented
    p = w["p_i"][:, None] + w["season"][None, :]
    if expected:
        failures = p * (1.0 - a)
        repair = np.broadcast_to(MEAN_REPAIR_H * (1.0 - r), failures.shape)
        down = failures * repair
    else:
        u, v = _draws(seed)
        failures = (u < p * (1.0 - a)).astype(float)
        repair = (REPAIR_MIN_H + (REPAIR_MAX_H - REPAIR_MIN_H) * v) * (1.0 - r)
        down = failures * repair
        repair = np.where(failures > 0, repair, np.nan)
    df = pd.DataFrame({
        "pump_id": np.repeat([p_ for p_, _ in PUMPS], N_WEEKS),
        "group_id": np.repeat([g for _, g in PUMPS], N_WEEKS),
        "criticality": np.repeat(w["crit"], N_WEEKS),
        "week": np.tile(np.arange(N_WEEKS), N_PUMPS),
        "adopt_week": pd.array(np.where(np.repeat(adopt, N_WEEKS) >= _NEVER, -1, np.repeat(adopt, N_WEEKS)), dtype="Int64"),
        "policy_on": on.reshape(-1).astype(int),
        "failures": failures.reshape(-1),
        "repair_hours": np.asarray(repair).reshape(-1),
        "downtime_hours": down.reshape(-1),
    })
    df["adopt_week"] = df["adopt_week"].where(df["adopt_week"] >= 0, pd.NA)
    return df[COLUMNS]


def identified_limit(adoption: dict) -> float:
    """Treated cells with week >= this have no comparison pump left (everybody is already treated): they cannot be estimated with
    not-yet-treated / never-treated controls and are outside the estimand. +inf if some line never adopts."""
    adoption = validate_adoption(adoption)
    return float("inf") if any(w is None for w in adoption.values()) else float(max(adoption.values()))


def true_effects(adoption: dict, seed: int, *, expected: bool = False) -> pd.DataFrame:
    """The true effect (hours of downtime per pump-week) of every treated, identifiable pump-week: factual minus counterfactual world,
    same seed, same luck. Columns: pump_id, group_id, adopt_week, week, event_time, effect."""
    f = simulate(adoption, seed, expected=expected, policy=True)
    c = simulate(adoption, seed, expected=expected, policy=False)
    m = (f["policy_on"] == 1) & (f["week"] < identified_limit(adoption))
    e = f.loc[m, ["pump_id", "group_id", "adopt_week", "week"]].copy()
    e["adopt_week"] = e["adopt_week"].astype(int)
    e["event_time"] = e["week"] - e["adopt_week"]
    e["effect"] = f.loc[m, "downtime_hours"].to_numpy() - c.loc[m, "downtime_hours"].to_numpy()
    return e.reset_index(drop=True)


def true_att(adoption: dict, seed: int, *, expected: bool = False) -> dict:
    """The estimand of module 18: the mean true effect over all identifiable treated pump-weeks, and the same by event time."""
    e = true_effects(adoption, seed, expected=expected)
    return {"overall": float(e["effect"].mean()), "n_treated_pump_weeks": int(len(e)),
            "by_event_time": {int(k): float(v) for k, v in e.groupby("event_time")["effect"].mean().items()}}


def baseline_by_group(seed: int) -> pd.DataFrame:
    """Expected untreated downtime (hours per pump-week) of each line: what a randomization should balance. One row per line."""
    d = simulate({g: None for g in GROUPS}, seed, expected=True, policy=False)
    return d.groupby("group_id", as_index=False)["downtime_hours"].mean().rename(columns={"downtime_hours": "baseline_downtime_h"})


def frame() -> dict:
    return {"fleet": FLEET, "groups": GROUPS, "pumps_per_group": PUMPS_PER_LINE, "n_pumps": N_PUMPS, "n_weeks": N_WEEKS,
            "outcome": "downtime_hours", "columns": COLUMNS}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("frame")
    for name in ("panel", "truth"):
        s = sub.add_parser(name)
        s.add_argument("--design", required=True)
        s.add_argument("--seed", type=int, default=18)
        s.add_argument("--expected", action="store_true", help="noise-free panel (expected values)")
        s.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "frame":
        print(json.dumps(frame(), indent=1))
        return
    adoption = load_adoption(a.design)
    if a.cmd == "panel":
        if not a.out:
            sys.exit("--out is required for `panel`")
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        simulate(adoption, a.seed, expected=a.expected).to_csv(a.out, index=False)
        print(f"[rollout] wrote {a.out} ({N_PUMPS} pumps x {N_WEEKS} weeks, seed {a.seed}{', expected values' if a.expected else ''})")
    else:
        out = json.dumps(true_att(adoption, a.seed, expected=a.expected), indent=1)
        if a.out:
            Path(a.out).write_text(out)
        print(out)


if __name__ == "__main__":
    main()
