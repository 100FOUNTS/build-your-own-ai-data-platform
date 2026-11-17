# Challenge R8 — Legacy code: the report that “always worked”

⏱ 4–5 h · Extends **module 14** · Services: none · Check: `make challenge-8`
**Tools:** pandas, pytest, Git, **a coding agent** (Claude Code or any other, for part 2). **What you practice:** reading someone else's code, reproducing before fixing, regression tests, a blameless postmortem, **supervising an AI coding agent**.

## What you will build
Nothing from scratch: this time you **inherit** code. In [`legacy/report/`](../../legacy/report/) is the monthly reliability report the previous team wrote,
which Marta uses every Monday to decide which pump to inspect. It has tests, they pass, and it is wrong. Your job: find **all** the bugs, fix them without changing the format,
write a regression test per bug and a postmortem.

**Part 2** adds the other thing a 2026 first year looks like: for **one** of the bugs you hand the fix to a **coding agent**, with the test written *before* you ask, and you review what it did
as critically as you would a colleague's pull request. You deliver a short written review.

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
- **Test first, then the agent**: the failing test is your *specification* of “correct”, written by you from the definition, before any code exists. It is the only judge that doesn't depend on the agent's own account of what it did.
- **A good prompt is a small work order**: the symptom, the definition it must meet, *where* to look, what **not** to touch, and how to verify. It is not “fix the bugs”.
- **Reviewing an agent's diff**: read every hunk before running anything. Typical failures: a fix that is plausible but incomplete, a confident summary that doesn't match the diff, changes nobody asked for (*scope creep*),
  tests edited or weakened to go green, and an unrelated “improvement” hidden inside. Accept, accept with changes or reject, and say why.
- **Why this is a 2026 job skill**: agents now write a large share of first drafts, so the scarce skill moved from *typing* the fix to *specifying* it and *verifying* it. Legacy code with weak tests is exactly where agents are used
  the most and where a wrong-but-convincing change is the most expensive. Teams hire juniors who can hand over a precise task, keep control of the test suite and say “no” to a diff with reasons; they don't need more people who paste code they didn't read.

## The contract
- `python -m legacy.report --month YYYY-MM --out file.csv` produces the report **with the definitions above** (same columns, same order).
- Your tests in a **new** file in `legacy/report/tests/` (you may keep or improve the old ones). They all pass with your code; **≥ 3 fail** against the original code.
- `docs/postmortem-report.md` with sections **Summary**, **Impact**, **Cause** (root), **Fix** and **Prevention**, with detail (each bug, how wrong the report was and why nobody saw it).
- **Part 2**: `notes/R8_agent_review.md` with a heading for each of **Prompt** (what you asked the agent), **Failing test** (the test you wrote first, named as `legacy/report/tests/<file>.py::<test_name>`, with its red pytest output),
  **Diff summary** (files and hunks the agent changed), **What the agent got right**, **What the agent got wrong** and **Decision** (accept, accept with changes or reject, and the reason).

## Your task
1. **Before touching anything**: save a copy of the current August and September reports (`--out before_08.csv`). They are your evidence.
2. Reproduce each symptom in the ticket with data: open the work orders of P104 and P112, compute the downtime and the availability by hand.
3. Read `report.py` **line by line** with the contract next to it. For each suspicious line, first write the test that should fail and **check that it fails**.
4. Fix one bug at a time and rerun everything. Careful: the ticket mentions three symptoms, but Marta says *“I don't know if there is more”*.
5. Compare `before_*.csv` with the new report and quantify the impact in the postmortem.

## Part 2 — supervise a coding agent
Do this **after** you have found and fixed at least two bugs by hand, with their tests (the point is to learn the code before delegating it). Then:

