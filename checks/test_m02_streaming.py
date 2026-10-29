"""Module 2 — Streaming with Kafka (Redpanda): validate, deduplicate and set the bad messages aside (DLQ)."""

from __future__ import annotations

import json
import os

import psycopg
import pytest

from conftest import ROOT, ok, kafka_read_all, kafka_topics, need, py, sim_burst, tail, unique


@pytest.fixture(scope="module")
def stream_env():
    """Temporary database with YOUR migrations and seeds (module 1) + its own dedupe prefix."""
    need("postgres", "valkey", "redpanda")
    base = os.environ["DATABASE_URL"]
    admin = psycopg.connect(base.rsplit("/", 1)[0] + "/postgres", autocommit=True)
    name = unique("chk")
    admin.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + f"/{name}"
    for script in ("scripts/migrate.py", "scripts/seed.py"):
        ok((ROOT / script).exists(), f"Missing {script} (module 1): the consumer needs the valid assets in Postgres.")
        r = py([script], env={"DATABASE_URL": url})
        ok(r.returncode == 0, f"{script} failed:\n{tail(r)}")
    yield url
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def run_consumer(env_db, raw, clean, dlq, group, prefix):
    f = ROOT / "pipelines" / "stream" / "consumer.py"
    ok(f.exists(), "Missing pipelines/stream/consumer.py (see guide/02_streaming.md).")
    r = py(["-m", "pipelines.stream.consumer"], timeout=300, env={
        "DATABASE_URL": env_db, "KAFKA_TOPIC_RAW": raw, "KAFKA_TOPIC_CLEAN": clean, "KAFKA_TOPIC_DLQ": dlq,
        "KAFKA_GROUP": group, "DEDUPE_PREFIX": prefix, "IDLE_EXIT_SECONDS": "6"})
    ok(r.returncode == 0, "`python -m pipelines.stream.consumer` ended with an error (it must exit with 0 when no messages arrive for IDLE_EXIT_SECONDS):\n" + tail(r))
    return r


@pytest.fixture(scope="module")
def stream_run(stream_env):
    raw, clean, dlq = unique("chk.raw"), unique("chk.clean"), unique("chk.dlq")
    kafka_topics(raw, clean, dlq)
    manifest = sim_burst(raw, n=4000, seed=7)
    group, prefix = unique("g"), unique("dd") + ":"
    run_consumer(stream_env, raw, clean, dlq, group, prefix)
    return {"raw": raw, "clean": clean, "dlq": dlq, "manifest": manifest, "group": group, "prefix": prefix}


def test_valid_messages_pass_without_duplicates(stream_run):
    m = stream_run["manifest"]
    clean = [json.loads(x) for x in kafka_read_all(stream_run["clean"])]
    ids = [c["msg_id"] for c in clean]
    ok(len(ids) == len(set(ids)), f"`clean` has {len(ids) - len(set(ids))} repeated messages (same msg_id). The simulator resends some: they must be dropped.")
    ok(len(clean) == m["valid_unique"], f"`clean` has {len(clean)} messages and the simulator sent {m['valid_unique']} unique valid ones ({m['duplicates']} resends, {sum(m['invalid'].values())} defective).\n"
       "If you have too few: are you rejecting good messages? If you have too many: are duplicates or defective ones left?")
    ok(all(c["asset_id"].startswith("PLT01-P1") and isinstance(c["value"], (int, float)) for c in clean), "Some messages in `clean` have the wrong shape.")


def test_defective_messages_go_to_the_dlq_with_a_reason(stream_run):
    m = stream_run["manifest"]
    dlq = [json.loads(x) for x in kafka_read_all(stream_run["dlq"])]
    bad = sum(m["invalid"].values())
    ok(len(dlq) == bad, f"The DLQ has {len(dlq)} records and the simulator sent {bad} defective messages. Each one must end up there, no more and no fewer (valid duplicates are NOT errors).")
    ok(all(d.get("reason") and d.get("raw") for d in dlq), "Each DLQ record must carry `reason` (why it was rejected) and `raw` (the original message as is). Without that nobody can diagnose or reprocess.")
    reasons = {d["reason"].split(":")[0] for d in dlq}
    ok(len(reasons) >= 4, f"The DLQ tells apart only {len(reasons)} different reasons ({sorted(reasons)}). At least 4 kinds of defect are expected (broken JSON, missing field, wrong type, out of range, unknown asset…).")


def test_restart_does_not_repeat(stream_env, stream_run):
    n1 = len(kafka_read_all(stream_run["clean"]))
    run_consumer(stream_env, stream_run["raw"], stream_run["clean"], stream_run["dlq"], stream_run["group"], stream_run["prefix"])
    n2 = len(kafka_read_all(stream_run["clean"]))
    ok(n1 == n2, f"Restarting the consumer with the same group changed `clean` from {n1} to {n2}: you are not committing offsets, or you commit them too early.")


def test_replaying_everything_with_another_group_does_not_duplicate_clean(stream_env, stream_run):
    n1 = len(kafka_read_all(stream_run["clean"]))
    run_consumer(stream_env, stream_run["raw"], stream_run["clean"], stream_run["dlq"], unique("g2"), stream_run["prefix"])
    n2 = len(kafka_read_all(stream_run["clean"]))
    ok(n1 == n2, f"Reprocessing the WHOLE raw topic with a new group added {n2 - n1} messages to `clean`. Deduplication must survive restarts (keep it outside the process: Redis with a TTL).")


def test_new_messages_arrive(stream_env, stream_run):
    n1 = len(kafka_read_all(stream_run["clean"]))
    m2 = sim_burst(stream_run["raw"], n=2000, seed=9, start_hours=500.0)
    run_consumer(stream_env, stream_run["raw"], stream_run["clean"], stream_run["dlq"], stream_run["group"], stream_run["prefix"])
    n2 = len(kafka_read_all(stream_run["clean"]))
    ok(n2 - n1 == m2["valid_unique"], f"After a second burst `clean` grew by {n2 - n1} and should have grown by {m2['valid_unique']}.")
