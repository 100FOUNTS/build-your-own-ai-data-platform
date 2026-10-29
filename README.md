# Build your own AI data platform

**Learn the modern data + AI stack by building a whole platform, piece by piece.** Streaming ingestion, a lakehouse, ML with
experiment tracking, a RAG assistant with evals and guardrails, APIs, a real-time frontend, security, observability, cloud
infrastructure as code, containers, Kubernetes and GitOps — all wired together around one plant, all running on your machine.

![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Time: 90-110 h](https://img.shields.io/badge/time-90--110%20h-orange)
![Runs locally](https://img.shields.io/badge/runs-100%25%20local-lightgrey)

> **Nothing is done here.** The “company” hands you a base platform (databases, Kafka, storage, Keycloak… started with one
> command), a plant simulator and data. **You write** everything on top: the ingestion, the lakehouse, the model, the AI,
> the APIs, the frontend, the infrastructure as code, the deployment and the observability.
> Each module has a guide with the **what, why and what-for**, automatic checks that verify your work against real services
> (`make check-N`) and an optional mentor (Claude Code) that asks you questions instead of giving you the answer.

**Stack:** Python · SQL · TypeScript · PostgreSQL · Redis (Valkey) · pgvector · Kafka (Redpanda) · Spark · Iceberg · Delta Lake · DuckDB · dbt ·
Polars · Dagster · pandas · scikit-learn · PyTorch · LightGBM · MLflow · Ollama · RAG · LangGraph · MCP · evals · guardrails · FastAPI · Node.js ·
React/Next.js · Keycloak (OIDC) · OpenBao · OpenTelemetry · Prometheus · Grafana · Loki · Tempo · OpenTofu · AWS (emulated) · Docker ·
Kubernetes (kind) · GitHub Actions · Argo CD · Backstage

---

## The case

You are an engineer at the company that runs **pumping plant PLT01**: 12 centrifugal pumps. Marta, the head of maintenance,
asks: *“Every day tell me which pumps are most at risk of failing in the next week, warn me in real time when one crosses the
threshold, and make it secure, observable and deployable without surprises.”*

You get: 90 days of history (777,600 readings, 16 failures), a simulator that emits readings **live** (with defective and
repeated messages, like real life), the pump master data, work orders and 12 maintenance manuals.

```
 sensors ─► Kafka ─► validate/dedupe ─► Spark ─► Iceberg ─► dbt+DuckDB ─► ML (sklearn+PyTorch, MLflow) ─► Postgres
 (simulator)  (M2)       (M2)           (M3)     (M3)         (M3)              (M4)                       (M1)
                                                                                                            │
   AI assistant: RAG(pgvector)+LangGraph+MCP+evals (M6)      Dagster orchestrates it all (M5)               ▼
                                                              FastAPI API (M7) ──► alerts ─► Kafka ─► SSE gateway (M7)
   Keycloak(OIDC) + OpenBao + threats (M8)                          │                              │         │
   Next.js/React (M9) ◄────────────────────────────────────────────┘                              ▼         ▼
   OTel→Prometheus/Tempo/Loki→Grafana (M10)      notifier → SNS/SQS on local AWS (M11)           browser   Docker/K8s (M12) · CI/CD+GitOps (M13)
```

## The modules

| # | Module | What you build | Tools | Hours |
|---|---|---|---|---|
| 0 | Engineering foundations | Professional Git, hooks, `uv`/`ruff`/`pytest`, shell, SQL | Python, uv, ruff, pytest, SQL, Git, Linux | 3–4 |
| 1 | Databases | Schema with constraints, migrations, indexes, a view; cache and rate limiting | PostgreSQL, Redis (Valkey) | 4–5 |
| 2 | Streaming | A consumer that validates, deduplicates and sets the bad aside (DLQ) | Kafka (Redpanda), Pydantic, Redis | 4–5 |
| 3 | Lakehouse | Spark batch and streaming → Iceberg; dbt on DuckDB | Spark, Iceberg, local S3, DuckDB, dbt | 8–10 |
| 4 | ML | Two models, experiments, champion registration, scoring | pandas, scikit-learn, PyTorch, MLflow | 5–6 |
| 5 | Orchestration | The pipeline as an asset graph, checks, scheduling | Dagster (Airflow as a bonus) | 3–4 |
| 6 | Generative AI | Evaluated RAG, guardrails, an agent, an MCP server | LLMs, embeddings, pgvector, LangGraph, MCP, evals, guardrails (Qdrant as a bonus) | 10–12 |
| 7 | Backend | A validated REST API, cache, real time | FastAPI, Pydantic, Node.js, TypeScript | 6–8 |
| 8 | Security | JWT/roles, secrets, a threat model | OAuth 2.0, OIDC, Keycloak, OpenBao, STRIDE | 6–8 |
| 9 | Frontend | A dashboard with SSR, login and live alerts | React, Next.js, TypeScript | 6–8 |
| 10 | Observability | Correlated traces, metrics and logs, a dashboard, alerts | OpenTelemetry, Prometheus, Grafana, Loki, Tempo | 5–6 |
| 11 | Cloud | Infrastructure as code for AWS (local) | AWS (S3, IAM, SNS/SQS, VPC), OpenTofu | 6–8 |
| 12 | Containers and K8s | Secure images and deployment to a cluster | Docker, Kubernetes (kind) | 6–8 |
| 13 | CI/CD and GitOps | A CI pipeline, Argo CD, a service catalog | GitHub Actions (act), Argo CD, Backstage | 5–6 |
| 14 | Integration | A runbook, ADRs and an end-to-end alert flow | everything | 3–4 |

**Total ≈ 90–110 hours.** You don't have to do it all: nobody masters every tool, and it pays to pick 2–3 areas for the role you want.
See the **paths by role** in [`guide/role_paths.md`](guide/role_paths.md).

## Industry challenges (optional)

Nine challenges that extend the modules with what you see most on a real job: a vendor that changes the schema and the units (R1), **Delta Lake** next to
Iceberg (R2), **Polars** at 5× scale with a benchmark done with method (R3), dimension history with **SCD2** in dbt (R4), **LightGBM** with the business metric
and the why of each alert (R5), **drift** that catches a silent unit change without alert fatigue (R6), safe **text-to-SQL** (R7), **legacy code** with
bugs, regression tests and a postmortem (R8) and the map onto **Databricks** (R9). ≈ 30–38 h. Guides in [`guide/challenges/`](guide/challenges/README.md); `make challenge-N` checks one, `make challenges` all of them.

## What your machine needs

- **Docker** (Docker Desktop on macOS/Windows, Docker Engine on Linux), with **≥ 12 GB of memory** for Docker. With 8 GB it works if you run one module at a time
  (each module says which services it needs; `make down` between modules).
- [`uv`](https://docs.astral.sh/uv/), **Node.js ≥ 20**, `make`, and [**Ollama**](https://ollama.com) running on the host with `mxbai-embed-large` (embeddings) and a chat model (`qwen3:4b-instruct`).
- For modules 11–13: OpenTofu, kind, act and kubectl (`brew install opentofu kind act kubectl` on macOS; see each project's docs on Linux).
- ~25 GB of free disk (Docker images, models and OpenTofu providers).
- You will check all of it with your own `scripts/doctor.sh` in module 0.

It was built and tested on macOS (Apple Silicon). The host ports are chosen to avoid the usual clashes (Postgres on 5433, Redis on 6380, MLflow on 5001); if one is busy on your machine, change it in `compose.yaml` and `platform.env`.

## Get started

```bash
git clone https://github.com/100FOUNTS/build-your-own-ai-data-platform.git
cd build-your-own-ai-data-platform
make setup                       # installs the Python dependencies with uv
ollama pull mxbai-embed-large    # embeddings for module 6
ollama pull qwen3:4b-instruct    # chat model for module 6 and challenge R7
make check-0                     # everything fails with a clear message: that is your starting point
```

Then open [`guide/00_foundations.md`](guide/00_foundations.md). The plant's 90-day history is already in `data/history/`
(`make history` regenerates it, identical, if you ever need to).

## How you work

1. Read the module guide in [`guide/`](guide/). Start with *Why this module exists*.
2. Start the services the module asks for (`make up-db`, `make up-stream`…).
3. Build. If you get stuck: `/hint N`. To understand a concept: `/why <topic>`.
4. Check: `make check-N` (the error message tells you what fails and why it matters; it doesn't give you the solution).
5. Review: `/review N` (Claude Code as a senior teammate). Then answer 2–3 comprehension questions in your own words.
6. Write down your decisions in `notes/`.

`make progress` shows which module you are on. `make check` runs them all. The challenges run separately: `make challenge-N`.

It also works **without Claude Code**: the guides and the checks are self-sufficient. With [Claude Code](https://claude.com/claude-code) the repo
turns on a mentor mode (`CLAUDE.md` and `.claude/commands/`): `/module N`, `/hint N`, `/why <topic>`, `/review N` (N can also be a challenge, e.g. `R5`).

## Rules of the game

1. **`data/history` and `platform/` are not edited**: they are “the plant” and “the platform team”.
2. **Don't open `checks/_heldout.jsonl`** until you finish module 6: it holds hidden test cases. Opening it takes away exactly the learning.
3. **No credentials in the code** (module 8 watches for it). Paths and URLs come from environment variables (`platform.env`, local development values only).
4. **There is no single solution.** The checks verify behavior and properties (does it duplicate when repeated? is there time leakage? is the
   forged token rejected?), not your style.

## What it covers and what it doesn't

Covered with building and automatic checks: everything in the table above. **Bonus exercises** (`make bonus`): comparing with Qdrant (module 6) and expressing the pipeline
as an Airflow DAG (module 5; written only, not run). **Industry challenges** (`make challenges`): Polars, Delta Lake, LightGBM, dbt snapshots (SCD2), PSI, text-to-SQL,
legacy code and Databricks (the last one as a design document; trying it on Databricks Free Edition is optional and not checked).

**Not covered** (on purpose): React Native, Pulumi, Flink, Spring Boot, Pinecone/Milvus, CloudWatch, and running Backstage (you only write
its catalog). They are in `guide/role_paths.md` with how to study them separately. And everything runs locally: real cloud accounts, TLS, multi-zone
networks and real costs need a real environment.

## Repository map

```
README.md  CLAUDE.md  Makefile  compose.yaml  platform.env
guide/      the guides (00_foundations … 14_integration), glossary, role paths and challenges/ (R1 … R9)
notes/      YOUR decision notebook
platform/   what the platform team gives you (simulator, configs, manuals corpus, Keycloak realm…) — not edited
data/       the plant's history (Parquet) — read only
checks/     the checks for every module and every challenge
legacy/     the previous team's code (challenge R8): here you DO fix it
db/ scripts/ sql/ services/ pipelines/ ml/ genai/ observability/ infra/ deploy/ docs/ tests/   ← this is where YOU build
.claude/    optional mentor mode for Claude Code
```

## Reference solutions

The `solution` branch has a reference solution for the nine **industry challenges** (not for the modules). Open it only after you finish a challenge:
it is *one* solution that passes `make challenge-N`, not the only one.

## License

[MIT](LICENSE).
