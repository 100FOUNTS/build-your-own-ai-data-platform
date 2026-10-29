"""Challenge R6 (extends module 4) — Data drift that catches a silent unit change without drowning maintenance in false alarms."""

from __future__ import annotations

import importlib

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT, ok
from expected import FEATURES, expected_gold

pytestmark = pytest.mark.challenge

REF = ("2026-07-03", "2026-09-02")
CUR = ("2026-09-17", "2026-09-30")
VIB = ["vib_mean", "vib_max", "vib_std", "vib_mean_7d"]


@pytest.fixture(scope="module")
def drift():
    ok((ROOT / "ml/drift.py").exists(), "Missing ml/drift.py with `psi(reference, current, bins=10)` and `drift_report(gold, reference, current, features=None)` (see guide/challenges/R6_drift.md).")
    return importlib.import_module("ml.drift")


@pytest.fixture(scope="module")
def gold():
    g = expected_gold()
    g["feature_date"] = pd.to_datetime(g["feature_date"])
    return g


def silent_unit_change(g: pd.DataFrame, asset: str = "PLT01-P105") -> pd.DataFrame:
    """The transmitter firmware starts sending in/s but still says mm/s: nobody warns anyone."""
    g = g.copy()
    m = (g.asset_id == asset) & (g.feature_date >= CUR[0])
    g.loc[m, VIB] = g.loc[m, VIB] / 25.4
    return g


def report(drift, g):
    r = drift.drift_report(g, REF, CUR, FEATURES)
    ok(isinstance(r, pd.DataFrame) and {"asset_id", "feature", "psi", "drift"} <= set(r), "drift_report must return a DataFrame with asset_id, feature, psi and drift (bool).")
    ok(len(r) == 12 * len(FEATURES), f"drift_report must have one row per (asset, feature): 12 × {len(FEATURES)} = {12 * len(FEATURES)} (it has {len(r)}).")
    return r


def test_r6_psi_behaves_as_it_should(drift):
    rng = np.random.default_rng(0)
    a, b = rng.normal(0, 1, 5000), rng.normal(0, 1, 5000)
    same, shifted = drift.psi(a, b), drift.psi(a, rng.normal(1, 1, 5000))
    ok(isinstance(same, float) and same < 0.05, f"PSI between two samples of the SAME distribution should be ≈ 0 (< 0.05) and it gives {same}.")
    ok(shifted > 0.25, f"PSI with the mean shifted by 1 standard deviation should exceed 0.25 (strong drift) and it gives {shifted:.3f}.")
    far = drift.psi(a, np.full(200, 50.0))
    ok(np.isfinite(far) and far > 1, f"If everything current falls outside the reference range the PSI must be large and FINITE (it gives {far}). Do you guard the empty bins (log of 0)?")
    with_nan = drift.psi(np.r_[a, [np.nan] * 50], np.r_[b, [np.nan] * 50])
    ok(np.isfinite(with_nan) and abs(with_nan - same) < 0.02, f"Nulls must be ignored (with nulls: {with_nan}, without nulls: {same}).")
    bins = drift.psi(np.arange(1000.0), np.arange(1000.0) + 0.5)
    ok(bins < 0.05, f"The bins must come from the quantiles of the REFERENCE (not from a fixed range). With almost identical data the PSI gives {bins:.3f}.")


def test_r6_detects_the_silent_unit_change(drift, gold):
    r = report(drift, silent_unit_change(gold))
    flagged = set(r.loc[(r.asset_id == "PLT01-P105") & r.drift.astype(bool), "feature"])
    ok("vib_mean" in flagged, f"A PLT01-P105 transmitter started sending in/s while saying mm/s (vibration “drops” 25 times) and your report doesn't flag `vib_mean` with drift (it flags: {sorted(flagged) or 'nothing'}).\n"
       "This is the incident that does the most damage: the model sees a “perfectly healthy” pump right when it may be failing.")


def test_r6_no_alert_fatigue(drift, gold):
    r = report(drift, gold)
    share = r.drift.astype(bool).mean()
    top = r[r.drift.astype(bool)].groupby("feature").size().sort_values(ascending=False).head(4).to_dict()
    ok(share <= 0.10, f"On data WITHOUT incidents you flag drift on {share:.0%} of the (asset, feature) pairs; ≤ 10% expected. Most flagged: {top}.\n"
       "With 14 days per asset the PSI is very noisy and the windowed features (7d, 28d) are autocorrelated: a fixed threshold of 0.25 alerts on almost everything.\n"
       "If maintenance gets 100 warnings a day, they stop reading them and miss the real one.")
    print(f"\n  pairs flagged on clean data: {share:.1%}")
