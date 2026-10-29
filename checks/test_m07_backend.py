"""Module 7 — Backend: REST API with FastAPI + Pydantic (Postgres, Valkey, Kafka) and a real-time gateway with Node.js/TypeScript."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time

import httpx
import psycopg
import pytest
import redis

from conftest import ROOT, free_port, kafka_topics, need, ok, seeded_db, serve, sh, tail, unique, kafka_read_all


@pytest.fixture(scope="module")
def stack():
    need("postgres", "valkey", "redpanda")
    ok((ROOT / "services" / "api" / "app" / "main.py").exists(), "Missing services/api/app/main.py (`app = FastAPI(...)`; see guide/07_backend.md).")
    r = redis.Redis.from_url(os.environ["REDIS_URL"])
    for k in r.scan_iter("risk:*"):
        r.delete(k)
    topic = unique("chk.alerts")
    kafka_topics(topic)
    with seeded_db() as url:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("insert into risk_scores (asset_id, feature_date, risk_score, model_version) values ('PLT01-P101','2026-09-29',0.20,'v1'),('PLT01-P101','2026-09-30',0.35,'v1'),('PLT01-P102','2026-09-30',0.90,'v1')")
        env = {"DATABASE_URL": url, "KAFKA_TOPIC_ALERTS": topic, "RATE_LIMIT_PER_MIN": "1000", "AUTH_DISABLED": "1"}
        port = free_port()
        cmd = ["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(port), "--log-level", "warning"]
        with serve(cmd, env, f"http://127.0.0.1:{port}/health"):
            yield {"base": f"http://127.0.0.1:{port}", "url": url, "topic": topic, "env": env, "redis": r}


def H(s):
    return httpx.Client(base_url=s["base"], timeout=15)


# ------------------------------------------------------------------ FastAPI + Pydantic
def test_openapi_describes_the_api(stack):
    spec = H(stack).get("/openapi.json").json()
    for p in ("/assets", "/assets/{asset_id}/risk", "/work-orders", "/alerts"):
        ok(p in spec["paths"], f"The OpenAPI contract doesn't include {p}. The docs come for free from the Pydantic types: did you declare response_model?")


def test_asset_list_ordered_by_risk(stack):
    r = H(stack).get("/assets")
    ok(r.status_code == 200, f"GET /assets → {r.status_code}")
    data = r.json()
    ok(len(data) == 12 and {"asset_id", "plant", "model", "criticality", "latest_risk", "as_of"} <= set(data[0]), "GET /assets must return the 12 assets with asset_id, plant, model, criticality, latest_risk and as_of.")
    ok([d["asset_id"] for d in data[:2]] == ["PLT01-P102", "PLT01-P101"] and data[0]["latest_risk"] == 0.9 and data[1]["latest_risk"] == 0.35,
       "The list must go from highest to lowest MOST RECENT risk of each asset (P102=0.90 first, P101=0.35 second; not 0.20, which is from yesterday).")
    ok(all(d["latest_risk"] is None for d in data[2:]), "Assets without a score go at the end with latest_risk=null.")


def test_detail_and_404(stack):
    c = H(stack)
    d = c.get("/assets/PLT01-P101")
    ok(d.status_code == 200 and "n_failures" in d.json() and "total_corrective_cost" in d.json(), "GET /assets/{id} must return the detail with n_failures and total_corrective_cost (use the view from module 1).")
    ok(c.get("/assets/NO-SUCH-ASSET").status_code == 404, "A nonexistent asset must give 404 (not 500).")
    ok(c.get("/assets/NO-SUCH-ASSET/risk").status_code == 404, "The risk of a nonexistent asset must give 404.")


def test_risk_uses_a_cache_with_ttl(stack):
    c, r = H(stack), stack["redis"]
    first = c.get("/assets/PLT01-P101/risk").json()
    ok(first["risk_score"] == 0.35 and first["as_of"] == "2026-09-30", f"The current risk of P101 is 0.35 from 2026-09-30 and the API returned {first}.")
    ttl = r.ttl("risk:PLT01-P101")
    ok(0 < ttl <= 30, f"Missing the Valkey cache with key `risk:PLT01-P101` and TTL ≤ 30 s (current TTL: {ttl}).")
    with psycopg.connect(stack["url"], autocommit=True) as con:
        con.execute("update risk_scores set risk_score = 0.99 where asset_id='PLT01-P101' and feature_date='2026-09-30'")
    ok(c.get("/assets/PLT01-P101/risk").json()["risk_score"] == 0.35, "Within the TTL the API must serve the cached value (0.35) even though the database has changed; that is how the database is protected under load.")
    r.delete("risk:PLT01-P101")
    ok(c.get("/assets/PLT01-P101/risk").json()["risk_score"] == 0.99, "After the cache expires/is invalidated, the new value must show.")


def test_history_with_parameter_validation(stack):
    c = H(stack)
    h = c.get("/assets/PLT01-P101/history", params={"days": 30}).json()
    ok([p["feature_date"] for p in h] == ["2026-09-29", "2026-09-30"], f"The history must be in ascending chronological order: {h}")
    for bad in (0, 1000, -3):
        ok(c.get("/assets/PLT01-P101/history", params={"days": bad}).status_code == 422, f"days={bad} must be rejected with 422 (validate ranges with Query(ge=1, le=365)).")


def test_work_orders_validation_and_creation(stack):
    c = H(stack)
    good = c.post("/work-orders", json={"asset_id": "PLT01-P103", "wo_type": "CORRECTIVE", "failure_mode": "seal", "description": "Leak", "cost_usd": 120.5})
    ok(good.status_code == 201 and good.json()["wo_id"], f"Creating a valid work order must give 201 with its wo_id (it gave {good.status_code}: {good.text[:150]}).")
    listed = c.get("/work-orders", params={"asset_id": "PLT01-P103", "status": "open"}).json()
    ok(any(w["wo_id"] == good.json()["wo_id"] for w in listed), "The created work order (not closed) must appear in GET /work-orders?asset_id=…&status=open.")
    cases = [
        ({"asset_id": "PLT01-P103", "wo_type": "CORRECTIVE"}, 422, "CORRECTIVE without failure_mode"),
        ({"asset_id": "PLT01-P103", "wo_type": "FOO"}, 422, "an invalid wo_type"),
        ({"asset_id": "PLT01-P103", "wo_type": "INSPECTION", "cost_usd": -1}, 422, "a negative cost"),
        ({"asset_id": "PLT01-P103", "wo_type": "INSPECTION", "weird_field": 1}, 422, "an unknown field (extra=forbid)"),
        ({"asset_id": "NO-SUCH-ASSET", "wo_type": "INSPECTION"}, 404, "a nonexistent asset"),
    ]
    for body, code, why in cases:
        got = c.post("/work-orders", json=body).status_code
        ok(got == code, f"POST /work-orders with {why} must give {code} and it gave {got}.")


def test_alert_is_saved_and_published_to_kafka(stack):
    c = H(stack)
    r = c.post("/alerts", json={"asset_id": "PLT01-P102", "severity": "HIGH", "risk_score": 0.9, "payload": {"why": "test"}})
    ok(r.status_code == 201, f"POST /alerts → {r.status_code}: {r.text[:150]}")
    aid = r.json()["alert_id"]
    ok(c.post("/alerts", json={"asset_id": "PLT01-P102", "severity": "EXTREME", "risk_score": 0.9}).status_code == 422, "An invalid severity must give 422.")
    msgs = [json.loads(m) for m in kafka_read_all(stack["topic"], timeout=15)]
    ok(any(m["alert_id"] == aid and m["asset_id"] == "PLT01-P102" for m in msgs), f"The alert {aid} didn't reach the Kafka topic `{stack['topic']}`. The real-time gateway depends on that message.")


def test_rate_limit(stack):
    port = free_port()
    env = {**stack["env"], "RATE_LIMIT_PER_MIN": "5"}
    with serve(["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(port), "--log-level", "warning"], env, f"http://127.0.0.1:{port}/health"):
        stack["redis"].delete(*stack["redis"].keys("rl:*"))
        codes = [httpx.get(f"http://127.0.0.1:{port}/assets", timeout=10).status_code for _ in range(9)]
        health = httpx.get(f"http://127.0.0.1:{port}/health").status_code
    ok(codes[:5] == [200] * 5 and 429 in codes[5:], f"With RATE_LIMIT_PER_MIN=5, the 9 requests gave {codes}: the first 5 must pass and then 429 must come.")
    ok(health == 200, "/health must not be rate limited (the load balancer polls it every few seconds).")


def test_your_own_api_tests():
    files = list((ROOT / "services" / "api").rglob("test_*.py"))
    ok(files, "Write tests for your API in services/api/tests/ (at least the Pydantic validations).")
    r = sh(["uv", "run", "pytest", "services/api", "-q", "-p", "no:cacheprovider", "-o", "addopts="], env={"PYTHONPATH": str(ROOT)})
    ok(r.returncode == 0, "Your tests in services/api fail:\n" + tail(r))


# ------------------------------------------------------------------ Node.js + TypeScript
def test_node_typescript_gateway_sends_alerts_in_real_time():
    need("redpanda")
    g = ROOT / "services" / "gateway"
    ok(shutil.which("node") and shutil.which("npm"), "You need Node.js ≥ 20 (brew install node).")
    ok((g / "package.json").exists() and (g / "package-lock.json").exists(), "Missing services/gateway/package.json and its package-lock.json (`npm install` creates it; the lockfile is committed for reproducible builds).")
    ts = json.loads((g / "tsconfig.json").read_text()) if (g / "tsconfig.json").exists() else {}
    ok(ts.get("compilerOptions", {}).get("strict") is True, "tsconfig.json must turn on `\"strict\": true`: without strict mode TypeScript protects you from almost nothing.")
    r = sh("npm ci --no-audit --no-fund && npm run build", cwd=g, shell=True, timeout=300)
    ok(r.returncode == 0, "`npm ci && npm run build` failed:\n" + tail(r))
    topic = unique("chk.gw")
    kafka_topics(topic)
    port = free_port()
    env = {"PORT": str(port), "KAFKA_TOPIC_ALERTS": topic, "AUTH_DISABLED": "1"}  # module 8 adds authentication; here it is turned off
    with serve(["node", "dist/index.js"], env, f"http://127.0.0.1:{port}/healthz", cwd=g, timeout=40):
        received: list[str] = []

        def listen():
            with httpx.stream("GET", f"http://127.0.0.1:{port}/events", timeout=30) as resp:
                ok(resp.headers["content-type"].startswith("text/event-stream"), "/events must respond with Content-Type: text/event-stream.")
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        received.append(line[5:].strip())
                        return

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        time.sleep(1.5)
        from confluent_kafka import Producer

        p = Producer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
        p.produce(topic, key="PLT01-P107", value=json.dumps({"alert_id": 77, "asset_id": "PLT01-P107", "severity": "HIGH"}).encode())
        p.flush()
        t.join(15)
        ok(received and json.loads(received[0])["alert_id"] == 77, "I published an alert to Kafka and the SSE client didn't receive it within 15 s. Is your consumer subscribed to the topic and forwarding `data: <json>\\n\\n` to the connected clients?")
        ok(httpx.get(f"http://127.0.0.1:{port}/nothing").status_code == 404, "A nonexistent route must respond 404.")
