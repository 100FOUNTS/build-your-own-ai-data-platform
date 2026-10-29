# Challenge R1 — New vendor: two schemas on the same topic

⏱ 3–4 h · Extends **module 2** · Services: `make up-db up-stream` · Check: `make challenge-1`
**Tools:** Kafka (Redpanda), Pydantic, Redis. **What you practice:** schema evolution, units, late data, migrations without downtime.

## What you will build
You extend your module 2 consumer so that, during the migration to a new sensor gateway, it accepts **two formats** at once on
`plant.readings.raw`: the usual v1 and the new vendor's **v2**, with another schema and **other units**. What goes out to `clean` is always **v1 with
canonical units**. Whatever can't be translated with certainty goes to the DLQ with a clear reason.

## Why this challenge exists
In module 2 the defects were “broken message”. In real life what does the most damage is what **looks right**: the vendor renames
a field, sends vibration in inches per second or milliseconds instead of an ISO string, and for weeks both gateways
live side by side and **send the same reading twice**. If the consumer doesn't translate at the boundary, every downstream system (Spark, dbt, the model,
the API) would have to know that two formats exist. And if it translates “by eye” a version nobody announced, it silently poisons the lake.

## Concepts
- **Canonical contract and translation at the boundary** (*anti-corruption layer*): inside the platform only v1 exists; the translation happens once, on the way in.
- **Explicit schema version**: the message says which version it is. An unknown version **is not guessed**: it is set aside.
- **Canonical units**: mm/s, degC, kPa, A. Converting is part of validation (the range is checked *after* converting).
- **A dedupe key that is stable across versions**: if v1 and v2 of the same reading produce different keys, the cutover duplicates data.
- **Late data**: a reading with a `ts` from days ago is not an error; it may be a communication gap that was recovered.

## The contract
**v2 message** (JSON, Kafka key = asset):

```json
{"schema_version": 2, "id": "PLT01-P101|VIB_MM_S|1792454400",
 "device": {"asset": "PLT01-P101", "site": "PLT01"},
 "event_time_ms": 1792454400000,
 "measurement": {"tag": "VIB_MM_S", "val": 0.0472, "uom": "in/s"},
 "status": "OK"}
```

| v2 | v1 | Note |
|---|---|---|
| `id` | `msg_id` | same format as v1 |
| `device.asset` / `device.site` | `asset_id` / `plant` | |
| `event_time_ms` | `ts` | milliseconds since 1970 in UTC → `"YYYY-MM-DDTHH:MM:SSZ"` |
| `measurement.tag` | `sensor` | |
| `measurement.val` + `uom` | `value` + `unit` | vibration in `in/s` (1 in/s = 25.4 mm/s), temperature in `degF` (°C = (°F − 32) × 5/9); the rest, same as v1 |
| `status` `"OK"` | `quality` `"GOOD"` | |

- A message **without** `schema_version` is v1 (as in module 2). A `schema_version` other than 2 → DLQ with a reason saying the **schema/version is unknown**.
- A unit you don't know how to convert → DLQ with a reason about the **unit**. A v2 without `measurement` → DLQ (missing field). Out of range after converting → DLQ.
- `clean`: exactly the 8 v1 keys, values rounded to 4 decimals, each reading **only once** even if it arrives through both gateways.
- Same environment variables and same behavior as module 2 (the module 2 check must stay green).

## Your task
1. Look at the messages: `make up-stream topics` and `make sim-v2`, and open them in the Redpanda console (http://localhost:8088). Classify 20 messages by hand: v1, v2, something else?
2. Write the v2 → v1 translation as a **pure function** (no Kafka) and test it with 3–4 cases, including one that must be rejected.
3. Plug it into your consumer: detect the version → translate → validate with your usual Pydantic model → deduplicate → produce.
4. Think about deduplication: which key do you use so that v1 and v2 of the same reading match? How much TTL do you need if a gap is recovered three days later?
5. Run `make check-2` again: what worked before must not break.

## How you will know you are done
`make challenge-1` sends a burst from the new gateway with an exact manifest (which readings are valid and their real value in canonical units),
runs your consumer and checks: `clean` = every valid reading once, everything in v1 format and canonical units, correct values and `ts` after converting,
each new defect in the DLQ with its reason, and that reprocessing with another group doesn't duplicate.

## Comprehension questions
1. Why translate in the consumer and not in Spark or dbt? What would happen the day a v3 arrives?
2. A teammate proposes converting to °C any temperature above 150 “because it surely comes in °F”. What do you answer?
3. Which dedupe TTL did you choose and why? What happens if a late reading arrives after it expires?
4. How would you agree with the vendor on the end of the migration (when to stop accepting v1)? Which metric would you watch?
5. What would a **Schema Registry** (Avro/Protobuf) give you that this design doesn't?

## Hints
<details><summary>Where to put the translation</summary>

Before Pydantic: `json.loads` → if it has `schema_version`, translate it to a v1 dict (or reject it) → from there on the path is the module 2 one. That way the range and asset validation is the same for everyone.
</details>
<details><summary>Unit conversion without endless `if`s</summary>

A `(sensor, unit) → function` dictionary makes explicit which combinations you know how to convert; whatever is not in the dictionary is, by definition, an unknown unit.
</details>
<details><summary>Time</summary>

`datetime.fromtimestamp(ms / 1000, tz=UTC)` and then `strftime("%Y-%m-%dT%H:%M:%SZ")`. Without `tz=UTC` you get your machine's local time.
</details>

## Common traps
- Detecting v2 by “it has `device`” instead of by the declared version: the day v3 also has `device` you swallow it without knowing what it looks like.
- Validating the range **before** converting (0.05 in/s passes; so does 650 °F).
- Sending the already translated message to the DLQ: to reprocess you need the original `raw`.
- Sending v2 keys out to `clean` “just in case”: you break the contract of every consumer.

## On a real job
A **Schema Registry** with compatibility rules (*backward*/*forward*), versioned data contracts, metrics of messages per version to know when
to switch the old one off, and unit conversions with a library (`pint`) instead of loose constants.

→ Next: [`R2_delta_iceberg.md`](R2_delta_iceberg.md) · Back to the [challenges index](README.md)
