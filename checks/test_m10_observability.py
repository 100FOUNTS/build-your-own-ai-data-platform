"""Module 10 — Observability: OpenTelemetry (traces, metrics, logs) → Collector → Prometheus / Tempo / Loki → Grafana."""

from __future__ import annotations

import json
import re
import time

import httpx
import psycopg
import pytest
import yaml

from conftest import ROOT, free_port, kafka_topics, need, ok, seeded_db, serve, sh, tail, unique, wait_until

PROM, TEMPO, LOKI, GRAFANA = "http://localhost:9090", "http://localhost:3200", "http://localhost:3100", "http://localhost:3030"
OBS = ROOT / "observability"


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
    p = sh(["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{OBS}:/obs:ro", "prom/prometheus:v3.13.4", "check", "rules", "/obs/rules/alerts.yml"])
    ok(p.returncode == 0, "`promtool check rules` rejects your rules:\n" + tail(p))


@pytest.fixture(scope="module")
def traffic():
    need("postgres", "valkey", "redpanda", "otlp", "prometheus", "loki", "tempo", "grafana")
    for f in ("otel-collector.yaml", "prometheus.yml"):
        ok((OBS / f).exists(), f"Missing observability/{f}.")
    ok(list((OBS / "grafana" / "provisioning" / "datasources").glob("*.y*ml")) if (OBS / "grafana/provisioning/datasources").exists() else False, "Missing the Grafana datasource provisioning (observability/grafana/provisioning/datasources/*.yml).")
    r = sh(["docker", "compose", "--profile", "obs", "up", "-d", "--force-recreate", "--wait", "otel-collector", "prometheus", "grafana"], timeout=240)
    ok(r.returncode == 0, "Could not restart the collector/Prometheus/Grafana with YOUR configs (invalid YAML?):\n" + tail(r))
    time.sleep(4)
    st = sh(["docker", "compose", "--profile", "obs", "ps", "--format", "{{.Service}} {{.State}}"]).stdout
    ok("otel-collector running" in st, "The OpenTelemetry Collector didn't stay running with your config. Look at `docker compose --profile obs logs otel-collector`.")
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
            yield {"t0": t0}
            time.sleep(7)  # let the last batch of telemetry go out


def test_metrics_reach_prometheus(traffic):
    def n():
        r = httpx.get(PROM + "/api/v1/query", params={"query": 'sum({__name__=~"http_server.*_count", service_name="plant-api"})'}, timeout=10).json()["data"]["result"]
        return float(r[0]["value"][1]) if r else 0

    got = wait_until(lambda: n() >= 20, 60)
    ok(got, "Prometheus has no HTTP metrics for `service_name=\"plant-api\"` after 60 s of traffic.\nChain to review: the API exports over OTLP → the Collector exposes them on :8889 (`prometheus` exporter with resource_to_telemetry_conversion) → prometheus.yml scrapes that target.")
    targets = httpx.get(PROM + "/api/v1/targets", timeout=10).json()["data"]["activeTargets"]
    ok(any(t["health"] == "up" and "8889" in t["scrapeUrl"] for t in targets), "The Collector target (otel-collector:8889) is not “up” in Prometheus.")


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
