# Challenge R5 — LightGBM and the metric maintenance cares about

⏱ 5–6 h · Extends **module 4** · Services: `make up-lake` (MLflow on :5001) · Check: `make challenge-5`
**Tools:** LightGBM, scikit-learn, MLflow, pandas, NumPy. **What you practice:** decision thresholds, business metrics, honest validation, explainability (SHAP), **uncertainty with a block bootstrap**.
**Mode:** solo, guided or walkthrough — see [modes](../modes.md) · set it with `/mode R5 <mode>`

## What you will build
`python -m ml.lgbm`: trains a **LightGBM** on `asset_daily_features`, picks the **alert threshold on validation** to meet the business target,
evaluates it **only once** on test with the metrics Marta understands, explains **why** each alert fires and leaves everything auditable in MLflow.
Then `python -m ml.policy_compare` answers the question a manager really asks: **“is your threshold better than the default 0.5, and how sure are you?”** with a confidence
interval that respects the fact that this is a time series.

## Why this challenge exists
In module 4 you picked the champion by *average precision*. But Marta doesn't receive an AP: she receives **alerts**. She cares about two things:
*“did you warn me in time?”* and *“how many times did you send me to a healthy pump?”*. A model with a good AP can be useless if, with the chosen
threshold, it sends 25 false alerts for every 100 healthy days: the technicians stop paying attention. And if an alert doesn't say **why** (which variable pushed the risk),
nobody knows what to check. LightGBM (*gradient boosting*) is also the most used model in the industry for tabular data like this.

There is a second trap, a quieter one. With 3–7 failures in the test period, “the new threshold saves 2 units per 100 days” is a *number*, not a *finding*: another month could give the opposite.
Data science with method means reporting **how much the number could move** (an interval), and computing that interval in a way that does not lie. The default tool,
resampling rows, lies on time series: it will happily tell you you are certain when you are not.

## Concepts
- **Threshold**: the model gives a score; the alert is `score ≥ threshold`. Choosing it is a business decision, and it is made on **validation**, never by looking at the test.
- **Three windows**: train → gap ≥ 7 days → validation → gap ≥ 7 days → test (the label looks 7 days ahead).
- **False alerts per 100 healthy asset-days**: alerts on rows with `label_failure_7d = 0`, per 100 of those rows. Business target: **≤ 5**.
- **Early warning**: a failure (CORRECTIVE work order, UTC day `f`) counts if its window `[f − 7, f − 3]` falls entirely inside the test; it is **warned** if there is any alert for that asset in that window.
- **SHAP / contributions**: `model.predict(X, pred_contrib=True)` in LightGBM gives, per row, how much each feature contributed to the score (the last column is the base value).
- **Variance with few failures**: with 3–5 failures in test, “I warned 4 of 4” and “I warned 2 of 4” can be the same model with another seed.
- **Cost of a policy**: a policy is a rule `score ≥ t`. Agreed with Maintenance: a **false alert** (alert on a row with label 0) costs **1 unit** (a wasted visit) and a **missed failure-day**
  (a row with `label_failure_7d = 1` and no alert) costs **10 units**. `cost = 1 × false alerts + 10 × missed failure-days`. Your threshold is the **cheapest** one on validation among those that meet the ≤ 5 limit.
- **Two policies**: *default* = `t = 0.5` (what a library gives you if nobody thinks about it) and *optimal* = your cost-optimal threshold. Both are compared **on the same test period**, per 100 asset-days so the unit doesn't depend on its length.
- **Bootstrap**: to see how much a statistic would move with other data, resample the data **with replacement** many times, recompute the statistic each time and look at the spread. The 2.5 % and 97.5 % percentiles form a **95 % interval** (percentile method).
  Use the **same** resample for both policies (a *paired* bootstrap): the interesting quantity is the **difference**, and the two policies share their good and bad days.
- **Why not resample rows**: the label looks 7 days ahead, so seven consecutive rows of one pump share the same future failure. They are not 300 independent observations: they are a few *episodes*.
  Resampling rows treats them as independent, loses the clumping and produces an interval that is **too narrow** (false confidence). A **block bootstrap** resamples whole blocks, so the clumping survives in every draw.
  Two valid blocks here: a **day** (all the pumps of one date) or an **asset-week** (7 consecutive days of one pump, counted from the first day of the period). Say which one you chose and why.

## The contract
**Running**: `python -m ml.lgbm` with `WAREHOUSE_PATH`, `MLFLOW_TRACKING_URI`, `MLFLOW_EXPERIMENT` (default `pump-risk`).

