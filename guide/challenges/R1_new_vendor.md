# Challenge R1 — New vendor: two schemas on the same topic

⏱ 4–5.5 h (part 1: 3–4 h · part 2: 1–1.5 h) · Extends **module 2** · Services: `make up-db up-stream` · Check: `make challenge-1`
**Tools:** Kafka (Redpanda), Pydantic, Redis, Schema Registry (built into Redpanda), JSON Schema. **What you practice:** schema evolution, units, late data, migrations without downtime, **data contracts enforced at the edge**.

## What you will build
You extend your module 2 consumer so that, during the migration to a new sensor gateway, it accepts **two formats** at once on
`plant.readings.raw`: the usual v1 and the new vendor's **v2**, with another schema and **other units**. What goes out to `clean` is always **v1 with
canonical units**. Whatever can't be translated with certainty goes to the DLQ with a clear reason.

**Part 2** turns the contract into something the platform *enforces* instead of something people remember: the schema of the topic lives in a **Schema Registry**
with a **compatibility rule**, the registry **rejects** the vendor's breaking change, and your consumer only lets in what conforms to a registered contract.
Part 1 first, then part 2: the check for part 2 reuses the same burst.

## Why this challenge exists
In module 2 the defects were “broken message”. In real life what does the most damage is what **looks right**: the vendor renames
a field, sends vibration in inches per second or milliseconds instead of an ISO string, and for weeks both gateways
live side by side and **send the same reading twice**. If the consumer doesn't translate at the boundary, every downstream system (Spark, dbt, the model,
the API) would have to know that two formats exist. And if it translates “by eye” a version nobody announced, it silently poisons the lake.

Part 1 detects the change **after the fact**, in code, on the consumer's side: the vendor was free to break the format and you found out when the data arrived.
Part 2 moves the decision to the **entrance**: a change to the shape of the data has to be *declared and checked* before the first message goes out,
and a message outside the declared contract never gets past the door. The cheapest place to stop a bad message is the place where it has not touched anything yet.

## Concepts
- **Canonical contract and translation at the boundary** (*anti-corruption layer*): inside the platform only v1 exists; the translation happens once, on the way in.
- **Explicit schema version**: the message says which version it is. An unknown version **is not guessed**: it is set aside.
- **Canonical units**: mm/s, degC, kPa, A. Converting is part of validation (the range is checked *after* converting).
- **A dedupe key that is stable across versions**: if v1 and v2 of the same reading produce different keys, the cutover duplicates data.
- **Late data**: a reading with a `ts` from days ago is not an error; it may be a communication gap that was recovered.

**Part 2**
- **Data contract**: an explicit, versioned, *checkable* agreement between whoever produces data and whoever consumes it: the structure (a schema), the meaning (units, ranges, what `quality` means),
  who owns it and how it may change. A wiki page is documentation; a contract is documentation **plus a machine that enforces it**.
- **Schema Registry**: a small service that stores the schemas, gives each version an id and **refuses** a new version that breaks the rules of its subject.
  Here it is Redpanda's own (free, already inside the container you run; it speaks the Confluent API, which almost every tool understands).
- **Subject**: the name under which the versions of one schema are kept. The usual convention is `<topic>-value`. The compatibility rule belongs to the subject.
- **Compatibility modes**, said from the consumer's side. *Backward*: a consumer on the **new** schema can still read data written with the **old** one (upgrade consumers first). *Forward*: a consumer on the **old**
  schema can read data written with the **new** one (upgrade producers first). *Full*: both. The `_TRANSITIVE` variants compare against **every** previous version, not just the last one. *None*: no check at all.
  Which one is right depends on who upgrades first and on **how long old data stays readable** (a Kafka topic is a log: it is replayed).
- **Shape vs. meaning**: a schema says “`value` is a number”; it cannot say “in mm/s”. The units, the ranges and the dedupe stay in code (Pydantic): the registry guarantees the *structure*, not the *physics*.
- **Enforce at the entrance**: validating in Spark or dbt tests finds the problem after it has been written, copied to the lake and maybe served by the API; every consumer re-implements the rules, and the data is already there to clean up.
  At the entrance a non-conforming message costs one DLQ record, with its original bytes, and nothing else.

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

