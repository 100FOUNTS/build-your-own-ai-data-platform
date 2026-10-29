# Challenge R9 — Take it to Databricks

⏱ 3–4 h · Extends **modules 3 and 14** · Services: none (optional: a Databricks Free Edition account) · Check: `make challenge-9`
**Tools:** Databricks, Delta, Unity Catalog, MLflow. **What you practice:** thinking in managed platforms, costs, staged migrations, documents for making decisions.

## What you will build
`docs/databricks.md`: the document your manager would use to decide whether to move the platform to Databricks. A map of every piece you built onto its managed equivalent,
what does **not** carry over, how it is billed, a migration plan in verifiable stages and your honest recommendation.

## Why this challenge exists
You built the platform with open pieces that run on your laptop. Many companies use a managed platform (Databricks, Snowflake, Microsoft Fabric), and a
frequent data interview question is *“how would you do this in Databricks?”*. Answering it well requires knowing **which role** each piece plays (not its name),
what you gain (governance, less operations) and what you lose (cost, vendor lock-in, local environment). And writing a document that lets people **decide** is a
skill in itself: it is what separates those who implement from those who propose.

## Concepts
- **Managed lakehouse**: storage in your cloud + the platform's compute + a catalog with permissions (Unity Catalog).
- **Services worth placing** (check the current names in the official docs; they change often): managed Delta and Iceberg tables, UniForm, Unity Catalog,
  Auto Loader and Lakeflow Connect (ingestion), Lakeflow Spark Declarative Pipelines (formerly Delta Live Tables; in the docs, “Lakeflow pipelines”), Lakeflow Jobs (formerly Workflows), managed MLflow,
  Model Serving, Vector Search, Databricks SQL (SQL warehouses), AI/BI Genie, Lakebase (managed Postgres), secret scopes, system tables, Asset Bundles.
- **DBU**: the unit Databricks bills compute in; each type of compute has its own price, on top of what the cloud underneath charges.
- **Staged migration**: run in parallel, compare, switch the traffic, turn off the old. Each stage with its verification and its rollback.
- **Lock-in**: what stays tied to the vendor and how much it would cost to leave.

## The contract
`docs/databricks.md` with:
- **A mapping table** (≥ 10 rows: one per piece of YOUR platform) with columns along the lines of *piece · what it does here · Databricks equivalent · what changes*. It must cover
  lake tables, catalog, ingestion from Kafka, transformation (dbt), orchestration (Dagster), MLflow, model serving, RAG/pgvector and queries (DuckDB).
- A section whose title says **“What doesn't carry over”**: what has no direct equivalent or changes shape.
- A **cost** section: how it is billed and an order-of-magnitude estimate for your volume (without making up prices: explain the method).
- A **“Plan”** section with ≥ 4 numbered steps, each with how you verify it went well.
- A **recommendation or decision** section that discusses **vendor lock-in**.

## Your task
1. Make a list of **every** piece you built (look at your `README`, your runbook and your module 14 ADRs).
2. For each one, look up in the official Databricks docs which service plays that role. Write down the date: names change.
3. For what doesn't fit (Kafka, the SSE gateway, Keycloak, Kubernetes…), decide: does it stay outside, get replaced, get removed?
4. Estimate the cost with a method (compute hours per service × DBU/hour × public price), without making up figures.
5. *(Optional, highly recommended)* Create a **Databricks Free Edition** account, upload 7 days of history and recreate `asset_daily_features` in a notebook or a declarative pipeline.
   Compare row by row with your gold. Write in the document what was easier and what was harder than on your platform.
6. Write the recommendation as if you were going to defend it in a meeting.

## How you will know you are done
`make challenge-9` reviews the structure of the document: a table of ≥ 10 rows covering the key pieces, sections on what doesn't carry over, costs, the plan (≥ 4 numbered steps) and the recommendation,
and that vendor lock-in is discussed. **The check can't judge whether your equivalences are right**: for that, `/review R9` with Claude Code and compare it with the official docs.

## Comprehension questions
1. Which piece of your platform would gain the most from moving to Databricks? And which would lose?
2. Why doesn't Kafka “migrate” to Databricks? What would Databricks do with it?
3. For your volume (12 pumps), what would drive the cost: the data size or the time the compute is on? How would you reduce it?
4. Which part of your platform is easiest to take out of Databricks if one day you want to leave? Which is hardest?
5. How would the document change if the alternative were Snowflake or Microsoft Fabric?

## Hints
<details><summary>A good table row</summary>

“Dagster · orchestrates assets with checks and a schedule · Lakeflow Jobs · the *assets* model is lost: Jobs thinks in tasks; checks become tasks or *expectations*”. Concrete, with what you gain and what you lose.
</details>
<details><summary>Costs without making things up</summary>

Write the formula and the assumptions (how many hours a day each service is on) and leave the final figure as “≈ order of magnitude according to the pricing page as of YYYY-MM”.
</details>

## Common traps
- A map of names without saying **what changes**.
- Recommending the migration “because it is what the industry uses”, with no figures or risks.
- Copying product names from an old blog (Delta Live Tables became Lakeflow Declarative Pipelines and then Lakeflow Spark Declarative Pipelines in 2025; Workflows became Lakeflow Jobs).

## On a real job
This document would be a large ADR or a *design doc* reviewed by architecture, security and finance, with a measured proof of concept before deciding.

→ Back to the [challenges index](README.md)
