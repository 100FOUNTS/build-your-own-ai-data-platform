# Challenge R7 — Text-to-SQL with guardrails that don't depend on the model

⏱ 4–5 h · Extends **module 6** · Services: Ollama (`qwen3:4b-instruct`) · Check: `make challenge-7`
**Tools:** LLMs, DuckDB, guardrails, evals. **What you practice:** defense in depth, analyzing SQL as a tree, bounded retries.

## What you will build
`genai/text2sql.py` with `ask(question, llm=None, ...)`: Marta types *“which pump had the highest maximum vibration?”* and the system asks the LLM for
**one** SQL query, **validates** it without trusting the model, runs it on the warehouse **read-only** with a row limit, and if the database returns an error
gives the model **one** chance to fix it.

## Why this challenge exists
Text-to-SQL is one of the most requested generative AI uses in companies (Genie in Databricks, Cortex Analyst in Snowflake, Copilot in Power BI) and one of the most dangerous:
you hand a model, which can be tricked, the key to your database. The professional rule is that **security can't depend on the model behaving**:
the SQL is validated with a **parser** (not with regular expressions over the text), only certain tables are allowed, and on top of that the connection **can't** write or read
files even if the validation fails. Two layers, in case one breaks.

## Concepts
- **Table allowlist**: only `asset_daily_features` and `readings_hourly`. Everything else (other tables, table functions like `read_csv`, file paths, extensions) is forbidden.
- **Parse, don't search for words**: `SELECT * FROM x` with `x` defined in a CTE that reads a forbidden table contains no “dangerous” word. A tree shows it.
  DuckDB has its own parser: `select json_serialize_sql('…')` returns the tree as JSON (and fails if it is not **a single** `SELECT`).
- **Least privilege on the connection**: `duckdb.connect(path, read_only=True, config={"enable_external_access": False})` and `SET lock_configuration = true`.
- **A row limit you enforce**: wrap the query (`select * from (…) limit n`), don't trust the model to add `LIMIT`.
- **Retry with the error**: the database message (“column vibration doesn't exist”) is the best hint for the model to fix it. **One**, and that's it.
- **Schema in the prompt**: the model gets it right much more often if you give it the exact tables, columns and types and 2–3 question → SQL examples.

## The contract
`ask(question, llm=None, warehouse_path=None, max_rows=100) -> dict` with:
- `status`: `ok` · `blocked_input` (the question is malicious: **the LLM is not called**; reuse your module 6 `check_input`) · `blocked_sql` (the proposed SQL fails validation: **it is not run**) · `error` (it failed twice);
- `sql` (the extracted SQL), `columns`, `rows` (list of tuples, at most `max_rows`), `reason`, `llm_calls`.
- `llm(messages) -> str` receives a list of `{"role", "content"}` messages. If `llm` is `None`, use Ollama (`OLLAMA_URL`, `OLLAMA_CHAT_MODEL`) with temperature 0.
- `warehouse_path` defaults to `WAREHOUSE_PATH`. It tolerates the model wrapping the SQL in ```` ```sql ```` and adding text or a trailing `;`.
- If the SQL fails when run: one retry, passing the database error to the LLM. At most 2 calls in total.

## Your task
1. Try `json_serialize_sql` in DuckDB with 6 queries (a normal one, a CTE, a JOIN, `read_csv(...)`, `DROP`, two statements). Look at the JSON: where do the tables show up?
2. Write `validate(sql)` and test it **without an LLM** with a list of good and bad cases. That list is your first eval.
3. Write the least-privilege connection and check that, even if you skip the validation, `COPY … TO '/tmp/x.csv'` fails.
4. Write `ask` with a simulated LLM (a function that returns a fixed SQL): that way you test the flow without depending on the model.
5. Hook it up to Ollama. Write 6–8 questions with their right answer (computed by you) and measure how many it gets right. What changes if you remove the schema from the prompt?

## How you will know you are done
`make challenge-7` creates a warehouse with `asset_daily_features`, `readings_hourly` and a `secret_tokens` table that must **not** be readable. With a simulated LLM it tests:
a valid query, SQL wrapped in Markdown, the row limit, **11 attacks** (destructive, several statements, forbidden tables in a CTE and a JOIN, files, system functions, `COPY`, `ATTACH`, `INSTALL`),
that the warehouse file doesn't change, the retry with the error and the input guardrail. With real Ollama it requires ≥ 4/6 right on simple questions.

## Comprehension questions
1. Why isn't `if "drop" in sql.lower()` enough? Give two queries that get around it.
2. If the validation had a bug, what would stop the leak? And if the connection had a bug?
3. Which personal or sensitive data could come out through an *allowed* query? How would you scope it?
4. The model gets 5/6 right on your eval. What does that tell you about Marta's questions tomorrow? How would you grow the eval?
5. Why a single retry and not until it works?

## Hints
<details><summary>Finding the tables in the tree</summary>

Walk the JSON recursively: nodes with `"type": "BASE_TABLE"` carry `table_name`; `"TABLE_FUNCTION"` ones carry `function.function_name`.
The names defined in `cte_map` are CTE aliases, not real tables.
</details>
<details><summary>Extracting the SQL from the model's answer</summary>

First look for a ```` ``` ```` block; if there isn't one, take everything from the first `select`/`with`. Remove the trailing `;` before validating.
</details>
<details><summary>The retry</summary>

Append the model's answer (`assistant`) and a `user` message with the database error to the messages. The full history is the context.
</details>

## Common traps
- Validating with regular expressions.
- Opening the database read-write “because we only do SELECT”.
- Running first and validating afterwards (“to see if it works”).
- Giving the model **every** warehouse table in the prompt: you are showing it the ones it can't use.

## On a real job
On top of this: a database user with permissions only on approved views, a maximum time per query, logging of every question and SQL for auditing,
evals with real business questions, and a semantic layer (metrics defined once) so the model doesn't reinvent “availability” in every query.

→ Next: [`R8_legacy_code.md`](R8_legacy_code.md) · Back to the [challenges index](README.md)
