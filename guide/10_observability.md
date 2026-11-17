# Module 10 — Observability: OpenTelemetry, Prometheus, Grafana, Loki and Tempo

⏱ 9–11 h · Services: `make up-db up-stream up-obs` and **Ollama** running (the tracing check runs your module 6 RAG) · Check: `make check-10`
**Tools:** OpenTelemetry (including its **GenAI semantic conventions**), Prometheus (recording rules, `promtool`), Grafana (plus **Loki** and **Tempo**, the open alternatives for logs and traces).
**Mode:** solo, guided or walkthrough — see [modes](modes.md) · set it with `/mode 10 <mode>`

## What you will build
The ability to **understand what is happening inside your system from the outside**:
1. **Instrumentation** of the API with OpenTelemetry: traces (including the database and Redis), HTTP metrics and logs with `trace_id`.
2. The **OpenTelemetry Collector** config that sends each signal to its destination: traces → Tempo, metrics → Prometheus, logs → Loki.
3. **Prometheus** with alert rules; **Grafana** with datasources and a dashboard **as code** (provisioned from files).
4. **Traces of the AI**: your module 6 agent and RAG instrumented with the OpenTelemetry **GenAI semantic conventions** (model calls with token counts, tool calls, retrieval), visible in Tempo.
5. **An SLO with an error budget** for `/risk`: recording rules for the SLI and **multi-window, multi-burn-rate alerts**, tested with `promtool` against synthetic traffic.

## Why this module exists
A distributed system fails in ways you can't see by reading the code: a slow request goes through the API, the cache, the database and Kafka. Without observability,
“it's slow” is an opinion; with it, it is a trace that says *“80% of the time went into this SQL query”*.

The three pillars answer different questions:
| Pillar | Answers | Tool here |
|---|---|---|
| **Metrics** | How much? Is it getting worse? (cheap aggregates, alerts) | Prometheus |
| **Traces** | Where did the time go in *this* request? | Tempo |
| **Logs** | What exactly happened? | Loki |

An AI system adds a fourth question the three pillars don't answer on their own: **“what did the model do?”**. An agent is a loop of model calls, tool calls and searches whose path is decided at run time,
so the same question can take 2 steps today and 6 tomorrow. **You can't debug an agent you can't see**: when an answer is wrong, slow or expensive you need to know which model call, which tool and which retrieved chunk made it so.
And once you can see, you need to decide **how much failure is acceptable**: an alert on “p95 > 500 ms” says nothing about whether last month was fine. That is what an SLO and its error budget are for.

The power is in **correlating** them (from the errors panel to the failed trace and to its logs with the same `trace_id`). **OpenTelemetry** is the standard for instrumenting **once** and sending anywhere.

## Concepts
- **Span / trace**: a span is an operation with a duration; a trace is the tree of spans of one request. Context propagation ties services together.
- **RED metrics**: *Rate* (requests/s), *Errors* (% of failures), *Duration* (latency, percentiles). They work for almost any service.
- **Histogram and percentile**: the average lies; p95 tells you what the slowest 5% suffer.
- **Collector**: a process that receives OTLP, **processes** it (batches, memory limits) and **exports** it. It decouples the application from the destinations.
- **Alert**: a rule that fires when a condition holds. An alert without `for:` wakes someone up for a one-second spike.
- **SLI (service level indicator)**: a measurement of *user-visible* quality, written as `good events / valid events`. Here: the share of `/risk` requests that were answered correctly **and** fast.
- **SLO (service level objective)**: the target for the SLI over a period (“99 % of `/risk` requests are good, over 30 days”). It is a product decision with a cost: 99.99 % costs far more than 99 %, and is not always worth it.
- **Error budget**: what the SLO lets you get wrong, `1 − SLO` (1 % of the requests in 30 days). It turns “should we ship or stabilize?” into arithmetic: budget left → ship; budget gone → reliability work first.
- **Burn rate**: how fast you are spending the budget relative to the pace that would use exactly all of it in the period. Burn rate 1 = you end the month at zero; 14.4 = you spend 2 % of a 30-day budget in one hour.
- **Multi-window, multi-burn-rate alert**: page on a *fast* burn and open a ticket on a *slow* one, and require **two windows** to agree (a long one to be sure it is serious, a short one so the alert stops soon after the problem does). Alerting on the raw SLI with a threshold either misses slow leaks or pages on blips.
- **Recording rule**: a PromQL expression Prometheus evaluates on a schedule and stores as a new series. You compute the SLI once per window and reuse it in every alert and dashboard.
- **GenAI semantic conventions**: the OpenTelemetry standard names for the spans and attributes of AI applications (`gen_ai.*`), so any backend understands “a model call with 211 input tokens”. Their status is still **“development”**: names can still change, so pin your library versions and read the spec when you upgrade.
- **Token**: the unit a model reads and writes, and what you are billed for. It is also the best predictor of a model call's latency.
- **Configuration as code**: datasources and dashboards in versioned files (nobody clicks around in production).

