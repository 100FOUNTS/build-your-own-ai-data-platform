# Module 17 — Serve it at the edge: a GGUF behind llama-server, constrained output, 20 technicians, one decision record

⏱ 5–7 h · Services: none in Docker (everything runs on the host; the bonus uses Ollama) · Check: `make check-17` · Role: **Inference Engineer** (bonus: **AI Engineer**)
**Tools:** llama.cpp (`llama-server`, `llama-quantize`, `convert_hf_to_gguf.py`, pinned release), an OpenAI-compatible HTTP API, `httpx`/`asyncio`, JSON Schema.
**Mode:** solo, guided or walkthrough — see [modes](modes.md) · set it with `/mode 17 <mode>`
**Hardware:** Apple Silicon with 16 GB recommended for developing. The **target** is the plant's box (module 15, interview): 16 GB RAM, **4 CPU cores, no GPU**, Ubuntu, no internet. You will emulate it on your machine (`-ngl 0 -t 4`, see the task list) and decide for *that* box. Needs the adapter from module 16.
**Part 2, "The second plant"**: PLT02's data cannot leave the site and its internet is flaky, so the model from module 16 must run **on a machine in the plant**, for 20 people at once.

## What you will build
```
adapter (module 16) → fused HF model → GGUF f16 → Q8_0 and Q4_K_M → llama-server :8090 (JSON-schema constrained)
                                                                        ▲
                                          20 concurrent "technicians" (your load test) → TTFT / end-to-end / tokens/s / quality per quant
```
and a **decision record** that picks one configuration against an SLO you define, with the numbers to back it. Bonus: a small **gateway** that answers with the local model and escalates to a bigger local model (Ollama) when the answer is invalid or low-confidence.

## Why this module exists
A fine-tune in an MLflow run is not a service. Serving is where small models meet physics: how many tokens per second does *this* machine produce, how does that divide among 20 people who all press "send" at the shift change, what does 4-bit quantization cost in accuracy, and **what happens to the p95 when the queue builds**. The answers decide whether the plant gets a tool or a toy.
You will also fix the thing module 16 leaves open: a model that is "usually valid JSON" is not good enough for a CMMS integration. **Constrained decoding** makes invalid output *impossible*, and costs almost nothing.

## Concepts
- **GGUF**: llama.cpp's single-file model format (weights + tokenizer + chat template + metadata). **Quantization** stores weights in fewer bits: `Q8_0` (8-bit, near lossless, ~0.65 GB for 0.6 B), `Q4_K_M` (4-bit blockwise "k-quant", ~0.4 GB, faster to read, a little less accurate). On small models, *memory bandwidth* often limits decoding, but CPU kernels and batching can reverse the size/speed ordering. Benchmark both quantizations on the target profile.
- **Fuse (merge) the adapter**: bake `W + BA` into the weights so the exported model needs no LoRA runtime. Convert the *fused, unquantized* model, then quantize; never quantize twice.
- **llama-server**: an HTTP server over llama.cpp with an OpenAI-compatible `/v1/chat/completions` (streaming included), `/health`, `/props`, `/slots`, `/metrics`.
- **Slots and continuous batching**: `-np N` creates N slots; the context `-c` is **divided** among them (`-c 4096 -np 4` = 1 024 tokens per slot). Requests beyond N wait in a queue. Continuous batching decodes the active slots together, so throughput rises with slots until the machine saturates and then only latency does.
- **Prompt cache**: all requests share the same long system prompt; llama.cpp can reuse the evaluated prefix of a slot. It matters for TTFT.
- **Constrained decoding**: at each step the sampler only allows tokens that keep the output valid for a **grammar** (GBNF). A **JSON Schema** can be compiled into a grammar. For the pinned b12061 build with Qwen3, use a GBNF grammar derived from the schema via `--grammar-file services/edge/schema.gbnf`: `-jf` treats the template prefill as output and fails sampler initialization. With `enum`s the model *cannot* invent a component; with the three required keys and a bounded grammar, only complete schema-valid objects can end normally (a token limit or transport failure can still truncate an answer). Guarantee of *syntax*, not of *truth*: a valid answer can still be wrong.
- **TTFT** (time to first token) = queueing + prompt processing; **end-to-end** = TTFT + decode time; **tokens/s** has two flavours: *per request* (what one user sees while the answer streams) and *aggregate* (what the machine does for everybody). Report percentiles (p50/p95), never only averages.
- **Open vs closed loop**: a load test where each simulated user waits for its answer and *thinks* before the next request is closed-loop (this module: 20 technicians). It under-reports overload compared with an open-loop arrival rate, because slow responses slow the arrivals; know which one you ran ("coordinated omission").
- **SLO** (service-level objective): a target written *before* measuring, e.g. "TTFT p95 ≤ 1.5 s, end-to-end p95 ≤ 4 s, macro-F1 ≥ 0.80 on the held-out notes". A **decision record** (ADR) says which option meets it and what you gave up.

