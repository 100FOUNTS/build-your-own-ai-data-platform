"""Challenge R6 — drift per asset with PSI and an empirical threshold (learned from each asset's own history)."""

from __future__ import annotations

import numpy as np
import pandas as pd

FLOOR = 0.25  # PSI > 0.25 = strong drift (usual convention)
EPS = 1e-4
WINDOW_STEP = 7


def psi(reference, current, bins: int = 10) -> float:
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    ref, cur = ref[~np.isnan(ref)], cur[~np.isnan(cur)]
    if len(ref) < 2 or len(cur) < 2:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.clip(np.histogram(ref, edges)[0] / len(ref), EPS, None)
    c = np.clip(np.histogram(cur, edges)[0] / len(cur), EPS, None)
    return float(np.sum((c - r) * np.log(c / r)))


def _null_psis(ref: pd.DataFrame, feature: str, width: int) -> list[float]:
    """PSI of `width`-day windows INSIDE the reference against the rest: how much the PSI moves when nothing is happening."""
    days = np.sort(ref["feature_date"].unique())
    out = []
    for i in range(0, len(days) - width, WINDOW_STEP):
        inside = ref["feature_date"].isin(days[i : i + width])
        out.append(psi(ref.loc[~inside, feature], ref.loc[inside, feature]))
    return [x for x in out if np.isfinite(x)]


def drift_report(
    gold: pd.DataFrame,
    reference: tuple[str, str],
    current: tuple[str, str],
    features: list[str] | None = None,
) -> pd.DataFrame:
    g = gold.copy()
    g["feature_date"] = pd.to_datetime(g["feature_date"])
    features = features or [c for c in g.columns if c not in {"asset_id", "feature_date", "label_failure_7d"}]
    rows = []
    for asset, d in g.groupby("asset_id"):
        ref = d[d["feature_date"].between(*reference)]
        cur = d[d["feature_date"].between(*current)]
        width = max(cur["feature_date"].nunique(), 2)
        for f in features:
            value = psi(ref[f], cur[f])
            nulls = _null_psis(ref, f, width)
            threshold = max(FLOOR, 1.5 * max(nulls)) if nulls else FLOOR
            rows.append(
                {
                    "asset_id": asset,
                    "feature": f,
                    "psi": value,
                    "threshold": threshold,
                    "drift": bool(np.isfinite(value) and value > threshold),
                }
            )
    return pd.DataFrame(rows)
