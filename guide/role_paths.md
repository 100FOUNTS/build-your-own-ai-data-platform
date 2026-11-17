# Paths by role

Nobody masters every tool in this stack, and you don't need to: pick 2–3 areas according to the role you are aiming for. These are the
recommended paths. They all start at module 0 and end at module 14 (the final integration checks whatever your path has built;
if you skipped modules, you do that test only with what you have), then continue into **Part 2, the second plant** (modules 15–18) when the role calls for it.

| Role you are aiming for | Modules (in this order) | Industry challenges | What you show in an interview |
|---|---|---|---|
| **Data engineer** | 0 · 1 · 2 · 3 · 4 (just enough to have `ml.train`) · 5 · 10 · 11 · 12 · 14 | R1 · R2 · R3 · R4 · R8 · R9 | Idempotent ingestion, streaming, data contracts (schema registry), lakehouse (Iceberg **and Delta**/dbt), SCD2, Polars, orchestration with drift checks, observability, Databricks |
| **Applied ML / AI** | 0 · 1 · 3 (up to dbt) · 4 · 5 · 6 · 7 (API) · 10 (· 16 optional) | R5 · R6 · R7 · R8 | Honest evaluation, LightGBM with a business metric and a bootstrap CI, drift, MLflow champion/challenger, evaluated RAG with a calibrated judge, OWASP-mapped guardrails, traced agents, safe text-to-SQL, MCP |
| **Backend / Full-stack** | 0 · 1 · 7 · 8 · 9 · 10 · 12 · 14 | R1 · R7 · R8 | A validated API, OIDC, real time, Next.js, containers, legacy code |
| **Platform / DevOps / SRE** | 0 · 1 (usage only) · 7 (just start it) · 10 · 11 · 12 · 13 · 8 · 14 | R8 · R9 | IaC, Kubernetes, GitOps, CI/CD with code scanning and a signed supply chain, admission policies, canary releases, SLOs and burn-rate alerts, observability, security, postmortems |
| **Forward-deployed / solutions engineer** | 0 · 1 · 6 (MCP part) · 7 (just start it) · 8 · 12 (images only) · 14 · **15** | R1 · R8 | A discovery interview that found the real constraints, an idempotent connector for a legacy system (watermark, DST, corrections, retries), a least-privilege MCP server, an offline-by-construction deployment and a runbook the client's IT can use |
| **ML Engineer (LLMs)** | 0 · 1 · 3 (up to dbt) · 4 · 5 · 6 · **16** · 10 | R5 · R6 · R7 | A fine-tune compared honestly with a rules baseline and with prompting, on hidden data, with a leakage guard; per-field macro-F1, JSON validity, MLflow-tracked experiments |
| **Inference engineer / LLM serving** | 0 · 4 (just `ml.train`) · 6 · **16** (the data, the contract and the check) · **17** · 10 | R7 | GGUF and quantization trade-offs measured under 20 concurrent users, constrained decoding, TTFT/p95/tokens per second, a decision record against an SLO, an escalation gateway (bonus) |
| **Data scientist** | 0 · 1 · 3 (up to dbt) · 4 · 5 · **18** · 10 (just the metrics) | R5 · R6 · R7 | Honest evaluation, a power calculation, a randomized staggered rollout, staggered-adoption DiD with an event study and placebo tests, why naive TWFE is biased, pump-level bootstrap intervals, a pre-registered plan and a decision memo (module 18 needs only module 0 and R5 helps) |
| **AI engineer (applications)** | 0 · 1 · 6 · 7 (API) · 10 · **15** (the MCP server) · **17** (the gateway bonus) | R7 · R8 | Evaluated RAG, guardrails, a least-privilege MCP server over a legacy system, a small model served at the edge with schema-constrained output and an escalation gateway to a bigger model |
| **Everything** | 0 → 18 | R1 → R9 | The full picture |

The challenges (in [`challenges/`](challenges/README.md)) are done after the module they extend and are not part of `make check`.

> Modules 7, 8, 9 and 14 reuse code from others. If your path skips them, look at which contracts you need (each guide lists them) and build
> just enough of that piece to satisfy them.

## What is not here (and how to study it separately)

| Tool | Why it is not here | How to practice it |
|---|---|---|
| React Native | It needs emulators/devices and a separate ecosystem | Expo + the same API; start with the official guide |
| Pulumi | It is an alternative to OpenTofu (same concept, another language) | Rewrite `infra/aws` in Python with Pulumi and compare |
| Flink | It overlaps with Spark Structured Streaming in this case | Flink SQL on the `plant.readings.clean` topic (1 h windows) |
| Airflow (running it) | Dagster covers orchestration; Airflow stays a static bonus | Start `apache/airflow` in Docker and mount `deploy/airflow/dags` |
| Backstage (running it) | Setting up the portal is a project of its own; here you write its catalog | `npx @backstage/create-app` and import your `catalog-info.yaml` |
| CloudWatch, Pinecone, Milvus, Spring Boot | Paid services, or outside this case | Official documentation; Floci emulates CloudWatch Logs/Metrics |

## Courses that complement it

Each module says at the end which free course reinforces it (Data Engineering Zoomcamp, MLOps Zoomcamp, LLM Zoomcamp,
Grafana Learning Hub, Full Stack Open, Uplimit's DevOps course…). Use them in parallel: the course teaches the topic; this exercise forces
you to **integrate** it with everything else, which is the hard part on a real job.
