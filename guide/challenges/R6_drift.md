# Challenge R6 — Data drift without alert fatigue

⏱ 3–4 h · Extends **module 4** · Services: none · Check: `make challenge-6`
**Tools:** NumPy, pandas. **What you practice:** model monitoring, PSI, calibrating a threshold with the data itself, the cost of an alert.

## What you will build
`ml/drift.py` with two functions: `psi(...)`, the *Population Stability Index* between a reference distribution and a current one, and `drift_report(...)`,
which compares every feature of every pump between two periods and flags where there is **real** drift.

## Why this challenge exists
One day the firmware of a vibration transmitter starts sending **inches per second** but still says “mm/s”. Nobody warns anyone. That pump's vibration
“drops” 25 times and the model sees it **healthier than ever**, right when it may be failing. A drift monitor catches it within hours.
The opposite problem is just as real: a naive monitor with the textbook threshold (PSI > 0.25) on two-week windows alerts on **almost every**
pump-feature combination. On this data, 92%. Maintenance gets 100 warnings a day, stops reading them and misses the one that mattered.
The craft is in the middle: catch what is serious without drowning anyone.

## Concepts
- **PSI**: the reference is split into *bins* (by **reference quantiles**), you measure what share of the current data falls in each one and add up `(a − r) · ln(a / r)`.
  Usual convention: < 0.1 stable, 0.1–0.25 moderate, > 0.25 strong.
- **Empty bins**: `ln(0)` is infinite; you guard it with a minimum (ε).
- **Small-sample noise and autocorrelation**: 14 days of a feature that is a 7-day moving average are not 14 independent observations.
- **Calibrated threshold**: instead of a textbook number, you measure how much the PSI moves **when nothing is happening** (for example, windows inside the reference itself) and only alert above that.
- **Alert precision**: every alert has a cost (someone looks at it). A monitor is evaluated too.

## The contract
- `psi(reference, current, bins=10) -> float`: arrays or Series; ignores nulls; bins from the reference quantiles; a finite result even with empty bins.
- `drift_report(gold, reference, current, features=None) -> pd.DataFrame` with columns `asset_id`, `feature`, `psi`, `drift` (bool), **one row per (asset, feature)**.
  `gold` has the `asset_daily_features` format; `reference` and `current` are inclusive date tuples `("YYYY-MM-DD", "YYYY-MM-DD")`; `features=None` = every feature.
- On data without incidents, `drift = True` on **≤ 10%** of the pairs; with the silent unit change on one pump, that pump is flagged on `vib_mean`.

## Your task
1. Implement `psi` and test it with synthetic NumPy data: same distribution, shifted mean, everything out of range, with nulls.
2. Implement the naive version of `drift_report` (fixed threshold 0.25), reference `2026-07-03 → 2026-09-02`, current `2026-09-17 → 2026-09-30`. What percentage does it flag?
3. Look at **what** it flags: which features show up most? why those?
4. Design how to reduce the false alarms without missing the unit change (simulate the change yourself: divide one pump's vibration by 25.4 in the current period).
5. Careful: some pumps **did** degrade in September (look at the CORRECTIVE work orders). Are those flags false alarms?

## How you will know you are done
`make challenge-6` tests `psi` with synthetic cases, introduces a silent unit change on `PLT01-P105` and requires you to flag it, and measures what percentage of pairs you flag
on data without incidents (≤ 10%). It prints your percentage.

## Comprehension questions
1. Why do the bins come from the quantiles of the **reference** and not from a fixed range or from the current data?
2. Your monitor flags drift on P109 in September and P109 had a failure on 09-20. Is that a hit or a false alarm? What is a drift monitor for, then, if the model already warns?
3. What is the advantage of calibrating the threshold with the history of **each** pump versus one for the whole fleet?
4. Which other cheap checks would have caught the unit change before the PSI? (think about module 2 and dbt).
5. If the monitor alerts, what should happen next (who gets what, with what information)?

## Hints
<details><summary>What makes the PSI noisy here</summary>

With 14 values and 10 bins there are 1.4 values per bin on average: anything looks like a change. And the `*_7d` and `*_delta_28d` features move slowly and in blocks.
Try measuring the PSI of 14-day windows **inside** the reference against the rest of the reference: that is the “normal” noise.
</details>
<details><summary>Combining signals</summary>

Another route: require a high PSI **and** a relevant effect size (how many standard deviations the mean moved), or a high PSI on several related features at once. Watch out for features whose mean is close to 0 (the *delta* ones).
</details>

## Common traps
- `np.histogram` with bins that don't cover the range (values outside are lost): put `-inf` and `+inf` at the ends.
- Comparing the **whole** fleet together: one pump's change gets diluted among twelve.
- Lowering the sensitivity until it flags nothing: that also meets “≤ 10%”, but the check requires catching the unit change.

## On a real job
Tools like Evidently, NannyML or Databricks/SageMaker monitoring compute PSI and other distances; the hard part is still the same: **thresholds that don't cause fatigue**,
grouping alerts, and knowing who gets each one. Range checks at ingestion (module 2) and dbt tests are the first line; drift is the second.

→ Next: [`R7_text2sql.md`](R7_text2sql.md) · Back to the [challenges index](README.md)
