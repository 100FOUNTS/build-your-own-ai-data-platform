"""Consumer: raw → validate (v1 and v2) → deduplicate (Redis) → clean | DLQ. Order: produce → flush → mark seen → commit."""

from __future__ import annotations

import json
import os
import time

import psycopg
import redis
from confluent_kafka import Consumer, Producer
from pydantic import ValidationError

from pipelines.stream.models import Reading, Rejected, from_v2

BATCH = 500
DEDUPE_TTL = (
    7 * 24 * 3600
)  # one week: covers resends after long outages and gaps that are recovered days later


def env(k: str, default: str | None = None) -> str:
    v = os.environ.get(k, default)
    if v is None:
        raise SystemExit(f"missing variable {k}")
    return v


def known_assets() -> set[str]:
    with psycopg.connect(env("DATABASE_URL")) as c:
        return {r[0] for r in c.execute("select asset_id from assets")}


def classify(raw: bytes, assets: set[str]) -> dict:
    """Returns the canonical (v1) message or raises Rejected."""
    try:
        m = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Rejected("malformed_json") from None
    if not isinstance(m, dict):
        raise Rejected("malformed_json:not an object")
    if "schema_version" in m:
        if m["schema_version"] != 2:
            raise Rejected(f"unknown_schema:version {m['schema_version']!r}")
        m = from_v2(m)
    try:
        r = Reading.model_validate(m)
    except ValidationError as exc:
        kinds = {e["type"] for e in exc.errors()}
        if "missing" in kinds:
            raise Rejected(
                "missing_field:" + ",".join(str(e["loc"][0]) for e in exc.errors() if e["type"] == "missing")
            ) from None
        if kinds & {"float_parsing", "float_type", "string_type", "literal_error"}:
            raise Rejected("bad_type") from None
        raise Rejected("out_of_range") from None
    if r.asset_id not in assets:
        raise Rejected(f"unknown_asset:{r.asset_id}")
    return r.model_dump()


def main() -> None:
    clean_t, dlq_t = (
        env("KAFKA_TOPIC_CLEAN", "plant.readings.clean"),
        env("KAFKA_TOPIC_DLQ", "plant.readings.dlq"),
    )
    prefix = env("DEDUPE_PREFIX", "dedupe:")
    idle = float(os.environ["IDLE_EXIT_SECONDS"]) if os.environ.get("IDLE_EXIT_SECONDS") else None
    cons = Consumer(
        {
            "bootstrap.servers": env("KAFKA_BOOTSTRAP"),
            "group.id": env("KAFKA_GROUP", "plant-consumer"),
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    prod = Producer(
        {"bootstrap.servers": env("KAFKA_BOOTSTRAP"), "enable.idempotence": True, "linger.ms": 10}
    )
    r = redis.Redis.from_url(env("REDIS_URL"))
    assets = known_assets()
    cons.subscribe([env("KAFKA_TOPIC_RAW", "plant.readings.raw")])
    last_msg = time.time()
    try:
        while True:
            msgs = cons.consume(BATCH, timeout=1.0)
            msgs = [m for m in msgs if not m.error()]
            if not msgs:
                if idle is not None and time.time() - last_msg > idle:
                    break
                continue
            last_msg = time.time()
            batch_ids: set[str] = set()
            for m in msgs:
                try:
                    good = classify(m.value(), assets)
                except Rejected as exc:
                    prod.produce(
                        dlq_t,
                        key=m.key(),
                        value=json.dumps(
                            {"reason": exc.reason, "raw": m.value().decode("utf-8", "replace")}
                        ).encode(),
                    )
                    continue
                key = prefix + good["msg_id"]
                if good["msg_id"] in batch_ids or r.exists(key):
                    continue  # a repeat (resend or the other gateway): not an error
                prod.produce(clean_t, key=good["asset_id"], value=json.dumps(good).encode())
                batch_ids.add(good["msg_id"])
            prod.flush()
            if batch_ids:
                pipe = r.pipeline()
                for i in batch_ids:
                    pipe.set(prefix + i, 1, ex=DEDUPE_TTL)
                pipe.execute()
            cons.commit(asynchronous=False)
    finally:
        cons.close()


if __name__ == "__main__":
    main()
