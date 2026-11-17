# Module 5 — Orchestration: the pipeline as a graph of data assets

⏱ 5–7 h · Services: `make up-db up-stream up-lake` · Check: `make check-5`
**Tools:** Dagster (and Airflow as an alternative), MLflow (registry aliases), NumPy/pandas (a PSI drift check).
**Mode:** solo, guided or walkthrough — see [modes](modes.md) · set it with `/mode 5 <mode>`

## What you will build
`pipelines/dagster/definitions.py`: your whole pipeline as an **asset graph** — history → Iceberg → dbt → model → scores —,
with **descriptions**, a **quality check** on gold and a **daily schedule** (06:00 UTC). And `make dagster` to see it and run it.

Then the part that makes it MLOps: a **closed loop** that keeps the model fresh without anyone watching. A **sensor** notices that something changed (a new day of gold,
or a quality/drift check that failed), a **`retrain_job`** trains a **challenger**, scores it **and the current `champion`** on the same holdout, and the champion alias
moves **only if the challenger is clearly better**. The decision and both numbers are written down as MLflow tags. Running the loop twice on the same data changes nothing.

## Why this module exists
So far you run four commands by hand. On a job, nobody runs that at 6 in the morning: something has to **run it in order**,
**retry it**, **alert if it fails**, **show which table is fresh** and **not run the downstream steps if the upstream one failed**. That is an orchestrator.

Orchestrating the pipeline is half of the story: a model is a **perishable good**. The world moves (a sensor gets replaced, a pump is overhauled, a season changes) and the
model that was the best in July quietly gets worse. Retraining on a schedule is the easy fix, and the dangerous one: if every retrain **replaces** the model in production,
one bad retrain (a broken join, a mislabeled week, an unlucky seed) puts a worse model in front of Marta's team **and nobody notices**, because nothing failed: the job was green.
A closed loop therefore has to *decide*, with a rule written down in advance and a trace you can audit later. That decision is the **promotion gate**.

**Dagster** models *data assets* (tables, models) and their dependencies — it fits a lakehouse. **Airflow** models *tasks*. The concepts (DAG,
dependencies, retries, scheduling) are the same; that is why the optional bonus asks you to express the same pipeline as an Airflow DAG.

## Concepts
- **Asset (`@asset`)**: a function that produces a piece of data; its dependencies (`deps=[other]`) form the graph.
- **Materialize**: run an asset to (re)produce its data. **Asset check**: a quality check attached to an asset (if it fails, it shows in red).
- **Schedule / sensor**: when to run (clock / event).
- **Isolation by environment**: the same definitions run on your laptop, in CI and in production **by changing environment variables**, not code.
- **Champion / challenger**: the **champion** is the model in production (the registry alias `champion`); a **challenger** is a freshly trained candidate. The challenger has to *earn* the alias.
  Both must be scored on **the same data**: a time-split holdout with purging (module 4), never rows either of them trained on, and never two different test sets.
- **Promotion gate**: the explicit rule that lets a challenger through, here *“AP(challenger) ≥ AP(champion) + margin”*. The margin exists because two trainings of the same model differ by noise;
  without it the alias flaps between equivalent models and every flap is a change in production. **Cold start**: with no champion yet, there is nothing to defend and the challenger is promoted.
- **Why a silent automatic promotion is dangerous**: an automatic retrain that always promotes turns every data problem into a production problem, with no human in the loop and no evidence
  of what was compared. A *gate* plus a *record* (tags with the decision and both metrics) is what makes automation auditable and reversible: you can answer “why is this the model today?” months later.
- **Sensor**: code Dagster evaluates every N seconds that decides whether to request a run. It must be **idempotent**: remember what it already acted on (a **cursor**, or a **`run_key`** that Dagster deduplicates), otherwise it
  starts the same run on every tick.
