"""Challenge R2 — publishes lake.<LAKE_NS>.readings (Iceberg) as a Delta table at DELTA_PATH, idempotent and incremental."""

from __future__ import annotations

import os

import pyarrow.compute as pc
from deltalake import DeltaTable, write_deltalake
from pyiceberg.catalog.rest import RestCatalog


def catalog() -> RestCatalog:
    return RestCatalog(
        "lake",
        **{
            "uri": os.environ["ICEBERG_REST_URL"],
            "s3.endpoint": os.environ["S3_ENDPOINT"],
            "s3.access-key-id": os.environ["S3_ACCESS_KEY"],
            "s3.secret-access-key": os.environ["S3_SECRET_KEY"],
            "s3.path-style-access": "true",
            "s3.region": "us-east-1",
        },
    )


def main() -> None:
    ns = os.environ.get("LAKE_NS", "bronze")
    path = os.environ.get("DELTA_PATH", "data/delta/readings")
    src = catalog().load_table(f"{ns}.readings").scan().to_arrow()
    src = src.append_column(
        "date", pc.strftime(src["ts"], format="%Y-%m-%d")
    )  # ts is a timestamptz: strftime works in UTC
    if not DeltaTable.is_deltatable(path):
        write_deltalake(path, src, partition_by=["date"])
        print(f"created {path}: {src.num_rows} rows")
        return
    dt = DeltaTable(path)
    have = dt.to_pyarrow_table(columns=["msg_id"])["msg_id"]
    new = src.filter(pc.invert(pc.is_in(src["msg_id"], value_set=have)))
    if new.num_rows == 0:
        print("no new rows")
        return
    # MERGE on msg_id: even if two exports run at the same time, a row doesn't get in twice
    (
        dt.merge(new, predicate="t.msg_id = s.msg_id", source_alias="s", target_alias="t")
        .when_not_matched_insert_all()
        .execute()
    )
    print(f"added {new.num_rows} rows → version {DeltaTable(path).version()}")


if __name__ == "__main__":
    main()
