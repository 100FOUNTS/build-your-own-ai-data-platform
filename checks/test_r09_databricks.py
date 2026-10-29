"""Challenge R9 (extends modules 3 and 14) — Take it to Databricks: the map of your platform onto a managed platform, with costs and a plan."""

from __future__ import annotations

import re

import pytest

from conftest import ROOT, ok

pytestmark = pytest.mark.challenge

DOC = ROOT / "docs" / "databricks.md"
# (what you built, accepted terms on the Databricks side)
MAPPING = {
    "Iceberg / lake tables": ("delta", "uniform", "managed iceberg", "iceberg"),
    "catalog (Iceberg REST)": ("unity catalog",),
    "ingestion from Kafka": ("auto loader", "lakeflow connect", "structured streaming", "readstream"),
    "dbt / silver-gold": ("spark declarative pipelines", "lakeflow declarative pipelines", "lakeflow pipelines", "delta live tables", "dlt", "dbt"),
    "Dagster": ("lakeflow jobs", "workflows", "jobs"),
    "MLflow": ("mlflow",),
    "model serving": ("model serving", "serving endpoint"),
    "pgvector / RAG": ("vector search",),
    "DuckDB / queries": ("databricks sql", "sql warehouse"),
}


@pytest.fixture(scope="module")
def doc():
    ok(DOC.exists(), "Missing docs/databricks.md (see guide/challenges/R9_databricks.md).")
    return DOC.read_text()


def table_rows(t):
    return [ln for ln in t.splitlines() if ln.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", ln)][1:]


def test_r9_mapping_table(doc):
    rows = table_rows(doc)
    ok(len(rows) >= 10, f"The mapping table has {len(rows)} rows; ≥ 10 are expected (one per piece of YOUR platform: ingestion, tables, catalog, transformation, orchestration, ML, serving, RAG, queries, security, observability…).")
    low = doc.lower()
    missing = [k for k, terms in MAPPING.items() if not any(t in low for t in terms)]
    ok(not missing, f"The map is missing equivalents for: {missing}. Look up in the official Databricks docs which service plays that role (names change: check the date).")


def test_r9_what_does_not_carry_over_and_what_it_costs(doc):
    for sec, why in (("carry over", "what in your platform has NO direct equivalent or changes shape (Kafka, Keycloak, the SSE gateway, Kubernetes…)"),
                     ("cost", "how it is billed (DBU, serverless compute vs. clusters, storage) and an order-of-magnitude estimate for your volume"),
                     ("plan", "a staged migration plan")):
        ok(re.search(rf"(?im)^#+\s*.*{sec}", doc), f"Missing a section whose title contains “{sec}”: {why}.")
    plan = re.split(r"(?im)^#+\s*.*plan.*$", doc)[-1]
    steps = re.findall(r"(?m)^\s*\d+\.\s+\S", plan)
    ok(len(steps) >= 4, f"The migration plan has {len(steps)} numbered steps; ≥ 4 are expected, each with how you verify it went well.")


def test_r9_honest_decision(doc):
    ok(re.search(r"(?im)^#+\s*.*(recommend|decision)", doc), "End with a recommendation/decision section: would you migrate it? which part first? what would you lose?")
    ok(re.search(r"(?i)lock-?in|vendor dependenc|tied to", doc), "Discuss vendor lock-in: it is the number one objection an architect will raise.")
    ok(len(doc) > 3000, "The document is too short for someone else to make a decision with it.")