**One MLflow run** with:
- params `model_type = lightgbm`, `train_end`, `val_start`, `val_end`, `test_start` (gaps ≥ 7 days), `threshold`, `threshold_selected_on = validation`;
- metrics (on test except where noted) `average_precision`, `baseline_average_precision`, `false_alerts_per_100_healthy`, `early_detected`, `early_total` and `val_false_alerts_per_100_healthy`;
- artifacts at the root: `val_predictions.csv` and `test_predictions.csv` (`asset_id, feature_date, score, label_failure_7d, alert`, labeled rows only) and
  `alert_explanations.csv` (`asset_id, feature_date, rank, feature, contribution`: the **3** features with the highest |contribution| for each test alert, `rank` 1–3).

**Rules**: `alert = score ≥ threshold`; on validation the threshold meets ≤ 5 false alerts/100 **and is the cheapest one that does** (cost above); on test AP ≥ max(0.25, 3 × baseline). The check **recomputes** every metric from your CSVs.

**Policy comparison**: `python -m ml.policy_compare --predictions <csv> --threshold <t> --seed <int> --n-boot <int> --out <json>`
- `--predictions`: a CSV with at least `asset_id, feature_date, score, label_failure_7d` (your `test_predictions.csv`); `--threshold`: the optimal threshold; the default policy is always `0.5`.
- `--out`: a JSON with exactly these fields (floats may have as many decimals as you like):
```json
{
  "unit_costs": {"false_alert": 1.0, "missed_failure_day": 10.0},
  "n_rows": 300, "n_boot": 2000, "seed": 7, "ci_level": 0.95,
  "block": "asset_week", "n_blocks": 48,
  "policies": {
    "default": {"threshold": 0.5, "false_alerts": 0, "missed_failure_days": 0, "cost_per_100_days": 0.0},
    "optimal": {"threshold": 0.0, "false_alerts": 0, "missed_failure_days": 0, "cost_per_100_days": 0.0}
  },
  "cost_diff_per_100_days":   {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0},
  "missed_diff_per_100_days": {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0}
}
```
  (the values above are placeholders; `block` is `"day"` or `"asset_week"`, `n_blocks` is how many blocks the period has).
- Differences are **default minus optimal** (positive = the optimal policy is cheaper / misses fewer), per **100 asset-days** (`100 × total / rows`). `estimate` is computed on the real data, not on the resamples.
- The interval is a **95 % percentile interval** of a **paired block bootstrap**: every draw takes as many blocks as the data has, with replacement, and evaluates both policies on that same draw.
  The same `seed` must give the same JSON (use a seeded generator, for example `numpy.random.default_rng(seed)`); `n_boot ≥ 1000`.

## Your task
1. Define the three windows and write them down in `notes/decisions.md`. How many failures fall in each one? (count them before training).
2. Train a simple LightGBM. Few leaves and few failures: start with a small, regularized model.
3. Write the business metric functions (false alerts per 100 healthy days, early warning) **and the cost function** (1 per false alert, 10 per missed failure-day), and test them with a toy case you can compute by hand.
4. Pick the threshold on validation: the **cheapest** one among the candidates that meet ≤ 5 false alerts per 100. Then evaluate on test **once**. Is the target also met on test? If not, why?
5. Compute the contributions of the alerts and look at them: do they make physical sense? (does vibration weigh in on a bearing failure?).
6. Compare it in MLflow with your two module 4 models.
7. **Compare the two policies on test**, point estimates first: false alerts, missed failure-days and cost per 100 asset-days for `0.5` and for your threshold. Which one wins? *Before* running any bootstrap, write down what you expect and how sure you are.
8. **Build `ml/policy_compare.py`.** First a version that resamples **rows** (it takes 10 lines) and look at its interval. Then change the unit to a block and compare the two widths. What did the row version promise that the block version doesn't?
9. Write in `notes/decisions.md` the block you chose and *why*, and what you would tell Marta: does the interval contain 0? What would you need to be able to say “yes, it is better”? (more months? more pumps?)

## How you will know you are done
`make challenge-5` creates a warehouse with an independently computed gold, runs your script against a new experiment, downloads your artifacts and checks
windows without leakage, that the labels match gold, that `alert` is exactly `score ≥ threshold`, that the threshold meets the target on validation,
that **every logged metric matches** the one recomputed from your predictions, the minimum AP, and that each alert comes with its 3 reasons properly ordered.
It also checks that your threshold is the **cheapest** one on validation among those that meet the target, and then runs **your** `ml.policy_compare` three times:
- on your test predictions: it recomputes the false alerts, the missed failure-days, the costs and both differences, and checks that each interval contains its own estimate and has a plausible width compared with the check's own block bootstrap;
- twice with the same seed and once with another: the JSON must be identical the first two times and the interval must move with the third (a result you can't reproduce can't be reviewed);
- on a **synthetic period built to expose a naive bootstrap**: all the difference between the policies lives in one week in which every pump drifts at once. A bootstrap that resamples rows reports an interval less than 75 % as wide as the right one, and the check says so.

It prints your test figures, the interval, and whether it contains 0. The check does **not** care which way the result goes: the optimal policy may well turn out to be *no better* than 0.5 on test. That is a finding, not a failure.

