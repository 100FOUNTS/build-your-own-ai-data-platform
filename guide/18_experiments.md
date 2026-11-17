# Module 18 — Prove it works: a staggered rollout

⏱ 5–6 h · Services: none · Check: `make check-18` · **Role:** Data Scientist (it also teaches any engineer how to read an A/B-style claim)
**Tools:** NumPy/pandas (everything is done with them), the simulator extension `platform/sim/rollout.py`, a bootstrap you write yourself.
**What you practice:** experimental design, power, difference-in-differences under staggered adoption, event studies and placebo tests, bootstrap uncertainty, a decision memo.
**Part 2, "The second plant":** the platform now serves more than one plant (PLT01, PLT02, …). This module answers the question every plant asks next: *did the new policy work?* It is a **fleet-wide rollout**: it runs on a simulated fleet of the company's pump lines, not on PLT02's 12 pumps (module 15) and not on the PLT01 data.

## What you will build
The client (Marta's boss, who signs the budget) says: *"Prove that the new alert policy reduces repair time and downtime across our plants."* The simulated fleet has **96 pump lines of 4 pumps**
(384 pumps, spread over the company's plants, ids `FLEET-L01…`) and 48 weeks of time. Nobody will switch the policy on for everyone on the same day: crews have to be trained, so each **line** adopts it in some week, one
cohort after another. You will turn that constraint into a **proof**:

1. `experiments/design.py` — the **randomized staggered rollout** (which line adopts when, or never) with the **power calculation** behind it, and `notes/m18_analysis_plan.md`,
   the plan you write **before** any data exists.
2. The **panel**, produced by the plant simulator (`platform/sim/rollout.py`): one row per pump per week.
3. `experiments/analyze.py` — a **staggered-adoption estimator** (group-time effects with not-yet-treated controls), an **event study** with **placebo leads**, a
   **pump-level bootstrap** interval, and the **naive two-way fixed-effects** estimate next to it, to see how wrong it is.
4. `notes/m18_decision_memo.md` — a one-page memo: roll out, extend the pilot, or stop, and why.

## Why this module exists
A model that predicts failures (modules 4–5) and an alert policy that uses it are *claims about the future*. The first thing a manager asks is not the AUC, it is
*"so what did it change?"*. Answering that is **causal inference**, and it is the part of data science where a perfectly executed analysis gives a confidently wrong number.
Three reasons the obvious approaches fail here:

- **Before/after lies.** Downtime in the months after the rollout is lower than before. Was it the policy, the season, a quiet summer, a new crew? A before/after comparison
  cannot tell. You need **comparison pumps that did not get the policy at that moment**.
- **Choosing who goes first is a bias machine.** If Maintenance starts with the lines that fail the most (reasonable!), those lines would have improved anyway
  (**regression to the mean**), and the policy gets the credit. If it starts with the easiest lines, the effect is overstated. **Randomizing** which line goes when
  removes the chooser from the equation; it is the only thing that does.
- **The standard regression breaks under staggered adoption.** The textbook tool, a regression with pump and week dummies and a "treated" dummy (**two-way fixed effects, TWFE**),
  silently compares *newly* treated lines with *already* treated lines. When the effect **grows with time since adoption** (crews learn) or **differs between lines**, those
  already-treated lines are a *bad control*: their effect is still growing and TWFE reads it as if the controls had a downward trend. Some cells even get **negative weights**.
  The estimate can be too small, zero, or even the wrong sign. Papers published with TWFE had to be redone after 2018; this module makes you see the problem with a known truth.

On a real job this is what separates an analysis the client *acts on* from one a sceptical engineer takes apart in a meeting. The deliverable is not a number: it is a number
**with its uncertainty, its checks, and a decision rule written down before the data existed**.

It is the same instinct as challenge **R5**: R5 asked *"how much would this number move with other data?"* and answered with a **block bootstrap** (resampling whole asset-weeks,
because neighbouring rows are not independent). Here the unit that is not independent is a **pump** (its 48 weeks belong together), so you resample pumps, not pump-weeks. If you
did R5, you already own 70 % of the inference part; if not, read its Concepts first.

## Concepts
- **Potential outcomes.** For every pump-week there are two outcomes: downtime *with* the policy and downtime *without* it. We only ever see one. The **effect** of the policy on that
  pump-week is the difference, and it is never observed. Everything in this module is a way of estimating a *mean* of unobserved differences. The simulator can compute the truth
  because it can run the same week twice, with and without the policy, **with the same luck** (same failure draws, same repair-time draws).
- **ATT.** Average treatment effect on the treated: the mean effect over the pump-weeks that actually have the policy on.
- **Randomization unit.** You assign **lines**, not pumps (a crew trains per line; pumps of a line share it). Randomize at the level you *intervene*.
- **Staggered adoption / cohort.** Lines adopt in different weeks. A **cohort** `g` is the set of lines that adopt in week `g`. A **never-treated** holdout is a cohort that does not adopt
  during the experiment. **Not-yet-treated** pumps are future adopters, still untreated *now*: good controls until their turn.
- **Difference-in-differences (DiD).** Change in treated pumps minus change in comparison pumps over the same weeks. It removes everything that is constant per pump (a pump that always
  fails more) and everything shared by all pumps in a week (a hot summer). It needs **parallel trends**: without the policy, treated and comparison pumps would have moved together.
  Randomization makes that plausible; it does not prove it, hence the placebo checks.
- **Group-time ATT, `ATT(g, t)`.** The DiD of cohort `g` at week `t`, against pumps still untreated at `t`. It never uses an already-treated pump as a control. (This is the idea behind
  the *Callaway–Sant'Anna* estimator.) You then **average** the `ATT(g, t)` cells in the way the question needs: overall, or by **event time** `e = t − g` (weeks since adoption).
- **Event study.** `ATT` by event time `e`: how the effect builds up after adoption (`e ≥ 0`) and what happens *before* it (`e < 0`).
- **Placebo leads / pre-trend test.** Before adoption the policy does nothing, so the estimated "effect" at `e = −5 … −2` must be about 0. If it is not, treated and comparison pumps were
  already drifting apart and your estimate of the real effect is contaminated. It is a *test of the assumption*, run on data that cannot contain the effect.
- **TWFE and its weights.** `Y = pump effect + week effect + β·treated`. With one adoption date it is a clean DiD. With staggered adoption, `β` is a weighted average of the effects of
  the treated cells with weights that can be **negative**: `weight = (treatment residual after removing pump means and week means) / (sum of squared residuals)`. The share of weight that
  is negative is a direct diagnostic of how untrustworthy it is.
- **Bootstrap at the right unit.** Resample whole **pumps** (all their weeks) with replacement; recompute the *whole estimator* every time; take the 2.5 % and 97.5 % percentiles.
  Resampling pump-weeks would pretend that a pump's 48 weeks are 48 independent observations (they share a pump level and the policy exposure), and give a too narrow interval (R5: same story).
  Keep each cohort's size fixed in every draw (stratified resampling) so that a draw never loses a cohort.
- **Power and MDE.** The **minimum detectable effect** is the smallest true effect the design would detect with probability `power` (say 80 %) at significance `α` (say 5 %). It is
  `(z₁₋α/₂ + z_power) × SE`. If the MDE is bigger than any effect that matters, *do not run the experiment*: you would spend eight months to learn nothing. It is computed **before** the data.
- **Pre-registration.** Writing down the estimand, estimator, checks and the decision rule *before* looking at outcomes. Without it, every analyst has a thousand small choices
  (which weeks, which pumps, which outcome) and will, innocently, make the ones that give the nicer result.
- **The truth, by counterfactual.** In real life you never see it. In the simulator you can: simulate again with nobody treated, same seed, subtract. That is how the check grades you, and
  how *you* can validate a method before trusting it on data where no one knows the answer.

## The contract
**The fleet** (`platform/sim/rollout.py`, don't modify it; `python platform/sim/rollout.py frame` prints the frame as JSON). 96 lines `FLEET-L01…L96`, 4 pumps each (`FLEET-L01-P1…`), 48 weeks (`0…47`). It is a simulation of the company's fleet, so its pumps are not PLT01's or PLT02's.
A pump fails with some weekly probability and then stops for 12–60 h; the downtime is booked in the week the work order opens (like `downtime_hours` in `work_orders.csv`).
Older lines (low numbers) fail more. The new policy shortens repairs and prevents some failures; the effect **depends on the pump's criticality and the line's crew** and **grows with weeks
since adoption**. That is all you are told about the truth.

**1. `python -m experiments.design --seed <int> --out <json>`** writes a design JSON (the simulator reads `adoption`; the rest is what you *declare*):
```json
{
  "seed": 18,
  "adoption": {"FLEET-L01": 18, "FLEET-L02": null, "...": "week of adoption, or null = never (all 96 lines)"},
  "randomization": {"unit": "line", "seed": 18, "cohort_sizes": {"12": 8, "18": 8, "never": 8}, "method": "free text"},
  "power": {"alpha": 0.05, "power": 0.8, "sd_pump_week_hours": 0.0, "mde_hours": 0.0}
}
```
- Adoption weeks in **10 … 40** (history to learn from before, follow-up after). **At least 3** different adoption weeks, **at least 2 lines per cohort**, adopting cohorts that differ by **at most 1 line**.
  Some comparison must remain at every event time `0…7` (keep never-treated lines, or leave enough weeks between the first and the last adoption).
- The same `--seed` gives the same design; another seed gives another one. `randomization.cohort_sizes` (key: adoption week as text, plus `"never"`) must match `adoption`.
- `power.mde_hours` is in **hours of downtime per pump-week**; `sd_pump_week_hours` is the standard deviation of that outcome you used (the PLT01 history, `platform/seeds/work_orders.csv`, is your pilot data).

**2. The panel:** `python platform/sim/rollout.py panel --design data/rollout/design.json --seed 18 --out data/rollout/panel.csv`. One row per pump-week:
`pump_id, group_id, criticality, week, adopt_week, policy_on, failures, repair_hours, downtime_hours`. The outcome is `downtime_hours`. (`--expected` writes a **noise-free** panel; see Hints.)

**3. `python -m experiments.analyze --panel <csv> --design <json> --seed <int> --n-boot <int> --out <json>`** reads **only** the panel and the design and writes:
```json
{
  "estimand": "overall_att_hours_per_pump_week", "seed": 18, "n_boot": 1000, "ci_level": 0.95,
  "att": {"estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0},
  "event_study": [{"event_time": -5, "estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0}, "... every event time from -5 to -2 and from 0 to 7 ..."],
  "twfe": {"estimate": 0.0, "negative_weight_mass": 0.0}
}
```
- **The estimand (fixed, so that estimators can be compared with the truth).** The mean effect over **every treated pump-week that has a comparison pump**: weeks `t ≥ g` for which some pump is
  *not yet treated* at `t` (adopts after `t`) or never treated. Every pump-week weighs the same. (If everybody ends up treated, the last cohort's weeks have no comparison and are outside it.)
  The event-study value at `e` is the mean effect over the treated pump-weeks at event time `e` (every pump-week of that `e` weighs the same). A stacked DiD or any other valid estimator is
  allowed, **if it targets this estimand** (reweight if needed).
- `att`: the estimate and a 95 % **percentile interval** of a **pump-level bootstrap** (resample pumps within each adoption cohort, recompute everything). `n_boot` is a command line option.
- `event_study`: the leads `−5…−2` are **placebos** (an estimator may need its baseline to end before them). `−1` is optional.
- `twfe`: the naive estimate (pump and week fixed effects, one treatment dummy: `policy_on`), and `negative_weight_mass` = minus the sum of the negative TWFE weights on treated cells.
- The analysis **must not read the truth** (`true_effects`, `true_att`, `policy=False`…): it only knows what a real analyst would know.

**4. `notes/m18_analysis_plan.md`** — written **first**. Headings (any wording that contains these ideas): *estimand · primary outcome · randomization/design · estimator · inference (bootstrap) · power and MDE ·
validity checks (placebo/pre-trend) · decision rule*. It must state the **same MDE** as `design.json`, the comparison group (not-yet-treated / never-treated) and the bootstrap. ≥ 200 words.

**5. `data/rollout/result.json`** — the output of `analyze` on **your own** panel, with `--n-boot` ≥ 1000. And **`notes/m18_decision_memo.md`**: 120–700 words, headings *Decision · Evidence ·
Uncertainty · Validity · Limits / next steps*; one line `Decision: ROLL OUT | EXTEND | STOP`; it quotes your estimate and both ends of your interval; it mentions TWFE, the placebo check, the bootstrap, the MDE and the unit (hours).
The decision must follow from your own interval and your own decision rule (an interval containing 0 does not support `ROLL OUT`).

## Your task
1. **Look at the problem before touching code.** Read the PLT01 `work_orders.csv`: how many corrective failures per pump per week? how long are the repairs? What would the outcome look like per pump-week
   (mostly zeros and a few big numbers)? Why does that make the experiment hard? Write the answer in `notes/`. *(Why: the noise level decides whether a proof is possible at all.)*
2. **Design the rollout.** Decide the unit (line), the number of cohorts, their weeks, the size of each, and whether you keep a never-treated holdout. For every choice write the trade-off:
   a holdout gives clean controls forever but withholds the policy from some lines; more cohorts means smaller steps but a shorter follow-up for the last ones.
   Randomize with a seeded generator. **Then think about what could still go wrong by chance:** with 96 lines, pure randomization can put the oldest lines first. How would you protect against that
   (blocking / stratifying on something you know *before* the experiment)? *(Why: randomization is only fair on average; you have one draw.)*
3. **Do the power calculation** with the pilot variance from PLT01 and your schedule, and compute the MDE in hours per pump-week. Compare it with what the client would call a worthwhile reduction
   (compare with the PLT01 mean downtime per pump-week). Would you run this experiment? What would you change if the MDE were too big (more lines? more weeks? a less noisy outcome?)
   *(Why: it is the line between "no effect found" and "no experiment possible".)*
4. **Write `notes/m18_analysis_plan.md` and stop.** Estimand, outcome, estimator, bootstrap, checks, and a **decision rule with numbers** ("roll out if the whole interval is below 0 and …"). Commit it
   *before* the next step. *(Why: this is the pre-registration; the git history is your proof it came first.)*
5. **Generate the panel** with the simulator and look at it: plot (or tabulate) mean downtime by week for each cohort. Can you *see* the effect? Can you see the age gradient? Do not estimate anything yet.
6. **Build the estimator on the noise-free panel first** (`--expected`). There is no noise, parallel trends hold exactly: a correct estimator returns the truth to 6 decimals. Any gap is a bug, not luck.
   *(Why: debugging an estimator on noisy data means you can never tell a bug from bad luck.)* Implement group-time ATTs against not-yet-treated pumps, aggregate overall and by event time, and make the leads exactly 0.
7. **Add the naive TWFE** (just the within-transformation: remove pump and week means from the outcome and from the treatment, then regress). Compute its weights and `negative_weight_mass`.
   Compare it with the truth on the noise-free panel. By how much and in which direction does it miss, and why (which cells does it use as controls)? Write the explanation in `notes/`.
8. **Add the bootstrap** (pump-level, stratified by cohort) and run on the noisy panel. Start with 100 draws while you debug; use ≥ 1000 for the result. Read the placebo leads: are their intervals around 0?
9. **Run on your panel and write the memo.** Fill `data/rollout/result.json`. Write the memo for the maintenance director, in plain words: what happened to downtime in hours, how sure you are, what you checked, what you do *not* know.
10. **Only now, compare with the truth:** `python platform/sim/rollout.py truth --design data/rollout/design.json --seed 18` prints the true ATT. Where does it sit in your interval? Would you have decided differently?
    In real life nobody gives you this number; that is why steps 5–8 exist.
11. *(Bonus)* Repeat for the secondary outcome *repair hours per failure* (a different estimand: it is defined only on failure weeks). What changes about the sample and the interpretation?

## How you will know you are done
`make check-18` needs **no services**. It runs your `experiments.design` (three times: the same seed twice, another seed once) and verifies that the assignment is a **reproducible randomization**, balanced between
cohorts, that the declared sizes are the real ones, that early adopters are **comparable on a baseline you could not see**, and that your MDE is in the right order of magnitude for the schedule.
Then it generates a **noise-free staggered panel** with the simulator and runs your `analyze`: your estimate (overall and for event times 0–7) must equal the **true effect, computed by counterfactual simulation**, to 1e-6 hours,
your placebo leads must be exactly 0, and the **naive TWFE** you report must equal the check's own TWFE and sit well away from the truth on that panel, with negative weights. Finally it generates **three noisy panels** from *your* design
and runs your `analyze` with a smaller bootstrap: your 95 % interval must cover the true ATT in at least two of the three, have a plausible width, the event study must follow the true dynamics and the placebo leads must be quiet.
It also checks that your code does not read the truth, that the pre-registered plan and the memo exist with the required sections, that the memo quotes your own `result.json` and that its decision does not contradict your interval.

## Comprehension questions
1. Why is a before/after comparison of downtime not evidence, even if downtime fell a lot? Name two things other than the policy that could have changed it.
2. Why randomize *which line goes when* instead of starting with the most problematic lines? What does randomization guarantee, and what does it **not** guarantee with only 96 lines?
3. In the staggered design, who are the valid controls for cohort `g` at week `t`? Why is an already-treated line not valid when the effect keeps growing?
4. Explain TWFE's failure in one paragraph to someone who knows regression but not causal inference. What does a negative weight mean in plain words?
5. Your TWFE estimate is smaller in magnitude than the truth. In another experiment (an effect that *fades* after a few weeks) could it be larger? Why?
6. What do the placebo leads test, exactly? If they are all near 0, what have you shown, and what have you *not* shown?
7. Why must the bootstrap resample pumps rather than pump-weeks? What would happen to the interval width if you resampled pump-weeks? How is this the same lesson as R5's block bootstrap?
8. The treatment is assigned at line level. When would resampling *lines* be the right choice instead of pumps (hint: a shock that hits every pump of a line in the same week, unrelated to the policy)?
9. The MDE is 1.8 h per pump-week and the PLT01 mean downtime is 3 h. What would you tell the client *before* the experiment? And if the interval you finally get is `[−2.1, +0.4]`?
10. What is the value of writing the decision rule *before* the data? Give a concrete way in which skipping it leads to a biased conclusion even with honest analysts.
11. The interval says downtime fell by 0.9–1.7 h per pump-week. A teammate says "so we cut downtime by 1.3 h per pump-week fleet-wide". Which assumptions does that sentence add? (Think: lines that never adopted, the weeks after the last observation, a plant that is not in the fleet.)
12. The simulator has no spillover between lines. In a real plant, what could make one line's adoption change another line's downtime, and which assumption of the whole method does that break?

## Hints
<details><summary>Hint 1 — the data as matrices</summary>

The panel is balanced (every pump has every week). Sort by `pump_id, week` and reshape the outcome into an array of shape `(n_pumps, n_weeks)`. Put each pump's adoption week in a vector (use a big
number for "never"). Then "the controls of cohort `g` at week `t`" is a boolean mask over rows, and every group-time ATT is two means over rows of one column. No pandas groupby needed in the hot loop,
which matters when the bootstrap repeats it a thousand times.
</details>
<details><summary>Hint 2 — one ATT(g, t) in words</summary>

For cohort `g` and week `t`: (mean over the cohort's pumps of `Y_t − baseline`) minus (mean over its controls of `Y_t − baseline`), where `baseline` is *each pump's own* average over a pre-period that ends before the
leads you want as placebos (for leads down to `−5`, up to week `g − 6`). Controls: pumps whose adoption week is later than both `t` and `g`. One single pre-week as baseline works too, but it makes
every cell inherit that week's luck: with this much noise it will give you an interval too wide to decide anything. Averaging the pre-period is what the pump fixed effect is *for*.
</details>
<details><summary>Hint 3 — averaging the cells</summary>

The overall estimate is the average of `ATT(g, t)` over cells `t ≥ g` with controls, each cell weighted by the number of pumps of its cohort (so that every treated pump-week weighs the same). The event-study
value at `e` is the same average restricted to `t = g + e`. Leads are the cells with `e < 0`; they use the same baseline and the same controls rule.
</details>
<details><summary>Hint 4 — the noise-free panel is your unit test</summary>

`python platform/sim/rollout.py panel --expected …` replaces every outcome by its expectation. Untreated outcomes are exactly *pump level + week level*, so a right estimator is exact. If you are off by 0.02 h
it is not rounding: look at which cells you average, which pumps you use as controls at the cell's week, and whether the baseline overlaps the leads.
</details>
<details><summary>Hint 5 — TWFE without a regression library</summary>

For a balanced panel, the TWFE coefficient on a dummy `D` is `Σ D̃·Y / Σ D̃²`, where `D̃ = D − (row mean) − (column mean) + (grand mean)` (removing pump and week means). The same `D̃ / Σ D̃²` evaluated on treated
cells are the **weights** TWFE puts on each cell's effect; they add up to 1 and some are negative. You don't need `statsmodels` (it is not in `pyproject.toml`); if you prefer a regression with dummies, `numpy.linalg.lstsq` gives the same number.
</details>
<details><summary>Hint 6 — the power formula</summary>

For a difference of two means, `MDE = (z₁₋α/₂ + z_power) × SE`, with `statistics.NormalDist().inv_cdf` for the z values. For a pump-level DiD, `SE ≈ σ × √(1/n_treated + 1/n_control) × √(1/w_post + 1/w_pre)` with
`σ` the SD of the **pump-week** outcome, `n_*` the number of **pumps** (not lines) and `w_*` the number of weeks averaged in each period. It is a back-of-the-envelope figure: the point is the order of magnitude and
how it moves when you change the schedule. A simulation (generate panels, estimate, count how often you detect an effect of a given size) is the honest alternative and a good bonus.
</details>
<details><summary>Hint 7 — a bootstrap that doesn't lose a cohort</summary>

Make a list of row indices per cohort (and one for the never-treated). A draw is: for each list, sample its own length with replacement, concatenate, and re-run the *whole* estimator on `Y[idx]`, `adopt[idx]`.
Percentiles of the draws are the interval. Fix the generator's seed: the same seed must give the same JSON.
</details>
<details><summary>Hint 8 — randomization that protects against bad luck</summary>

A simple, defensible idea: sort the lines by something known in advance that predicts the outcome (here, line number is a proxy for age), cut the list into consecutive blocks as large as the number of
groups (cohorts + holdout), and inside every block shuffle which line gets which cohort. Every cohort then gets lines of every age. Say in the plan that you did it and why.
</details>
<details><summary>Hint 9 — the interval is far too narrow</summary>

Check what you resample. If your bootstrap draws rows of the *panel*, a pump's weeks are treated as independent. Draw whole pumps. Then check that the interval comes from percentiles of the **overall** estimate recomputed on each draw,
not from a standard error of one cell.
</details>

## Common traps
- Estimating a regression with `treated` and two sets of dummies and calling it the result. That is the *naive* estimate this module exists to take apart.
- Using already-treated pumps as controls ("they are there, why not?").
- A baseline that includes the weeks you then call "placebo": the leads are no longer a test of anything.
- Resampling pump-weeks or single rows in the bootstrap (R5's mistake, again).
- Writing the plan after seeing the estimate, or editing it after (the git history shows it).
- Choosing the cohorts by hand "to be fair", or looking at which assignment gives the nicest balance *after* seeing outcomes.
- Reading "no pre-trend" as "no confounding": placebos reject some stories, they don't prove parallel trends.
- Quoting an effect in per cent of an unspecified baseline, or hours per pump-week as "hours per failure".
- A memo that says "statistically significant" and nothing about the size, the MDE or the decision.
- Reading `--expected` output as if it were real data: it exists to debug the estimator, nothing more.

## On a real job
The same logic with messier data: unbalanced panels (pumps added or retired), adoption dates that slip, **anticipation** (crews change behaviour before the official date), **spillovers** (a line's spare pump
carries another line), outcomes from a CMMS with delayed work orders, covariates to make parallel trends more credible (conditional DiD), and **synthetic control** when only one plant adopts. Libraries do the heavy
lifting (`did` in R, `csdid` / `differences` / `pyfixest` in Python, DoubleML for covariates) and you will use them, but only people who have implemented the estimator once know which question each one answers.
You would add **sequential monitoring** with pre-specified stopping rules (so you can stop early without inflating errors), a **holdout kept for months** (the cost of keeping it is part of the business case),
and a **registry of experiments** next to the model registry (module 5): every promoted policy carries the plan, the data version and the memo that justified it.
**Courses / reading:** *Causal Inference: The Mixtape* (staggered DiD chapter), *Causal Inference for the Brave and True* (free), Callaway & Sant'Anna (2021) and Goodman-Bacon (2021) for the two papers behind this module.

→ [Back to the module list](../README.md) · Related: [`challenges/R5_lgbm_business.md`](challenges/R5_lgbm_business.md) (block bootstrap), [`05_orchestration.md`](05_orchestration.md) (champion/challenger gate)
