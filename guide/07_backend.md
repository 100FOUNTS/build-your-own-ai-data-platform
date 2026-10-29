# Module 7 — Backend: an API with FastAPI + Pydantic and a real-time gateway with Node.js

⏱ 6–8 h · Services: `make up-db up-stream` · Check: `make check-7`
**Tools:** FastAPI, Pydantic, Node.js, TypeScript, REST APIs, testing, Redis (cache), Kafka.

## What you will build
- **`services/api`**: a REST API (FastAPI) with assets, risk (cached in Valkey), history, work orders and alerts; strict validation with Pydantic,
  rate limiting and alert publishing to Kafka.
- **`services/gateway`**: a **Node.js + TypeScript** service that forwards alerts from Kafka to browsers in real time over **Server-Sent Events (SSE)**.

## Why this module exists
The data and the model are useless until **another system or person** uses them. The API is the platform's public contract: whoever consumes it doesn't
know about DuckDB, Spark or scikit-learn; they just make an HTTP request. A good contract takes care of three things: **what it accepts** (validation), **what it promises** (format and status codes)
and **how it behaves under pressure** (cache, rate limiting).

Two languages because they live side by side on real teams: Python for anything that touches data and ML, **TypeScript/Node** for concurrent I/O services like a gateway holding long connections.

