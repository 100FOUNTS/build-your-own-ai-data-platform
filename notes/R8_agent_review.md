# R8, part 2 — supervising a coding agent: review of one fix

> **Worked example (reference solution).** It shows the shape of a good review, not the only one. The two diffs below were written by hand to
> reproduce typical agent behaviour (a plausible-but-incomplete first attempt, then a correct second attempt with some scope creep); your agent will make *different* mistakes.
> The test output and the numbers are real, from running the repository.

Bug handed to the agent: **a stoppage that crosses from one month into the next is not split between the two months** (the ticket doesn't mention it; I found it
by comparing the `availability_pct` definition with the code). I had already fixed the time zone and the `.dt.seconds` bug by hand, each with its own test.

## Prompt

> In `legacy/report/report.py`, `build_report` only counts work orders *opened* in the requested month, and counts their full duration. Per the agreed definition,
> `availability_pct` must use the failure downtime hours **within the month**: a CORRECTIVE work order that crosses a month boundary is split between the months.
> Make `legacy/report/tests/test_regressions.py::test_a_stoppage_that_crosses_months_is_split` pass. Change only what that needs, do not touch `n_failures`
> (it still counts orders *opened* in the month), do not modify any test, and run the whole `legacy/report/tests` folder. Show me the diff; don't commit.

## Failing test

Written **before** asking, and run red on the code I had at that point (time zone and `.seconds` already fixed by me):

`legacy/report/tests/test_regressions.py::test_a_stoppage_that_crosses_months_is_split`

```python
def test_a_stoppage_that_crosses_months_is_split():
    # P112: work order opened 08-31 23:32 UTC, closed 09-03 06:08 UTC. September must get its ~54 h of downtime
    df = build_report("2026-09")
    assert df[df.asset_id == "PLT01-P112"].iloc[0].availability_pct < 93
```

```text
E       assert np.float64(100.0) < 93
FAILED legacy/report/tests/test_regressions.py::test_a_stoppage_that_crosses_months_is_split
1 failed in 3.66s
```

The expected value comes from the definition, not from the code: ~54 h of 720 is 7.5 %, so availability must be ≈ 92.5 %; I only assert `< 93` so the test doesn't depend on rounding.

## Diff summary

**Attempt 1** (`report.py`, +5/−1): clips `opened_at` and `closed_at` to the month inside the per-pump loop. It **left unchanged** `wo = wo[wo["month"] == month]`, so September never even sees the work order that was opened in August.
My test stayed **red** (still 100.0) and the agent's summary said “handles orders crossing month boundaries”. I pasted the failing output back and asked it to look at where the work orders are filtered.

**Attempt 2** (`report.py`, +9/−4): removes the opened-month filter; keeps `failures` (opened in the month, for `n_failures`) separate from `overlap`
(orders whose interval intersects the month, for the downtime), clips both ends to `[start, end)` and sums `total_seconds()`. It also **changed the `vib` line** to use only readings while running, which I had not asked for.
My test went green and the folder passed (1 passed).

## What the agent got right

It kept `n_failures` on *opened in the month* as I told it, and separated the two concepts (failures vs. stoppage overlap) instead of patching the same variable: that was the real cause of the bug. It clipped both ends of the interval
and used `total_seconds()`. The month boundaries are built with an explicit UTC `Timestamp`, consistent with the timezone fix I had already made. With the fix, P112 gets ≈ 54 h of downtime in September and
the August figure for P112 is also correct: 99.94 % (it is stopped only 28 minutes in August), P104 stays at 92.88 %. I recomputed those two by hand from the work-order timestamps.

## What the agent got wrong

1. **First attempt was incomplete and *claimed* to be done**: it fixed the arithmetic but not the filter that decided which orders get there. Only the test caught it; reading the summary would not have.
2. **Scope creep**: the second diff also changed `vib_p95` (only while running). That change is *correct* (it is another bug in the report) but I asked for something else, it has no test of its own and it mixes two fixes in one change, so a reviewer
   (or `git bisect`, or a revert) can no longer separate them.
3. **Latent edge, not covered**: `closed_at > start` is false for an order with no `closed_at` (still open), so such an order would silently contribute 0 h of downtime. There is no such row in today's `work_orders.csv`, so I did not widen the scope, but it goes into the Prevention section of the postmortem.
4. The agent never ran the whole folder in attempt 1 although I asked it to. I ran it myself.

## Decision

**Accept with changes.** I accept the downtime/overlap hunk: my test passes, I recomputed two pumps by hand and the definition is now followed.
I **reject the `vib_p95` hunk from this change**: I reverted it, wrote its own failing test first (`test_vibration_p95_only_while_running`, red on the original) and applied the fix as a separate commit.
Reason: one change = one bug = one test; an agent's diff is untrusted until a test I wrote *before* it has gone red-to-green and I have read every hunk.
