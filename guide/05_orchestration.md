# Module 5 — Orchestration: the pipeline as a graph of data assets

⏱ 3–4 h · Services: `make up-db up-stream up-lake` · Check: `make check-5`
**Tools:** Dagster (and Airflow as an alternative).

## What you will build
`pipelines/dagster/definitions.py`: your whole pipeline as an **asset graph** — history → Iceberg → dbt → model → scores —,
with **descriptions**, a **quality check** on gold and a **daily schedule** (06:00 UTC). And `make dagster` to see it and run it.

## Why this module exists
So far you run four commands by hand. On a job, nobody runs that at 6 in the morning: something has to **run it in order**,
**retry it**, **alert if it fails**, **show which table is fresh** and **not run the downstream steps if the upstream one failed**. That is an orchestrator.

**Dagster** models *data assets* (tables, models) and their dependencies — it fits a lakehouse. **Airflow** models *tasks*. The concepts (DAG,
dependencies, retries, scheduling) are the same; that is why the optional bonus asks you to express the same pipeline as an Airflow DAG.

## Concepts
- **Asset (`@asset`)**: a function that produces a piece of data; its dependencies (`deps=[other]`) form the graph.
- **Materialize**: run an asset to (re)produce its data. **Asset check**: a quality check attached to an asset (if it fails, it shows in red).
- **Schedule / sensor**: when to run (clock / event).
- **Isolation by environment**: the same definitions run on your laptop, in CI and in production **by changing environment variables**, not code.

## The contract
`pipelines.dagster.definitions` exposes `defs = Definitions(...)` with:
- ≥ 4 chained assets (dependency chain of length ≥ 4), each with a **description** (> 15 characters);
- ≥ 1 **asset check** (quality: rows, duplicates, share of positives…);
- ≥ 1 **schedule** (daily at 06:00 UTC);
- The assets **call the code you already wrote** (Spark via `docker compose run`, dbt, `ml.train`); they respect `LAKE_NS`, `WAREHOUSE_PATH`, `DATABASE_URL`, `MLFLOW_*`.
- If a step fails, the asset **fails** (raises an exception), it doesn't “pass silently”.

## Your task
1. Draw the graph in `notes/` before writing it: what does each asset produce and which one does it depend on?
2. Write the assets as thin functions that run your commands in a subprocess and raise an error if the exit code is ≠ 0.
3. Add the asset check and the schedule. Start the UI: `make dagster` (http://localhost:3400). Materialize everything with the button.
4. **Break it on purpose**: rename a column in dbt and check that Dagster marks the asset red and doesn't run the model or the scores.
5. *(Bonus)* Express the same flow as an Airflow DAG in `deploy/airflow/dags/plant_daily.py` (without running it). What changes when you think in tasks?

## How you will know you are done
`make check-5` loads your definitions, validates the graph and **runs everything with `dagster asset materialize --select '*'`** in a new Iceberg namespace, warehouse and Postgres database (your data is not touched).

## Comprehension questions
1. What is the practical difference between saying “run A, B, C” and declaring “C depends on B depends on A”?
2. If `bronze` fails, what should happen to the model? And to the API, which keeps serving yesterday's risk? How would the consumer notice?
3. What is needed for rerunning the whole pipeline to be safe? (go back to modules 2–4)
4. Why does the check run your orchestrator on new folders and databases?
5. When would you use a *sensor* instead of a *schedule*?

## Hints
<details><summary>Skeleton</summary>

```python
from dagster import Definitions, asset

@asset(deps=[bronze_readings], description="…")
def dbt_models() -> None: ...
```
To run commands: `subprocess.run([...], capture_output=True, text=True)` and raise `RuntimeError` with the last lines of the output if `returncode != 0`.
</details>
<details><summary>Spark from an asset</summary>

`docker compose --profile tools run --rm spark /jobs/backfill_history.py` inherits your environment (`LAKE_NS`…) thanks to the `${LAKE_NS}` interpolation in `compose.yaml`.
</details>
<details><summary>Asset check</summary>

`@asset_check(asset=dbt_models)` returning `AssetCheckResult(passed=..., metadata={...})`.
</details>

## Common traps
- Running the commands with hard-coded paths or names (the check changes them through environment variables).
- Not declaring dependencies: the assets run with no guaranteed order.
- Swallowing the subprocess error output: the asset stays “green” without having done anything.
- Putting business logic inside the asset instead of calling your modules (two versions of the truth).

## On a real job
You would add partitions (reprocess a date range), sensors on files/messages arriving, alerts (Slack/PagerDuty) per failed asset and injectable resources. In Airflow, the equivalents are DAGs, operators and *sensors*.
**Courses:** *Data Engineering Zoomcamp* (orchestration) and freeCodeCamp's Airflow course.

→ Next: [`06_generative_ai.md`](06_generative_ai.md)
