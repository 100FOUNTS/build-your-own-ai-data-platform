"""The data contract of the raw topic: register the v1 readings schema in the Schema Registry and pin its compatibility rule.

Run it with `python -m pipelines.stream.contract`. It is idempotent: running it twice does not create a second version.
The registry is the source of truth: the consumer reads the schema from there, it does not import this file.
"""

from __future__ import annotations

import json
import os

import httpx

HEADERS = {"Content-Type": "application/vnd.schemaregistry.v1+json"}

# BACKWARD: a consumer that reads with the NEW schema can still read data written with the OLD one. We upgrade consumers first,
# and the raw topic keeps old messages for days (replays, late data), so new code must always be able to read the old ones.
COMPATIBILITY = "BACKWARD"

_STR = {"type": "string"}
READINGS_V1 = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "plant.readings.raw v1",
    "type": "object",
    "properties": {
        "msg_id": _STR,
        "asset_id": _STR,
        "plant": _STR,
        "ts": _STR,
        "sensor": _STR,
        "value": {"type": "number"},
        "unit": _STR,
        "quality": _STR,
    },
    "required": ["msg_id", "asset_id", "plant", "ts", "sensor", "value", "unit", "quality"],
}


def register(url: str, topic: str) -> int:
    """Pins the compatibility rule on the subject and registers the v1 schema. Returns the schema id."""
    subject = f"{topic}-value"
    with httpx.Client(base_url=url, headers=HEADERS, timeout=10) as c:
        # the rule goes first: from the very first version the subject is protected
        c.put(f"/config/{subject}", json={"compatibility": COMPATIBILITY}).raise_for_status()
        r = c.post(f"/subjects/{subject}/versions", json={"schemaType": "JSON", "schema": json.dumps(READINGS_V1)})
        r.raise_for_status()
        return r.json()["id"]


if __name__ == "__main__":
    topic = os.environ.get("KAFKA_TOPIC_RAW", "plant.readings.raw")
    schema_id = register(os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:18081"), topic)
    print(f"registered {topic}-value (schema id {schema_id}, compatibility {COMPATIBILITY})")
