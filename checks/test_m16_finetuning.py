"""Module 16 — Fine-tune a small LLM: turn a technician's note into JSON {component, cause, action}.

The check never trusts the scores you log: it feeds the 150 hidden held-out notes (checks/_plt02_heldout.jsonl: the most recent
work orders of PLT02, labelled by the lead technician) to YOUR command line, parses the raw text that comes back and recomputes
per-field macro-F1 and the JSON-valid rate itself. Then it compares what you logged to MLflow with what it measured.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from conftest import ROOT, need, ok, py, tail

sys.path.insert(0, str(ROOT / "platform"))
from plt02 import schema  # noqa: E402

HELDOUT = ROOT / "checks" / "_plt02_heldout.jsonl"
POOL = [ROOT / "platform" / "plt02" / "data" / "labelled.jsonl", ROOT / "platform" / "plt02" / "data" / "unlabelled.jsonl"]
TRAIN = ROOT / "ml" / "notes_llm" / "data" / "train.jsonl"
SYNTHETIC = ROOT / "ml" / "notes_llm" / "data" / "synthetic.jsonl"
EXPERIMENT = "notes-llm"

# ------------------------------------------------------------------------------------------------ thresholds
# Measured on Apple Silicon / Qwen3-0.6B, 240 notes, 1 000 MLX LoRA steps:
# rules .7516, few-shot .5837, LoRA .7877; weakest LoRA field .7018,
# JSON-valid 1.0, LoRA gain .2040. Floors leave roughly 7-10 F1 points.
M16_RULES_MIN_F1 = 0.50  # a real baseline, not a copy of the reference rules (reference .75; majority class ~.1)
M16_FEWSHOT_MIN_F1 = 0.35  # proves the base model ran for real; prompts vary (reference .58)
M16_LORA_MIN_F1_MACRO = 0.72
M16_LORA_MIN_FIELD_F1 = 0.60
M16_LORA_MIN_JSON_VALID = 0.98
M16_LORA_GAIN_OVER_FEWSHOT = 0.12
M16_LOG_TOLERANCE = 0.15  # largest measured dev/held-out macro-F1 gap .0434
M16_MAX_HELDOUT_TEXT_OVERLAP = 0  # measured zero; identical exam text is leakage
M16_SYN_MAX_SIMILARITY = 0.9     # calibrate in final test pass: char-trigram Jaccard between a synthetic note and any held-out note
M16_SYN_MAX_SIMILAR_SHARE = 0.03  # calibrate in final test pass: share of synthetic notes above 0.8 similarity to a held-out note
M16_PREDICT_TIMEOUT = {"rules": 300, "fewshot": 1800, "lora": 2700}  # seconds; CPU-only fine-tunes are slow at inference too

_CACHE: dict[str, dict] = {}


# ------------------------------------------------------------------------------------------------ independent computation
def jl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def grams(text: str) -> set[str]:
    t = " " + norm(text) + " "
    return {t[i:i + 3] for i in range(len(t) - 2)}


def macro_f1(truth: list[str], pred: list[str]) -> float:
    """Classes present in the truth; a missing/invalid prediction counts as a miss for the true class."""
    scores = []
    for c in sorted(set(truth)):
        tp = sum(t == c and p == c for t, p in zip(truth, pred))
        fp = sum(t != c and p == c for t, p in zip(truth, pred))
        fn = sum(t == c and p != c for t, p in zip(truth, pred))
        scores.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(scores) / len(scores)


def measure(rows: list[dict], raws: dict[str, str]) -> dict:
    parsed = {}
    for r in rows:
        try:
            obj = json.loads(raws[r["wo_id"]].strip())
        except (ValueError, AttributeError):
            obj = None
        parsed[r["wo_id"]] = obj if isinstance(obj, dict) else None
    out = {"json_valid_rate": sum(schema.valid_answer(parsed[r["wo_id"]]) for r in rows) / len(rows)}
    for f in schema.FIELDS:
        pred = []
        for r in rows:
            o = parsed[r["wo_id"]]
            v = o.get(f) if o else None
            pred.append(v if isinstance(v, str) and v in schema.ENUMS[f] else "__invalid__")
        out[f"f1_{f}"] = macro_f1([r["label"][f] for r in rows], pred)
    out["f1_macro"] = sum(out[f"f1_{f}"] for f in schema.FIELDS) / len(schema.FIELDS)
    return out


def fmt(m: dict) -> str:
    return "  ".join(f"{k}={v:.2f}" for k, v in m.items())


def heldout() -> list[dict]:
    ok(HELDOUT.exists(), "checks/_plt02_heldout.jsonl is missing (it ships with the repository).")
    return jl(HELDOUT)


def run_system(system: str, tmp_path_factory) -> dict:
    """Runs YOUR `python -m ml.notes_llm.predict` on the held-out notes (no labels) and returns {'raws', 'm'}; cached per session."""
    if system in _CACHE:
        return _CACHE[system]
    rows = heldout()
    d = tmp_path_factory.mktemp(f"m16_{system}")
    src, dst = d / "notes.jsonl", d / "predictions.jsonl"
    src.write_text("".join(json.dumps({k: r[k] for k in ("wo_id", "asset_tag", "technician", "note")}) + "\n" for r in rows))
    r = py(["-m", "ml.notes_llm.predict", "--system", system, "--in", str(src), "--out", str(dst)], timeout=M16_PREDICT_TIMEOUT[system])
    ok(r.returncode == 0 and dst.exists(),
       f"`python -m ml.notes_llm.predict --system {system} --in <jsonl> --out <jsonl>` failed (exit {r.returncode}).\n"
       "Its contract (guide/16_llm_finetuning.md): read {wo_id, note} rows, write one {wo_id, raw} row per input, where `raw` is the exact text the system produced.\n" + tail(r))
    preds = jl(dst)
    ids = [p.get("wo_id") for p in preds]
    ok(sorted(ids) == sorted(r_["wo_id"] for r_ in rows),
       f"The predictions of --system {system} must contain exactly one row per input wo_id ({len(ids)} rows for {len(rows)} notes, "
       f"{len(set(ids))} distinct ids).")
    ok(all(isinstance(p.get("raw"), str) for p in preds),
       "Every prediction row needs a string field `raw`: the EXACT text your system produced (the check parses it itself, "
       "so that 'JSON-valid' means what a downstream consumer would see).")
    raws = {p["wo_id"]: p["raw"] for p in preds}
    _CACHE[system] = {"raws": raws, "m": measure(rows, raws), "rows": rows}
    return _CACHE[system]


# ------------------------------------------------------------------------------------------------ the checks
def test_training_data_is_clean():
    """Whatever you trained on must come from the 300 hand labels, the unlabelled pool or a bigger model: never from the held-out notes."""
    ok(TRAIN.exists(), "ml/notes_llm/data/train.jsonl not found.\nWrite your training set there (one JSON per line: wo_id, note, label, source) "
       "so the check can verify that nothing in it comes from the held-out notes.")
    train, held = jl(TRAIN), heldout()
    pool = {r["wo_id"]: r for p in POOL for r in jl(p)}
    held_ids = {r["wo_id"] for r in held}
    ok(train and all({"wo_id", "note", "label", "source"} <= set(r) for r in train),
       "Every row of train.jsonl needs wo_id, note, label and source ('labelled' or 'synthetic').")
    leaked = [r["wo_id"] for r in train if r["wo_id"] in held_ids]
    ok(not leaked, f"{len(leaked)} training rows have the wo_id of a held-out work order (e.g. {leaked[:3]}). Held-out notes are the exam: they can't be in the training set.")
    bad_source = {r["source"] for r in train} - {"labelled", "synthetic", "pseudo"}
    ok(not bad_source, f"Unknown source(s) {sorted(bad_source)}: a training row is `labelled` (one of the lead technician's 300), `pseudo` (an unlabelled note "
       "labelled by a model) or `synthetic` (a note written by a model).")
    fake = [r["wo_id"] for r in train if r["source"] in ("labelled", "pseudo") and (r["wo_id"] not in pool or norm(pool[r["wo_id"]]["note"]) != norm(r["note"]))]
    ok(not fake, f"{len(fake)} rows claim to be real work orders (`labelled`/`pseudo`) but are not in platform/plt02/data/*.jsonl (e.g. {fake[:3]}).\n"
       "Only the lead technician's 300 notes have labels. Do not mint labels by calling the plant simulator with other seeds.")
    n_lab = sum(r["source"] == "labelled" for r in train)
    ok(n_lab <= 300, f"{n_lab} `labelled` rows, but only 300 notes were ever labelled.")
    held_text = {norm(r["note"]) for r in held}
    same = [r["wo_id"] for r in train if norm(r["note"]) in held_text]
    ok(len(same) <= M16_MAX_HELDOUT_TEXT_OVERLAP, f"{len(same)} training notes are identical to a held-out note (e.g. {same[:3]}): that is leakage, even with another id.")


def test_a_rules_baseline_is_a_real_baseline(tmp_path_factory):
    res = run_system("rules", tmp_path_factory)
    m = res["m"]
    ok(m["json_valid_rate"] >= 0.999, f"The rules system must always answer valid JSON (valid rate {m['json_valid_rate']:.2f}).")
    ok(m["f1_macro"] >= M16_RULES_MIN_F1, f"The rules baseline scores macro-F1 {m['f1_macro']:.2f} on the held-out notes ({fmt(m)}); "
       f"it should reach {M16_RULES_MIN_F1}. Read 40 notes first and write down what the technicians actually do (abbreviations, typos, 'seal ok').")


def test_the_base_model_few_shot_runs_through_ollama(tmp_path_factory):
    need("ollama")
    res = run_system("fewshot", tmp_path_factory)
    m = res["m"]
    ok(m["f1_macro"] >= M16_FEWSHOT_MIN_F1, f"The few-shot base model scores macro-F1 {m['f1_macro']:.2f} ({fmt(m)}), which is no better than guessing. "
       "Is the model really answering (think: false? the examples in the right roles?) and are you scoring `raw` with the same prompt contract?")


def test_lora_fine_tune_beats_prompting(tmp_path_factory):
    res = run_system("lora", tmp_path_factory)
    m = res["m"]
    ok(m["json_valid_rate"] >= M16_LORA_MIN_JSON_VALID, f"Only {m['json_valid_rate']:.0%} of the fine-tuned model's answers are valid JSON as plain text ({fmt(m)}). "
       f"Needed ≥ {M16_LORA_MIN_JSON_VALID:.0%}. Look at some `raw` values: a template mismatch between training and inference (thinking block, missing system prompt) is the usual cause.")
    weak = {f: m[f"f1_{f}"] for f in schema.FIELDS if m[f"f1_{f}"] < M16_LORA_MIN_FIELD_F1}
    ok(not weak, f"Per-field macro-F1 too low for {weak} (minimum {M16_LORA_MIN_FIELD_F1}); all: {fmt(m)}. Which classes does the model never predict?")
    ok(m["f1_macro"] >= M16_LORA_MIN_F1_MACRO, f"Fine-tuned macro-F1 is {m['f1_macro']:.2f} ({fmt(m)}); needed ≥ {M16_LORA_MIN_F1_MACRO}. "
       "More epochs? learning rate? rare classes (see the label counts)? an unseen technician's style?")
    need("ollama")
    few = run_system("fewshot", tmp_path_factory)["m"]["f1_macro"]
    ok(m["f1_macro"] >= few + M16_LORA_GAIN_OVER_FEWSHOT, f"The LoRA model ({m['f1_macro']:.2f}) must beat the same base model with examples in the prompt "
       f"({few:.2f}) by {M16_LORA_GAIN_OVER_FEWSHOT}. Otherwise you paid for training and got nothing: is the adapter really loaded in `--system lora`?")


def test_the_three_systems_are_logged_and_the_numbers_are_true(tmp_path_factory):
    need("mlflow")
    from mlflow import MlflowClient

    client = MlflowClient()
    exp = client.get_experiment_by_name(EXPERIMENT)
    ok(exp is not None, f"No MLflow experiment named `{EXPERIMENT}`. Log one run per system (tag `system` = rules | fewshot | lora) with the metrics "
       "f1_component, f1_cause, f1_action, f1_macro and json_valid_rate.")
    runs = client.search_runs([exp.experiment_id], order_by=["attributes.start_time DESC"], max_results=200)
    for system in ("rules", "fewshot", "lora"):
        mine = [r for r in runs if r.data.tags.get("system") == system]
        ok(mine, f"No run tagged system={system} in experiment `{EXPERIMENT}`.")
        logged = mine[0].data.metrics
        missing = [k for k in ("f1_component", "f1_cause", "f1_action", "f1_macro", "json_valid_rate") if k not in logged]
        ok(not missing, f"The latest `{system}` run lacks the metrics {missing}.")
        ok(all(0.0 <= logged[k] <= 1.0 for k in ("f1_component", "f1_cause", "f1_action", "f1_macro", "json_valid_rate")), f"The `{system}` metrics must be in [0, 1].")
        if system == "fewshot":
            need("ollama")
        real = run_system(system, tmp_path_factory)["m"]
        gap = abs(logged["f1_macro"] - real["f1_macro"])
        ok(gap <= M16_LOG_TOLERANCE, f"You logged f1_macro {logged['f1_macro']:.2f} for `{system}`, the check measured {real['f1_macro']:.2f} on the held-out notes (gap {gap:.2f}, "
           f"allowed {M16_LOG_TOLERANCE}). Your dev set and the held-out set differ, but not by that much: is the logged run the one you think it is?")


@pytest.mark.bonus
def test_bonus_synthetic_notes_have_a_leakage_guard():
    """Optional: notes written by a bigger local model. The check looks for near-copies of the hidden held-out notes."""
    ok(SYNTHETIC.exists(), "ml/notes_llm/data/synthetic.jsonl not found (bonus: augment the rare classes with a bigger local model).")
    syn, held = jl(SYNTHETIC), heldout()
    ok(syn and all(r.get("source") == "synthetic" and r.get("label", {}).get("component") in schema.COMPONENTS and r["label"].get("cause") in schema.CAUSES
                   and r["label"].get("action") in schema.ACTIONS for r in syn), "Every synthetic row needs source='synthetic', a note and an enum-valid label.")
    hg = [grams(r["note"]) for r in held]
    sims = [max(len(g & h) / (len(g | h) or 1) for h in hg) for g in (grams(r["note"]) for r in syn)]
    ok(max(sims) < M16_SYN_MAX_SIMILARITY, f"A synthetic note is {max(sims):.2f}-similar to a held-out note: the generator saw (or memorised) the exam.")
    share = sum(s >= 0.8 for s in sims) / len(sims)
    ok(share <= M16_SYN_MAX_SIMILAR_SHARE, f"{share:.0%} of the synthetic notes are ≥ 0.8-similar to a held-out note (allowed {M16_SYN_MAX_SIMILAR_SHARE:.0%}). "
       "Add a near-duplicate filter against everything you evaluate on and never put dev/held-out notes in the generator's prompt.")
    train = jl(TRAIN)
    ok(sum(r["source"] == "synthetic" for r in train) <= 3 * max(1, sum(r["source"] == "labelled" for r in train)),
       "More than 3 synthetic notes per real one: the model will learn the generator's voice, not the technicians'.")
