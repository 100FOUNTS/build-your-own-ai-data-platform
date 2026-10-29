"""Challenge R7 — natural-language questions → SQL on the warehouse, with defense in depth.

Layers: input guardrail (module 6) → the LLM proposes SQL → it is extracted and PARSED (tree, not text) → table allowlist →
it runs on DuckDB opened read-only and without external access → a row limit we enforce. One retry with the error.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable

import duckdb
import httpx
from genai.guardrails import check_input

ALLOWED_TABLES = {"asset_daily_features", "readings_hourly"}
EXAMPLES = [
    (
        "How many days of data does PLT01-P101 have?",
        "SELECT count(*) AS days FROM asset_daily_features WHERE asset_id = 'PLT01-P101'",
    ),
    (
        "Mean vibration of each asset",
        "SELECT asset_id, avg(vib_mean) AS vib FROM asset_daily_features GROUP BY asset_id ORDER BY asset_id",
    ),
]

Llm = Callable[[list[dict]], str]


def ollama(messages: list[dict]) -> str:
    r = httpx.post(
        f"{os.environ.get('OLLAMA_URL', 'http://localhost:11434')}/api/chat",
        timeout=120,
        json={
            "model": os.environ.get("OLLAMA_CHAT_MODEL", "qwen3:4b-instruct"),
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        },
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def connect(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path, read_only=True, config={"enable_external_access": False})
    con.execute("SET lock_configuration = true")
    return con


def schema_text(con: duckdb.DuckDBPyConnection) -> str:
    rows = con.execute(
        "select table_name, column_name, data_type from information_schema.columns order by table_name, ordinal_position"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for t, c, ty in rows:
        if t in ALLOWED_TABLES:
            out.setdefault(t, []).append(f"{c} {ty}")
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in out.items())


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    sql = m.group(1) if m else text
    m = re.search(r"\b(with|select)\b.*", sql, re.S | re.I)
    return (m.group(0) if m else sql).strip().rstrip(";").strip()


def _walk(node, tables: list[str], funcs: list[str], ctes: set[str]) -> None:
    if isinstance(node, dict):
        for item in (node.get("cte_map") or {}).get("map", []):
            ctes.add(item["key"])
        if node.get("type") == "BASE_TABLE":
            tables.append(
                node["table_name"]
                if not node.get("schema_name")
                else f"{node['schema_name']}.{node['table_name']}"
            )
        elif node.get("type") == "TABLE_FUNCTION":
            funcs.append(node["function"]["function_name"])
        for v in node.values():
            _walk(v, tables, funcs, ctes)
    elif isinstance(node, list):
        for v in node:
            _walk(v, tables, funcs, ctes)


def validate(con: duckdb.DuckDBPyConnection, sql: str) -> str | None:
    """None if it can run; otherwise, the reason."""
    tree = json.loads(con.execute("select json_serialize_sql(?)", [sql]).fetchone()[0])
    if tree.get("error"):
        return "only ONE SELECT statement is allowed"
    tables: list[str] = []
    funcs: list[str] = []
    ctes: set[str] = set()
    _walk(tree, tables, funcs, ctes)
    if funcs:
        return f"table functions not allowed: {sorted(set(funcs))}"
    bad = {t for t in tables if t not in ctes and t.lower() not in ALLOWED_TABLES}
    if bad:
        return f"tables outside the allowlist: {sorted(bad)}"
    return None


def ask(
    question: str, llm: Llm | None = None, warehouse_path: str | None = None, max_rows: int = 100
) -> dict:
    res = {"status": "error", "sql": None, "columns": [], "rows": [], "reason": None, "llm_calls": 0}
    verdict = check_input(question)
    if not verdict.allowed:
        return {**res, "status": "blocked_input", "reason": verdict.reason}
    llm = llm or ollama
    con = connect(warehouse_path or os.environ.get("WAREHOUSE_PATH", "data/warehouse.duckdb"))
    try:
        shots = "\n".join(f"Q: {q}\nSQL: {s}" for q, s in EXAMPLES)
        messages = [
            {
                "role": "system",
                "content": "You translate questions into ONE DuckDB SQL query. Answer only with the SQL.\n"
                f"Available tables:\n{schema_text(con)}\n\nExamples:\n{shots}",
            },
            {"role": "user", "content": question},
        ]
        for attempt in range(2):
            reply = llm(messages)
            res["llm_calls"] += 1
            sql = extract_sql(reply)
            res["sql"] = sql
            reason = validate(con, sql)
            if reason:
                return {**res, "status": "blocked_sql", "reason": reason}
            try:
                cur = con.execute(f"select * from ({sql}) as q limit {int(max_rows)}")
                return {
                    **res,
                    "status": "ok",
                    "columns": [d[0] for d in cur.description],
                    "rows": [tuple(r) for r in cur.fetchall()],
                }
            except duckdb.Error as exc:
                res["reason"] = str(exc).splitlines()[0]
                if attempt == 0:
                    messages += [
                        {"role": "assistant", "content": reply},
                        {
                            "role": "user",
                            "content": f"The query failed with this database error: {res['reason']}. Fix it.",
                        },
                    ]
        return {**res, "status": "error"}
    finally:
        con.close()
