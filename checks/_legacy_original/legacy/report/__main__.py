"""python -m legacy.report --month 2026-08 --out report.csv"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from legacy.report.report import build_report


def main() -> None:
    ap = argparse.ArgumentParser(description="Monthly reliability report")
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--out", default="report.csv")
    a = ap.parse_args()
    history = Path(os.environ["HISTORY_DIR"]) if os.environ.get("HISTORY_DIR") else None
    wos = Path(os.environ["WORK_ORDERS_CSV"]) if os.environ.get("WORK_ORDERS_CSV") else None
    df = build_report(a.month, history, wos)
    df.to_csv(a.out, index=False)
    print(f"{len(df)} rows → {a.out}")


if __name__ == "__main__":
    main()
