# Challenge R8 — Legacy code: the report that “always worked”

⏱ 3–4 h · Extends **module 14** · Services: none · Check: `make challenge-8`
**Tools:** pandas, pytest, Git. **What you practice:** reading someone else's code, reproducing before fixing, regression tests, a blameless postmortem.

## What you will build
Nothing from scratch: this time you **inherit** code. In [`legacy/report/`](../../legacy/report/) is the monthly reliability report the previous team wrote,
which Marta uses every Monday to decide which pump to inspect. It has tests, they pass, and it is wrong. Your job: find **all** the bugs, fix them without changing the format,
write a regression test per bug and a postmortem.

## Why this challenge exists
Your first year on a job looks much more like this than like building something new: code you didn't write, with the author gone, with tests that check the shape
but not the values, and a user who says *“this doesn't add up”*. Method matters more than talent: **reproduce** the symptom, isolate the cause with data, write the test
that fails, fix, watch the test pass, and write it up so it doesn't happen again. No blame: the goal is to fix the system that allowed the error.

## The ticket

> **INF-2026-10 — The August report doesn't add up** (opened by Marta, head of maintenance)
>
> Hi. Something doesn't add up in the monthly report:
> - P104 was **stopped for more than two days** in early August because of the seal, and the report gives it **99.34%** availability.
> - P112 **failed on the night of August 31** (work order opened at 23:32 UTC) and the August report shows **no failure at all** for it.
> - The **MTBF** values don't look like anything I know: P102 “fails every 3543 hours” and the month has 744.
>
> I don't know if there is more that's wrong. I need to be able to trust this report. Thanks.

**Definitions agreed with Maintenance** (this is the contract; the code should meet it):

| Column | Definition |
|---|---|
| `month` | **UTC** month `YYYY-MM`; the month is the interval `[day 1 00:00 UTC, day 1 of the next month 00:00 UTC)` |
| `availability_pct` | `100 × (1 − failure downtime hours within the month / hours in the month)`, 2 decimals. Downtime = from `opened_at` to `closed_at` of each **CORRECTIVE** work order, **clipped to the month** (a stoppage that crosses months is split) |
| `n_failures` | CORRECTIVE work orders **opened** within the month |
| `operating_hours` | number of (10-minute) instants in the month with `RUN_STATE = 1` × 10/60, 2 decimals |
| `mtbf_hours` | `operating_hours / n_failures` (empty if there were no failures), 2 decimals |
| `vib_p95` | 95th percentile (linear interpolation, like pandas) of `VIB_MM_S` **only at instants with the pump running**, 4 decimals |

One row per pump with data in the month; the same columns in the same order (Marta has a spreadsheet that reads them).

## Concepts
- **Reproduce before fixing**: if you can't make the bug appear at will, you won't know whether you fixed it.
- **Regression test**: it fails with the bug and passes without it. If it passes with the old code, it protects against nothing.
- **Shape tests vs. value tests**: “12 rows and percentages between 0 and 100” catches no computation bug. A value computed by hand does.
- **Plausibility**: some numbers simply can't be (more operating hours than the month has). A cheap test that catches whole families of bugs.
- **Blameless postmortem**: summary, impact, root cause, fix, prevention. It is written about the system, not about people.

## The contract
- `python -m legacy.report --month YYYY-MM --out file.csv` produces the report **with the definitions above** (same columns, same order).
- Your tests in a **new** file in `legacy/report/tests/` (you may keep or improve the old ones). They all pass with your code; **≥ 3 fail** against the original code.
- `docs/postmortem-report.md` with sections **Summary**, **Impact**, **Cause** (root), **Fix** and **Prevention**, with detail (each bug, how wrong the report was and why nobody saw it).

## Your task
1. **Before touching anything**: save a copy of the current August and September reports (`--out before_08.csv`). They are your evidence.
2. Reproduce each symptom in the ticket with data: open the work orders of P104 and P112, compute the downtime and the availability by hand.
3. Read `report.py` **line by line** with the contract next to it. For each suspicious line, first write the test that should fail and **check that it fails**.
4. Fix one bug at a time and rerun everything. Careful: the ticket mentions three symptoms, but Marta says *“I don't know if there is more”*.
5. Compare `before_*.csv` with the new report and quantify the impact in the postmortem.

## How you will know you are done
`make challenge-8` generates the August and September reports with your code and compares them with an independent computation of the definitions; it runs your tests with your code (they must pass)
and with a copy of the **original code** (at least 3 must fail); and it reviews the structure and the detail of the postmortem.

## Comprehension questions
1. Which of the bugs would a plausibility test have caught without computing anything by hand? Which one wouldn't?
2. Why does the time zone bug only show in some months and on some pumps?
3. One of your tests compares against a number computed by hand (for example 1.656). What is the advantage and what is the risk?
4. What would you change in the team's process (not in the code) so this doesn't happen again?
5. Marta asks whether the reports for previous months were also wrong. What do you answer, and how do you check it?

## Hints
<details><summary>Where to start</summary>

The third symptom (MTBF) is the easiest to reproduce: how many hours does August have? how many `operating_hours` does the report give? multiplied by how much?
</details>
<details><summary>pandas `Timedelta`s</summary>

Look calmly at what `.dt.seconds` returns for a duration of 2 days and 5 hours. Compare it with `.dt.total_seconds()`.
</details>
<details><summary>“I don't know if there is more”</summary>

Check each column against its definition, including the last one. Which readings go into the percentile?
</details>

## Common traps
- Fixing the symptom (“if it goes over 744, set 744”) instead of the cause.
- Changing the report format “because it is clearer this way”: you break Marta's spreadsheet.
- Writing the tests **after** fixing and not checking that they failed before.
- A postmortem that says “there was a bug and it was fixed”.

## On a real job
The fixes go on a branch with their PR, the description links the ticket, the regression tests stay in CI forever, and the postmortem is shared with whoever uses the report
(not only with the technical team), with an honest estimate of which decisions may have been affected.

→ Next: [`R9_databricks.md`](R9_databricks.md) · Back to the [challenges index](README.md)