## The contract
**`services/common/telemetry.py`** — `setup_telemetry(service_name, app)` sets up traces, metrics and logs over OTLP/gRPC, and instruments FastAPI (excluding `/health`), psycopg and redis.
The API calls it **only if** `OTEL_EXPORTER_OTLP_ENDPOINT` exists (without it, everything works the same); `OTEL_SERVICE_NAME` defaults to `plant-api`. It logs **one line per request**
(`request GET /assets -> 200`).

**`observability/otel-collector.yaml`** — OTLP receiver (gRPC 4317, HTTP 4318); `memory_limiter` and `batch` processors; exporters: traces → `tempo:4317`, logs → `http://loki:3100/otlp`,
metrics → the `prometheus` exporter on `0.0.0.0:8889` with `resource_to_telemetry_conversion` (to get the `service_name` label).
**`observability/prometheus.yml`** — scrapes `otel-collector:8889` and loads `/etc/prometheus/rules/*.yml`.
**`observability/rules/alerts.yml`** — ≥ 2 alerts (e.g. 5xx rate and p95 latency), each with `for`, `labels.severity` and `annotations.summary`; it must pass `promtool check rules`.
**`observability/grafana/`** — `provisioning/datasources/*.yml` (Prometheus, Loki, Tempo), `provisioning/dashboards/*.yml` and a JSON dashboard tagged `plant`, with **≥ 6 panels** from the three pillars
(rate, p95 latency, 5xx, by route/status, logs, traces) and ≥ 3 Prometheus panels with data after generating traffic.

