# Module 0 — Engineering foundations: the habits that make everything else work

⏱ 3–4 h · Services: none · Check: `make check-0`
**Tools:** Python (uv, ruff, pytest), SQL, Git/GitHub, Linux (shell).

## What you will build
Not platform code: the project's **habits**. A Git history people can read, a hook that stops you from
pushing unformatted code, an environment diagnosis script, tests from day one and five warm-up SQL queries.

## Why this module exists
On a team, the code you write will be read, run and maintained by others (and by you six months from now). What separates a healthy
project from a chaotic one is almost never the algorithm: it is whether the history tells what happened, whether a formatting mistake is caught before it reaches
review, whether someone new can get started in 10 minutes, and whether there are tests that warn you when something breaks.

| Habit | What it prevents |
|---|---|
| Small commits with standard messages (*Conventional Commits*) | An unreadable history; hand-written changelogs |
| Branches + *merge* | Half-done work on `main`; no way to revert a single feature |
| A `pre-commit` hook with ruff | Formatting debates in every review; silly CI failures |
| `scripts/doctor.sh` | “It works on my machine” and a morning lost installing things |
| Tests from the start | Fear of touching the code |
| Fluent SQL | Everything else: 80% of data work is SQL |

## Concepts
- **Conventional Commits**: `type(scope): summary`, with types `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `ci`…
- **No-fast-forward merge** (`git merge --no-ff`): keeps a marker of “this is where this feature was integrated”.
- **Git hooks**: scripts Git runs at certain moments. `core.hooksPath` lets you version them in the repository.
- **`set -euo pipefail`** in bash: abort on an error (`-e`), on undefined variables (`-u`) and on failures inside pipes.
- **Window functions** (`LAG`, `ROW_NUMBER`) and the **anti-join**: two SQL tools you will use constantly.

## Your task
1. **Git**: `git init -b main` (if you haven't). Create at least **6 commits** with Conventional Commits, building at least two
   features on **branches** (`git switch -c feat/doctor`) that you integrate with `git merge --no-ff`.
2. **`.gitignore`**: it must cover `.venv`, `node_modules`, `data/`, `.env` (and later `.secrets.local`).
3. **Hook**: create `.githooks/pre-commit` (executable) that runs `ruff format --check` and `ruff check`; turn it on with
   `git config core.hooksPath .githooks`. Test it: break the formatting on purpose and check that the commit is rejected.
4. **`scripts/doctor.sh`**: checks that `git`, `uv`, `docker`, `node` (version ≥ 20) and `ollama` exist; shows ✓/✗ for each
   one; **exits with code 1 if anything is missing**. Use `set -euo pipefail`.
5. **Tests**: `tests/test_smoke.py` with ≥ 3 tests that check something real in the project (do the seeds exist? is the history Parquet?).
6. **SQL** (`sql/q1.sql` … `sql/q5.sql`), on the `assets` and `work_orders` tables loaded from `platform/seeds/*.csv`:

| File | Question | Output columns |
|---|---|---|
| q1 | How many corrective failures did each asset have? Order from most to fewest (ties broken by `asset_id`) | `asset_id, n_failures` |
| q2 | What was the total cost of corrective failures per criticality? Order by criticality | `criticality, total_cost` (2 decimals) |
| q3 | For each failure that has a previous one on the same asset: how many **calendar days** passed since the previous one? Order by asset and date | `asset_id, days_since_prev` |
| q4 | Mean downtime hours of corrective failures per failure mode | `failure_mode, avg_downtime_hours` (2 decimals) |
| q5 | Assets that **never** had a corrective failure | `asset_id` |

   Try them with DuckDB: `duckdb.sql("create table work_orders as select * from read_csv_auto('platform/seeds/work_orders.csv')")`.
   ⚠️ `TIMESTAMPTZ` values are read in your system's time zone; set `SET TimeZone='UTC'` in your session.

## How you will know you are done
`make check-0` reviews your Git history, the `.gitignore`, the hook, ruff, your tests, the real behavior of `doctor.sh`
(it even runs it with an empty `PATH` to see whether it *really* detects missing tools) and compares your five queries with an
independent computation in pandas.

## Comprehension questions
1. Why does the hook live in `.githooks/` and not in `.git/hooks/`?
2. A teammate runs `git commit --no-verify`. What extra defense do you have? (hint: module 13)
3. What is the difference between `LAG` and a self-join to compute “the previous event”? When would you prefer each one?
4. Why must `doctor.sh` fail with a code ≠ 0 instead of just printing a warning?

## Hints
<details><summary>Hook</summary>

The hook is a shell script: `#!/usr/bin/env bash`, `set -euo pipefail`, `uv run ruff format --check . && uv run ruff check .`.
Don't forget `chmod +x`.
</details>
<details><summary>q3 with LAG</summary>

`lag(opened_at) over (partition by asset_id order by opened_at)` inside a CTE filtered to corrective ones; then `date_diff('day', prev::date, opened_at::date)`.
</details>
<details><summary>q5 (anti-join)</summary>

`assets a LEFT JOIN work_orders w ON w.asset_id = a.asset_id AND w.wo_type = 'CORRECTIVE' WHERE w.wo_id IS NULL`. The type condition goes in the `ON`, not in the `WHERE`: why?
</details>

## Common traps
- Giant commits (“everything”) or messages like “fixes”.
- Putting the filter on the right-hand table in the `WHERE` of a LEFT JOIN (it turns it into an INNER JOIN).
- Shell scripts without `set -euo pipefail`: a failure is ignored and the script “succeeds”.

## On a real job
All of this is automated in CI (module 13): the same `ruff` and the same tests run on every pull request. The local hook only gives you the
feedback a minute earlier. **Course that reinforces it:** *Kaggle Learn (Python, Pandas, SQL)* and the introduction of *Full Stack Open*.

→ Next: [`01_databases.md`](01_databases.md)
