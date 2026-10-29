"""One test per bug in ticket INF-2026-10 (each one fails with the original code)."""

import pytest

from legacy.report.report import build_report


def row(month, asset):
    df = build_report(month)
    return df[df.asset_id == asset].iloc[0]


def test_the_august_31_failure_counts_in_august():
    # P112 opened the work order at 23:32 UTC on 08-31: in Madrid time it is already September
    assert row("2026-08", "PLT01-P112").n_failures == 1


def test_a_stoppage_of_several_days_counts_in_full():
    # P104 was stopped from 08-01 00:36 to 08-03 05:32 UTC (52.9 h): .seconds ignored the days
    assert row("2026-08", "PLT01-P104").availability_pct < 93


def test_a_stoppage_that_crosses_months_is_split():
    # P112: 08-31 23:32 → 09-03 06:08. September gets ~54 h, even though the work order was opened in August
    assert row("2026-09", "PLT01-P112").availability_pct < 93


def test_operating_hours_do_not_exceed_the_month():
    df = build_report("2026-08")
    assert (df.operating_hours <= 31 * 24).all()


def test_vibration_p95_only_while_running():
    # with the pump stopped vibration is ~0.02 mm/s: if it goes into the computation, the p95 drops.
    # Value computed by hand with pandas over the September instants with RUN_STATE = 1.
    assert row("2026-09", "PLT01-P107").vib_p95 == pytest.approx(1.656, abs=1e-3)
