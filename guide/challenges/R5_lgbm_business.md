# Challenge R5 — LightGBM and the metric maintenance cares about

⏱ 4–5 h · Extends **module 4** · Services: `make up-lake` (MLflow on :5001) · Check: `make challenge-5`
**Tools:** LightGBM, scikit-learn, MLflow, pandas. **What you practice:** decision thresholds, business metrics, honest validation, explainability (SHAP).

## What you will build
`python -m ml.lgbm`: trains a **LightGBM** on `asset_daily_features`, picks the **alert threshold on validation** to meet the business target,
evaluates it **only once** on test with the metrics Marta understands, explains **why** each alert fires and leaves everything auditable in MLflow.

## Why this challenge exists
In module 4 you picked the champion by *average precision*. But Marta doesn't receive an AP: she receives **alerts**. She cares about two things:
*“did you warn me in time?”* and *“how many times did you send me to a healthy pump?”*. A model with a good AP can be useless if, with the chosen
threshold, it sends 25 false alerts for every 100 healthy days: the technicians stop paying attention. And if an alert doesn't say **why** (which variable pushed the risk),
nobody knows what to check. LightGBM (*gradient boosting*) is also the most used model in the industry for tabular data like this.

## Concepts
- **Threshold**: the model gives a score; the alert is `score ≥ threshold`. Choosing it is a business decision, and it is made on **validation**, never by looking at the test.
- **Three windows**: train → gap ≥ 7 days → validation → gap ≥ 7 days → test (the label looks 7 days ahead).
- **False alerts per 100 healthy asset-days**: alerts on rows with `label_failure_7d = 0`, per 100 of those rows. Business target: **≤ 5**.
- **Early warning**: a failure (CORRECTIVE work order, UTC day `f`) counts if its window `[f − 7, f − 3]` falls entirely inside the test; it is **warned** if there is any alert for that asset in that window.
- **SHAP / contributions**: `model.predict(X, pred_contrib=True)` in LightGBM gives, per row, how much each feature contributed to the score (the last column is the base value).
- **Variance with few failures**: with 3–5 failures in test, “I warned 4 of 4” and “I warned 2 of 4” can be the same model with another seed.

## The contract
**Running**: `python -m ml.lgbm` with `WAREHOUSE_PATH`, `MLFLOW_TRACKING_URI`, `MLFLOW_EXPERIMENT` (default `pump-risk`).

**One MLflow run** with:
- params `model_type = lightgbm`, `train_end`, `val_start`, `val_end`, `test_start` (gaps ≥ 7 days), `threshold`, `threshold_selected_on = validation`;
- metrics (on test except where noted) `average_precision`, `baseline_average_precision`, `false_alerts_per_100_healthy`, `early_detected`, `early_total` and `val_false_alerts_per_100_healthy`;
- artifacts at the root: `val_predictions.csv` and `test_predictions.csv` (`asset_id, feature_date, score, label_failure_7d, alert`, labeled rows only) and
  `alert_explanations.csv` (`asset_id, feature_date, rank, feature, contribution`: the **3** features with the highest |contribution| for each test alert, `rank` 1–3).

**Rules**: `alert = score ≥ threshold`; on validation the threshold meets ≤ 5 false alerts/100; on test AP ≥ max(0.25, 3 × baseline). The check **recomputes** every metric from your CSVs.

## Your task
1. Define the three windows and write them down in `notes/decisions.md`. How many failures fall in each one? (count them before training).
2. Train a simple LightGBM. Few leaves and few failures: start with a small, regularized model.
3. Write the business metric functions and test them with a toy case you can compute by hand.
4. Pick the threshold on validation (for example, the most sensitive one that meets ≤ 5). Then evaluate on test **once**. Is the target also met on test? If not, why?
5. Compute the contributions of the alerts and look at them: do they make physical sense? (does vibration weigh in on a bearing failure?).
6. Compare it in MLflow with your two module 4 models.

## How you will know you are done
`make challenge-5` creates a warehouse with an independently computed gold, runs your script against a new experiment, downloads your artifacts and checks
windows without leakage, that the labels match gold, that `alert` is exactly `score ≥ threshold`, that the threshold meets the target on validation,
that **every logged metric matches** the one recomputed from your predictions, the minimum AP, and that each alert comes with its 3 reasons properly ordered. It prints your test figures.

## Comprehension questions
1. On validation you meet ≤ 5 false alerts and on test you get 7. Did you do something wrong? What would you tell Marta?
2. Why can't the threshold be chosen on test, if “it is just a number”?
3. If you retrain on train + validation after choosing the threshold, is the threshold still valid? Why?
4. An alert says the most important feature was `temp_delta_28d`. What would you tell the technician to check?
5. What weighs more for maintenance: a false alert or a failure that wasn't warned? How would you put that cost into the choice of threshold?

## Hints
<details><summary>A small model</summary>

With ~800 training rows and ~60 positives, `num_leaves` 7–15, `min_child_samples` ≈ 10 and `learning_rate` 0.05 are a good starting point. LightGBM accepts nulls without imputing.
</details>
<details><summary>Choosing the threshold</summary>

Go through the unique validation scores from lowest to highest and keep the first one that meets the target: it is the most sensitive one allowed.
</details>
<details><summary>Saving CSVs as artifacts</summary>

Write them to a temporary folder and `mlflow.log_artifacts(folder)`: they end up at the root of the run's artifacts.
</details>
<details><summary>`download_artifacts` hangs</summary>

With this MLflow the presigned download URLs point to `seaweedfs:8333`, a hostname that only exists inside Docker. Download through the server proxy:
`GET {MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/<path from artifact_uri>/<file>`. It is a good example of why “it works in my container” isn't enough.
</details>

## Common traps
- Choosing the threshold on test, or retuning hyperparameters by looking at the test.
- Counting false alerts over **all** rows instead of only the healthy ones.
- Including the last 7 days in the predictions (they have no label: they are scored, but not evaluated).
- Explaining with the model's **global** importance instead of the contribution for **that** alert.

## On a real job
The threshold is chosen with an explicit **cost** (how much a wasted visit costs versus a failure that wasn't warned), it is reviewed every month with new data, and the explanations
travel with the alert all the way to the work order. You can go further with probability calibration and time-based cross-validation (several test windows).

→ Next: [`R6_drift.md`](R6_drift.md) · Back to the [challenges index](README.md)
