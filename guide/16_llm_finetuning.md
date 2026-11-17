# Module 16 — Fine-tune a small LLM: from a technician's scribble to structured JSON

⏱ 6–8 h · Services: Ollama (host) and MLflow (`make up-lake`) · Check: `make check-16` · Role: **ML Engineer**

> **Status:** this module is new and not validated end to end yet. The check thresholds are first estimates and may change.

**Tools:** Ollama, `mlx-lm` (Apple Silicon) or Hugging Face `transformers` + `peft` (everything else), MLflow, a bit of regex.
**Hardware:** Apple Silicon with 16 GB is the comfortable setup (training takes minutes). Linux/Windows/Intel Macs work through a documented CPU path, slower (see "CPU path").
**Part 2 of the exercise, "The second plant"**: modules 15–18 are about PLT02. They can be done in any order after module 4; this one needs nothing from module 15.

## What you will build
A small language model that reads a note like

```
p7 vibes bad, DE brg hot, swapped mech seal
```

and answers with exactly one JSON object `{"component": "seal", "cause": "unknown", "action": "replace"}`. You build **three systems** and compare them honestly:

| | System | What it is |
|---|---|---|
| (a) | **rules** | A regex/lexicon baseline you write by hand. Cheap, explainable, brittle. |
| (b) | **few-shot** | The *base* small model (Qwen3-0.6B) through Ollama, with a few labelled examples in the prompt. No training. |
| (c) | **LoRA** | The same model, fine-tuned on the lead technician's labelled notes with LoRA. |

Everything is scored the same way (per-field macro-F1 + JSON-valid rate) and logged to MLflow. Optional bonus: synthetic training notes from a bigger local model, with a **leakage guard**.

## Why this module exists
PLT02 has 20 technicians who type failure notes by hand, in English shop-floor shorthand with typos (`brg`, `mech seal`, `cav`, `r&r`, "seal ok, brg noisy"). Nobody can query that: you cannot count seal failures per pump, so you cannot plan spares or feed the models of module 4.
Two facts about the plant decide the engineering:
- **The data must not leave the site** (a contract with the owner), so a hosted LLM API is out; and the plant's **internet is flaky**, so even an allowed one would be an operational risk.
- Only **~300 notes** were ever labelled, by the lead technician (T01), by hand. Everything else (1 000+ notes) has no labels, and the most recent 150 notes are the **hidden exam**.

A 0.6 B-parameter model that runs on a laptop-class machine at the plant, fine-tuned on 240 notes, is the realistic answer. The *engineering question* of this module is not "can I fine-tune?" (a recipe does that) but "**is it worth it against a rules script and against prompting**, and how do I know?". That is what you measure.

## Concepts
- **Structured extraction**: text → fixed schema. Three fields, closed vocabularies (`platform/plt02/schema.py`): component (7), cause (9, with `unknown`), action (7, with `none`). The labelling rule of the lead technician: *component* = the part found faulty or worked on (not the part that merely shows a symptom); *cause* = only if the note says it, or the verb does ("realigned" ⇒ misalignment), otherwise `unknown`; *action* = what was **done** (`none` = nothing yet: "parts ordered", "will monitor").
- **Base vs instruct vs fine-tuned**: the base instruct model follows instructions in general; fine-tuning teaches *this* task, *this* vocabulary and *this* output format.
- **LoRA**: instead of updating all 600 M weights, freeze them and learn small low-rank matrices (`W + BA`, rank `r` ≈ 8–16) on some layers. Tiny trainable parameter count ⇒ fits in memory, trains in minutes, and the adapter is a few MB. **`alpha`** scales the update; **target modules** decide which matrices get an adapter (attention only vs attention + MLP).
- **Chat template**: models are trained on text wrapped in special tokens (`<|im_start|>system …`). Training and inference must render the prompt **identically**. Qwen3 is a *hybrid reasoning* model: its template can open a `<think>` block. We use it **without thinking** (`enable_thinking=False`); a mismatch here is the number-one reason a fine-tune "works in training and fails in the check".
- **Prompt masking**: compute the loss only on the answer tokens, not on the (long, constant) system prompt.
- **Macro-F1**: F1 per class, averaged over classes **equally**. With `cause = unknown` on ~40 % of the notes, accuracy rewards ignoring the rare causes (imbalance, cavitation); macro-F1 doesn't. We compute it **per field** and average the three.
- **JSON-valid rate**: the share of answers that are, *as plain text*, one JSON object with the three keys and enum values. Models that are "usually right" but 5 % unparsable are a production incident, not a metric nuance.
- **Held-out set and leakage**: the exam must be notes the model (and *you*) never used to fit anything. Leakage shows up as a score that is too good: near-duplicates, the same work order under another id, a generator that saw the exam.
- **Label noise**: the lead technician is human; a few percent of the 300 labels are slips. A model can't be better than its labels unless you notice.

