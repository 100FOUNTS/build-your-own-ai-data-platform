"""Challenge R5 — two alert policies compared on the held-out period, with a block-bootstrap confidence interval.

    python -m ml.policy_compare --predictions test_predictions.csv --threshold 0.31 --seed 7 --n-boot 2000 --out policy_comparison.json

Why blocks: the label looks 7 days ahead, so the rows of one pump on consecutive days share the same future failure
and are NOT independent. Resampling rows would pretend we have 300 independent observations when we have
a handful of failure episodes, and the interval would come out too narrow. We resample whole blocks instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

COST_FALSE_ALERT = 1.0  # one wasted visit
COST_MISSED_DAY = 10.0  # one asset-day with label 1 and no alert
LEVEL = 0.95
BLOCKS = ("asset_week", "day")


def block_labels(df: pd.DataFrame, block: str) -> pd.Series:
    """The resampling unit of every row: a day (all the pumps) or an asset-week (7 consecutive days of one pump)."""
    if block == "day":
        return df["feature_date"].astype(str)
    week = (df["feature_date"] - df["feature_date"].min()).dt.days // 7
    return df["asset_id"] + "|" + week.astype(str)


def policy_columns(df: pd.DataFrame, threshold: float) -> tuple[pd.Series, pd.Series]:
    alert = df["score"] >= threshold
    positive = df["label_failure_7d"] == 1
    return (alert & ~positive).astype(float), (~alert & positive).astype(float)  # false alerts, missed failure-days


def cost_per_100(fa, missed, n):
    return 100 * (COST_FALSE_ALERT * fa + COST_MISSED_DAY * missed) / n


def compare(df: pd.DataFrame, threshold: float, default_threshold: float = 0.5, n_boot: int = 2000, seed: int = 7, block: str = "asset_week") -> dict:
    df = df.reset_index(drop=True)
    fa_d, mi_d = policy_columns(df, default_threshold)
    fa_o, mi_o = policy_columns(df, threshold)
    cols = pd.DataFrame({"fa_d": fa_d, "mi_d": mi_d, "fa_o": fa_o, "mi_o": mi_o, "n": 1.0})
    blocks = cols.groupby(block_labels(df, block).to_numpy()).sum().to_numpy()  # one row per block
    total = blocks.sum(axis=0)

    def stats(s):  # s: (..., 5) sums -> (cost difference, missed difference), both per 100 asset-days
        n = s[..., 4]
        return (cost_per_100(s[..., 0], s[..., 1], n) - cost_per_100(s[..., 2], s[..., 3], n), 100 * (s[..., 1] - s[..., 3]) / n)

    rng = np.random.default_rng(seed)
    draws = blocks[rng.integers(0, len(blocks), size=(n_boot, len(blocks)))].sum(axis=1)  # whole blocks, with replacement
    lo, hi = (1 - LEVEL) / 2 * 100, (1 + LEVEL) / 2 * 100
    out = {}
    for name, est, dist in zip(("cost_diff_per_100_days", "missed_diff_per_100_days"), stats(total), stats(draws)):
        out[name] = {"estimate": float(est), "ci_low": float(np.percentile(dist, lo)), "ci_high": float(np.percentile(dist, hi))}

    def policy(thr, fa, mi):
        return {"threshold": float(thr), "false_alerts": int(fa), "missed_failure_days": int(mi), "cost_per_100_days": float(cost_per_100(fa, mi, total[4]))}

    return {
        "unit_costs": {"false_alert": COST_FALSE_ALERT, "missed_failure_day": COST_MISSED_DAY},
        "n_rows": int(total[4]), "n_boot": n_boot, "seed": seed, "ci_level": LEVEL, "block": block, "n_blocks": int(len(blocks)),
        "policies": {"default": policy(default_threshold, total[0], total[1]), "optimal": policy(threshold, total[2], total[3])},
        **out,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--predictions", required=True, help="CSV with asset_id, feature_date, score, label_failure_7d")
    ap.add_argument("--threshold", type=float, required=True, help="the cost-optimal threshold chosen on validation")
    ap.add_argument("--default-threshold", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--block", choices=BLOCKS, default="asset_week")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    df = pd.read_csv(a.predictions, parse_dates=["feature_date"])
    res = compare(df, a.threshold, a.default_threshold, a.n_boot, a.seed, a.block)
    Path(a.out).write_text(json.dumps(res, indent=2))
    c = res["cost_diff_per_100_days"]
    print(f"cost saved per 100 asset-days (default - optimal): {c['estimate']:.2f}  95% CI [{c['ci_low']:.2f}, {c['ci_high']:.2f}]  ({res['block']} blocks: {res['n_blocks']})")


if __name__ == "__main__":
    main()