## Your turn (walkthrough mode)
On your own (20–30 min), add a regression test for `ml.policy_compare` using a small predictions CSV you create and a threshold different from 0.5.
Compute both policies' costs and their difference per 100 asset-days by hand; assert that the output point estimates match, independently of the bootstrap seed.
Run `python -m pytest tests/test_r5_policy_costs.py -q` (put your test there), with two different seeds and at least 1,000 draws each.
Claude reviews your change but does not write it. Re-run `make challenge-5`.

## Comprehension questions
1. On validation you meet ≤ 5 false alerts and on test you get 7. Did you do something wrong? What would you tell Marta?
2. Why can't the threshold be chosen on test, if “it is just a number”?
3. If you retrain on train + validation after choosing the threshold, is the threshold still valid? Why?
4. An alert says the most important feature was `temp_delta_28d`. What would you tell the technician to check?
5. What weighs more for maintenance: a false alert or a failure that wasn't warned? How would you put that cost into the choice of threshold? (Here the costs are given: how sensitive is your threshold to the 10? What happens at 3 and at 30?)
6. Your validation has 7 positive rows and your test has about 40, coming from fewer than 8 failures. Why are 300 rows “less data” than they look? What does that do to the interval if you resample rows?
7. Suppose the interval for the cost difference (default minus optimal) is [−26, +1]. What do you tell Marta: “the new threshold is worse”, “it is better” or something else? What would change your answer?
8. You chose asset-week blocks. A colleague used days. Can both be right? In which situation would the two intervals differ a lot (hint: one failure that hits several pumps on the same day)?
9. Would you resample blocks if the rows were 10,000 *different* pumps, one reading each? Why not?
10. The threshold was chosen on validation by minimizing cost and then evaluated on test. If the optimal policy looks *worse* than 0.5 on test, is the threshold wrong? Which of the two periods is noisier, and why?

## Hints
<details><summary>A small model</summary>

With ~800 training rows and ~60 positives, `num_leaves` 7–15, `min_child_samples` ≈ 10 and `learning_rate` 0.05 are a good starting point. LightGBM accepts nulls without imputing.
</details>
<details><summary>Choosing the threshold</summary>

Candidates are the unique validation scores (plus one just above the maximum: “never alert”). Keep only the candidates that meet ≤ 5 false alerts per 100; among those pick the one with the lowest cost.
If several tie, say how you break the tie and why (more alerts or fewer for the same cost?).
</details>
<details><summary>A cost function you can check by hand</summary>

Take three rows with labels `[0, 1, 0]` and scores `[0.9, 0.2, 0.1]`. Count by hand the false alerts, the missed failure-days and the cost at threshold 0.5, and again at 0.05.
Your function must give those numbers; if it doesn't, one of the two is wrong, and it is worth finding out which before touching real data.
</details>
<details><summary>Blocks without a loop over draws</summary>

You don't need a Python loop that builds a DataFrame per draw. First compute, **per block**, the sums you need (false alerts, missed failure-days and rows for each policy): a small table with one row per block.
A draw is then “choose `K` block numbers with replacement” (`rng.integers(0, K, size=(n_boot, K))`) and sum those rows. Both policies and the row count come from the *same* draw.
</details>
<details><summary>Asset-week blocks</summary>

`week = (feature_date − first date of the period).days // 7`, block = `(asset_id, week)`. The last block of a period may be shorter than 7 days: that is why the statistic is *per 100 asset-days* and not a total.
</details>
<details><summary>A percentile interval and its estimate</summary>

`np.percentile(draws, [2.5, 97.5])`. The estimate is **not** the mean of the draws: it is the statistic computed once on the real data. If the estimate falls outside the interval, something is wrong (usually the unit differs between the estimate and the draws).
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
- Resampling **rows** “because that's what the tutorial does”. The interval looks professional and is too narrow.
- Reading “the interval contains 0” as “the policies are equal”. It means *this data cannot tell them apart*, which is a different (and more honest) sentence.
- Choosing the block **after** seeing which one gives the nicer interval.
- Estimating on the resamples (the mean of the draws) instead of on the real data.
- Forgetting the seed, so Monday's report and Tuesday's report disagree with no change in the data.

## On a real job
The threshold is chosen with an explicit **cost** (how much a wasted visit costs versus a failure that wasn't warned), it is reviewed every month with new data, and the explanations
travel with the alert all the way to the work order. A policy change is proposed to the business **with its interval** and the seed and data version needed to reproduce it, and the comparison
usually runs on several test windows (time-based cross-validation) so that one lucky month doesn't decide. You can go further with probability calibration, with a *moving-block* bootstrap
(blocks that overlap) and with a check on how much the optimal threshold itself moves when the validation set is resampled.

→ Next: [`R6_drift.md`](R6_drift.md) · Back to the [challenges index](README.md)
