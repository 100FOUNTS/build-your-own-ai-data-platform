"""Module 14 — Final integration: operational documentation and an end-to-end flow that crosses the whole platform."""

from __future__ import annotations

import json
import os
import re
import threading
import time

import boto3
import httpx
import psycopg
import pytest

from conftest import ROOT, free_port, kafka_topics, need, ok, py, seeded_db, serve, sh, tail, unique

DOCS = ROOT / "docs"


# ------------------------------------------------------------------ runbook and decisions
def test_operational_runbook():
    f = DOCS / "runbook.md"
    ok(f.exists(), "Missing docs/runbook.md: the document read at 3 a.m. by someone who did NOT write the system (see guide/14_integration.md).")
    t = f.read_text()
    low = t.lower()
    for h in ("overview", "startup", "slo", "incidents", "restore"):
        ok(h in low, f"The runbook needs a “{h}” section.")
    ok("```mermaid" in t, "Include a (mermaid) diagram of how the data flows: a picture is worth a page of text.")
    ok(all(s in low for s in ("api", "gateway", "kafka", "postgres")), "The runbook must mention the key components (api, gateway, kafka, postgres).")
    slos = re.findall(r"^\s*[-*]\s+\*\*[^*]+\*\*.*\d", t.split("SLO", 1)[1].split("\n## ", 1)[0] if "SLO" in t else "", re.M)
    ok(len(slos) >= 3, f"Define at least 3 SLOs with a measurable numeric target (e.g. “99.5% of requests without a 5xx”); I found {len(slos)}. A target without a number can't be missed.")
    inc = t.lower().split("incidents", 1)[1].split("\n## ", 1)[0]
    cases = re.split(r"\n### ", inc)[1:]
    ok(len(cases) >= 5, f"Document at least 5 common incidents (### title) with symptom and steps; there are {len(cases)}.")
    ok(all("symptom" in c.lower() and len(c) > 120 for c in cases), "Each incident must have “Symptom:” and concrete diagnosis steps (not “check the logs”).")


