"""Independent computation (pandas) of what the lakehouse layers MUST produce, from the history and the seeds."""

from __future__ import annotations

import numpy as np
import pandas as pd

from conftest import ROOT

FEATURES = ["run_share", "vib_mean", "vib_max", "vib_std", "temp_mean", "temp_max", "press_mean", "press_std", "curr_mean",
            "vib_mean_7d", "vib_delta_28d", "temp_delta_28d", "press_delta_28d"]


def expected_gold(history_dir=None, work_orders_csv=None) -> pd.DataFrame:
    history_dir = history_dir or ROOT / "data" / "history"
    h = pd.concat([pd.read_parquet(p) for p in sorted(history_dir.glob("date=*/readings.parquet"))], ignore_index=True)
    h = h[h.quality == "GOOD"].drop_duplicates("msg_id")
    w = h.pivot_table(index=["asset_id", "ts"], columns="sensor", values="value", aggfunc="first").reset_index()
    w["feature_date"] = w["ts"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    run = w[w["RUN_STATE"] == 1]
    g = w.groupby(["asset_id", "feature_date"]).agg(run_share=("RUN_STATE", "mean")).join(
        run.groupby(["asset_id", "feature_date"]).agg(
            vib_mean=("VIB_MM_S", "mean"), vib_max=("VIB_MM_S", "max"), vib_std=("VIB_MM_S", "std"), temp_mean=("TEMP_BEARING", "mean"),
            temp_max=("TEMP_BEARING", "max"), press_mean=("PRESS_DISCH", "mean"), press_std=("PRESS_DISCH", "std"), curr_mean=("CURRENT_A", "mean")),
        how="left").reset_index().sort_values(["asset_id", "feature_date"])
    parts = []
    for _, d in g.groupby("asset_id"):
        d = d.copy()
        d["vib_mean_7d"] = d["vib_mean"].rolling(7, min_periods=1).mean()
        for c in ("vib", "temp", "press"):
            d[f"{c}_delta_28d"] = d[f"{c}_mean"] - d[f"{c}_mean"].shift(1).rolling(28, min_periods=1).mean()
        parts.append(d)
    g = pd.concat(parts)
    wo = pd.read_csv(work_orders_csv or ROOT / "platform/seeds/work_orders.csv", parse_dates=["opened_at"])
    wo = wo[wo.wo_type == "CORRECTIVE"]
    fails = {a: [x.tz_convert("UTC").tz_localize(None).normalize() for x in grp.opened_at] for a, grp in wo.groupby("asset_id")}
    last = g["feature_date"].max()
    g["label_failure_7d"] = [np.nan if d > last - pd.Timedelta(days=7) else float(any(d < f <= d + pd.Timedelta(days=7) for f in fails.get(a, [])))
                             for a, d in zip(g.asset_id, g.feature_date)]
    return g.reset_index(drop=True)


# ------------------------------------------------------------------ R5: cost model for the alert policies (agreed with Maintenance)
R5_COST_FALSE_ALERT = 1.0  # one wasted visit = 1 unit
R5_COST_MISSED_DAY = 10.0  # one asset-day with label_failure_7d = 1 and no alert = 10 units
R5_CI_LEVEL = 0.95
R5_MIN_BOOT = 1000


# ------------------------------------------------------------------ M6: calibration of the LLM judge and OWASP vocabulary
# Thresholds come from a validation run (52 labels, 26 faithful / 26 not) with a correct reference judge (qwen3:4b-instruct,
# temperature 0, claim-by-claim): agreement 46/52 = 88 %, catches 25/26 = 96 %, accepts 21/26 = 81 %. A judge that only checks
# numbers got 71 % / 50 % / 92 %; "always faithful" gets 50 % / 0 % / 100 %. Each threshold sits ~8-10 points under the
# reference (2-3 items of slack for model/hardware noise) and clearly above both lazy judges on the metric where they fail.
M6_JUDGE_MIN_AGREEMENT = 0.80  # share of the 52 human labels the judge gets right (a numbers-only judge: 71 %)
M6_JUDGE_MIN_CATCH = 0.75  # share of the UNFAITHFUL answers it flags (numbers-only judge: 50 %; always-faithful: 0 %)
M6_JUDGE_MIN_ACCEPT = 0.70  # share of the FAITHFUL answers it lets through (it must not cry wolf; reference: 81 %)
M6_MIN_FAITHFULNESS = 0.60  # end-to-end faithfulness of the learner's RAG on the 24 golden questions
M6_GATE_LIMIT = 3  # questions judged in the cheap "does the gate fire?" run
# Valid ids and names exactly as in the official PDFs: OWASP Top 10 for LLM Applications 2026 and OWASP Top 10 for Agentic Applications 2026 (genai.owasp.org).
M6_OWASP_LLM = {
    "LLM01:2026": "Prompt Injection", "LLM02:2026": "Sensitive Information Disclosure", "LLM03:2026": "Excessive Agency",
    "LLM04:2026": "Supply Chain", "LLM05:2026": "Data and Model Poisoning", "LLM06:2026": "Unbounded Consumption",
    "LLM07:2026": "Misinformation", "LLM08:2026": "Hidden Context Exposure", "LLM09:2026": "Vector and Embedding Weaknesses",
    "LLM10:2026": "Improper Output Handling"}
M6_OWASP_ASI = {
    "ASI01": "Agent Goal Hijack", "ASI02": "Tool Misuse and Exploitation", "ASI03": "Identity and Privilege Abuse",
    "ASI04": "Agentic Supply Chain Vulnerabilities", "ASI05": "Unexpected Code Execution (RCE)", "ASI06": "Memory & Context Poisoning",
    "ASI07": "Insecure Inter-Agent Communication", "ASI08": "Cascading Failures", "ASI09": "Human-Agent Trust Exploitation",
    "ASI10": "Rogue Agents"}


# ------------------------------------------------------------------ Module 5: the retrain / compare / promote loop
M5_RETRAIN_JOB = "retrain_job"
M5_TAG_DECISION = "promotion.decision"
M5_TAG_CHALLENGER_AP = "promotion.challenger_ap"
M5_TAG_CHAMPION_AP = "promotion.champion_ap"
M5_DECISIONS = ("promoted", "rejected")
M5_MARGIN = 0.02            # the check sets PROMOTION_MARGIN to this value
M5_DRIFT_DAYS = 14          # the shifted tail of the drift scenario
M5_SHIFT_FACTOR = 0.04      # every feature of that tail is multiplied by this (a unit change on every transmitter)
M5_DEGRADED_DAYS = 40       # labels older than (last labeled day - 40 days) are inverted: a broken label join in the old data


def m5_degraded_gold(gold: pd.DataFrame) -> pd.DataFrame:
    """Same gold, but the oldest labels are inverted. A model trained on it is useless on the (untouched) recent holdout."""
    g = gold.copy()
    cut = g.loc[g["label_failure_7d"].notna(), "feature_date"].max() - pd.Timedelta(days=M5_DEGRADED_DAYS)
    m = (g["feature_date"] <= cut) & g["label_failure_7d"].notna()
    g.loc[m, "label_failure_7d"] = 1.0 - g.loc[m, "label_failure_7d"]
    return g


def m5_stationary_gold(gold: pd.DataFrame, seed: int = 5) -> pd.DataFrame:
    """Same keys and labels, but every day's features are drawn from the same distribution (the first 40 days): no drift by construction."""
    g = gold.sort_values(["feature_date", "asset_id"]).reset_index(drop=True)
    pool = g[g["feature_date"] <= g["feature_date"].min() + pd.Timedelta(days=40)][FEATURES].to_numpy()
    idx = np.random.default_rng(seed).integers(len(pool), size=len(g))
    g[FEATURES] = pool[idx]
    return g


def m5_shifted_gold(gold: pd.DataFrame) -> pd.DataFrame:
    """The last M5_DRIFT_DAYS days with every feature rescaled, like a silent unit change on every transmitter."""
    g = gold.copy()
    tail = g["feature_date"] > g["feature_date"].max() - pd.Timedelta(days=M5_DRIFT_DAYS)
    g.loc[tail, FEATURES] = g.loc[tail, FEATURES] * M5_SHIFT_FACTOR
    return g


# ------------------------------------------------------------------ R1: the vendor's v2 contract (what the vendor publishes) and the registry rules
# The vendor's v2 message (guide/challenges/R1_new_vendor.md) written as JSON Schema. It renames and nests every v1 field, so as an
# "evolution" of v1 it is a breaking change by any definition; registered as its OWN contract (subject `<topic>-v2-value`) it is legitimate.
_R1_STR = {"type": "string"}
R1_VENDOR_V2_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "vendor gateway v2",
    "type": "object",
    "properties": {
        "schema_version": {"const": 2},
        "id": _R1_STR,
        "device": {"type": "object", "properties": {"asset": _R1_STR, "site": _R1_STR}, "required": ["asset", "site"]},
        "event_time_ms": {"type": "integer"},
        "measurement": {"type": "object", "properties": {"tag": _R1_STR, "val": {"type": "number"}, "uom": _R1_STR}, "required": ["tag", "val", "uom"]},
        "status": _R1_STR,
    },
    "required": ["schema_version", "id", "device", "event_time_ms", "measurement", "status"],
}
R1_SUBJECT_V1 = "{topic}-value"        # the canonical contract of the topic (wire version 1: no `schema_version` key)
R1_SUBJECT_V2 = "{topic}-v2-value"     # a foreign contract accepted on the same topic (wire version 2)
R1_COMPAT_FORBIDDEN = {"NONE"}         # any other explicitly chosen level is acceptable; the behavior (reject v2, accept optional field) is what is verified
R1_OPTIONAL_FIELD = "firmware"         # the compatible evolution the check tries to register: a new, optional, string field
R1_REASON_PREFIX = "schema"            # DLQ reason of anything that does not conform to a registered contract: `schema:<detail>`


# ------------------------------------------------------------------ module 13 (pinned tools and fixed names of the checks)
M13_KYVERNO_CHART = "3.9.2"       # Helm chart of Kyverno v1.19.2 (admission controller only)
M13_ROLLOUTS_VERSION = "v1.10.1"  # Argo Rollouts controller + CRDs
M13_LAB_CLUSTER = "chk-m13"       # the throwaway kind cluster the check creates (and deletes) for Rollouts and Kyverno
M13_CANARY_OVERLAY = "deploy/k8s/overlays/canary"  # where the learner's canary overlay must live
M13_STUB_TAGS = {"good": "m13-stub:v1", "good2": "m13-stub:v2", "bad": "m13-stub:v3-bad"}  # stand-in API versions (checks/m13_fixtures/stub_api)
