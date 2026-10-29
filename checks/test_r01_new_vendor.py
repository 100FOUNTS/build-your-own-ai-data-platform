"""Challenge R1 (extends module 2) — New vendor: two schemas, other units, late data and double sends during the cutover."""

from __future__ import annotations

import json

import pytest

from conftest import kafka_read_all, kafka_topics, ok, unique
from vendor_v2 import burst
from test_m02_streaming import run_consumer, stream_env  # noqa: F401  (same temporary database as module 2)

pytestmark = pytest.mark.challenge

CANON_UNITS = {"VIB_MM_S": "mm/s", "TEMP_BEARING": "degC", "PRESS_DISCH": "kPa", "CURRENT_A": "A", "RUN_STATE": "bool"}
V1_KEYS = {"msg_id", "asset_id", "plant", "ts", "sensor", "value", "unit", "quality"}


@pytest.fixture(scope="module")
def r1_run(stream_env):  # noqa: F811
    raw, clean, dlq = unique("chk.raw"), unique("chk.clean"), unique("chk.dlq")
    kafka_topics(raw, clean, dlq)
    manifest = burst(raw, n=3000, seed=21)
    group, prefix = unique("g"), unique("dd") + ":"
    run_consumer(stream_env, raw, clean, dlq, group, prefix)
    return {"raw": raw, "clean": clean, "dlq": dlq, "manifest": manifest, "group": group, "prefix": prefix,
            "clean_rows": [json.loads(x) for x in kafka_read_all(clean)], "dlq_rows": [json.loads(x) for x in kafka_read_all(dlq)]}


def test_r1_clean_has_every_valid_reading_once(r1_run):
    m, rows = r1_run["manifest"], r1_run["clean_rows"]
    ids = [r.get("msg_id") for r in rows]
    ok(len(ids) == len(set(ids)), f"`clean` has {len(ids) - len(set(ids))} repeated readings. During the cutover both gateways send the same reading "
       "(same msg_id, different format): deduplication has to work ACROSS versions.")
    missing = set(m["clean"]) - set(ids)
    extra = set(ids) - set(m["clean"])
    ok(not missing, f"{len(missing)} valid readings are missing from `clean` (e.g. {sorted(missing)[:2]}). Among them are v2 messages and readings that arrived late: "
       "do you reject them because they come in another schema or carry an old `ts`?")
    ok(not extra, f"There are {len(extra)} messages in `clean` that are not valid readings (e.g. {sorted(extra)[:2]}).")


def test_r1_clean_is_always_v1_format_and_canonical_units(r1_run):
    rows = r1_run["clean_rows"]
    bad_shape = [r for r in rows if set(r) != V1_KEYS]
    ok(not bad_shape, f"Everything that goes out to `clean` must have EXACTLY the v1 contract ({sorted(V1_KEYS)}); there are {len(bad_shape)} messages with another shape, "
       f"e.g. keys {sorted(bad_shape[0]) if bad_shape else None}. Downstream consumers (Spark, module 3) don't know v2 exists: the translation happens at the boundary.")
    bad_unit = [r for r in rows if r["unit"] != CANON_UNITS[r["sensor"]]]
    ok(not bad_unit, f"{len(bad_unit)} messages go out with a non-canonical unit (e.g. {(bad_unit or [{}])[0].get('sensor')} in {(bad_unit or [{}])[0].get('unit')!r}). "
       "In `clean` vibration is in mm/s and temperature in degC, wherever it comes from.")


def test_r1_values_converted_correctly(r1_run):
    exp = r1_run["manifest"]["clean"]
    wrong = []
    for r in r1_run["clean_rows"]:
        e = exp.get(r["msg_id"])
        if e and (abs(float(r["value"]) - e["value"]) > 1e-3 or r["ts"] != e["ts"] or r["asset_id"] != e["asset_id"]):
            wrong.append((r["msg_id"], r["value"], e["value"], r["ts"], e["ts"]))
    ok(not wrong, f"{len(wrong)} readings have a value or a `ts` different from the real one after translating them. Example (msg_id, your value, real value, your ts, real ts): {wrong[:1]}.\n"
       "Check the unit conversion (in/s → mm/s, degF → degC) and the conversion of `event_time_ms` (milliseconds since 1970, UTC) to ISO 8601 with Z.")


def test_r1_dlq_explains_each_new_defect(r1_run):
    m, dlq = r1_run["manifest"], r1_run["dlq_rows"]
    total = sum(m["dlq"].values())
    ok(len(dlq) == total, f"The DLQ has {len(dlq)} records and the vendor sent {total} unusable messages {m['dlq']}.")
    ok(all(d.get("reason") and d.get("raw") for d in dlq), "Each DLQ record needs `reason` and `raw` (the original message, not the translated one).")
    reasons = {}
    for d in dlq:
        reasons.setdefault(d["reason"].split(":")[0], 0)
        reasons[d["reason"].split(":")[0]] += 1
    unk = [k for k in reasons if "schema" in k.lower() or "version" in k.lower()]
    ok(unk and sum(reasons[k] for k in unk) == m["dlq"]["unknown_schema"],
       f"{m['dlq']['unknown_schema']} messages arrived from a schema version nobody announced. They must go to the DLQ with a reason saying the schema/version is unknown "
       f"(current reasons: {reasons}). If you translated them “by eye” you would be guessing the contract.")
    unit = [k for k in reasons if "unit" in k.lower()]
    ok(unit and sum(reasons[k] for k in unit) == m["dlq"]["unknown_unit"],
       f"{m['dlq']['unknown_unit']} messages arrived with a unit you don't know how to convert: they must go to the DLQ with a reason about the unit (current reasons: {reasons}).")


def test_r1_reprocessing_with_another_group_does_not_duplicate(stream_env, r1_run):  # noqa: F811
    n1 = len(kafka_read_all(r1_run["clean"]))
    run_consumer(stream_env, r1_run["raw"], r1_run["clean"], r1_run["dlq"], unique("g2"), r1_run["prefix"])
    n2 = len(kafka_read_all(r1_run["clean"]))
    ok(n1 == n2, f"Reprocessing everything with a new group added {n2 - n1} messages to `clean`: deduplication must use the SAME key for v1 and v2.")