**Part 2: the contract at the edge** (JSON Schema; Avro and Protobuf need extra libraries and are the natural next step)
- **Registry**: `SCHEMA_REGISTRY_URL` (the check uses `http://localhost:18081` unless you set it). It is part of Redpanda: you do not install anything.
- **Subjects**: the canonical v1 contract of a topic is `<topic>-value`. A foreign format accepted on the same topic is registered as **its own** subject, `<topic>-v2-value` for wire version 2
  (the wire version is the message's `schema_version`; no `schema_version` means 1).
- **`python -m pipelines.stream.contract`** registers the v1 schema of `KAFKA_TOPIC_RAW` under `<topic>-value` and sets the subject's compatibility rule. It reads `KAFKA_TOPIC_RAW` and `SCHEMA_REGISTRY_URL`,
  exits 0 and is **idempotent** (running it twice leaves one version). Run it for the real topic after `make topics` (`make contract`): the registry lives in the Redpanda container, so it starts empty after a `make down`.
- **Compatibility rule**: yours to choose and justify (comprehension question 7), set **on the subject**, never `NONE`. What the check verifies is the *behavior*: the registry
  **rejects** the vendor's v2 as a new version of `<topic>-value` (HTTP 409) and **accepts** a compatible evolution (a new optional field).
- **The consumer** (same command, same variables, plus `SCHEMA_REGISTRY_URL`): when the topic has a `<topic>-value` subject it is **governed**; the consumer reads the schemas **from the registry**
  (not from a file of yours) and every message must conform to the schema of its declared version. Anything that does not (a version with no registered contract, or a message that breaks its schema)
  goes to the DLQ with `reason` starting with `schema` (`schema:<detail>`) and `raw` untouched. Without a registry, or on a topic with no subject, it behaves exactly as in part 1 and module 2.
- With **only v1 registered**, the vendor's v2 is outside the contract: all of it goes to the DLQ. When the vendor's v2 contract is registered as `<topic>-v2-value`, the part 1 translation applies to it again.

## Your task
1. Look at the messages: `make up-stream topics` and `make sim-v2`, and open them in the Redpanda console (http://localhost:8088). Classify 20 messages by hand: v1, v2, something else?
2. Write the v2 → v1 translation as a **pure function** (no Kafka) and test it with 3–4 cases, including one that must be rejected.
3. Plug it into your consumer: detect the version → translate → validate with your usual Pydantic model → deduplicate → produce.
4. Think about deduplication: which key do you use so that v1 and v2 of the same reading match? How much TTL do you need if a gap is recovered three days later?
5. Run `make check-2` again: what worked before must not break.

**Part 2: the contract at the edge**
6. Explore the registry with `curl` (see the hints) and with `rpk registry` inside the container; read the Redpanda console's *Schema Registry* page if your version shows it. List the subjects, read the global compatibility level.
7. Write the **v1 schema** (JSON Schema) from the module 2 contract and a script, `pipelines/stream/contract.py`, that sets the subject's compatibility rule and registers it. Run it twice.
8. Choose the compatibility rule and write down **why** (who upgrades first? how long does the topic keep old data?). Then try to register the vendor's v2, written by you from the table above, as a new version of the same subject.
   Read the registry's answer. Try also an evolution that *should* be allowed (a new optional field) and one that should not (a new required field, a field that changes type) **without registering them**: the registry has a dry-run endpoint.
9. Make the consumer **read the schema from the registry** and validate each message against the schema of its declared version, before the part 1 translation. Decide what “governed topic” means for your consumer and what it does when the registry is down.
10. Register the vendor's v2 contract under its own subject (written from the table, in your own words) and check that part 1 still works: v2 gets in **because it has a contract**, not because the code happens to know it.
11. `make challenge-1` again: part 1 must stay green.

## How you will know you are done
`make challenge-1` sends a burst from the new gateway with an exact manifest (which readings are valid and their real value in canonical units),
runs your consumer and checks: `clean` = every valid reading once, everything in v1 format and canonical units, correct values and `ts` after converting,
each new defect in the DLQ with its reason, and that reprocessing with another group doesn't duplicate.

**Part 2** (it needs the registry on `SCHEMA_REGISTRY_URL`): the check runs **your** `contract` script on fresh topics and talks to the registry's HTTP API like any client would. It verifies that the subject exists with a JSON Schema
that accepts a good v1 reading and rejects the structural defects, that the script is idempotent, that a compatibility rule was chosen on the subject, that the vendor's v2 is **rejected** as an evolution and a compatible
change is **accepted**. Then it runs your consumer on a burst where only v1 is registered (v2 and the unannounced version must go to the DLQ with reason `schema`, and so must v1-looking messages that break the schema,
while every valid v1 reading gets through) and on a burst where the vendor's v2 contract is also registered (translation works again). Its probes leave nothing behind: it drops its temporary subjects and topics.

## Comprehension questions
1. Why translate in the consumer and not in Spark or dbt? What would happen the day a v3 arrives?
2. A teammate proposes converting to °C any temperature above 150 “because it surely comes in °F”. What do you answer?
3. Which dedupe TTL did you choose and why? What happens if a late reading arrives after it expires?
4. How would you agree with the vendor on the end of the migration (when to stop accepting v1)? Which metric would you watch?
5. Part 1 detects the change after the fact; what does a **Schema Registry** change about *who* finds out, *when*, and what it costs? (Answer it before part 2, then again after.)

**Part 2**
6. What is a **data contract**, and what does it contain that a schema alone does not? Which parts of this contract are enforced by the registry and which by your code (units, ranges, dedupe)? Why?
7. Which **compatibility mode** did you choose and why? Who upgrades first in your platform, and what happens if you replay the topic from the beginning? What would change with `FORWARD`, `FULL`, or `BACKWARD_TRANSITIVE`?
8. The registry rejected the vendor's v2 as a new version of `<topic>-value`. Name **two legitimate ways** to get v2 in (hint: it is not “switch the rule off”) and what each costs. Who should be able to register a new subject in a real company?
9. Why validate at the **entrance** instead of in Spark or dbt tests? Name one thing you lose and one you gain. What is the cost of a false rejection at the entrance compared with finding the same problem in the lake?
10. The schema says `value` is a number. Does it catch the vendor sending vibration in in/s? What does, and where does it live? What kind of bad data will a registry never catch?
11. Your consumer reads the schema once at startup. The vendor adds an optional field, registered as a compatible new version. What happens to messages that carry it? And if the registry is **down** when the consumer starts: stop or let everything through? Argue both.
12. In this design a topic with **no subject** is simply not governed. What is the risk, how could it happen by accident (hint: what does `make down` do to the registry?), and how would a real platform close it?

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

### Part 2 (graded: open the next one only if the previous did not help)
<details><summary>1 · I don't know how to start with the registry</summary>

It is a plain HTTP API. With a toy topic called `pets`, from your terminal: `curl localhost:18081/subjects` lists the subjects, `curl localhost:18081/config` shows the global compatibility level,
`curl localhost:18081/subjects/pets-value/versions/latest` reads the newest schema. Inside the container, `docker compose --profile stream exec redpanda rpk registry --help` does the same with a CLI. First **look**, then write.
</details>
<details><summary>2 · Registering returns an error about the schema itself</summary>

The body of a registration is JSON whose `schema` field is your schema **as a string** (a JSON document inside a JSON document), plus `"schemaType": "JSON"` (otherwise the registry assumes Avro).
Toy example, with `json.dumps` doing the escaping: `httpx.post(url + "/subjects/pets-value/versions", json={"schemaType": "JSON", "schema": json.dumps(pet_schema)})`.
Set the content type `application/vnd.schemaregistry.v1+json` if your client does not. The answer includes the schema's `id`: it is global, not per subject.
</details>
<details><summary>3 · Choosing the compatibility rule</summary>

Ask two questions. **Who upgrades first** when the format changes, producers or consumers? And **how far back** must a new consumer be able to read: only the previous message, or anything still in the topic (it is a log that gets replayed)?
Then read the registry's own description of each level and test your choice: a rule you have not seen reject something is a rule you have not tested. `PUT /config/<subject>` sets it on the subject; without it the subject inherits the global default.
</details>
<details><summary>4 · Testing a change without registering it</summary>

`POST /compatibility/subjects/<subject>/versions/latest` takes the same body as a registration and answers `{"is_compatible": true|false}` without storing anything. That is what a CI job would call on every pull request.
When a registration is refused (HTTP 409) the message says *which* difference broke the rule: read it, it is the explanation you will give the vendor.
</details>
<details><summary>5 · The consumer: where does the schema come from and when?</summary>

`GET /subjects/<subject>/versions/latest` returns `{"id": …, "version": …, "schema": "<string>"}`; `json.loads` the string. The `jsonschema` library (already in your environment) builds a validator from it
(`jsonschema.validators.validator_for(schema)(schema)`) and `iter_errors(message)` tells you what is wrong. Fetch once per run, not per message, and think about what “once” means for a consumer that runs for weeks.
</details>
<details><summary>6 · Order and reasons inside the consumer</summary>

`json.loads` → find the **declared** version → fetch the validator for **that** version (none registered = not conforming) → validate → only then the part 1 translation and Pydantic.
The DLQ reason is `schema:<where>: <what>`; keep `raw` as the original bytes. A message that is not even JSON keeps its part 1 reason.
</details>

## Common traps
- Detecting v2 by “it has `device`” instead of by the declared version: the day v3 also has `device` you swallow it without knowing what it looks like.
- Validating the range **before** converting (0.05 in/s passes; so does 650 °F).
- Sending the already translated message to the DLQ: to reprocess you need the original `raw`.
- Sending v2 keys out to `clean` “just in case”: you break the contract of every consumer.
- Setting the compatibility to `NONE` “to get unblocked” when the registry rejects something: you have just switched the contract off for everyone.
- Registering the schema from the consumer at startup: whoever deploys the consumer silently changes the contract. The producer's team (or CI) registers; consumers only read.
- Validating against a copy of the schema in your repository instead of the registry's: two sources of truth that drift apart the first day someone registers a new version.
- A schema without `required`: an empty message “conforms”. Every field of the contract has to be said, not assumed.
- Testing your consumer only with messages that conform: the contract is proven by what it **rejects**.

## On a real job
Metrics of messages per version to know when to switch the old one off, and unit conversions with a library (`pint`) instead of loose constants. And, going further than part 2:
- **Avro or Protobuf** instead of JSON: smaller messages and the **schema id travels in every message** (a magic byte plus four bytes), so a consumer always knows which exact version wrote it. It needs serializers (`confluent-kafka[avro]`/`fastavro`) in producer and consumer.
- **Broker-side validation**: the broker itself checks that a message carries a registered schema id before accepting it (Redpanda has it in its licensed editions; check the license). Part 2 enforces at the consumer; with this, a bad message cannot even enter the topic.
- **Compatibility in CI**: a pipeline that calls the dry-run endpoint on every pull request that touches a schema and blocks the merge, with the schema owners as required reviewers (`CODEOWNERS`).
- **Contracts as files**, beyond the schema: owner, SLA, units, semantics, deprecation policy (the Open Data Contract Standard and `datacontract-cli` are the common starting points) and **consumer-driven contract tests** so a producer learns who it would break.
- **Operating the registry**: it is a single point of failure for new consumers (replicas, cache), its data is a Kafka topic (`_schemas`: back it up, one registry per environment), access control on who may register, and an alert on the DLQ `schema` rate per vendor.

→ Next: [`R2_delta_iceberg.md`](R2_delta_iceberg.md) · Back to the [challenges index](README.md)
