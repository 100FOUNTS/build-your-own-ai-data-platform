# Paths by role

Nobody masters every tool in this stack, and you don't need to: pick 2–3 areas according to the role you are aiming for. These are the
recommended paths. They all start at module 0 and end at module 14 (the final integration checks whatever your path has built;
if you skipped modules, you do that test only with what you have).

| Role you are aiming for | Modules (in this order) | Industry challenges | What you show in an interview |
|---|---|---|---|
| **Data engineer** | 0 · 1 · 2 · 3 · 5 · 10 · 11 · 12 · 14 | R1 · R2 · R3 · R4 · R8 · R9 | Idempotent ingestion, streaming, lakehouse (Iceberg **and Delta**/dbt), SCD2, Polars, orchestration, observability, Databricks |
| **Applied ML / AI** | 0 · 1 · 3 (up to dbt) · 4 · 6 · 7 (API) · 10 | R5 · R6 · R7 · R8 | Honest evaluation, LightGBM with a business metric, drift, MLflow, evaluated RAG, safe text-to-SQL, agents, MCP |
| **Backend / Full-stack** | 0 · 1 · 7 · 8 · 9 · 10 · 12 · 14 | R1 · R7 · R8 | A validated API, OIDC, real time, Next.js, containers, legacy code |
| **Platform / DevOps / SRE** | 0 · 1 (usage only) · 7 (just start it) · 10 · 11 · 12 · 13 · 8 · 14 | R8 · R9 | IaC, Kubernetes, GitOps, CI/CD, observability, security, postmortems |
| **Everything** | 0 → 14 | R1 → R9 | The full picture |

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
