# Module 1 — Databases: PostgreSQL and Redis

⏱ 4–5 h · Services: `make up-db` · Check: `make check-1`
**Tools:** PostgreSQL, Redis (here **Valkey**, the open fork: same protocol, same clients), SQL.

## What you will build
The plant's **system of record** in PostgreSQL (assets, work orders, risk scores and alerts), with versioned
migrations, constraints that make it impossible to store absurd data, a well-thought-out index and a view. Plus a small
cache and rate-limiting library on top of Redis.

## Why this module exists
A company's data lives in databases; *lakehouses* and models are consumers. If the system of record accepts a
work order closed before it was opened, or a risk of 1.7, **everything built on top inherits the error**, and nobody will know where it comes from.
The golden rule: **important rules are enforced in the database**, not only in the application code, because the database is the
only point everyone goes through (the API, a teammate's script, a bulk load).

And Redis exists because some data is requested a thousand times and changes once: reading it from the database every time is waste. A cache speeds things up,
but introduces a new problem —stale data— which is why the cache here always has an expiry (TTL).

## Concepts
- **Migration**: a numbered SQL file that takes the schema from one state to another. It is applied only once and is **never edited** after it has been applied.
- **Constraints**: `PRIMARY KEY`, `FOREIGN KEY`, `NOT NULL`, `CHECK`, `UNIQUE`. They are “tests” the database runs on every write.
- **Index**: a structure that lets you find rows without reading the whole table. **Column order matters** (an index on `(a, b)` serves `a` and `a+b`, not `b` alone).
- **`EXPLAIN`**: shows how Postgres will run your query (`Seq Scan` or `Index Scan`?).
- **View**: a named query; ideal for encapsulating business logic (how many failures an asset had).
- **Cache with TTL** and **atomic operations**: two simultaneous clients must not step on each other. `INCR` is atomic; “read, add, write” is not.
- **Rate limiting**: how many requests a system lets through per time window.

## The contract
Use the `DATABASE_URL` variable (in `platform.env`); never write it in the code.

**Files**: `db/migrations/NNN_name.sql` · `scripts/migrate.py` · `scripts/seed.py` · `services/common/cache.py`

**Tables** (the column names are mandatory because the CSV seeds must load; the types are your choice):

| Table | Columns | Rules the database must enforce |
|---|---|---|
| `assets` | `asset_id`(PK), `plant`, `model`, `criticality`, `install_date`, `rated_flow_m3h`, `rated_kw` | criticality ∈ {A,B,C}; unique `asset_id` |
| `work_orders` | `wo_id`(PK), `asset_id`(FK), `wo_type`, `opened_at`, `closed_at`, `failure_mode`, `cost_usd`, `downtime_hours`, `description` | type ∈ {PREVENTIVE, CORRECTIVE, INSPECTION}; cost ≥ 0; `closed_at ≥ opened_at`; a CORRECTIVE one **requires** `failure_mode`; the asset must exist |
| `risk_scores` | `asset_id`(FK), `feature_date`, `risk_score`, `model_version`, `scored_at` | key (`asset_id`, `feature_date`); risk ∈ [0,1] |
| `alerts` | `alert_id`(auto-increment), `asset_id`(FK), `created_at`, `severity`, `risk_score`, `status`, `payload`(JSON) | severity ∈ {LOW,MEDIUM,HIGH}; default status `OPEN` ∈ {OPEN,ACKNOWLEDGED,CLOSED}; risk ∈ [0,1] |

**View** `v_asset_summary(asset_id, n_failures, total_corrective_cost, last_failure_at)`: **one row per asset** (including the ones that never failed:
`n_failures = 0`, cost `0`, date `NULL`).

**Index**: the query “work orders of one asset in a date range” must use an index with 120,000 rows in the table.

**`scripts/migrate.py`**: reads `DATABASE_URL`, applies the pending migrations in order and keeps a record (`schema_migrations`). Idempotent: running it twice
doesn't fail or repeat anything. (Professional extra: store a *checksum* and warn if someone edited a migration that was already applied.)

**`scripts/seed.py`**: loads `platform/seeds/assets.csv` and `work_orders.csv`. Idempotent.

**`services/common/cache.py`**:
- `cache_get_or_set(key, ttl_seconds, compute)` → returns the cached value, or computes it, stores it with a TTL and returns it.
- `invalidate(key)`
- `allow_request(subject, limit, window_seconds)` → `True`/`False`; fixed window; **safe with 60 concurrent threads**.

## Your task
1. Design before writing: draw the 4 tables and their relationships. What is the key of each one?
2. Write the migrations (`001_core.sql`, `002_…`, `003_…`). Test each constraint **by trying to violate it** with a bad `INSERT`.
3. Write `migrate.py` and `seed.py`. Choose how to make the seed load idempotent (hint: `ON CONFLICT`).
4. Insert 100,000 synthetic work orders with `generate_series`, run `ANALYZE` and use `EXPLAIN` to design the index.
5. Write the view with a `LEFT JOIN` and aggregates with `FILTER`.
6. Write `cache.py` and test it with `redis-cli -p 6380`, looking at the TTL (`TTL key`) with your own eyes.

## How you will know you are done
`make check-1` creates a **new, empty** database at test time and applies YOUR migrations and YOUR seeds from scratch. It tests each constraint
by trying to violate it, checks the execution plan with 120,000 rows, compares your view with an independent computation and puts your rate limiter under 60 threads.

## Comprehension questions
1. Why doesn't a `CHECK` constraint in the database replace validation in the API, and vice versa?
2. Why is an applied migration never edited? How do you fix a schema mistake in production?
3. Your index is `(asset_id, opened_at)`. Does it help `WHERE opened_at > ...` without `asset_id`? Why?
4. Describe a race condition if the limiter does a `GET` and then a `SET`. Which Redis operation avoids it?
5. What would happen with a cache without a TTL when the model publishes a new risk every day?

## Hints
<details><summary>Constraint across columns</summary>

`CHECK (closed_at IS NULL OR closed_at >= opened_at)` and `CHECK (wo_type <> 'CORRECTIVE' OR failure_mode IS NOT NULL)` (equivalent to “if it is corrective, then…”).
</details>
<details><summary>Idempotent load with COPY</summary>

`COPY` doesn't support `ON CONFLICT`. Copy into a temporary table first and then `INSERT … SELECT … ON CONFLICT DO NOTHING`. Watch the names if you load two tables in the same transaction.
</details>
<details><summary>Atomic limiter</summary>

`SET key 0 EX window NX` creates the counter *with* an expiry in a single operation; after that `INCR key` is atomic and returns the new value.
</details>

## Common traps
- Putting `NOT NULL`/`CHECK` only in the API.
- An index on `(opened_at, asset_id)` when the query filters by asset first.
- Forgetting that `COUNT(*)` over a `LEFT JOIN` also counts the null rows (use `COUNT(w.wo_id)`).
- A cache without a TTL, or a TTL so long that it serves yesterday's stale data.

## On a real job
Migrations are managed with Flyway, Liquibase or Alembic; the cache is invalidated with events, not only with a TTL; the limiter usually lives in the *API gateway*.
You will use pgvector (an extension already installed in this Postgres) in module 6. **Courses:** *Data Engineering Zoomcamp* (the Postgres and Docker week).

→ Next: [`02_streaming.md`](02_streaming.md)
