"""Module 10 — Observability: OpenTelemetry (traces, metrics, logs) → Collector → Prometheus / Tempo / Loki → Grafana."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time

import httpx
import psycopg
import pytest
import yaml

from conftest import ROOT, free_port, kafka_topics, need, ok, py, seeded_db, serve, sh, tail, unique, wait_until

PROM, TEMPO, LOKI, GRAFANA = "http://localhost:9090", "http://localhost:3200", "http://localhost:3100", "http://localhost:3030"
OBS = ROOT / "observability"
PROMTOOL_IMAGE = "prom/prometheus:v3.13.4"  # same version as compose.yaml


def test_prometheus_alerts_are_valid():
    f = OBS / "rules" / "alerts.yml"
    ok(f.exists(), "Missing observability/rules/alerts.yml (Prometheus alert rules).")
    doc = yaml.safe_load(f.read_text())
    rules = [r for g in doc["groups"] for r in g["rules"] if "alert" in r]
    ok(len(rules) >= 2, f"There are {len(rules)} alert rules; at least 2 are expected (e.g. 5xx error rate and p95 latency).")
    for r in rules:
        ok(r.get("for"), f"The alert {r['alert']} has no `for:`. Without a waiting window, a one-second spike wakes someone up in the middle of the night.")
        ok(r.get("labels", {}).get("severity"), f"The alert {r['alert']} needs `labels.severity` (page/ticket): it decides who gets notified and how.")
        ok(len(r.get("annotations", {}).get("summary", "")) >= 15, f"The alert {r['alert']} needs an `annotations.summary` that whoever receives it can understand.")
    p = sh(["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{OBS}:/obs:ro", PROMTOOL_IMAGE, "check", "rules", "/obs/rules/alerts.yml"])
    ok(p.returncode == 0, "`promtool check rules` rejects your rules:\n" + tail(p))


@pytest.fixture(scope="module")
def obs_stack():
    """Restarts the Collector, Prometheus and Grafana with YOUR files (Tempo and Loki keep the platform's config)."""
    need("otlp", "prometheus", "loki", "tempo", "grafana")
    for f in ("otel-collector.yaml", "prometheus.yml"):
        ok((OBS / f).exists(), f"Missing observability/{f}.")
    ok(list((OBS / "grafana" / "provisioning" / "datasources").glob("*.y*ml")) if (OBS / "grafana/provisioning/datasources").exists() else False, "Missing the Grafana datasource provisioning (observability/grafana/provisioning/datasources/*.yml).")
    r = sh(["docker", "compose", "--profile", "obs", "up", "-d", "--force-recreate", "--wait", "otel-collector", "prometheus", "grafana"], timeout=240)
    ok(r.returncode == 0, "Could not restart the collector/Prometheus/Grafana with YOUR configs (invalid YAML?):\n" + tail(r))
    time.sleep(4)
    st = sh(["docker", "compose", "--profile", "obs", "ps", "--format", "{{.Service}} {{.State}}"]).stdout
    ok("otel-collector running" in st, "The OpenTelemetry Collector didn't stay running with your config. Look at `docker compose --profile obs logs otel-collector`.")


@pytest.fixture(scope="module")
def traffic(obs_stack):
    need("postgres", "valkey", "redpanda")
    topic = unique("chk.obs")
    kafka_topics(topic)
    with seeded_db() as url:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("insert into risk_scores (asset_id, feature_date, risk_score, model_version) values ('PLT01-P101','2026-09-30',0.35,'v1')")
        port = free_port()
        env = {"DATABASE_URL": url, "AUTH_DISABLED": "1", "RATE_LIMIT_PER_MIN": "100000", "KAFKA_TOPIC_ALERTS": topic, "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317", "OTEL_SERVICE_NAME": "plant-api"}
        with serve(["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(port), "--log-level", "warning"], env, f"http://127.0.0.1:{port}/health"):
            c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
            t0 = time.time()
            for _ in range(15):
                c.get("/assets")
            for _ in range(10):
                c.get("/assets/PLT01-P101/risk")
            for _ in range(5):
                c.get("/assets/NO-SUCH-ASSET")
            for _ in range(3):
                c.post("/work-orders", json={"asset_id": "PLT01-P101", "wo_type": "FOO"})
            yield {"t0": t0, "url": f"http://127.0.0.1:{port}"}
            time.sleep(7)  # let the last batch of telemetry go out


def test_metrics_reach_prometheus(traffic):
    def n():
        r = httpx.get(PROM + "/api/v1/query", params={"query": 'sum({__name__=~"http_server.*_count", service_name="plant-api"})'}, timeout=10).json()["data"]["result"]
        return float(r[0]["value"][1]) if r else 0

    got = wait_until(lambda: n() >= 20, 60)
    ok(got, "Prometheus has no HTTP metrics for `service_name=\"plant-api\"` after 60 s of traffic.\nChain to review: the API exports over OTLP → the Collector exposes them on :8889 (`prometheus` exporter with resource_to_telemetry_conversion) → prometheus.yml scrapes that target.")
    def collector_up():  # a target's health is updated a moment after its first samples land
        targets = httpx.get(PROM + "/api/v1/targets", timeout=10).json()["data"]["activeTargets"]
        return any(t["health"] == "up" and "8889" in t["scrapeUrl"] for t in targets)

    ok(wait_until(collector_up, 30), "The Collector target (otel-collector:8889) is not “up” in Prometheus.")


def test_traces_reach_tempo_with_database_spans(traffic):
    def search():
        r = httpx.get(TEMPO + "/api/search", params={"tags": "service.name=plant-api", "limit": 30}, timeout=10)
        return r.json().get("traces", []) if r.status_code == 200 else []

    traces = wait_until(search, 60)
    ok(traces, "Tempo has no traces for the `plant-api` service. Do you export traces over OTLP, and does the Collector forward them to tempo:4317?")
    found_db = False
    for t in traces[:15]:
        body = httpx.get(f"{TEMPO}/api/traces/{t['traceID']}", timeout=10).text
        if "postgresql" in body or "psycopg" in body:
            found_db = True
            break
    ok(found_db, "The API traces contain no PostgreSQL spans. Instrument psycopg (and redis): without that a slow request is a black box.")


def test_logs_reach_loki_correlated_with_traces(traffic):
    def logs():
        now = int(time.time() * 1e9)
        r = httpx.get(LOKI + "/loki/api/v1/query_range", params={"query": '{service_name="plant-api"}', "limit": 100, "start": int(traffic["t0"] * 1e9) - 10**9, "end": now}, timeout=10)
        res = r.json().get("data", {}).get("result", []) if r.status_code == 200 else []
        return res

    res = wait_until(logs, 60)
    ok(res, "Loki has no logs for `service_name=\"plant-api\"`. Export the logs over OTLP (LoggingHandler) and forward them with the otlphttp exporter to Loki's /otlp endpoint.")
    text = json.dumps(res)
    ok(re.search(r"request GET /assets", text), "The API logs should include one line per request (method, path, status).")
    ok(re.search(r"trace_?id\W+[0-9a-f]{32}|[\"']trace_?id[\"']\s*:\s*[\"'][0-9a-f]{32}", text, re.I), "The logs don't carry `trace_id`. It is what lets you jump from a log to its trace in Grafana: with the OpenTelemetry LoggingHandler it comes for free.")


def test_grafana_dashboard_provisioned_with_data(traffic):
    s = httpx.Client(auth=("admin", "admin"))
    ds = s.get(GRAFANA + "/api/datasources", timeout=10).json()
    types = {d["type"] for d in ds}
    ok({"prometheus", "loki", "tempo"} <= types, f"Grafana must have Prometheus, Loki and Tempo datasources provisioned from files (it has {sorted(types)}). Configuration as code: nobody clicks around in production.")
    found = wait_until(lambda: s.get(GRAFANA + "/api/search", params={"type": "dash-db", "tag": "plant"}, timeout=10).json(), 30)
    ok(found, "There is no dashboard tagged `plant` provisioned from observability/grafana/dashboards/ (JSON file + provisioning/dashboards/*.yml).")
    dash = s.get(GRAFANA + f"/api/dashboards/uid/{found[0]['uid']}", timeout=10).json()["dashboard"]
    panels = [p for p in dash.get("panels", []) for p in ([p] + p.get("panels", []))]
    ok(len(panels) >= 6, f"The dashboard has {len(panels)} panels; at least 6 are expected (rate, latency, errors, by route/status, logs, traces).")
    kinds = {(p.get("datasource") or {}).get("type") for p in panels}
    ok({"prometheus", "loki", "tempo"} <= kinds, f"A good dashboard brings the three pillars together: metrics, logs and traces (yours use {sorted(k for k in kinds if k)}).")
    withdata = 0
    for p in panels:
        if (p.get("datasource") or {}).get("type") != "prometheus":
            continue
        for t in p.get("targets", []):
            q = re.sub(r"\$__(rate_)?interval", "1m", t.get("expr", ""))
            r = httpx.get(PROM + "/api/v1/query", params={"query": q}, timeout=10).json()
            ok(r["status"] == "success", f"The query of the panel “{p.get('title')}” is invalid: {r.get('error')}")
            withdata += bool(r["data"]["result"])
    ok(withdata >= 3, f"Only {withdata} Prometheus panels return data after generating traffic; ≥ 3 expected. Check metric names and labels (service_name, http_status_code…?).")


# =====================================================================================================================
# Trace the AI: your module 6 agent and RAG must emit OpenTelemetry spans that follow the GenAI semantic conventions.
# The check runs YOUR agent with a scripted model (deterministic, like module 6), configures the tracing SDK itself,
# and compares what the spans say with what it knows it fed into the agent.
# =====================================================================================================================
AGENT_NAME, MODEL, PROVIDER, TOP_K = "plant-agent", "scripted-llm-7b", "ollama", 3
TOKENS = [(211, 37), (356, 52)]  # (input, output) tokens the scripted model reports for its 1st and 2nd call, in every run

AGENT_DRIVER = r'''
import json, sys
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

service, export, canary, model, provider_name, tokens = sys.argv[1], sys.argv[2] == "export", sys.argv[3], sys.argv[4], sys.argv[5], json.loads(sys.argv[6])
provider = TracerProvider(resource=Resource.create({"service.name": service}))
mem = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(mem))
if export:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)))
trace.set_tracer_provider(provider)

from genai import rag                      # YOUR code, imported AFTER the tracing is configured
from genai.agent import build_agent

seen = []


def search_manuals(query, k=3):
    res = rag.retrieve(query, k=k)
    seen.extend(r["doc_id"] for r in res)
    return res


def create_work_order(asset_id, description):
    return {"wo_id": "WO-1"}


class Llm:
    """Scripted model. After every call it publishes what it consumed in `usage`, like a real client would."""
    def __init__(self, replies):
        self.replies, self.calls, self.usage = replies, 0, None

    def __call__(self, messages):
        reply = self.replies[self.calls]
        self.usage = {"provider": provider_name, "model": model, "input_tokens": tokens[self.calls][0], "output_tokens": tokens[self.calls][1]}
        self.calls += 1
        return reply() if callable(reply) else reply


def norm(sp):
    c = sp.get_span_context()
    return {"trace_id": format(c.trace_id, "032x"), "span_id": format(c.span_id, "016x"), "parent_id": format(sp.parent.span_id, "016x") if sp.parent else None,
            "name": sp.name, "start": sp.start_time, "attrs": dict(sp.attributes or {}), "status": sp.status.status_code.name,
            "events": [{"name": e.name, "attrs": dict(e.attributes or {})} for e in sp.events]}


def run(replies, question):
    agent = build_agent(Llm(replies), {"search_manuals": search_manuals, "create_work_order": create_work_order})
    out = agent.invoke({"question": question})
    spans = [norm(s) for s in mem.get_finished_spans()]
    mem.clear()
    return {"spans": spans, "stopped_reason": out.get("stopped_reason"), "answer": out.get("answer")}


tool = lambda name, **a: json.dumps({"action": "tool", "name": name, "args": a})
final_ok = lambda: json.dumps({"action": "final", "answer": f"The interval is in the lubrication manual [{seen[0]}]."})
happy = run([tool("search_manuals", query=f"bearing grease interval {canary}"), final_ok], f"What is the grease interval for the pump bearings? (ticket {canary})")
failing = run([tool("delete_everything", note=canary), json.dumps({"action": "final", "answer": "I can't find that information in the manuals."})], "Please do something unusual")
provider.shutdown()
print("RESULT:" + json.dumps({"happy": happy, "failing": failing}, default=str))
'''


def _run_agent_driver(tmp_path, db_url: str, export: bool) -> dict:
    ok((ROOT / "genai" / "agent.py").exists() and (ROOT / "genai" / "rag.py").exists(), "Missing genai/agent.py or genai/rag.py (module 6): this check instruments THAT agent.")
    script = tmp_path / "agent_driver.py"
    script.write_text(AGENT_DRIVER)
    canary, service = unique("CANARY"), unique("chk-agent")
    r = py([str(script), service, "export" if export else "local", canary, MODEL, PROVIDER, json.dumps(TOKENS)], env={"DATABASE_URL": db_url, "RAG_SCHEMA": "rag"}, timeout=300)
    ok(r.returncode == 0, "Your agent crashed while running with tracing configured (a scripted model, one RAG search and one unknown tool call):\n" + tail(r, n=18))
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")]
    ok(line, "The agent run printed no result; did something call sys.exit or swallow stdout?\n" + tail(r))
    res = json.loads(line[-1][len("RESULT:"):])
    res["canary"] = canary
    return res


@pytest.fixture(scope="module")
def agent_db():
    """Temporary database (YOUR migrations) + the RAG index built by YOUR `python -m genai.rag ingest` (module 6)."""
    need("postgres", "ollama")
    ok((ROOT / "genai" / "rag.py").exists(), "Missing genai/rag.py (module 6): the agent searches the manuals through it.")
    with seeded_db() as url:
        r = py(["-m", "genai.rag", "ingest"], env={"DATABASE_URL": url, "RAG_SCHEMA": "rag"}, timeout=600)
        ok(r.returncode == 0, "`python -m genai.rag ingest` failed (is Ollama running with the embedding model?):\n" + tail(r))
        yield url


@pytest.fixture(scope="module")
def agent_local(agent_db, tmp_path_factory):
    """Run 1: spans captured in memory, no Collector involved. If this is wrong, the problem is in your instrumentation."""
    return _run_agent_driver(tmp_path_factory.mktemp("agent_local"), agent_db, export=False)


@pytest.fixture(scope="module")
def agent_exported(agent_db, obs_stack, tmp_path_factory):
    """Run 2: the same agent run, exported over OTLP to YOUR Collector, which forwards it to Tempo."""
    return _run_agent_driver(tmp_path_factory.mktemp("agent_export"), agent_db, export=True)


def _val(v: dict):
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return int(v[k]) if k == "intValue" else v[k]
    return json.dumps(v)


def _hexid(x: str | None) -> str | None:
    if not x:
        return None
    return x.lower() if re.fullmatch(r"[0-9a-fA-F]{16}|[0-9a-fA-F]{32}", x) else base64.b64decode(x).hex()


def _tempo_spans(trace_id: str) -> list[dict]:
    r = httpx.get(f"{TEMPO}/api/traces/{trace_id}", timeout=10)
    if r.status_code != 200:
        return []
    doc = r.json()
    batches = doc.get("batches") or doc.get("trace", {}).get("resourceSpans") or doc.get("resourceSpans") or []
    out = []
    for b in batches:
        for ss in b.get("scopeSpans") or b.get("instrumentationLibrarySpans") or []:
            for sp in ss.get("spans", []):
                code = str((sp.get("status") or {}).get("code", "UNSET")).upper()
                out.append({
                    "trace_id": _hexid(sp.get("traceId")), "span_id": _hexid(sp["spanId"]), "parent_id": _hexid(sp.get("parentSpanId")),
                    "name": sp["name"], "start": int(sp.get("startTimeUnixNano", 0)),
                    "attrs": {a["key"]: _val(a["value"]) for a in sp.get("attributes", [])},
                    "status": "ERROR" if "ERROR" in code or code == "2" else "UNSET",
                    "events": [{"name": e.get("name"), "attrs": {a["key"]: _val(a["value"]) for a in e.get("attributes", [])}} for e in sp.get("events", [])],
                })
    return out


def _op(spans: list[dict], op: str) -> list[dict]:
    return sorted((s for s in spans if s["attrs"].get("gen_ai.operation.name") == op), key=lambda s: s["start"])


def _ancestors(span: dict, by_id: dict) -> list[dict]:
    chain, cur = [], span
    while cur["parent_id"] and cur["parent_id"] in by_id and len(chain) < 50:
        cur = by_id[cur["parent_id"]]
        chain.append(cur)
    return chain


def verify_trace(spans: list[dict], where: str, canary: str, scenario: str) -> None:
    """The contract of guide/10 'Trace the AI', checked on a list of normalized spans (from memory or from Tempo)."""
    ok(spans, f"No spans {where}.")
    by_id = {s["span_id"]: s for s in spans}
    roots = [s for s in spans if not s["parent_id"]]
    ok(len(roots) == 1, f"An agent run must be ONE trace with exactly one root span; {where} there are {len(roots)} spans without a parent ({[r['name'] for r in roots]}).\nIf the LLM, tool and retrieval spans are not children of one `invoke_agent` span, you can't see a run as a whole.")
    root = roots[0]
    ok(root["attrs"].get("gen_ai.operation.name") == "invoke_agent", f"The root span ({root['name']!r}) needs `gen_ai.operation.name = \"invoke_agent\"`: it represents one agent run.")
    ok(root["attrs"].get("gen_ai.agent.name") == AGENT_NAME and root["name"] == f"invoke_agent {AGENT_NAME}", f"The root span must be named `invoke_agent {AGENT_NAME}` and carry `gen_ai.agent.name = \"{AGENT_NAME}\"` (it has name={root['name']!r}, agent.name={root['attrs'].get('gen_ai.agent.name')!r}).")
    for s in spans:
        if s is not root:
            chain = _ancestors(s, by_id)
            ok(chain and chain[-1] is root, f"The span {s['name']!r} is not a descendant of the root span: its context was lost between the steps of the agent. Spans must be started *inside* their parent's context.")

    # --- model calls
    chats = _op(spans, "chat")
    ok(len(chats) == len(TOKENS), f"The scripted model was called {len(TOKENS)} times, but {where} there are {len(chats)} spans with `gen_ai.operation.name = \"chat\"`. Every model call needs its own span (retries included): that is where latency and cost live.")
    for i, (sp, (tin, tout)) in enumerate(zip(chats, TOKENS, strict=True), start=1):
        a = sp["attrs"]
        ok(a.get("gen_ai.request.model") == MODEL and sp["name"] == f"chat {MODEL}", f"Model-call span #{i}: expected name `chat {MODEL}` and `gen_ai.request.model = {MODEL!r}`; got name={sp['name']!r}, model={a.get('gen_ai.request.model')!r}.")
        ok(a.get("gen_ai.provider.name") == PROVIDER, f"Model-call span #{i}: `gen_ai.provider.name` is a required attribute of these spans (expected {PROVIDER!r}, got {a.get('gen_ai.provider.name')!r}).")
        got_in, got_out = a.get("gen_ai.usage.input_tokens"), a.get("gen_ai.usage.output_tokens")
        ok(isinstance(got_in, int) and isinstance(got_out, int) and got_in > 0 and got_out > 0, f"Model-call span #{i} carries no token counts (input={got_in!r}, output={got_out!r}).\nTokens are the cost and the latency of an agent: without `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` you can't tell a cheap turn from an expensive one. The model client reports them: take them from there.")
        ok((got_in, got_out) == (tin, tout), f"Model-call span #{i} reports {got_in} input / {got_out} output tokens but the model reported {tin} / {tout}. Use what the model says it consumed (`llm.usage`); don't estimate.")

    # --- tools and retrieval
    tools = _op(spans, "execute_tool")
    if scenario == "happy":
        ok(len(tools) == 1, f"The agent ran 1 tool but {where} there are {len(tools)} `execute_tool` spans.")
        t = tools[0]
        ok(t["attrs"].get("gen_ai.tool.name") == "search_manuals" and t["name"] == "execute_tool search_manuals", f"Tool span: expected name `execute_tool search_manuals` and `gen_ai.tool.name = \"search_manuals\"`; got name={t['name']!r}, tool.name={t['attrs'].get('gen_ai.tool.name')!r}.")
        ok(t["status"] != "ERROR", "A tool call that succeeded must not have an error status.")
        rets = _op(spans, "retrieval")
        ok(rets, f"No span with `gen_ai.operation.name = \"retrieval\"` {where}. The RAG search (`retrieve` in genai/rag.py) decides what the model gets to read: when an answer is wrong, it is the first span you open.")
        ok(rets[0]["attrs"].get("gen_ai.retrieval.top_k") == TOP_K, f"The retrieval span should record `gen_ai.retrieval.top_k` = the k that was asked for ({TOP_K}); got {rets[0]['attrs'].get('gen_ai.retrieval.top_k')!r}.")
        ok(t in _ancestors(rets[0], by_id), "The retrieval span must be a child of the `execute_tool search_manuals` span that triggered it: the search runs inside the tool call, in the same trace context.")
    else:
        bad = [s for s in tools if s["attrs"].get("gen_ai.tool.name") == "delete_everything"]
        ok(bad, f"The model asked for a tool that doesn't exist and {where} there is no `execute_tool` span for it. The attempt must be visible: that is exactly what you debug an agent for.")
        ok(bad[0]["status"] == "ERROR" and bad[0]["attrs"].get("error.type"), f"The span of the failed tool call must have status ERROR and an `error.type` attribute (status={bad[0]['status']}, error.type={bad[0]['attrs'].get('error.type')!r}). Otherwise failures look like successes in the trace view.")

    # --- privacy
    dump = json.dumps([{"attrs": s["attrs"], "events": s["events"]} for s in spans], default=str)
    ok(canary not in dump, "Text that came from the user (the question, the search query or the tool arguments) is being recorded in span attributes or events.\nPrompts and tool arguments can hold personal or confidential data, and spans are stored and shared widely: record names, sizes and counts, not content (the semantic conventions make content opt-in for that reason).")


def test_agent_emits_genai_spans(agent_local):
    verify_trace(agent_local["happy"]["spans"], "in the spans your agent emitted", agent_local["canary"], "happy")


def test_failed_tool_calls_are_visible_in_the_trace(agent_local):
    verify_trace(agent_local["failing"]["spans"], "in the spans your agent emitted", agent_local["canary"], "failing")


def test_agent_trace_reaches_tempo(agent_exported):
    happy = agent_exported["happy"]["spans"]
    ok(happy, "The agent run produced no spans in memory: see the previous tests.")
    tid, n = happy[0]["trace_id"], len(happy)
    got: list[dict] = []

    def fetch():
        nonlocal got
        got = _tempo_spans(tid)
        return len(got) >= n

    wait_until(fetch, 60)
    ok(got, f"Tempo has no trace {tid} for the agent run. The spans were exported to localhost:4317: does your Collector's traces pipeline forward them to tempo:4317?")
    ok(len(got) == n, f"Tempo only has {len(got)} of the {n} spans of the agent trace after 60 s. Are all spans finished and exported?")
    verify_trace(got, "in the trace stored in Tempo", agent_exported["canary"], "happy")


# =====================================================================================================================
# SLOs and error budgets for the API: recording rules for an SLI + multi-window, multi-burn-rate alerts.
# promtool feeds SYNTHETIC series (generated here) through YOUR rules and asserts what fires and what does not.
# =====================================================================================================================
SLO_FILE = OBS / "rules" / "slo.yml"
SLI_WINDOWS = ("5m", "30m", "1h", "6h")
SLO_ALERTS = {"RiskSLOFastBurn": "page", "RiskSLOSlowBurn": "ticket"}
RISK_TARGET = "/assets/{asset_id}/risk"  # what the FastAPI instrumentation really exports in http_target: the route template
METRIC = "http_server_duration_milliseconds"


def _expand(phases: list[tuple], idx: int) -> list[int]:
    """phases = [(minutes, fast_ok, slow_ok, client_4xx, server_5xx)], counts PER MINUTE → per-minute list of column `idx`."""
    return [ph[idx] for ph in phases for _ in range(ph[0])]


def _cum(per_min: list[int]) -> str:
    out, v = [0], 0
    for x in per_min:
        v += x
        out.append(v)
    return " ".join(map(str, out))


def _slo_series(phases: list[tuple]) -> list[dict]:
    """Synthetic histogram series for /risk (+ noise that a correct SLI must ignore)."""
    fast, slow, c4, e5 = (_expand(phases, i) for i in (1, 2, 3, 4))
    minutes = sum(ph[0] for ph in phases)
    series = []

    def add(service, target, status, count, le500):
        base = f'service_name="{service}", http_target="{target}", http_status_code="{status}"'
        series.append({"series": f"{METRIC}_count{{{base}}}", "values": _cum(count)})
        series.append({"series": f'{METRIC}_bucket{{{base}, le="500.0"}}', "values": _cum(le500)})
        series.append({"series": f'{METRIC}_bucket{{{base}, le="+Inf"}}', "values": _cum(count)})

    add("plant-api", RISK_TARGET, 200, [f + s for f, s in zip(fast, slow, strict=True)], fast)
    add("plant-api", RISK_TARGET, 404, c4, c4)
    add("plant-api", RISK_TARGET, 500, e5, e5)
    always_failing = [100] * minutes
    add("plant-api", "/assets", 500, always_failing, always_failing)    # other routes: not part of this SLI
    add("plant-api", "/assets/{asset_id}", 500, always_failing, always_failing)
    add("other-api", RISK_TARGET, 500, always_failing, always_failing)  # another service: not part of this SLI
    return series


def _firing(alert: str) -> str:
    return f'(count(ALERTS{{alertname="{alert}", alertstate="firing", severity="{SLO_ALERTS[alert]}"}}) > bool 0) or vector(0)'


def _promtool_test(tmp_path, phases: list[tuple], exprs: list[tuple[str, str, float]]) -> subprocess.CompletedProcess:
    """exprs = [(promql, eval_time, expected value)]. Runs `promtool test rules` against a copy of YOUR slo.yml."""
    shutil.copy(SLO_FILE, tmp_path / "slo.yml")
    doc = {"rule_files": ["slo.yml"], "evaluation_interval": "1m",
           "tests": [{"interval": "1m", "input_series": _slo_series(phases),
                      "promql_expr_test": [{"expr": e, "eval_time": t, "exp_samples": [{"labels": "{}", "value": v}]} for e, t, v in exprs]}]}
    (tmp_path / "scenario.test.yml").write_text(yaml.safe_dump(doc, sort_keys=False, width=10**6))
    return sh(["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{tmp_path}:/t:ro", PROMTOOL_IMAGE, "test", "rules", "/t/scenario.test.yml"])


def _load_slo() -> list[dict]:
    ok(SLO_FILE.exists(), "Missing observability/rules/slo.yml (the SLI recording rules and the burn-rate alerts; see the contract in guide/10).")
    doc = yaml.safe_load(SLO_FILE.read_text())
    ok(isinstance(doc, dict) and doc.get("groups"), "observability/rules/slo.yml has no `groups:`.")
    return [r for g in doc["groups"] for r in g.get("rules", [])]


def test_slo_rules_are_valid():
    rules = _load_slo()
    records = {r["record"] for r in rules if "record" in r}
    missing = [f"sli:risk_bad_ratio:rate{w}" for w in SLI_WINDOWS if f"sli:risk_bad_ratio:rate{w}" not in records]
    ok(not missing, f"Missing recording rules {missing}. Multi-window alerts need the same ratio over several windows, computed once and reused: that is what recording rules are for.")
    alerts = {r["alert"]: r for r in rules if "alert" in r}
    for name, sev in SLO_ALERTS.items():
        ok(name in alerts, f"Missing the alert `{name}` (see the names in the contract).")
        ok(alerts[name].get("labels", {}).get("severity") == sev, f"`{name}` must have `labels.severity: {sev}`: a fast burn wakes someone up, a slow one opens a ticket.")
        ok(len(alerts[name].get("annotations", {}).get("summary", "")) >= 15, f"`{name}` needs an `annotations.summary` that says how fast the budget is burning and what is at stake.")
    p = sh(["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{OBS}:/obs:ro", PROMTOOL_IMAGE, "check", "rules", "/obs/rules/slo.yml"])
    ok(p.returncode == 0, "`promtool check rules` rejects observability/rules/slo.yml:\n" + tail(p))


SLI_CASES = {
    # name: (one phase (minutes, fast_ok, slow_ok, client_4xx, server_5xx) per minute, why it matters)
    "failed_requests": ((120, 70, 0, 10, 20), "20 of every 100 /risk requests fail with 5xx and 10 are the client's mistake (404): the bad ratio must be 0.2. Client errors are not your failure; other routes and other services are not this SLI."),
    "slow_requests": ((120, 70, 10, 0, 0), "10 of every 80 successful /risk answers take longer than 500 ms: the bad ratio must be 0.125. A correct answer that arrives too late is bad for the user: the SLI must count latency, not only status codes."),
}


@pytest.mark.parametrize("case", SLI_CASES)
def test_sli_recording_rules_compute_the_bad_ratio(case, tmp_path):
    _load_slo()
    phase, why = SLI_CASES[case]
    _, fast, slow, c4, e5 = phase
    want = (slow + e5) / (fast + slow + c4 + e5)
    p = _promtool_test(tmp_path, [phase], [(f"round(sum(sli:risk_bad_ratio:rate{w}), 0.001)", "2h", want) for w in SLI_WINDOWS])
    ok(p.returncode == 0, f"Your SLI (sli:risk_bad_ratio:rate*) returns a wrong value on synthetic traffic.\n{why}\n\npromtool said:\n{tail(p, n=25)}")


# phases are (minutes, fast_ok, slow_ok, client_4xx, server_5xx), counts per minute
BURN_CASES = {
    "high_burn": dict(
        phases=[(120, 80, 0, 0, 20)], at="2h", fast=1, slow=None,
        why="20 % of the requests fail for two hours: the budget burns 20x too fast (the fast-burn threshold is 14.4x). The fast-burn alert must be firing."),
    "high_burn_from_latency": dict(
        phases=[(120, 80, 20, 0, 0)], at="2h", fast=1, slow=None,
        why="Everything answers 200 but 20 % is slower than 500 ms for two hours. That burns the budget exactly like errors do: the fast-burn alert must be firing."),
    "healthy": dict(
        phases=[(420, 199, 0, 0, 1)], at="7h", fast=0, slow=0,
        why="0.5 % of the requests fail for seven hours: burn rate 0.5x, well inside the budget. No alert must fire (another route and another service ARE failing all the time: they are not this SLO)."),
    "moderate_burn": dict(
        phases=[(480, 92, 0, 0, 8)], at="8h", fast=0, slow=1,
        why="8 % of the requests fail for eight hours: burn rate 8x. Too fast to ignore, not an emergency: the slow-burn alert must fire and the fast-burn alert must NOT (its threshold is 14.4x)."),
    "spike_that_recovered": dict(
        phases=[(60, 100, 0, 0, 0), (10, 0, 0, 0, 100), (20, 100, 0, 0, 0)], at="90m", fast=0, slow=None,
        why="Ten minutes of total failure that ended 20 minutes ago. The 1-hour ratio is still above the threshold (16 %) but the 5-minute ratio is back to zero: the fast-burn alert must have stopped. (This is what the short window is for: it makes the alert reset quickly.)"),
    "short_spike_now": dict(
        phases=[(60, 100, 0, 0, 0), (3, 0, 0, 0, 100)], at="63m", fast=0, slow=0,
        why="Three minutes of total failure right now. The 5-minute ratio is huge but the 1-hour ratio is only ~5 %: a blip that costs a sliver of the monthly budget, not a page. A single short window would fire here; the long window is what prevents it."),
}


@pytest.mark.parametrize("case", BURN_CASES)
def test_burn_rate_alerts_fire_only_when_they_should(case, tmp_path):
    _load_slo()
    c = BURN_CASES[case]
    exprs = [(_firing("RiskSLOFastBurn"), c["at"], float(c["fast"]))]
    if c["slow"] is not None:
        exprs.append((_firing("RiskSLOSlowBurn"), c["at"], float(c["slow"])))
    p = _promtool_test(tmp_path, c["phases"], exprs)
    ok(p.returncode == 0, f"Your burn-rate alerts misbehave on synthetic traffic (SLO: 99 % of /risk requests good over 30 days, so the error budget is 1 %).\n{c['why']}\n(If an alert should fire but doesn't, also check its `for:`: it must be ≤ 5m on the fast burn and ≤ 30m on the slow one.)\n\npromtool said:\n{tail(p, n=25)}")


def test_sli_works_on_the_real_metrics(traffic):
    """Synthetic data proves the logic; this proves your selectors match what YOUR API really exports."""
    expr = next((r["expr"] for r in _load_slo() if r.get("record") == "sli:risk_bad_ratio:rate5m"), None)
    ok(expr, "Missing the recording rule `sli:risk_bad_ratio:rate5m`.")
    c = httpx.Client(base_url=traffic["url"], timeout=15)
    got = None

    def value():
        nonlocal got
        for _ in range(4):
            c.get("/assets/PLT01-P101/risk")
        c.get("/assets/NO-SUCH-ASSET/risk")  # a 404 is the client's mistake: it must not count as bad
        r = httpx.get(PROM + "/api/v1/query", params={"query": expr}, timeout=10).json()["data"]["result"]
        got = float(r[0]["value"][1]) if r else None
        return got is not None and got == got  # not NaN

    wait_until(value, 120, interval=1.0)
    ok(got is not None and got == got, "Your SLI expression returns no value on the real metrics after ~2 minutes of /risk traffic. The synthetic tests use the label names of the contract, so compare them with what your API really exports (look at the real series in Prometheus: how are `http_target`, `http_status_code` and the `le` bucket boundary written?).")
    ok(0 <= got < 0.05, f"With healthy /risk traffic (plus some 404s) your SLI says {got:.2f} of the requests are bad; ≈ 0 expected. Are 404s counted as failures?")
