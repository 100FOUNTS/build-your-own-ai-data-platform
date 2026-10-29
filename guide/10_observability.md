# Module 10 — Observability: OpenTelemetry, Prometheus, Grafana, Loki and Tempo

⏱ 5–6 h · Services: `make up-db up-stream up-obs` · Check: `make check-10`
**Tools:** OpenTelemetry, Prometheus, Grafana (plus **Loki** and **Tempo**, the open alternatives for logs and traces).

## What you will build
The ability to **understand what is happening inside your system from the outside**:
1. **Instrumentation** of the API with OpenTelemetry: traces (including the database and Redis), HTTP metrics and logs with `trace_id`.
2. The **OpenTelemetry Collector** config that sends each signal to its destination: traces → Tempo, metrics → Prometheus, logs → Loki.
3. **Prometheus** with alert rules; **Grafana** with datasources and a dashboard **as code** (provisioned from files).

## Why this module exists
A distributed system fails in ways you can't see by reading the code: a slow request goes through the API, the cache, the database and Kafka. Without observability,
“it's slow” is an opinion; with it, it is a trace that says *“80% of the time went into this SQL query”*.

The three pillars answer different questions:
| Pillar | Answers | Tool here |
|---|---|---|
| **Metrics** | How much? Is it getting worse? (cheap aggregates, alerts) | Prometheus |
| **Traces** | Where did the time go in *this* request? | Tempo |
| **Logs** | What exactly happened? | Loki |

The power is in **correlating** them (from the errors panel to the failed trace and to its logs with the same `trace_id`). **OpenTelemetry** is the standard for instrumenting **once** and sending anywhere.

## Concepts
- **Span / trace**: a span is an operation with a duration; a trace is the tree of spans of one request. Context propagation ties services together.
- **RED metrics**: *Rate* (requests/s), *Errors* (% of failures), *Duration* (latency, percentiles). They work for almost any service.
- **Histogram and percentile**: the average lies; p95 tells you what the slowest 5% suffer.
- **Collector**: a process that receives OTLP, **processes** it (batches, memory limits) and **exports** it. It decouples the application from the destinations.
- **SLI / SLO / alert**: a metric (SLI), a target (SLO: “p95 < 300 ms”) and an alert that fires when it is missed. An alert without `for:` wakes someone up for a one-second spike.
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

## Your task
1. Start `make up-obs` (with the starting config that only prints) and look at http://localhost:3030 (Grafana), :9090 (Prometheus). What do you see empty, and why?
2. Write `telemetry.py` and turn it on in the API. First check in the Collector console (`docker compose --profile obs logs -f otel-collector`) that signals **arrive**.
3. Configure the Collector (three pipelines) and Prometheus. Check in Prometheus: `http_server_duration_milliseconds_count` with the label `service_name="plant-api"`.
4. Generate traffic (a `curl` loop) and look for *your* trace in Tempo (Grafana → Explore → Tempo). Do you see the PostgreSQL span? And that request's logs in Loki, with the same `trace_id`?
5. Write two useful alerts and validate them with `promtool`. Ask yourself for each one: who receives it and what do they do in the first 5 minutes?
6. Build the dashboard in Grafana, **export it to JSON** and let it be provisioned from the file. Restart Grafana: it must still be there.

## How you will know you are done
`make check-10` restarts the Collector, Prometheus and Grafana **with YOUR files**, starts YOUR instrumented API, generates traffic (including 404s and 422s), and queries: Prometheus (the service metrics and an “up” target), Tempo (traces with PostgreSQL spans),
Loki (logs with `trace_id`), Grafana (provisioned datasources and dashboard, panels with data) and `promtool` (your rules).

## Comprehension questions
1. Why use a Collector instead of having the API talk directly to Prometheus/Tempo/Loki?
2. Your p95 is 120 ms and the average 40 ms. What does the difference tell you? Which would you use in an SLO?
3. Why is `/health` excluded from the traces? What would happen to volume and costs otherwise?
4. An alert fires every night at 3:00 and nobody does anything. What is wrong with the design of that alert?
5. How would you find the trace of the request a user reported as slow, if they only gave you the approximate time and their name?
6. Which sensitive data could end up in a log or in a span attribute? How do you prevent it?

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

## Common traps
- A hand-built dashboard nobody versions, which disappears when Grafana is recreated.
- Alerts without `for` (noise) or without a `summary` (nobody knows what to do).
- Measuring only the average latency.
- **High-cardinality** labels (a user id as a metric label): it blows up Prometheus.
- Instrumenting `/health` (millions of useless traces).

## On a real job
Trace sampling (you don't keep 100%), exemplars that link a metric to a trace, SLOs with *burn rate*, alerts with Alertmanager and routing per team, retention and storage cost. On AWS, the equivalent includes CloudWatch.
**Courses:** the *Grafana Labs Learning Hub* (there is no free course that ties OTel + Prometheus + Grafana together; that is why this module does).

→ Next: [`11_cloud.md`](11_cloud.md)