1. **Choose ONE bug you have already diagnosed but not fixed yet.** You know the symptom, the definition it breaks and roughly where. Don't ask the agent to *find* the bugs: that would delegate the very thing the challenge teaches.
2. **Write the regression test first** in your new test file, run it and **keep the red output**. If it doesn't fail on the current code, it isn't a test of this bug yet.
3. **Make the diff reviewable.** Commit your work so far, or `git switch -c agent-fix`. When the agent finishes, `git diff` must show *only* what it did.
4. **Write the prompt** (keep it; it goes in your review). Include the symptom, the definition from the ticket, the file, the test it must make pass, the constraints (*don't modify tests, don't change the report's columns or anything outside this bug*) and “run the whole `legacy/report/tests` folder”.
5. **Let the agent work, then read the diff before running anything.** For each hunk: does it follow the definition? Does it touch more than I asked? Does the summary match the code? Which edge cases did it not cover? Did it touch a test?
6. **Verify with your own tools**: your failing test turns green, the whole folder passes, and you recompute **one pump's number by hand** from the work orders. Compare `before_*.csv` with the new report: only the columns of this bug should move.
7. **Write `notes/R8_agent_review.md`** with the sections listed in *The contract*. If the agent got everything right, say what you checked to be able to say so; “it looks fine” is not a finding.
8. **Decide**: accept, accept with changes (revert the hunks you reject and keep the rest) or reject (fix it yourself, or give it a *sharper* prompt: that is a legitimate second attempt, mention it). Whatever stays in the repository must pass your test.

Which tool? Any coding agent. **In this repository Claude Code is a mentor by default**: it won't write R8 fixes or point out bugs. The project's `CLAUDE.md` has one explicit exception for this step: only when you ask it to act as the coding agent
**and** you have already written a failing regression test, it will make that one fix and show you the diff, without hunting for other bugs and without touching tests. Otherwise use another agent (a clean checkout is fine).

## How you will know you are done
`make challenge-8` generates the August and September reports with your code and compares them with an independent computation of the definitions; it runs your tests with your code (they must pass)
and with a copy of the **original code** (at least 3 must fail); and it reviews the structure and the detail of the postmortem.

For part 2 the check is deliberately light: it can't judge a review, it can only see that you wrote one. It verifies that `notes/R8_agent_review.md` has the six sections and that each has substance (not a line saying “ok”),
that the Failing test section includes red pytest output, and that the test it names **exists, fails on the original code and passes on yours**. Whether the review is *good* is for the `/review R8` conversation with your mentor.

## Comprehension questions
1. Which of the bugs would a plausibility test have caught without computing anything by hand? Which one wouldn't?
2. Why does the time zone bug only show in some months and on some pumps?
3. One of your tests compares against a number computed by hand (for example 1.656). What is the advantage and what is the risk?
4. What would you change in the team's process (not in the code) so this doesn't happen again?
5. Marta asks whether the reports for previous months were also wrong. What do you answer, and how do you check it?
6. Why must the test exist **before** you ask the agent, and not be written afterwards “to confirm it works”? What can an agent do with a test that it can't do with a vague description?
7. The agent says “fixed, all tests pass”. What evidence do you trust and what don't you? Where did you check it yourself?
8. The agent made your test pass by editing the test. What do you do, and what sentence do you add to the next prompt?
9. Which parts of your own diagnosis went into the prompt? What would have happened with just the ticket's text?
10. The diff for a 3-line bug is 80 lines. Accept? What does it cost the team later (review time, `git blame`, rollback)?
11. Which part of this challenge would you *never* delegate to an agent, and why? (Think: what makes you able to review it.)

## Hints
<details><summary>Where to start</summary>

The third symptom (MTBF) is the easiest to reproduce: how many hours does August have? how many `operating_hours` does the report give? multiplied by how much?
</details>
<details><summary>pandas `Timedelta`s</summary>

Look calmly at what `.dt.seconds` returns for a duration of 2 days and 5 hours. Compare it with `.dt.total_seconds()`.
</details>
<details><summary>Part 2: a prompt that works</summary>

Think of a work order for a contractor who has never seen the plant: *what is wrong* (a number that doesn't match the definition), *the definition* (copied from the ticket), *where* (file, function), *the test that must pass*, *what not to touch*, *how to verify* (run the folder, show the diff, don't commit).
Whatever you leave out, the agent will decide for you.
</details>
<details><summary>Part 2: reading a diff</summary>

`git diff --stat` first (how many files? how many lines?), then each hunk. For every changed line ask: *which sentence of my prompt asked for this?* A change with no answer is scope creep.
Then ask the opposite: which part of the definition did the agent **not** implement? (Think about the inputs the test doesn't exercise.)
</details>
<details><summary>“I don't know if there is more”</summary>

Check each column against its definition, including the last one. Which readings go into the percentile?
</details>

## Common traps
- Fixing the symptom (“if it goes over 744, set 744”) instead of the cause.
- Changing the report format “because it is clearer this way”: you break Marta's spreadsheet.
- Writing the tests **after** fixing and not checking that they failed before.
- Part 2: asking the agent to “fix the report” without having diagnosed anything, then reviewing *its* diagnosis instead of yours.
- Part 2: running the agent first and writing the test afterwards **from its code**: the test then confirms the code instead of the definition.
- Part 2: accepting a diff because the tests pass, without reading it; or because the agent's summary was convincing.
- Part 2: a review that says “the agent did well, accepted” with no evidence of what you checked.
- A postmortem that says “there was a bug and it was fixed”.

## On a real job
The fixes go on a branch with their PR, the description links the ticket, the regression tests stay in CI forever, and the postmortem is shared with whoever uses the report
(not only with the technical team), with an honest estimate of which decisions may have been affected.
If an agent wrote part of the change, the PR says so, and the reviewer is held to the same standard as for any author: the test that proves the fix was written by a human from the definition, one PR fixes one bug,
and the person who merges is the one who understands the diff.

→ Next: [`R9_databricks.md`](R9_databricks.md) · Back to the [challenges index](README.md)