## The contract
### Setup (given, environment only)
`make edge-setup` installs a **pinned** llama.cpp (release `b12061`, 2026-11-02, MIT) in `.tools/llama.cpp/` (`bin/` prebuilt binaries, `src/` the same tag's source for `convert_hf_to_gguf.py`; Homebrew follows the newest release, which changes flags under you). `uv sync --group llm` installs the converter's Python dependencies. Windows: use WSL2.

### The server (what the check talks to)
- `llama-server` on **`http://localhost:8090`** (override the check's target with `EDGE_URL`); `make edge-serve` starts it (`QUANT=Q4_K_M` by default). The model file is a **GGUF** under `models/plt02/` (git-ignored); `/props` must report it.
- **Constrained by default**: the server is started with the JSON Schema of the answer (`schema.NOTE_JSON_SCHEMA`, written to `services/edge/schema.json` and compiled to `services/edge/schema.gbnf`), so a request that says nothing about format still gets a valid object. The check sends *plain* chat requests (no `response_format`) with temperature 1.5 and garbage notes: the output must stay schema-valid.
- **Prompt contract** (same as module 16): `messages = [{"role": "system", "content": schema.SYSTEM_PROMPT}, {"role": "user", "content": <note>}]`; answer `{"component":…,"cause":…,"action":…}`. Thinking must be off (`--reasoning off`, which selects the same empty thinking prefill used in training).
- OpenAI-compatible **streaming**: `"stream": true` returns server-sent events; the first event with non-empty `delta.content` marks the TTFT.

### Your code
| File / command | Contract |
|---|---|
| `python -m services.edge.export` | adapter → fused model → `models/plt02/notes-f16.gguf` → `notes-Q8_0.gguf`, `notes-Q4_K_M.gguf`; writes `services/edge/schema.json` and its equivalent `schema.gbnf`. |
| `bash services/edge/serve.sh` (`make edge-serve`) | Starts `llama-server` on 8090 for `$QUANT` with schema, no thinking, `-np`, `-c`, GPU offload. |
| `python -m services.edge.loadtest --label L [--url U] [--users 20] [--duration S] [--quality] [--out FILE]` | **20 concurrent technicians**: each loops *think 1–3 s → send a note (streaming) → wait for the whole answer*. Writes one JSON: `label, users, duration_s, requests, errors, ttft_ms{p50,p95}, e2e_ms{p50,p95}, tokens_per_s` and, with `--quality`, `quality{f1_macro, json_valid_rate, n}` computed on **your dev notes**. Default output `reports/edge/<label>.json`. `tokens_per_s` = completion tokens of all finished requests / wall time of the run. |
| `reports/edge/Q8_0.json`, `reports/edge/Q4_K_M.json` | One report per quantization (`--label Q8_0`, …, with `--quality`). |
| `notes/edge_decision.md` | Your decision record, below. |

### The decision record
```markdown
---
slo_ttft_p95_ms: 1500
slo_e2e_p95_ms: 4000
slo_min_f1_macro: 0.80
chosen: Q4_K_M
---
## Context   (who uses it, where it runs, what the machine is, why local)
## Options   (Q8_0 vs Q4_K_M, and anything else you tried: slots, context, an f16 model…; a table with your measured numbers)
## Decision  (which one, against which SLO, and which number decided it)
## Consequences (what you give up, what you would monitor, when you would revisit: new hardware, more users, a new model)
```
The SLO is **yours** (written before you look at the table), but not a joke: the check refuses an SLO looser than TTFT p95 ≤ 5 s, end-to-end p95 ≤ 10 s, F1 ≥ 0.65, and requires the chosen configuration to meet it *in your own reports*.

## Your task
1. `make edge-setup`; run `llama-server --help | less` and find the flags you will need (`-m`, `-c`, `-np`, `-ngl`, `--grammar-file`, `--reasoning`, `--alias`, `--host`, `--port`).
2. **Export.** Compile the schema to GBNF (three required keys in order, enum alternatives, no extra keys, bounded optional spaces). Fuse the adapter, convert to GGUF f16, quantize to `Q8_0` and `Q4_K_M`. Note the file sizes. Sanity-check the f16 file with one request before quantizing: if it is wrong in f16, quantization is not to blame.
3. **Serve** one quant. Call it with `curl` (non-streaming, then `"stream": true`) and read `/props` and `/health`. Send a note *without* any format instruction and confirm the JSON comes back.
4. **Constrain.** First serve *without* the schema and hit it at temperature 1.5 with a garbage note (`"asdf qwer"`); count the invalid answers. Then add the schema and repeat. Write down what changed in the answers **and** in the speed.
5. **Load test.** Write `services/edge/loadtest.py` (asyncio + `httpx` streaming is the natural fit). Decide and document: think time, which notes, what counts as TTFT, how you compute percentiles, what happens to errors. Run it against `Q8_0` and `Q4_K_M` (`--quality`), keep the JSON files.
6. **Emulate the plant box.** Your laptop is faster than the box the maintenance lead described (4 cores, no GPU). Serve the same GGUF with `EDGE_GPU_LAYERS=0 EDGE_THREADS=4 make edge-serve` (`-ngl 0 -t 4`, CPU only, 4 threads) and repeat the load test. The numbers you put in the decision record must be the ones for the *plant box profile*; say in `## Context` which profile each number comes from. (`M17_SLO_SCALE=4 make check-17` scales the check's latency limits for that profile.)
7. **Explore** at least two knobs on your chosen quant: slots (`-np 1/2/4/8`) and what happens to p95 TTFT; context per slot; offloading all layers or not. One table, one paragraph of interpretation.
8. **Decide.** Write the SLO first, then the table, then `notes/edge_decision.md`.
9. Start the server with the quant you chose and run `make check-17`.
10. *(AI Eng bonus)* **Gateway** (`services/edge/gateway.py`, `uvicorn services.edge.gateway:app --port 8091`): `POST /classify {"note": …}` → `{component, cause, action, source: "local"|"escalated", confidence}`. Ask the local server with `logprobs`; define `confidence` as the **minimum token probability** of the completion; escalate to the bigger Ollama model (`OLLAMA_CHAT_MODEL`, with the JSON schema in `format`) when the local answer is not schema-valid, the local call fails, or `confidence < ESCALATE_BELOW` (env, default 0.6). Measure how often it escalates on the held-out-like dev notes and what that does to p95 and to F1: is the escalation threshold a *quality* knob or a *latency* knob?

## How you will know you are done
`make check-17` needs **your server running** (`make edge-serve`) and then, on its own:
1. confirms a GGUF is served by llama-server (`/props`, `/v1/models`);
2. sends 40 requests with **temperature 1.5** and garbage/injection notes, plus a stress request that biases the tokenizer’s `!!!` token by +100, and demands 100 % schema-valid answers ending normally. A fine-tune may pass ordinary sampling without a grammar; the bias probe verifies a hard decoding constraint;
3. scores the served model on the 150 hidden held-out notes (macro-F1 floor, valid-rate 100 %);
4. runs **its own** 20-technician load for 20 s and checks TTFT p95, end-to-end p95, aggregate tokens/s and zero errors against fixed limits;
5. runs **your** `services.edge.loadtest` for 15 s and requires it to agree with its own measurement (same story within a factor of 2.0);
6. reads `reports/edge/*.json` and `notes/edge_decision.md`: both quants compared, SLO sane, the chosen quant is the one actually being served, your reported quality matches what it measured, and the record discusses the alternative with numbers.

The validated reference used an Apple M4 Max with 36 GB RAM, 4 slots, 4 096 total context, 20 users for 30 seconds, and one unmeasured warm-up request. Q8_0 / Q4_K_M GGUF sizes were 639.4 / 396.7 MB. On GPU, TTFT p95 was 41.3 / 37.6 ms and end-to-end p95 145.6 / 133.9 ms; held-out macro-F1 was .7895 / .7573. On the four-thread CPU profile, Q8_0 / Q4_K_M end-to-end p95 was 488.6 / 1002.3 ms. Both met the original SLO; the reference chose Q8_0 for its higher quality and measured CPU latency advantage. These are measurements on one Mac, not plant-box hardware guarantees.

The default latency limits assume an Apple-Silicon GPU; for the CPU-only plant-box profile (or any slower machine) scale the *latency* limits, not the quality limits: `M17_SLO_SCALE=4 make check-17`. `make bonus` runs the gateway check, which needs **no model at all**: it starts your gateway against two stub servers (a fake local model and a fake Ollama) and drives every escalation path.

## Your turn (walkthrough mode)
On your own, add per-request completion-token p50 and p95 to the report written by `services/edge/loadtest.py`, using the server's usage counts.
With your chosen quant running, run `python -m services.edge.loadtest --label token-lengths --users 20 --duration 30 --quality --out /tmp/edge-token-lengths.json`.
Inspect the new percentiles alongside aggregate `tokens_per_s` and latency; exclude failed requests and keep the existing report fields and dev-only quality evaluation.
Then re-run `make check-17`.

## Comprehension questions
1. Why do smaller quantizations usually produce *more* tokens per second on this machine? When would they not?
2. You serve with `-c 4096 -np 8`. How many tokens of context does each request get, and what breaks if your longest prompt is 1 300 tokens?
3. TTFT p95 is 3 s but the decode speed is a healthy 150 tokens/s per user. Where is the time? Give two different fixes.
4. What exactly does the JSON schema guarantee, and what does it not? Give an example of a perfectly valid, perfectly wrong answer.
5. Constrained decoding applies a mask at every step. Why is it nearly free for this schema but not for an arbitrary 100-KB grammar?
6. Your load test is closed-loop with a think time. Describe a situation where it would make the system look healthier than it is.
7. Q4_K_M loses 2 points of macro-F1 against Q8_0 and is 25 % faster at p95. Under which SLOs do you pick each? Who decides, and where is it written down?
8. The check measures from *its own* load generator, not from yours. Why is that the right way to verify a performance claim?
9. Why is the gateway's confidence the *minimum* token probability and not the average? What would the average hide?
10. Name one operational risk of running a model server in a plant with a flaky network, and one with a stable one but 20 users sharing a laptop-class machine.

## Hints
<details><summary>Fusing</summary>

Apple Silicon: `python -m mlx_lm.fuse --model <base> --adapter-path <adapter> --save-path <dir>` writes a normal Hugging Face folder (safetensors + tokenizer). CPU path: `PeftModel.from_pretrained(base, adapter).merge_and_unload().save_pretrained(dir)` and save the tokenizer next to it. Do **not** pass `--de-quantize` unless your base was quantized. For a fully offline export, set `NOTES_BASE_MODEL` to the existing local Hugging Face snapshot directory; mlx-lm otherwise tries to fetch the model card even when the weights are cached.
</details>
<details><summary>Converting and quantizing</summary>

`python .tools/llama.cpp/src/convert_hf_to_gguf.py <fused_dir> --outfile models/plt02/notes-f16.gguf --outtype f16`, then `.tools/llama.cpp/bin/llama-quantize notes-f16.gguf notes-Q4_K_M.gguf Q4_K_M` (and `Q8_0`). If the converter complains about a missing Python package, it is one of the `uv sync --group llm` dependencies; if it complains about the tokenizer, the fused folder lacks the tokenizer files.
</details>
<details><summary>Starting the server</summary>

`llama-server -m models/plt02/notes-Q4_K_M.gguf --alias plt02-notes --host 127.0.0.1 --port 8090 -c 4096 -np 4 -ngl 99 --grammar-file services/edge/schema.gbnf --reasoning off --metrics`. Flags change between llama.cpp releases: trust `llama-server --help` of *your pinned build*, not a blog post.
</details>
<details><summary>Streaming and TTFT</summary>

POST with `"stream": true, "stream_options": {"include_usage": true}`. Lines look like `data: {...}`; the last is `data: [DONE]`. The first events may have an empty `delta` (the role): TTFT is the first **non-empty** `delta.content`. The last event before `[DONE]` carries `usage.completion_tokens`.
</details>
<details><summary>Percentiles and honesty</summary>

With 20 users thinking 1–3 seconds between answers, 30 seconds gives at most about 300 requests even with instant generation; queueing reduces that number. Record the actual count. 20 users for 3 s is nothing: warm-up (first request loads and compiles) dominates. Throw away warm-up explicitly, or run long enough that it doesn't matter, and say which you did.
</details>
<details><summary>My load test and the check disagree</summary>

Compare definitions one by one: when does your timer start (before the connection or after)? Is TTFT the first byte of the response or the first content token? Do you count tokens from `usage` or from chunks? Do you include the think time in the end-to-end latency (you shouldn't)? Same notes, same `max_tokens`, same temperature?
</details>
<details><summary>Answers are valid but the f1 dropped after quantizing</summary>

Evaluate f16, Q8_0 and Q4_K_M on the **same** notes with the **same** server flags. If f16 is already worse than the adapter was in module 16, the problem is the export (wrong prompt/template, thinking block, the base model instead of the fused one), not the quantization.
</details>
<details><summary>Gateway: confidence from logprobs</summary>

Ask the local server for `"logprobs": true, "top_logprobs": 1`; each content token has a `logprob` (natural log). Probability = `exp(logprob)`. On the validated b12061 server these logprobs are returned with the normal chat response. They are model scores, not calibrated correctness probabilities; the default is `post_sampling_probs: false`. Tokens that only carry JSON punctuation are often nearly certain: look at which tokens carry the information (the enum values) before choosing a threshold, and choose it from the *dev notes*, not by feel.
</details>

## Common traps
- Converting the **base** model (or the adapter alone) instead of the fused one.
- Training with thinking off and serving with it on (or the reverse): TTFT explodes and answers get truncated.
- `-c 4096 -np 8` and a long system prompt: every request is silently truncated or rejected.
- Measuring the first request (model warm-up) and calling it latency.
- Measuring from the same process that serves (a shared CPU), or from a Python loop that is itself the bottleneck at 20 threads.
- Choosing the quant before writing the SLO. The decision record must be falsifiable.
- Comparing quants on different notes, different temperature or different flags.
- Believing "valid JSON" means "correct".
- Leaving a web UI or the server bound to `0.0.0.0` on a plant network: `--host 127.0.0.1` unless something else must reach it, and then an `--api-key`.

## On a real job
You would add a **second replica** and a health-aware router, a **warm-up request** on start, **Prometheus** scraping of llama-server's `/metrics` into the Grafana of module 10 (queue depth, tokens/s, slot usage), a **canary** for new adapters with the quality check running against the live endpoint, request logging with PII rules, an **API key** and a systemd unit/launchd plist so the model survives reboots, a **speculative-decoding** or smaller-draft experiment, and a *fallback chain*: local model → bigger local model → human. An optional hosted fallback (for instance Claude's API) is possible where the data-residency contract allows it; it is a paid, internet-dependent path, never a requirement here.
**Courses:** the llama.cpp server README and `docs/` (read the flags of the release you pinned), *LLM Zoomcamp* (deployment), Hugging Face's guide on GGUF and quantization.

→ Next: [`18_experiments.md`](18_experiments.md)
