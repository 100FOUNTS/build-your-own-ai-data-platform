# Taking the PLT01 platform to Databricks

A document to decide whether to migrate (and how). Product names checked against the official documentation in October 2026: Databricks
renames services often, so check the date before quoting it.

## Mapping table

| Piece of my platform | What it does here | Databricks equivalent | What changes |
|---|---|---|---|
| Iceberg tables on SeaweedFS (S3) | bronze with snapshots and hidden partitioning | Managed **Delta** tables in Unity Catalog (with UniForm so Iceberg engines can read them) or **managed Iceberg** tables | the platform manages storage; compaction (OPTIMIZE, liquid clustering) is automatic with *predictive optimization* |
| Iceberg REST catalog | where each table is | **Unity Catalog** | more than a catalog: row/column permissions, lineage and auditing |
| Kafka consumer (module 2) | validates, deduplicates, DLQ | Structured Streaming `readStream.format("kafka")` in a pipeline; **Lakeflow Connect** for SaaS/database sources | the DLQ becomes a quarantine table with *expectations* |
| Spark backfill job | loads the history | **Auto Loader** (`cloudFiles`) on the Parquet files | discovers new files on its own; it manages the checkpoint |
| dbt + DuckDB (silver/gold) | SQL transformation with tests | **Lakeflow Spark Declarative Pipelines** (formerly Delta Live Tables) or dbt-databricks on a SQL warehouse | dbt tests become *expectations* (or dbt stays) |
| Dagster | orchestrates assets, checks and the schedule | **Lakeflow Jobs** (formerly Workflows) | Dagster's *assets* model is lost: Jobs thinks in tasks |
| Self-hosted MLflow (Postgres + S3) | experiments and registry | **Managed MLflow** with models registered in Unity Catalog | the `champion` alias stays; UC grants the model permissions |
| Risk FastAPI + `risk_scores` in Postgres | serves the risk | **Model Serving** (endpoint) + **Lakebase** (managed Postgres) for the operational table | our own API can stay in front, reading from Lakebase |
| pgvector + RAG | search over the manuals | **Vector Search** (index synced with a Delta table) | the index updates by itself when the table changes |
| Text-to-SQL with guardrails (R7) | natural-language questions | **AI/BI Genie** | Genie respects UC permissions; our own guardrails are still needed for whatever leaves the platform |
| DuckDB for analysis | ad hoc queries | **Databricks SQL** (serverless SQL warehouse) | you pay for warehouse usage instead of using your laptop |
| Keycloak (OIDC) | user identity | workspace identity (SSO with the company IdP, SCIM) | Keycloak keeps serving our own API and frontend |
| OpenBao | secrets | **secret scopes** | only for what runs inside Databricks |
| Prometheus/Grafana/Tempo | observability | **system tables** (usage, costs, jobs, auditing) + Databricks SQL alerts | our API/gateway metrics stay in our stack |

## What doesn't carry over
- **Kafka (Redpanda)**: Databricks doesn't provide a broker; it stays outside (Confluent, MSK or the current one) and Databricks only reads it.
- **The Node SSE gateway, the Next.js frontend and Keycloak**: they are applications, not data. They stay on Kubernetes (or move to Databricks Apps, with network and scaling limits that would have to be measured).
- **Kubernetes, Argo CD and OpenTofu for the data platform**: replaced by *Databricks Asset Bundles* (deployment as code) and the Databricks Terraform provider; GitOps for the apps stays.
- **Dagster**: *asset checks* and the per-asset lineage view have no 1:1 equivalent (UC lineage is per table/column, not per business asset).
- **Running everything locally**: with Databricks there is no complete local environment; integration tests need a workspace.

## Costs
- Databricks bills in **DBUs** (a compute unit per hour) plus the cloud underneath: the price per DBU depends on the type of compute (jobs, serverless SQL, pipelines, serving) and the plan.
- PLT01 volume: ~8,600 readings/day per plant, 777,600 in 90 days (≈ 12 MB in Parquet). It is a volume that fits on one machine: the spend would be driven by **time switched on** (SQL warehouse, serving, 24/7 streaming), not by data size.
- The most expensive part would be continuous streaming: with micro-batches every 15 minutes (a scheduled `availableNow`) the cost drops a lot in exchange for latency.
- Estimate: count monthly compute hours per service × DBU/hour × the region's price on the official pricing page. The **Free Edition** is for learning and a proof of concept, not for production.

## Migration plan
1. **Test on Free Edition**: upload 7 days of history, recreate `asset_daily_features` with a declarative pipeline and compare row by row with my gold (the same test as the module 3 check). Verification: 0 differences.
2. **Bronze in parallel**: a job reads `plant.readings.clean` and writes a Delta table in UC while the current platform stays the source of truth. Verification: identical daily counts for 2 weeks.
3. **Silver/gold and ML**: move dbt (dbt-databricks) and the training to Lakeflow Jobs with MLflow in UC. Verification: the same AP and the same false alerts/100 healthy days on the same split.
4. **Serving**: publish `risk_scores` to Lakebase and point the API at that database behind a *feature flag*. Verification: the API answers the same (module 7 tests) and the p95 latency doesn't get worse.
5. **Switch off the duplicates**: only once 1–4 have been stable for a month, retire the local Iceberg, Dagster and the self-hosted MLflow. Verification: an updated runbook and an incident drill done on the new platform.

## Recommendation
For a single plant of 12 pumps **I would not migrate**: the volume fits on one machine and the current platform costs almost nothing. I would do it beyond ~10 plants,
when more than one team consumes the data or when governance (column permissions, auditing, lineage) becomes a requirement.
The main risk is **vendor lock-in**: Delta + UniForm/Iceberg and MLflow are open, so the data and the models can leave;
what stays tied are the declarative pipelines, Genie, Vector Search and the Unity Catalog permissions. If we migrate, I would start with bronze and gold (steps 1–3), which is where we gain the most and the easiest to undo.
