# Module 2 — Streaming with Kafka: validate, deduplicate and lose nothing

⏱ 4–5 h · Services: `make up-db up-stream` · Check: `make check-2`
**Tools:** Kafka (here **Redpanda**, compatible with the Kafka API and without a JVM), Python, Pydantic, Redis.

## What you will build
A consumer (`python -m pipelines.stream.consumer`) that reads sensor readings from `plant.readings.raw`, **validates** every message,
**drops repeats**, sends the good ones to `plant.readings.clean` and the defective ones to a **dead-letter queue (DLQ)** `plant.readings.dlq`
with the reason — without losing or duplicating anything even if you restart it.

## Why this module exists
In the real plant data **doesn't arrive clean**: a sensor sends garbage, the connector repeats messages after a network outage, someone
changes a format. The simulator reproduces that. A naive consumer does one of two bad things: it crashes (and stops the whole plant because of one
broken message) or it silently swallows it (and poisons the data lake). The professional design is: **isolate the bad with its reason**, so it can be
diagnosed and reprocessed later, and let the good through.

## Concepts
- **Topic, partition, offset, consumer group**: Kafka's ordered log. The *offset* says “where I am”; committing = saving my progress.
- **Delivery guarantees**: *at most once* (may lose), *at least once* (may repeat) and *exactly once* (hard). The usual
  practice is **at least once + an idempotent consumer**.
- **Order of operations**: produce → make sure it was written (`flush`) → **then** commit the offset. If you commit first and die, you lose data.
- **DLQ (dead-letter queue)**: a queue where the unrecoverable is set aside, with the reason.
- **Validation with Pydantic**: the schema as code (types, ranges, required fields).
- **Deduplication with external state**: the `msg_id` key is stored in Redis with a TTL; it survives restarts (an in-memory set doesn't).

## The contract
**v1 message** (JSON): `msg_id, asset_id, plant, ts, sensor, value, unit, quality`. Kafka key = `asset_id`.
Valid physical ranges: `VIB_MM_S` 0–30 · `TEMP_BEARING` −20–150 · `PRESS_DISCH` 0–1500 · `CURRENT_A` 0–300 · `RUN_STATE` 0–1.
An asset is valid if it exists in the Postgres `assets` table (module 1).
As in real life: not everything that arrives is usable. Find out which kinds of defect there are by looking at your DLQ.

**Running**: `python -m pipelines.stream.consumer`, configurable through the environment:
`KAFKA_BOOTSTRAP`, `KAFKA_TOPIC_RAW`, `KAFKA_TOPIC_CLEAN` (default `plant.readings.clean`), `KAFKA_TOPIC_DLQ` (`plant.readings.dlq`), `KAFKA_GROUP`,
`DATABASE_URL`, `REDIS_URL`, `DEDUPE_PREFIX` (prefix of the Redis keys), `IDLE_EXIT_SECONDS` (if set, exit with code 0 after that long without messages).

**DLQ**: each record is JSON with `reason` (it tells apart **≥ 4 kinds** of defect) and `raw` (the original message).
**Clean**: no repeats (unique `msg_id`).

## Your task
1. Create the raw topic (`make topics`) and look at the Redpanda console on http://localhost:8088.
2. Run the simulator in burst mode and **look** at the messages: `docker compose --profile tools run --rm sim burst --n 3000`. Classify the defects you see.
3. Model the message with Pydantic (`pipelines/stream/models.py`).
4. Write the consumer. Think about the order: read → validate → (duplicate?) → produce → `flush` → mark as seen → **commit**.
5. Test it: burst → consumer → count what is in clean and in the DLQ. Restart it: does anything change? Run it with **another group**: is everything repeated in clean?

## How you will know you are done
`make check-2` sends simulator bursts (with an exact manifest of what was sent) to new topics, runs your consumer and checks: `clean` = unique valid ones, `DLQ` = defective ones (each with a reason),
restarting with the same group doesn't repeat, reprocessing everything with **another group** doesn't duplicate either (persistent dedupe) and a second burst adds exactly what is new.

## Comprehension questions
1. Your process dies right after `producer.flush()` and before the `commit`. What happens on restart? Is anything lost? Is anything repeated? Who detects it?
2. Why don't valid repeats go to the DLQ?
3. What happens to deduplication if the Redis TTL is 1 minute and the resend arrives 5 minutes later? How do you choose the TTL?
4. Why is `asset_id` used as the message key?
5. What would you do if the DLQ suddenly starts growing 100×? (hint: module 10)

## Hints
<details><summary>Telling error kinds apart</summary>

`json.loads` fails → broken JSON. `ValidationError.errors()[i]["type"]` gives you `missing`, `float_parsing`, `value_error`… An unknown asset passes Pydantic: it is a business rule you will check against the database.
</details>
<details><summary>Dedupe without losing messages</summary>

If you mark “seen” in Redis **before** producing and you die, the retry will think you already sent it. Mark it after the `flush` (accept that, after a crash, a batch may be repeated in `clean`: downstream —Spark with MERGE— deduplicates again).
</details>
<details><summary>Committing in batches</summary>

Committing every message is slow; commit every N messages or when idle, always after the `flush`.
</details>

## Common traps
- `enable.auto.commit=true`: Kafka commits on its own even if your code hasn't finished.
- Dedupe in a Python `set()`: it is lost on restart.
- Catching `Exception` and dropping the message without a DLQ: the data disappears and nobody knows.
- A DLQ without `raw`: impossible to reprocess.

## On a real job
You would see versioned schemas (Schema Registry/Avro), lag metrics, alerts on the DLQ size and *exactly-once* with Kafka transactions or idempotency at the destination.
**Courses:** *Data Engineering Zoomcamp* (Kafka); to go deeper, the Redpanda documentation.

**Industry challenges (optional, once this module is green):** [R1 · New vendor](challenges/R1_new_vendor.md). Index: [`challenges/README.md`](challenges/README.md).

→ Next: [`03_lakehouse.md`](03_lakehouse.md)