## The contract
### Data (given)
- `platform/plt02/data/labelled.jsonl` — 300 notes with the lead technician's labels (`wo_id, asset_tag, technician, opened_at, note, label`).
- `platform/plt02/data/unlabelled.jsonl` — 1 050 earlier notes, no labels (for pseudo-labelling or ideas).
- `checks/_plt02_heldout.jsonl` — the 150 most recent work orders **with** labels: **the exam. Don't open it, don't train on it.** (The check shows you scores and failure messages, which is enough to debug.)
- `platform/plt02/notes.py` is the *plant simulator* that produced all of it (module 15's mock CMMS serves the same work orders). **It is not a labelling tool**: calling it with other seeds to mint labelled notes would make the whole exercise meaningless, and the check refuses rows that claim to be labelled but are not in the 300.

### Your code (`ml/notes_llm/`)
| File / command | Contract |
|---|---|
| `python -m ml.notes_llm.predict --system {rules,fewshot,lora} --in notes.jsonl --out predictions.jsonl` | **The command the check runs.** Input rows `{wo_id, note, …}`; output one row per input: `{"wo_id": …, "raw": "<the exact text the system produced>"}`. For `rules`, `raw` is the JSON string of your prediction. The check parses `raw` itself. |
| `ml/notes_llm/data/train.jsonl` | Your training set, one row per example: `wo_id, note, label, source`; `source` ∈ `labelled` (one of the 300), `pseudo` (an unlabelled note labelled by a model) or `synthetic` (written by a model; `wo_id` is yours). |
| MLflow experiment `notes-llm` | One run per system you evaluate (on **your own dev split** of the 300): tag `system` = `rules` \| `fewshot` \| `lora`; metrics `f1_component`, `f1_cause`, `f1_action`, `f1_macro` (mean of the three), `json_valid_rate`; params `base_model`, `n_eval`. |
| `NOTES_ADAPTER_DIR` (default `ml/notes_llm/artifacts/adapter`), `NOTES_BASE_MODEL` (default `Qwen/Qwen3-0.6B`) | Where `--system lora` finds the adapter and which base model it loads. |

### The prompt (shared with module 17)
Every model is asked **exactly**: a `system` message = `schema.SYSTEM_PROMPT` and a `user` message = the note; it answers `{"component":"…","cause":"…","action":"…"}`. Import the vocabulary and the prompt; don't retype them:

```python
import sys; sys.path.insert(0, "platform")      # `platform.plt02` can't be imported: `platform` is also a stdlib module
from plt02 import schema                           # schema.SYSTEM_PROMPT, schema.ENUMS, schema.valid_answer(...)
```

### Metrics (what the check recomputes)
- A prediction is **JSON-valid** iff `json.loads(raw.strip())` is an object with exactly the keys `component`, `cause`, `action` and enum values. No code fences, no prose around it.
- Per field: macro-F1 over the classes that exist in the held-out truth; an invalid or missing value counts as a miss for the true class. `f1_macro` is the mean of the three.
- Thresholds live at the top of `checks/test_m16_finetuning.py` as named constants (`M16_LORA_MIN_F1_MACRO`, …). They are the same for everyone; they are not the goal, the comparison is.

## Your task
1. **Read before you build.** Print 40 labelled notes. Write in `notes/m16_notes.md` what you see: abbreviations, typos, who writes how (`technician`), negations ("seal ok, brg noisy"), notes with no cause, notes where nothing was done. Count the classes: what does a model that always answers the majority class score?
2. **Split.** Make a stable train/dev split of the 300 (e.g. 240/60 by hashing `wo_id`) and write `ml/notes_llm/data/train.jsonl` and `dev.jsonl`. Dev is for *you*; the exam is hidden.
3. **System (a), rules.** Lexicons + a negation window + typo tolerance (the standard library has `difflib`). Write the scoring function once (macro-F1, JSON-valid) and use it for all three systems. Look at what the rules get wrong **by technician** and **by class**.
4. **System (b), few-shot.** `ollama pull qwen3:0.6b` (Apache-2.0, 523 MB). Call `/api/chat` with `think: false`, `temperature: 0`, the shared system prompt and *k* labelled examples as user/assistant turns. Try fixed examples vs the *k* most similar to the note. Measure the JSON-valid rate **without** constrained output first: that number is your motivation for module 17.
5. **System (c), LoRA.** Train an adapter on `train.jsonl` (Apple Silicon: `mlx_lm.lora`; otherwise the CPU path below). Watch the validation loss; stop where it stops improving, not where your patience ends. Load it in `--system lora`.
6. **Evaluate all three on dev**, log three MLflow runs, and write the comparison (`notes/m16_results.md`): a table, three examples each system gets right/wrong that the others don't, and your verdict on *when each one is the right tool*. Include cost: your time, training minutes, inference latency.
7. **Run the check.** It re-scores everything on the hidden notes. Then read the gap between your dev number and the check's number.
8. *(Bonus)* **Synthetic augmentation**: ask a bigger local model (`qwen3:4b-instruct`, already in your Ollama) for notes of the rare classes, **with the label you asked for** (never let the big model label), keep `source: synthetic`, and add a **leakage guard** (near-duplicate filter against everything you evaluate on; never put dev notes in the generator's prompt). `make bonus` checks the synthetic file against the hidden notes.

## How you will know you are done
`make check-16` feeds the 150 hidden notes to **your** `predict` command three times (rules, few-shot, LoRA), parses `raw`, recomputes every metric, and then compares them with what you logged to MLflow (a big gap means invented or stale numbers). It also checks that `train.jsonl` doesn't contain the hidden notes (by id or by text) and that nothing claims to be a human label that isn't. The LoRA system must be valid JSON ≥ 97 % of the time, reach the F1 floors, and beat few-shot by a margin. Needs Ollama running (`ollama serve`, `qwen3:0.6b` pulled) and `make up-lake`. Expect 10–25 minutes the first time (model loading, 450 generations); CPU-only: up to ~45 minutes.

## CPU path (Linux, Windows via WSL2, Intel Mac)
`uv sync --group llm` installs `peft` + `transformers` instead of `mlx-lm` on these platforms. Same contract, same prompt, different trainer.
- **Model:** `Qwen/Qwen3-0.6B` works on CPU in float32 (≈ 3 GB RAM, 8 GB machine is enough). Estimate: **30–90 minutes** to train 3 epochs on 240 notes (about 700 sequences of ~230 tokens) on a modern 8-core CPU, and **10–20 minutes** to generate the 150 answers. These are estimates, not promises: time one step before committing.
- **Too slow / not enough RAM?** Use `NOTES_BASE_MODEL=HuggingFaceTB/SmolLM2-360M-Instruct` (Apache-2.0, 360 M): roughly 40 % faster, a bit less accurate; the check's thresholds are the same, so you may need more epochs.
- **Tricks that are legitimate:** shorter `max_length`, batch 8, LoRA on attention + MLP projections with `r=16`, train only on the answer tokens (the prompt is the same for every example, so it carries no signal).
- Set `NOTES_BACKEND=peft` to force this path on a Mac.

## Comprehension questions
1. Why is accuracy a bad headline metric here? Which of the three fields would it flatter most?
2. In one sentence each: what do `r` and `alpha` do in LoRA? Why does a LoRA adapter need the *same* base model at inference?
3. Your fine-tune scores 0.95 on dev and 0.70 on the check. List three explanations, from most to least likely, and say how you would tell them apart.
4. Why do training and inference have to share the exact chat template? What would you see if they don't?
5. Few-shot with 8 retrieved examples is "free" (no training). Name two costs that make it worse than a fine-tune in production on a flaky-connection, 20-user plant.
6. The lead technician mislabels ~4 % of the notes. What does that do to your dev score, and what would you do about it?
7. Why is it important that the generator in the optional bonus never sees the dev notes and never assigns the label? What does a "leakage guard" actually compare?
8. When would the rules baseline be the *right* production choice?
9. The held-out set is the most recent 150 work orders, not a random sample. What does that protect you from, and what could still be wrong with it?
10. Qwen3 has a thinking mode. Why do we switch it off for this task, and where would the thinking tokens go in your latency budget (module 17)?

## Hints
<details><summary>Setup</summary>

`uv sync --group llm` (Apple Silicon: installs `mlx-lm`; elsewhere `peft`, `transformers`, `accelerate`). Models are downloaded from Hugging Face on first use (`Qwen/Qwen3-0.6B`, ~1.5 GB); set `HF_HOME` if your home disk is small.
`ollama pull qwen3:0.6b`.
</details>
<details><summary>Scoring function</summary>

For each class `c` in the truth: `TP`, `FP`, `FN` ⇒ `F1 = 2TP / (2TP + FP + FN)`, then average. Write it with plain Python so that you can unit-test it on a toy example before trusting any number. Decide in advance what an invalid answer is (a miss for the true class).
</details>
<details><summary>Rules baseline</summary>

Start from the most frequent component/cause/action words. Typos: compare each token with the vocabulary using `difflib.get_close_matches(token, vocab, cutoff=0.8)`. Negation: a component mentioned right before/after `ok`, `fine`, `good`, `normal` or after `no`/`not` is *not* the faulty part. The action verb is usually next to the component that was worked on.
</details>
<details><summary>Few-shot through Ollama</summary>

`POST /api/chat` with `{"model": "qwen3:0.6b", "messages": [...], "stream": false, "think": false, "options": {"temperature": 0, "num_predict": 80}}`; the answer is in `message.content`. Put the examples as alternating `user`/`assistant` messages after the system message. Qwen3 without `think: false` spends your tokens on a reasoning block.
</details>
<details><summary>Training data for mlx-lm</summary>

`mlx_lm.lora` wants a folder with `train.jsonl` and `valid.jsonl`; each line `{"messages": [{"role": "system", …}, {"role": "user", …}, {"role": "assistant", …}]}`. Start with `python -m mlx_lm.lora --help`. The knobs you will touch: `--model`, `--data`, `--train`, `--adapter-path`, `--iters`, `--batch-size`, `--learning-rate`, `--num-layers`, `--mask-prompt`, `--steps-per-eval`. 240 examples at batch 4 is 60 steps per epoch; 5–8 epochs is a sane range to explore. Learning rate between `5e-5` and `3e-4`.
</details>
<details><summary>Generating with the adapter (mlx)</summary>

`from mlx_lm import load, generate`; `model, tok = load(base, adapter_path=...)`; build the prompt with `tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, enable_thinking=False)`; `generate(model, tok, prompt=prompt, max_tokens=80)` (greedy by default). Load once, generate for all notes.
</details>
<details><summary>PEFT skeleton (CPU)</summary>

```python
from peft import LoraConfig, get_peft_model
model = get_peft_model(base_model, LoraConfig(r=16, lora_alpha=32, target_modules=[...], task_type="CAUSAL_LM"))
```
Build `input_ids` = prompt tokens + answer tokens, `labels` = `-100` on the prompt tokens (ignored by the loss) and the answer ids elsewhere. A plain PyTorch loop (AdamW, a few epochs) is easier to reason about than a trainer class. `model.save_pretrained(adapter_dir)`; at inference `PeftModel.from_pretrained(base, adapter_dir)`. Remember the end-of-turn token (`<|im_end|>`) at the end of the answer.
</details>
<details><summary>The score is fine in training and the JSON is garbage at inference</summary>

Print the **exact prompt string** your training code and your inference code produce for the same note and diff them. Look for a `<think>` block, a missing system prompt, or a different end-of-turn token. Then print 5 `raw` outputs.
</details>
<details><summary>A class is never predicted</summary>

Count its examples in `train.jsonl`. With 6 examples a model can't learn it; options: more epochs, upweighting by repeating them, synthetic notes (bonus), or accepting it and writing the limitation in your results note. Which one would you defend to Marta?
</details>
<details><summary>Logging to MLflow</summary>

`mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])`, `mlflow.set_experiment("notes-llm")`, `with mlflow.start_run(run_name=system): mlflow.set_tag("system", system); mlflow.log_params({...}); mlflow.log_metrics({...})`.
</details>

## Common traps
- Scoring on the training notes, or on a dev split that leaked into training through the `valid.jsonl` you used to pick hyper-parameters.
- Training and inference with different prompts (system message, thinking block, end token).
- Using `accuracy` and being happy: `unknown` is the most common cause.
- Parsing the model output leniently in your own evaluation (stripping code fences, regex-extracting JSON) and then being surprised by the check's strict validity.
- Comparing three systems on three different sets of notes.
- Calling the plant simulator with another seed "just to get more data".
- Letting the bigger model label the synthetic notes, or letting it see the dev notes.
- Trusting a single number: look at the per-class report and at the notes the model gets wrong.

## On a real job
You would add **active learning** (the lead technician labels the notes the model is least sure of, not random ones), a labelling guideline and **inter-annotator agreement** (two people label 50 notes: what is the ceiling?), **continuous evaluation** on a fresh sample every month (technicians change habits; new hires bring new shorthand), shadow mode before the model writes into the CMMS, a confidence threshold that sends doubtful notes to a human, writing the result back to the platform (the module 15 connector leaves `failure_mode = 'unclassified'` on PLT02 work orders: `schema.repo_failure_mode(label)` maps a label to the platform's bearing / seal / cavitation vocabulary), and a model card. Bigger or hosted models (including a paid API such as Claude's) would likely score higher on this task; they are out here for data-residency and connectivity reasons, which is exactly why the comparison above is worth doing instead of assuming.
**Courses:** *LLM Zoomcamp* (fine-tuning and evaluation), Hugging Face's LLM course (PEFT chapter), Apple's `mlx-lm` LoRA documentation.

→ Next: [`17_edge_inference.md`](17_edge_inference.md)