## Concepts
- **REST and status codes**: 200/201 ok · 401 no identity · 403 not allowed · 404 doesn't exist · 409 conflict · 422 invalid input · 429 too many requests · 5xx is **your** fault.
- **Pydantic**: types and rules as code. `extra="forbid"` rejects unknown fields (a typo isn't silently ignored). Validators express rules across fields.
- **OpenAPI**: FastAPI generates it from your types (`/docs`). It is documentation that doesn't lie.
- **Cache with TTL** (cache-aside): you read from the cache; if it is missing, you compute and store. Trade-off: slightly stale data in exchange for speed.
- **SSE**: an open HTTP connection over which the server pushes events (`data: …\n\n`). Simpler than WebSockets when the flow is server→client only.
- **Readiness vs liveness**: *readiness* = “can I take traffic?” (the gateway is only ready once its Kafka consumer has joined the group; otherwise it would lose messages).
- **TypeScript `strict`**: the compiler protects you from `undefined` and from wrong types.

## The contract
**API** — `services.api.app.main:app`, run with `uvicorn`. Environment: `DATABASE_URL`, `REDIS_URL`, `KAFKA_BOOTSTRAP`, `KAFKA_TOPIC_ALERTS` (`plant.alerts`), `RATE_LIMIT_PER_MIN`,
`AUTH_DISABLED=1` (development only; module 8 adds real authentication and this switch must keep existing, off by default).

| Endpoint | Must satisfy |
|---|---|
| `GET /health` | 200 `{"status":"ok"}`; no rate limit |
| `GET /assets` | the 12 assets with `asset_id, plant, model, criticality, latest_risk, as_of`; ordered by **most recent risk** descending; unscored ones at the end (`null`) |
| `GET /assets/{id}` | detail + `n_failures`, `total_corrective_cost`, `last_failure_at` (use the module 1 view); 404 if it doesn't exist |
| `GET /assets/{id}/risk` | `{asset_id, as_of, risk_score, model_version}` for the most recent day; **cached in Valkey with key `risk:{asset_id}` and TTL ≤ 30 s**; 404 if it doesn't exist |
| `GET /assets/{id}/history?days=30` | points `{feature_date, risk_score}` in **chronological** order; `days` ∈ [1, 365] → otherwise 422 |
| `POST /work-orders` | strict body (`asset_id, wo_type, failure_mode?, description?, cost_usd ≥ 0`); CORRECTIVE requires `failure_mode`; 201 with `wo_id`; 404 unknown asset; 422 invalid input |
| `GET /work-orders?asset_id&status=open|closed&limit` | filtered list |
| `POST /alerts` | body `{asset_id, severity (LOW/MEDIUM/HIGH), risk_score ∈ [0,1], payload?}`; stores in Postgres **and publishes to Kafka** (`{alert_id, asset_id, …}`); 201 |
| `GET /alerts?status=OPEN` | alerts |
| rate limit | per client, `RATE_LIMIT_PER_MIN` per minute → **429** (with `Retry-After`) |

Also write **tests** in `services/api/tests/` (at least the Pydantic validations).

**Gateway** — `services/gateway` (TypeScript `strict`, `package.json` + `package-lock.json`, scripts `build` → `dist/index.js` and `start`). Environment: `PORT` (8010), `KAFKA_BOOTSTRAP`, `KAFKA_TOPIC_ALERTS` (and, from module 8 on, `AUTH_DISABLED=1` for development, same as the API).
`GET /healthz` → 503 while starting, 200 once it is consuming · `GET /events` → `text/event-stream`; every message on the topic arrives as `data: <json>` · any other route → 404.

## Your task
1. Design the API on paper: resources, verbs, codes. What is a 404 and what is a 422?
2. **Pydantic models** (input, output) and the routes. Look at `/docs` in the browser (`make api`).
3. Cache: your `services/common/cache.py` from module 1. Test that within the TTL you don't see a change made by hand in the database, and that `redis-cli -p 6380 ttl risk:PLT01-P101` goes down.
4. Rate limiting as *middleware*. Alerts: store and publish (what happens if Kafka is down? should the request fail? decide and justify it in `notes/`).
5. **Gateway**: `npm init`, `npm i kafkajs`, `npm i -D typescript @types/node`, `tsc --init` with `strict`. A consumer with a unique group per process, `fromBeginning: false`, a list of SSE clients, a heartbeat (`: heartbeat`) every 15 s, clean shutdown on `SIGTERM`.
6. Check it live: open `curl -N http://localhost:8010/events` in one terminal and `POST /alerts` in another.

## How you will know you are done
`make check-7` starts YOUR real API with `uvicorn` against a temporary database (and a new alerts topic), tests every endpoint and every error case, checks the cache and the TTL **in Valkey**, the Kafka message and the 429.
Then it runs `npm ci && npm run build` on your gateway, starts it, connects over SSE, publishes an alert to Kafka and waits to receive it.

## Comprehension questions
1. Why is `POST /work-orders` with a nonexistent asset a 404 and not a 422? And an invalid `wo_type`?
2. The cache keeps values for 30 s. An engineer changes the risk by hand and doesn't see it. Is it a bug? How would you solve it if it mattered (event-based invalidation)?
3. What happens if two instances of your gateway use the **same** Kafka `groupId`? And if they use different ones?
4. Why isn't the gateway “ready” until the consumer has joined the group?
5. SSE or WebSockets for this case? When would you switch?

## Hints
<details><summary>Validation across fields</summary>

`@model_validator(mode="after")` on the Pydantic model for “if it is CORRECTIVE then `failure_mode` is required”. Also declare `Field(ge=0)` for the cost.
</details>
<details><summary>Query parameters</summary>

`days: Annotated[int, Query(ge=1, le=365)] = 30` rejects out-of-range values with 422 without writing an `if`.
</details>
<details><summary>Top of the list</summary>

“Latest risk per asset”: `SELECT DISTINCT ON (asset_id) … ORDER BY asset_id, feature_date DESC` and a `LEFT JOIN` with `assets` so you don't lose the unscored assets (`ORDER BY … DESC NULLS LAST`).
</details>
<details><summary>Gateway: waiting until it is ready</summary>

kafkajs emits the `consumer.events.GROUP_JOIN` event; that is where you set `ready = true`. Until then `/healthz` answers 503.
</details>

## Common traps
- Also caching the “not found” (the asset exists tomorrow and you keep returning 404).
- Returning 200 with `{"error": …}` in the body.
- Concatenating text into the SQL instead of bound parameters (injection).
- In Node: one consumer per client connection (it explodes) instead of one per process that fans out to everyone.
- `any` everywhere in TypeScript: you lose what the types came to give you.

## On a real job
You add pagination, versioning (`/v1`), idempotent POSTs (`Idempotency-Key` header), traces and metrics (module 10) and authentication (module 8). **Courses:** *Full Stack Open* (Node/TypeScript) and *ML Zoomcamp* (FastAPI).

→ Next: [`08_security.md`](08_security.md)