### Instrument your module 6 agent (trace the AI)
You change `genai/agent.py` and `genai/rag.py` (module 6 stays as it is: you add to it here). The code only uses the OpenTelemetry **API** (`opentelemetry.trace`), never configures an SDK: if nothing configured tracing, the agent behaves exactly the same (the API is a no-op).
The check configures the SDK itself (an exporter to `localhost:4317`), runs your agent with a scripted model and a real `retrieve`, and reads the spans back. Names follow the [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (**status: development**: they now live in the `open-telemetry/semantic-conventions-genai` repository; check there if a name doesn't match what you read elsewhere).

Each `agent.invoke(...)` produces **one trace** (one root span, everything else below it) with these spans. No span may record the text of the question, of a search query or of tool arguments (content is *opt-in* in the conventions: it can hold personal data):

| Span | Name | Attributes (all required unless noted) |
|---|---|---|
| the agent run (**root**) | `invoke_agent plant-agent` | `gen_ai.operation.name = invoke_agent`, `gen_ai.agent.name = plant-agent` |
| each call to the model (retries included) | `chat {model}` | `gen_ai.operation.name = chat`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` (integers, > 0) |
| each tool the model asks for | `execute_tool {tool}` | `gen_ai.operation.name = execute_tool`, `gen_ai.tool.name`; a tool that fails or doesn't exist: span status **ERROR** and `error.type` |
| the RAG search (`retrieve`), a **child of the tool span** that triggered it | `retrieval` | `gen_ai.operation.name = retrieval`, `gen_ai.retrieval.top_k` = the `k` asked for |

**Where the token counts come from.** `llm(messages) -> str` stays as in module 6, but after every call the `llm` object must expose what that call consumed in an attribute `llm.usage = {"provider": …, "model": …, "input_tokens": …, "output_tokens": …}`.
Read it right after calling the model; an `llm` without `usage` (a plain function) must still work, just with no token attributes. (Ollama's `/api/chat` answers with `prompt_eval_count` and `eval_count`: those are the counts.) Use what the model reports: don't estimate by counting characters.

### SLOs and error budgets for the API
**`observability/rules/slo.yml`** — the SLO is **“99 % of `GET /assets/{id}/risk` requests are good, over 30 days”**, so the error budget is **1 %**. A request is **good** when the server did not answer 5xx **and** it took ≤ 500 ms. 4xx answers (a 404 for an unknown asset) are the client's mistake: they are good.
Only requests of the service `plant-api` for the `/risk` route count (not the other routes, not other services). Use the metric you already have: `http_server_duration_milliseconds_{bucket,count}` with the labels `service_name`, `http_target`, `http_status_code` and `le`. **Look at the real series in Prometheus before writing the selectors**: the check's synthetic traffic uses them exactly as your API exports them, e.g. `http_target` is the route *template* (`/assets/{asset_id}/risk`) and the 500 ms bucket is written `le="500.0"`.

- **Recording rules**: `sli:risk_bad_ratio:rate5m`, `…:rate30m`, `…:rate1h`, `…:rate6h` = the fraction of **bad** requests over that window (a number between 0 and 1; one series, aggregated without labels).
- **Alerts** (built on those rules, thresholds expressed as *burn rate × budget*):
  - `RiskSLOFastBurn`, `labels.severity: page`: burn rate **> 14.4** (2 % of the budget in 1 hour) on **both** the 1 h and the 5 m windows. If you use `for:`, ≤ 5 m.
  - `RiskSLOSlowBurn`, `labels.severity: ticket`: burn rate **> 6** (5 % of the budget in 6 hours) on **both** the 6 h and the 30 m windows. If you use `for:`, ≤ 30 m.
  - Both with an `annotations.summary` that says how fast the budget is burning.
- The file must pass `promtool check rules`, and Prometheus must load it from `/etc/prometheus/rules/` (it is already mounted).

## Your task
1. Start `make up-obs` (with the starting config that only prints) and look at http://localhost:3030 (Grafana), :9090 (Prometheus). What do you see empty, and why?
2. Write `telemetry.py` and turn it on in the API. First check in the Collector console (`docker compose --profile obs logs -f otel-collector`) that signals **arrive**.
3. Configure the Collector (three pipelines) and Prometheus. Check in Prometheus: `http_server_duration_milliseconds_count` with the label `service_name="plant-api"`.
4. Generate traffic (a `curl` loop) and look for *your* trace in Tempo (Grafana → Explore → Tempo). Do you see the PostgreSQL span? And that request's logs in Loki, with the same `trace_id`?
5. Write two useful alerts and validate them with `promtool`. Ask yourself for each one: who receives it and what do they do in the first 5 minutes?
6. Build the dashboard in Grafana, **export it to JSON** and let it be provisioned from the file. Restart Grafana: it must still be there.
7. **Trace the AI.** First read the spans of a real run: configure a tracer provider in a scratch script (an OTLP exporter to `localhost:4317`), call your agent once and open the trace in Grafana → Explore → Tempo. What would you want to see that is not there yet?
   Then add the spans of the contract: the agent run, the model calls, the tools, the search. Teach your `llm` wrapper to expose `usage`. Ask yourself: if a user says “the assistant took 40 s”, which span tells you why?
8. **SLO.** Before writing any rule, write on paper: who are the users of `/risk`, what is “good” for them, and what number you would promise. Then write the recording rules and the two alerts. Test them *without waiting a month*: write a `promtool test rules` file with a few synthetic series
   (a window of healthy traffic, a window of 20 % errors, a 3-minute blip) and decide in advance what must fire. Finally, in Prometheus (:9090) check that your SLI returns a sensible number on your real API's traffic.

## How you will know you are done
`make check-10` restarts the Collector, Prometheus and Grafana **with YOUR files**, starts YOUR instrumented API, generates traffic (including 404s and 422s), and queries: Prometheus (the service metrics and an “up” target), Tempo (traces with PostgreSQL spans),
Loki (logs with `trace_id`), Grafana (provisioned datasources and dashboard, panels with data) and `promtool` (your rules).

For the AI traces, it runs **your agent and your RAG** with a scripted model that reports known token counts, one search and one call to a tool that doesn't exist. It captures the spans in memory (to tell you *your instrumentation* is wrong) and again over OTLP through *your* Collector (to tell you the *pipeline* is wrong),
and then reads the trace back from the Tempo HTTP API: one root span, model-call spans whose token counts match what the model reported, tool spans with their attributes, a retrieval span under its tool, errors visible, and no user text in any attribute.

For the SLO, it feeds `promtool test rules` with **synthetic series it generates** (20 % failures, 20 % slow answers, a healthy week, an 8 % leak, a spike that recovered, a blip that just started; plus noise from another route and another service) and asserts what your SLI computes and which alerts fire and which don't.
Finally it checks that your SLI expression returns a sensible value on the live metrics of your API. It does not check that you chose a *good* SLO: that is the comprehension questions.

## Your turn (walkthrough mode)
On your own, add a provisioned Grafana panel in `observability/grafana/` showing `1 - sli:risk_bad_ratio:rate5m` as the five-minute good-request share.
Run `make up-obs`, generate risk requests with `curl http://localhost:8000/assets/PLT01-P101/risk` (use a token if authentication is enabled), and allow a scrape interval.
Open the dashboard at http://localhost:3030; compare the panel with the same expression in Prometheus, then restart Grafana and verify the panel remains.
Then re-run `make check-10`.

## Comprehension questions
1. Why use a Collector instead of having the API talk directly to Prometheus/Tempo/Loki?
2. Your p95 is 120 ms and the average 40 ms. What does the difference tell you? Which would you use in an SLO?
3. Why is `/health` excluded from the traces? What would happen to volume and costs otherwise?
4. An alert fires every night at 3:00 and nobody does anything. What is wrong with the design of that alert?
5. How would you find the trace of the request a user reported as slow, if they only gave you the approximate time and their name?
6. Which sensitive data could end up in a log or in a span attribute? How do you prevent it?
7. Open the trace of an agent run where the answer was wrong. In which order do you look at the spans, and what in each of them tells you whether the *retrieval*, the *model* or a *tool* is to blame?
8. Why is the token count an attribute of the model-call span and not of the root span? What would you do with it in a dashboard, and what would you alert on?
9. The GenAI conventions are still “development”. What can go wrong if you build dashboards and alerts on those attribute names, and how do you limit the damage?
10. Your SLO is 99 % over 30 days. How many minutes of total outage is that budget? Why is 99.99 % not simply “better”?
11. Why does the fast-burn alert need the 5-minute window if the 1-hour window already says the budget is burning? And why does the slow-burn alert need the 6-hour window?
12. The error budget is 80 % spent on day 10. What do you do on Monday? Who decides, and what changes in how the team works?
13. Your SLI counts 404s as good. A bug makes `/risk` answer 404 for every valid asset. What happens to the SLI and the alerts? What would you add to catch it?

## Hints
<details><summary>Which OpenTelemetry packages you need</summary>

`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` and the `opentelemetry-instrumentation-{fastapi,psycopg,redis}` ones. For logs: `LoggerProvider` + `BatchLogRecordProcessor(OTLPLogExporter())` + a `LoggingHandler` on the root logger (so every log carries `trace_id`).
</details>
<details><summary>Collector: exporter names</summary>

`otlp/tempo` (with `tls: {insecure: true}`), `otlphttp/loki` (`endpoint: http://loki:3100/otlp`) and `prometheus` (`endpoint: 0.0.0.0:8889`). Each pipeline (`traces`, `metrics`, `logs`) joins a receiver, processors and exporters.
</details>
<details><summary>The service_name label doesn't show up in Prometheus</summary>

The Collector's Prometheus exporter needs `resource_to_telemetry_conversion: {enabled: true}` to turn resource attributes (like `service.name`) into labels.
</details>
<details><summary>My panel is empty</summary>

Copy the expression into the Prometheus UI (:9090). In Grafana `$__rate_interval` is a variable; if your panel is a division, check that the denominator isn't zero. And check the exact metric name and its labels (`http_status_code`, `http_target`).
</details>

<details><summary>Spans: where to start (generic example)</summary>

`tracer = trace.get_tracer("plant.genai")` at module level; then `with tracer.start_as_current_span("name", kind=…) as span: span.set_attribute("key", value)`. A span started *inside* another one's `with` block becomes its child automatically (context lives in a `contextvar`).
Two things to decide: where the *root* is opened (every `invoke` must open and close it, whatever path the graph takes), and what a node does when something goes wrong (`span.set_status(Status(StatusCode.ERROR))`).
The span name is sometimes only known after the call (the model name): `span.update_name(...)`.
</details>
<details><summary>My spans are not in one trace</summary>

A child needs its parent to be the *current* span when it starts. If you start a span in one place and end it in another (or in a callback), or the work runs in another thread, the context is not there. Print `span.get_span_context().trace_id` in two steps and compare them.
</details>
<details><summary>The spans exist in memory but never reach Tempo</summary>

The check exports to `localhost:4317`. Does your Collector's `traces` pipeline have a receiver and the `otlp/tempo` exporter? Look at the Collector log. Also: a batch processor sends on a timer; the check shuts the provider down at the end to flush.
</details>
<details><summary>Burn rate, in numbers</summary>

Error budget = 1 − SLO = 0.01. Burn rate = (bad ratio in the window) / budget. So “burn rate > 14.4” is “bad ratio > 14.4 × 0.01 = 14.4 %”. Check: at burn rate 14.4 a 30-day budget (720 h) lasts 720 / 14.4 = 50 h, i.e. you spend 1/50 = 2 % of it every hour.
</details>
<details><summary>Writing the SLI</summary>

Think in *counts*: good = requests with `le="500"` that are not 5xx; total = all requests. A histogram's `_bucket{le="500"}` is **cumulative** (everything ≤ 500 ms), so it needs no subtraction. Use `rate(...[window])` on both sides, `sum` away the labels you don't want, and decide what 0 / 0 means. Test it on paper with 3 requests first.
</details>
<details><summary>A promtool test, generic example</summary>

```yaml
rule_files: [rules.yml]
evaluation_interval: 1m
tests:
  - interval: 1m
    input_series:
      - {series: 'jobs_total{result="ok"}',   values: '0+9x120'}   # +9 per minute, 121 samples
      - {series: 'jobs_total{result="fail"}', values: '0+1x120'}
    promql_expr_test:
      - expr: sum(rate(jobs_total{result="fail"}[10m])) / sum(rate(jobs_total[10m]))
        eval_time: 1h
        exp_samples: [{labels: '{}', value: 0.1}]
```
`values: '0+9x120'` means “start at 0, add 9, 120 times”. `promtool test rules file.yml` runs it. Alerts can be asserted with `alert_rule_test`, or with a query on the `ALERTS` series.
</details>
<details><summary>My alert never fires in the test</summary>

`for:` makes it *pending* first, and promtool evaluates at the interval you give it: is the evaluation time late enough? Is the recording rule the alert uses evaluated before it (same group, in order)? Query the recording rule at that time to see what it returned: it is often `NaN` from a 0 / 0.
</details>

## Common traps
- A hand-built dashboard nobody versions, which disappears when Grafana is recreated.
- Alerts without `for` (noise) or without a `summary` (nobody knows what to do).
- Measuring only the average latency.
- **High-cardinality** labels (a user id as a metric label): it blows up Prometheus.
- Instrumenting `/health` (millions of useless traces).
- Putting the prompt, the question or the tool arguments in span attributes “to debug”: you just copied user data into a system many people can read.
- Estimating tokens by counting characters: the bill and the context limit use the model's own count.
- An SLO nobody agreed on, or an SLI that counts what is easy to measure instead of what the user feels (a fast 500 is not “good”; neither is a correct answer after 30 s).
- Alerting on the SLI with a fixed threshold and a long `for:`: it misses slow leaks and pages for blips. And a burn-rate alert with a **single window** pages for an incident that ended an hour ago.
- Counting client errors (4xx) as failures: a bot sending garbage can burn your budget.

## On a real job
What you would add next:
- **Trace sampling** (you don't keep 100 %: tail-based sampling keeps all the errors and the slow ones) and **exemplars** that link a metric to a trace.
- **GenAI metrics** next to the spans (`gen_ai.client.token.usage`, `gen_ai.client.operation.duration`) to alert on cost and latency per model, and **opt-in content capture** (prompts and answers) only in a protected environment, with redaction and a short retention.
- **Evaluation scores attached to traces** (LLM-as-judge, user feedback), so you can filter “traces where the answer was unfaithful”; and dashboards by **prompt version** and **model version**. LLM-specific tools (Langfuse, Phoenix) build on the same OpenTelemetry data.
- **SLO tooling and process**: generate the rules from a declarative SLO (Sloth, Pyrra), a dashboard with the budget remaining, an **error budget policy** that says what the team does when it is spent, and SLOs for the data side too (freshness of the gold table, share of successful pipeline runs).
- **Alertmanager** with routing per team, grouping, silences and inhibition (a down database should not also page for every service that uses it), and runbooks linked from every alert.
- Retention and storage cost of metrics, logs and traces. On AWS, the equivalent includes CloudWatch.
**Courses:** the *Grafana Labs Learning Hub* (there is no free course that ties OTel + Prometheus + Grafana together; that is why this module does) and the free chapters of Google's *Site Reliability Engineering Workbook* on SLOs and alerting on SLOs (the burn-rate windows used here come from there).

→ Next: [`11_cloud.md`](11_cloud.md)
