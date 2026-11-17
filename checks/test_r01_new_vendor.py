"""Challenge R1 (extends module 2) — New vendor: two schemas, other units, late data and double sends during the cutover.
Part 2: the contract is enforced at the edge with a Schema Registry (Redpanda's built-in one)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx
import pytest

from conftest import ROOT, kafka_read_all, kafka_topics, ok, py, tail, unique
from expected import (R1_COMPAT_FORBIDDEN, R1_OPTIONAL_FIELD, R1_REASON_PREFIX, R1_SUBJECT_V1, R1_SUBJECT_V2,
                      R1_VENDOR_V2_JSON_SCHEMA)
from vendor_v2 import burst, v1
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


# ====================================================================================================== Part 2: the contract at the edge
SR_HEADERS = {"Content-Type": "application/vnd.schemaregistry.v1+json"}
V1_FIELDS = sorted(V1_KEYS)
VALID_V2_UNITS = {"in/s", "degF", "mm/s", "degC", "kPa", "A", "bool"}


def sr_url() -> str:
    return os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:18081").rstrip("/")


def sr(method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, sr_url() + path, headers=SR_HEADERS, timeout=15, **kw)


def drop_subject(subject: str) -> None:
    try:  # best-effort cleanup: soft delete, then permanent
        sr("DELETE", f"/subjects/{subject}")
        sr("DELETE", f"/subjects/{subject}?permanent=true")
    except httpx.HTTPError:
        pass


def latest(subject: str) -> httpx.Response:
    return sr("GET", f"/subjects/{subject}/versions/latest")


@pytest.fixture(scope="module")
def registry():
    try:
        sr("GET", "/subjects").raise_for_status()
    except httpx.HTTPError as exc:
        ok(False, f"Cannot reach the Schema Registry at {sr_url()} ({type(exc).__name__}).\n"
           "It is built into Redpanda (it listens on port 8081 inside the container); this platform publishes it on localhost:18081.\n"
           "Start it with `make up-stream`; if Redpanda was already running before this challenge, recreate it: `docker compose --profile stream up -d redpanda`.\n"
           "To use another address, set SCHEMA_REGISTRY_URL.")
    return sr_url()


def run_contract_script(topic: str) -> None:
    ok((ROOT / "pipelines" / "stream" / "contract.py").exists(),
       "Missing pipelines/stream/contract.py (see guide/challenges/R1_new_vendor.md, part 2): the script that registers the schema of the raw topic and sets its compatibility rule.")
    r = py(["-m", "pipelines.stream.contract"], env={"KAFKA_TOPIC_RAW": topic, "SCHEMA_REGISTRY_URL": sr_url()}, timeout=60)
    ok(r.returncode == 0, "`python -m pipelines.stream.contract` ended with an error (it must exit 0, reading KAFKA_TOPIC_RAW and SCHEMA_REGISTRY_URL):\n" + tail(r))


def versions(subject: str) -> list[int]:
    r = sr("GET", f"/subjects/{subject}/versions")
    return r.json() if r.status_code == 200 else []


@pytest.fixture(scope="module")
def r1_contract(registry):
    """What YOUR script leaves in the registry for a fresh topic, after running it twice (a script that registers must be safe to re-run)."""
    topic = unique("chk.contract")
    subject = R1_SUBJECT_V1.format(topic=topic)
    run_contract_script(topic)
    after_first = versions(subject)
    run_contract_script(topic)
    yield {"topic": topic, "subject": subject, "after_first": after_first, "after_second": versions(subject)}
    drop_subject(subject)


def test_r1_the_v1_schema_is_registered_for_the_topic(r1_contract):
    subject = r1_contract["subject"]
    ok(r1_contract["after_first"], f"After running your script the registry has no subject `{subject}`. A topic is governed when its value schema is registered under "
       "`<topic>-value`: without a subject there is no contract to enforce, and the consumer has nothing to validate against.")
    got = latest(subject).json()
    ok(got.get("schemaType") == "JSON", f"The registered schema is of type {got.get('schemaType', 'AVRO')!r}. This challenge verifies JSON Schema (`schemaType` JSON); "
       "Avro and Protobuf need extra libraries and are the natural next step, not part of the check.")
    ok(r1_contract["after_second"] == r1_contract["after_first"], f"Running your script a second time changed the subject's versions from {r1_contract['after_first']} to {r1_contract['after_second']}. "
       "Registering must be idempotent: the same schema is the same version. Otherwise every deploy of the platform adds noise to the history that consumers rely on.")


def test_r1_the_schema_has_teeth(r1_contract):
    """The schema is the contract: it must accept a good v1 reading and reject the structural defects. Checked offline against what is registered."""
    from jsonschema.validators import validator_for

    r = sr("GET", f"/subjects/{r1_contract['subject']}/versions/1")
    ok(r.status_code == 200, "The subject has no version 1.")
    schema = json.loads(r.json()["schema"])
    v = validator_for(schema)(schema)
    good = v1("PLT01-P101", "VIB_MM_S", datetime(2026, 10, 20, tzinfo=UTC), 2.5)
    ok(v.is_valid(good), f"The registered schema rejects a perfectly good v1 reading: {good}. A contract that blocks legitimate data is as broken as one that lets everything in.")
    slips = []
    for k in V1_FIELDS:
        if v.is_valid({x: y for x, y in good.items() if x != k}):
            slips.append(f"without `{k}`")
    for k, bad in {"value": "abc", "ts": 1792454400, "msg_id": None, "sensor": 7}.items():
        if v.is_valid({**good, k: bad}):
            slips.append(f"`{k}` = {bad!r}")
    ok(not slips, f"The registered schema accepts messages that break the v1 contract: {', '.join(slips)}.\n"
       "Every v1 field is required and has a type (see module 2). A schema that accepts these is a contract on paper only.")


def test_r1_a_compatibility_rule_was_chosen_for_the_subject(r1_contract):
    r = sr("GET", f"/config/{r1_contract['subject']}?defaultToGlobal=false")
    ok(r.status_code == 200, f"`{r1_contract['subject']}` has no compatibility rule of its own: it inherits the registry's global default.\n"
       "Choosing the rule per subject is part of the contract (it must travel with the schema, not depend on how one registry happens to be configured).")
    level = r.json().get("compatibilityLevel")
    ok(level not in R1_COMPAT_FORBIDDEN, f"The compatibility level of the subject is {level}: the registry would accept ANY change, including the vendor's breaking v2. "
       "That is the same as having no registry.")


def test_r1_registry_rejects_the_vendor_v2_as_an_evolution(r1_contract):
    subject = r1_contract["subject"]
    before = versions(subject)
    r = sr("POST", f"/subjects/{subject}/versions", json={"schemaType": "JSON", "schema": json.dumps(R1_VENDOR_V2_JSON_SCHEMA)})
    if r.status_code == 200:  # leave the subject as we found it for the next test
        sr("DELETE", f"/subjects/{subject}/versions/{r.json()['version']}")
        sr("DELETE", f"/subjects/{subject}/versions/{r.json()['version']}?permanent=true")
    ok(r.status_code == 409, f"The registry answered {r.status_code} when the vendor's v2 (other field names, nested objects, other units) was registered as a new version of `{subject}`; "
       "it should refuse it (HTTP 409). With your rule a breaking change reaches every consumer of the topic at the same time, and the first one to find out is the one that crashes.")
    ok(versions(subject) == before, f"The registry answered 409 but the subject's versions changed from {before} to {versions(subject)}.")


def test_r1_registry_accepts_a_compatible_evolution(r1_contract):
    subject = r1_contract["subject"]
    base = json.loads(sr("GET", f"/subjects/{subject}/versions/1").json()["schema"])
    evolved = json.loads(json.dumps(base))
    evolved.setdefault("properties", {})[R1_OPTIONAL_FIELD] = {"type": "string"}
    r = sr("POST", f"/subjects/{subject}/versions", json={"schemaType": "JSON", "schema": json.dumps(evolved)})
    ok(r.status_code == 200, f"The registry refused to add a new OPTIONAL string field `{R1_OPTIONAL_FIELD}` to your schema (HTTP {r.status_code}): {r.text[:300]}\n"
       "A contract that cannot evolve forces every change through a new topic. The rule you chose should block breaking changes and let the safe ones through.")
    ok(len(versions(subject)) == 2, f"After the accepted evolution the subject should have 2 versions; it has {versions(subject)}.")


# ------------------------------------------------------------------ the consumer enforces the registered contract
def run_governed_consumer(env_db, raw, clean, dlq, group, prefix):
    ok((ROOT / "pipelines" / "stream" / "consumer.py").exists(), "Missing pipelines/stream/consumer.py (see guide/02_streaming.md).")
    r = py(["-m", "pipelines.stream.consumer"], timeout=300, env={
        "DATABASE_URL": env_db, "KAFKA_TOPIC_RAW": raw, "KAFKA_TOPIC_CLEAN": clean, "KAFKA_TOPIC_DLQ": dlq, "KAFKA_GROUP": group,
        "DEDUPE_PREFIX": prefix, "IDLE_EXIT_SECONDS": "6", "SCHEMA_REGISTRY_URL": sr_url()})
    ok(r.returncode == 0, "`python -m pipelines.stream.consumer` (with SCHEMA_REGISTRY_URL set) ended with an error (exit 0 expected after IDLE_EXIT_SECONDS):\n" + tail(r))


def crafted_v1() -> list[dict]:
    """v1 messages that break the contract's STRUCTURE (they look like v1 and are about valid assets): the registry's schema must catch them."""
    base = v1("PLT01-P101", "VIB_MM_S", datetime(2026, 10, 19, tzinfo=UTC), 1.5)
    no_sensor = {k: x for k, x in base.items() if k != "sensor"}
    return [{**no_sensor, "msg_id": "CRAFTED|1"}, {**base, "msg_id": "CRAFTED|2", "value": "abc"}, {**base, "msg_id": "CRAFTED|3", "ts": 1792454400}]


def drop_topics(*names: str) -> None:
    from confluent_kafka.admin import AdminClient

    try:  # best-effort: each run creates three topics and a small broker has a limited partition budget
        for f in AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]}).delete_topics(list(names)).values():
            f.result(timeout=20)
    except Exception:  # noqa: BLE001
        pass


def governed_run(stream_env, vendor_v2_registered: bool) -> dict:  # noqa: F811
    from confluent_kafka import Producer

    raw, clean, dlq = unique("chk.raw"), unique("chk.clean"), unique("chk.dlq")
    kafka_topics(raw, clean, dlq)
    manifest = burst(raw, n=3000, seed=21)
    bad = []
    if not vendor_v2_registered:
        bad = crafted_v1()
        prod = Producer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
        for m in bad:
            prod.produce(raw, key=m["asset_id"], value=json.dumps(m).encode())
        prod.flush()
    run_contract_script(raw)
    subjects = [R1_SUBJECT_V1.format(topic=raw)]
    if vendor_v2_registered:  # the vendor's v2 contract is approved and registered as its own subject
        s2 = R1_SUBJECT_V2.format(topic=raw)
        r = sr("POST", f"/subjects/{s2}/versions", json={"schemaType": "JSON", "schema": json.dumps(R1_VENDOR_V2_JSON_SCHEMA)})
        ok(r.status_code == 200, f"The check could not register the vendor's v2 contract: {r.status_code} {r.text[:200]}")
        subjects.append(s2)
    try:
        group, prefix = unique("g"), unique("dd") + ":"
        run_governed_consumer(stream_env, raw, clean, dlq, group, prefix)
        return {"raw": raw, "manifest": manifest, "crafted": bad, "raw_rows": [json.loads(x) for x in kafka_read_all(raw)],
                "clean_rows": [json.loads(x) for x in kafka_read_all(clean)], "dlq_rows": [json.loads(x) for x in kafka_read_all(dlq)]}
    finally:
        for s in subjects:
            drop_subject(s)
        drop_topics(raw, clean, dlq)


@pytest.fixture(scope="module")
def r1_v1_only(stream_env, registry):  # noqa: F811
    """The topic has ONLY the v1 contract registered. Everything else on the topic is, by definition, outside the contract."""
    return governed_run(stream_env, vendor_v2_registered=False)


@pytest.fixture(scope="module")
def r1_v1_and_v2(stream_env, registry):  # noqa: F811
    """Both contracts registered (v1 under `<topic>-value`, the vendor's v2 under `<topic>-v2-value`)."""
    return governed_run(stream_env, vendor_v2_registered=True)


def reason_kind(d: dict) -> str:
    return str(d.get("reason", "")).split(":")[0]


def test_r1_governed_topic_lets_in_only_what_the_registry_knows(r1_v1_only):
    run, crafted_ids = r1_v1_only, {m["msg_id"] for m in r1_v1_only["crafted"]}
    expected = {m["msg_id"] for m in run["raw_rows"] if "schema_version" not in m and m["msg_id"] not in crafted_ids}
    ids = [r["msg_id"] for r in run["clean_rows"]]
    ok(len(ids) == len(set(ids)), f"`clean` has {len(ids) - len(set(ids))} repeated readings.")
    extra = set(ids) - expected
    ok(not extra, f"{len(extra)} readings in `clean` do not conform to the registered contract of the topic (e.g. {sorted(extra)[:2]}). Only the v1 schema is registered for this topic, "
       "so a v2 message (or one that breaks the v1 schema) is outside the contract and must not get in. Is your consumer reading the registry, or deciding on its own what is acceptable?")
    missing = expected - set(ids)
    ok(not missing, f"{len(missing)} valid v1 readings are missing from `clean` (e.g. {sorted(missing)[:2]}). Enforcing the contract must not block data that conforms to it.")


def test_r1_messages_from_unregistered_versions_go_to_the_dlq_with_reason_schema(r1_v1_only):
    run = r1_v1_only
    rows = [(d, json.loads(d["raw"])) for d in run["dlq_rows"] if d.get("raw")]
    foreign = [(d, m) for d, m in rows if isinstance(m, dict) and "schema_version" in m]
    n_foreign = sum(1 for m in run["raw_rows"] if "schema_version" in m)
    ok(len(foreign) == n_foreign, f"The topic received {n_foreign} messages that declare a `schema_version` with no registered contract (the vendor's v2 and the unannounced version), and the DLQ has {len(foreign)} of them. "
       "At the edge they are all non-conforming: valid or not, nobody has registered what they mean.")
    wrong = [d["reason"] for d, _ in foreign if reason_kind(d) != R1_REASON_PREFIX]
    ok(not wrong, f"{len(wrong)} of those DLQ records have a reason that does not start with `{R1_REASON_PREFIX}` (e.g. {wrong[:2]}). "
       f"The DLQ reason is what operations filters on: anything that fails the registry's contract is `{R1_REASON_PREFIX}:<detail>`.")


def test_r1_a_v1_message_that_breaks_the_schema_is_caught_by_the_schema(r1_v1_only):
    crafted = {m["msg_id"] for m in r1_v1_only["crafted"]}
    mine = {}
    for d in r1_v1_only["dlq_rows"]:
        try:
            m = json.loads(d["raw"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(m, dict) and m.get("msg_id") in crafted:
            mine[m["msg_id"]] = d["reason"]
    ok(set(mine) == crafted, f"{len(crafted) - len(mine)} of the 3 v1-looking messages with a missing field or a wrong type did not reach the DLQ.")
    wrong = {k: v for k, v in mine.items() if reason_kind({"reason": v}) != R1_REASON_PREFIX}
    ok(not wrong, f"These messages break the registered schema (a missing field, a string where a number goes, a number where a string goes) but their DLQ reason is not `{R1_REASON_PREFIX}`: {wrong}.\n"
       "Declaring the version is not enough: the message must be validated against the schema the registry holds for that version.")


def test_r1_when_the_vendor_v2_is_registered_the_translation_still_works(r1_v1_and_v2):
    run = r1_v1_and_v2
    exp, rows = run["manifest"]["clean"], run["clean_rows"]
    ids = [r["msg_id"] for r in rows]
    ok(len(ids) == len(set(ids)), f"`clean` has {len(ids) - len(set(ids))} repeated readings.")
    missing = set(exp) - set(ids)
    ok(not missing, f"{len(missing)} valid readings are missing from `clean` (e.g. {sorted(missing)[:2]}). Both contracts are registered now (v1 as `<topic>-value`, the vendor's v2 as `<topic>-v2-value`): "
       "a v2 message that conforms to its contract must be translated and accepted, as in part 1.")
    ok(set(ids) <= set(exp), f"There are {len(set(ids) - set(exp))} messages in `clean` that are not valid readings.")
    ok(all(set(r) == V1_KEYS for r in rows), "Everything in `clean` must still be exactly the v1 contract.")
    wrong = [(r["msg_id"], r["value"], exp[r["msg_id"]]["value"]) for r in rows if abs(float(r["value"]) - exp[r["msg_id"]]["value"]) > 1e-3 or r["ts"] != exp[r["msg_id"]]["ts"]]
    ok(not wrong, f"{len(wrong)} readings have a wrong value or `ts` after translating (msg_id, yours, real): {wrong[:1]}.")


def test_r1_when_the_vendor_v2_is_registered_the_defects_are_still_explained(r1_v1_and_v2):
    run = r1_v1_and_v2
    total = sum(run["manifest"]["dlq"].values())
    dlq = run["dlq_rows"]
    ok(len(dlq) == total, f"The DLQ has {len(dlq)} records and the vendor sent {total} unusable messages {run['manifest']['dlq']}.")
    ok(all(d.get("reason") and d.get("raw") for d in dlq), "Each DLQ record needs `reason` and `raw`.")
    bad = {"version": [], "measurement": [], "unit": []}
    for d in dlq:
        m, reason = json.loads(d["raw"]), d["reason"].lower()
        if m.get("schema_version") not in (None, 2):
            kind, said = "version", "schema" in reason or "version" in reason
        elif "measurement" not in m:
            kind, said = "measurement", "schema" in reason or "missing" in reason
        elif m["measurement"].get("uom") not in VALID_V2_UNITS:
            kind, said = "unit", "unit" in reason
        else:  # out of range after converting: any explanation
            continue
        if not said:
            bad[kind].append(d["reason"])
    ok(not any(bad.values()), f"Some DLQ reasons do not explain the defect: {({k: v[:2] for k, v in bad.items() if v})}.\n"
       "An unannounced version must say the schema/version is unknown, a v2 without `measurement` a schema or missing-field problem, and an unknown unit must say so.")