def test_adr_architecture_decisions():
    files = sorted((DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")) if (DOCS / "adr").exists() else []
    ok(len(files) >= 4, f"Write at least 4 numbered ADRs in docs/adr/ (0001-….md). There are {len(files)}. An ADR explains WHY something was decided, so nobody undoes it without understanding.")
    for f in files:
        t = f.read_text().lower()
        for sec in ("context", "decision", "consequences", "alternatives"):
            ok(sec in t, f"{f.name}: missing the “{sec}” section.")
        ok(len(t) > 350, f"{f.name} is too short to be useful: explain the context and the real alternatives you ruled out.")


# ------------------------------------------------------------------ end-to-end flow
@pytest.fixture(scope="module")
def platform():
    need("postgres", "valkey", "redpanda", "keycloak", "floci")
    g = ROOT / "services" / "gateway"
    r = sh("npm ci --no-audit --no-fund && npm run build", cwd=g, shell=True, timeout=300)
    ok(r.returncode == 0, "The gateway doesn't build (module 7):\n" + tail(r))
    topic = unique("chk.e2e")
    kafka_topics(topic)
    aws = boto3.client
    kw = dict(endpoint_url="http://localhost:4566", aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
    name = unique("e2e").replace("_", "-")
    sns, sqs = aws("sns", **kw), aws("sqs", **kw)
    topic_arn = sns.create_topic(Name=name)["TopicArn"]
    qurl = sqs.create_queue(QueueName=name)["QueueUrl"]
    qarn = sqs.get_queue_attributes(QueueUrl=qurl, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=qarn, Attributes={"RawMessageDelivery": "true"})
    with seeded_db() as url:
        api_port, gw_port = free_port(), free_port()
        api_env = {"DATABASE_URL": url, "KAFKA_TOPIC_ALERTS": topic, "AUTH_DISABLED": "0", "RATE_LIMIT_PER_MIN": "1000"}
        gw_env = {"PORT": str(gw_port), "KAFKA_TOPIC_ALERTS": topic, "AUTH_DISABLED": "0"}
        with serve(["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(api_port), "--log-level", "warning"], api_env, f"http://127.0.0.1:{api_port}/health"):
            with serve(["node", "dist/index.js"], gw_env, f"http://127.0.0.1:{gw_port}/healthz", cwd=g):
                yield {"api": f"http://127.0.0.1:{api_port}", "gw": f"http://127.0.0.1:{gw_port}", "topic": topic, "topic_arn": topic_arn, "queue": qurl, "url": url}
    sns.delete_topic(TopicArn=topic_arn)
    sqs.delete_queue(QueueUrl=qurl)


def token(user):
    r = httpx.post(f"{os.environ['KEYCLOAK_URL']}/realms/plant/protocol/openid-connect/token", data={"grant_type": "password", "client_id": "plant-cli", "username": user, "password": user}, timeout=15)
    return r.json()["access_token"]


def test_an_alert_travels_through_the_whole_platform(platform):
    p = platform
    ana, bruno = token("ana"), token("bruno")
    got: list[dict] = []

    def listen():
        with httpx.stream("GET", p["gw"] + "/events", params={"access_token": ana}, timeout=40) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    got.append(json.loads(line[5:]))
                    return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(2)
    # 1) someone who is not an engineer can't create alerts
    denied = httpx.post(p["api"] + "/alerts", headers={"Authorization": f"Bearer {ana}"}, json={"asset_id": "PLT01-P107", "severity": "HIGH", "risk_score": 0.9}, timeout=15)
    ok(denied.status_code == 403, f"An operator should not be able to create alerts (it gave {denied.status_code}).")
    # 2) the engineer creates the alert → API → Postgres → Kafka
    r = httpx.post(p["api"] + "/alerts", headers={"Authorization": f"Bearer {bruno}"}, json={"asset_id": "PLT01-P107", "severity": "HIGH", "risk_score": 0.93, "payload": {"source": "e2e"}}, timeout=15)
    ok(r.status_code == 201, f"The engineer could not create the alert: {r.status_code} {r.text[:150]}")
    alert_id = r.json()["alert_id"]
    with psycopg.connect(p["url"]) as con:
        ok(con.execute("select count(*) from alerts where alert_id = %s", (alert_id,)).fetchone()[0] == 1, "The alert was not saved in Postgres.")
    # 3) gateway → browser in real time
    t.join(20)
    ok(got and got[0]["alert_id"] == alert_id, "The alert didn't reach the client connected to the gateway over SSE within 20 s (Kafka → gateway → browser).")
    # 4) notifier → SNS → SQS
    n = py(["-m", "services.notifier.notifier"], env={"KAFKA_TOPIC_ALERTS": p["topic"], "KAFKA_GROUP": unique("g"), "IDLE_EXIT_SECONDS": "6", "ALERTS_TOPIC_ARN": p["topic_arn"],
                                                      "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "us-east-1", "AWS_ENDPOINT_URL": "http://localhost:4566"}, timeout=120)
    ok(n.returncode == 0, "The notifier failed:\n" + tail(n))
    sqs = boto3.client("sqs", endpoint_url="http://localhost:4566", aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
    msgs, end = [], time.time() + 20
    while not msgs and time.time() < end:
        msgs = sqs.receive_message(QueueUrl=p["queue"], WaitTimeSeconds=2, MaxNumberOfMessages=10).get("Messages", [])
    ok(msgs and json.loads(msgs[0]["Body"])["alert_id"] == alert_id, "The alert didn't reach the SQS queue (Kafka → notifier → SNS → SQS).")
    # 5) no identity, no entry
    ok(httpx.get(p["gw"] + "/events", timeout=5).status_code == 401, "The gateway must keep rejecting connections WITHOUT a token.")
    ok(httpx.get(p["api"] + "/assets", timeout=5).status_code == 401, "The API must keep rejecting requests WITHOUT a token.")