- **Data partition**: here, a new `feature_date` in gold. **Drift**: the data the model sees today stops looking like the data it learned from.
- **PSI (Population Stability Index)**: split the *reference* values into bins by their quantiles, measure what share of the *current* values falls in each bin, and add up `(a − r) · ln(a / r)` over the bins
  (clip the shares to a small ε so that an empty bin doesn't give `ln 0`). A common rule of thumb: < 0.1 stable, 0.1–0.25 moderate, > 0.25 strong. It is a *signal*, not a verdict: here it only decides whether to ask the loop to look again.
- **Idempotent loop**: no new data and no failed check ⇒ no run; and if a run happens anyway on the same data, it must end with the same champion.

## The contract
`pipelines.dagster.definitions` exposes `defs = Definitions(...)` with:
- ≥ 4 chained assets (dependency chain of length ≥ 4), each with a **description** (> 15 characters);
- ≥ 1 **asset check** (quality: rows, duplicates, share of positives…);
- ≥ 1 **schedule** (daily at 06:00 UTC);
- The assets **call the code you already wrote** (Spark via `docker compose run`, dbt, `ml.train`); they respect `LAKE_NS`, `WAREHOUSE_PATH`, `DATABASE_URL`, `MLFLOW_*`.
- If a step fails, the asset **fails** (raises an exception), it doesn't “pass silently”.

### The closed loop (also part of `defs`)
- **A job named `retrain_job`** (an `@job`, or `define_asset_job`). It reads gold from `WAREHOUSE_PATH` and **does not rebuild** the upstream layers. Environment: `WAREHOUSE_PATH`, `MLFLOW_TRACKING_URI`,
  `MLFLOW_EXPERIMENT`, `MODEL_NAME`, `PROMOTION_MARGIN`. Read them **when the job runs**, not when the file is imported.
  (If you build it from assets in the main graph, remember that `--select '*'` will run it in the daily pipeline too.)
- **Challenger**: it trains the module 4 models on the train side of the time split and takes the best one by average precision. Its MLflow run logs `train_end`, `test_start` (gap ≥ 7 days) and the metric `average_precision`.
- **Same holdout**: the champion (`MODEL_NAME` with alias `champion`) is scored **by the job, now,** on the **same holdout rows** as the challenger (the same split rule as `ml.train`). Whatever `ml.train` registered must be scoreable
  later by someone who did not write it: that is a requirement on how you package the model, not only on this job.
- **Gate**: promote (register the challenger and move the alias) **iff** `AP(challenger) ≥ AP(champion) + PROMOTION_MARGIN`. Otherwise the alias **does not move**. `PROMOTION_MARGIN` defaults to a value you choose (> 0).
  **No champion yet** ⇒ the challenger is promoted.
- **Record**: exactly one MLflow run per execution (the challenger's, in `MLFLOW_EXPERIMENT`) carries the tags `promotion.decision` (`promoted` or `rejected`), `promotion.challenger_ap` and `promotion.champion_ap` (numbers;
  when there was no champion, `promotion.champion_ap` may be `none`).
- **A sensor** (any name) that targets `retrain_job` and asks for a run when **(a)** gold (`asset_daily_features` in `WAREHOUSE_PATH`) has a `feature_date` it has not acted on yet, **or** **(b)** an asset check of your graph has failed since the last time it reacted.
  Evaluating it again with nothing new asks for nothing.
- **A drift check** among your asset checks: the PSI of a few gold features between an older *reference* window and the latest *current* window, computed over the **whole table** (not per pump). It reads `WAREHOUSE_PATH` and fails above your threshold.
  It must be quiet on data from a single distribution and must fail when every feature of the last 14 days is rescaled (a silent unit change).

## Your task
1. Draw the graph in `notes/` before writing it: what does each asset produce and which one does it depend on?
2. Write the assets as thin functions that run your commands in a subprocess and raise an error if the exit code is ≠ 0.
3. Add the asset check and the schedule. Start the UI: `make dagster` (http://localhost:3400). Materialize everything with the button.
4. **Break it on purpose**: rename a column in dbt and check that Dagster marks the asset red and doesn't run the model or the scores.
5. **Close the loop** (steps 5–10 are the MLOps part). Draw it first in `notes/`: what triggers it, what it compares, what it writes, what could go wrong. Write down your promotion rule **and the margin**, and why that number.
6. Write the drift check: PSI of a few features, reference window vs latest window, whole table. Run it on the real gold. **Look at what it says and ask why**: if it fires, is the data really drifting, or is your reference window unrepresentative?
   Decide the windows and the threshold with the data, not by copying a number.
7. Make the champion scoreable: open the model `ml.train` registered and ask what someone else would need to score it on new rows (the features? the preprocessing? the normalization?). Fix `ml/` if the answer is “my script's local variables”.
8. Write `retrain_job`: train the challenger, score challenger and champion on the same holdout, apply the gate, write the tags, move the alias only if it passes. Handle the cold start.
9. Write the sensor. Think about **what it remembers** between ticks. Run `make dagster` and look at the sensor tab.
10. **Try to fool it**: run the job twice on the same data; run it on data where the old labels are wrong; run it after rescaling a feature. Does the alias move when it shouldn't? Does it fail to move when it should? Read the tags of each run in http://localhost:5001.
11. *(Bonus)* Express the same flow as an Airflow DAG in `deploy/airflow/dags/plant_daily.py` (without running it). What changes when you think in tasks?

## How you will know you are done
`make check-5` loads your definitions, validates the graph and **runs everything with `dagster asset materialize --select '*'`** in a new Iceberg namespace, warehouse and Postgres database (your data is not touched).
For the loop (it only needs `make up-db up-lake`) it builds, with an independent computation, three variants of gold: one **stationary** (no drift by construction), one where the **last 14 days are rescaled**
and one where the **old labels are broken**. Then it drives your sensor tick by tick, runs your asset checks, and runs your `retrain_job` in-process against MLflow in fresh experiments and models:
a *worse* challenger must leave the champion untouched, a *better* one must be promoted, the tags must tell the truth, and running it again on the same data must change nothing.

## Your turn (walkthrough mode)
On your own, add the measured PSI and your chosen threshold as metadata on the drift asset check in `pipelines/dagster/definitions.py`.
Run `make dagster`, materialize the checked gold asset and execute its checks in the UI at http://localhost:3400.
Open the drift-check result: the numbers should explain its pass/fail status without changing the windows or promotion gate.
Then re-run `make check-5`.

## Comprehension questions
1. What is the practical difference between saying “run A, B, C” and declaring “C depends on B depends on A”?
2. If `bronze` fails, what should happen to the model? And to the API, which keeps serving yesterday's risk? How would the consumer notice?
3. What is needed for rerunning the whole pipeline to be safe? (go back to modules 2–4)
4. Why does the check run your orchestrator on new folders and databases?
5. When would you use a *sensor* instead of a *schedule*?
6. What is the difference between a champion and a challenger, and why do both have to be scored on the *same* holdout, at the time of the comparison, instead of comparing the new AP with the number logged when the champion was trained?
7. Your loop promotes automatically and last night's retrain made the model worse. Which pieces of this module would have stopped it, and which would have let you find out afterwards what happened?
8. Why does the promotion gate need a margin? What happens with a margin of 0? And with one so big that nothing is ever promoted?
9. The PSI of `vib_mean` jumps from 0.03 to 3.2 overnight. Name two very different causes and say what the loop does in each case. Is retraining the right answer for both?
10. Why must the sensor be idempotent? Describe what you would see in Dagster if it were not.
11. After a promotion, the `risk_scores` rows in Postgres were produced by the old champion. Whose job is it to refresh them, and how would the API consumer notice the difference?
12. A teammate proposes to promote automatically with no gate “because the new model is trained on more data, so it is always better”. Give them two ways in which it can be worse.

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

<details><summary>Scoring the champion on new rows</summary>

AP only depends on the *ranking* of the scores, but the champion still needs its inputs prepared exactly as when it was trained (imputation, standardization…). Where does that preprocessing live today?
MLflow can package a model *together with* its preprocessing (look at the `pyfunc` flavor and `mlflow.pyfunc.PythonModel`), so that `mlflow.pyfunc.load_model("models:/<name>@champion")` gives anyone a `predict`.
</details>
<details><summary>Loading the champion hangs and logs retries to `seaweedfs`</summary>

MLflow 3 can download big artifacts in parallel through presigned URLs, and here those URLs point to a hostname that only exists inside Docker. Tell the client to download through the tracking server instead:
`MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD=false` in the environment of whatever process loads the model (the check sets it for you; `make dagster` and your own scripts need it).
</details>
<details><summary>A sensor that remembers</summary>

`@sensor(job=...)` receives a `context` with `context.cursor` (a string you choose) and can return a `SensorResult(run_requests=[...], cursor=...)`. A `RunRequest(run_key=...)` with the same key is never launched twice.
To react to a failed check, look for the latest evaluation of an asset check in the instance (`context.instance`, something like `get_latest_asset_check_evaluation_record`) and remember which one you already handled.
Your check objects expose `check_keys`.
</details>
<details><summary>My drift check fires on the real gold</summary>

That may be an honest finding, not a bug. What is in your reference window? If some pumps degraded or were repaired during it, the “normal” you are comparing against is not stable. And with few values per bin, PSI is noisy by itself.
Try the PSI of two windows that you *know* are alike before trusting a threshold. (Challenge [R6](challenges/R6_drift.md) goes deep into this; you don't need it here.)
</details>
<details><summary>The alias moves on every rerun</summary>

Two trainings of the same code on the same data should give the same model: look for unseeded randomness. Then ask what the smallest AP difference is that you would be willing to call “better”.
</details>

## Common traps
- Running the commands with hard-coded paths or names (the check changes them through environment variables).
- Not declaring dependencies: the assets run with no guaranteed order.
- Swallowing the subprocess error output: the asset stays “green” without having done anything.
- Putting business logic inside the asset instead of calling your modules (two versions of the truth).
- Promoting “because it finished”: a green job is not evidence that the model is better.
- Comparing the challenger's AP with the AP *logged when the champion was trained*: different data, different holdout, a stale number.
- Evaluating the champion on rows it trained on, or on a holdout without purging: it looks better than it is and nothing ever beats it (or the reverse).
- A sensor that remembers nothing: it launches the same run every tick.
- Moving the alias before the tags are written (or writing tags only when promoting): the rejected decisions are the ones you will want to audit.
- Setting the drift threshold until the check is green on the first dataset you tried.

## On a real job
You would add partitions (reprocess a date range), sensors on files/messages arriving, alerts (Slack/PagerDuty) per failed asset and injectable resources. In Airflow, the equivalents are DAGs, operators and *sensors*.
For the model loop: a **shadow** period where the challenger scores live traffic without serving it, a **canary** or A/B stage, a **rollback** (moving the alias back is one call: rehearse it), a **human approval** step for high-risk models,
refreshing the published scores after a promotion, per-pump or per-segment drift with thresholds that don't cause fatigue (challenge R6) and a model registry with owners and an audit trail. Platforms like Databricks, SageMaker or Vertex package this loop; the gate is still yours to define.
**Courses:** *Data Engineering Zoomcamp* (orchestration) and freeCodeCamp's Airflow course.

→ Next: [`06_generative_ai.md`](06_generative_ai.md)
