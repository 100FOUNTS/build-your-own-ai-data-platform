"""The previous team's tests. They pass."""

from legacy.report.report import build_report


def test_one_row_per_pump():
    df = build_report("2026-08")
    assert len(df) == 12


def test_availability_is_a_percentage():
    df = build_report("2026-08")
    assert df["availability_pct"].between(0, 100).all()
