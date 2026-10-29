"""Shared helpers for the checks.

The checks are a companion that verifies, with real data and real services, what each guide module asks for.
When one fails, the message says WHAT is not met and WHY it matters; it never gives you the solution.
You don't need to read them to make progress.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Same variables as platform.env (the checks don't depend on you exporting them)
ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql://plant:plant@localhost:5433/plant",
    "REDIS_URL": "redis://localhost:6380/0",
    "QDRANT_URL": "http://localhost:6333",
    "KAFKA_BOOTSTRAP": "localhost:19092",
    "KAFKA_TOPIC_RAW": "plant.readings.raw",
    "S3_ENDPOINT": "http://localhost:8333",
    "S3_ACCESS_KEY": "lakeaccess",
    "S3_SECRET_KEY": "lakesecret",
    "ICEBERG_REST_URL": "http://localhost:8181",
    "MLFLOW_TRACKING_URI": "http://localhost:5001",
    "KEYCLOAK_URL": "http://localhost:8089",
    "KEYCLOAK_REALM": "plant",
    "OPENBAO_ADDR": "http://localhost:8200",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    "AWS_ENDPOINT_URL": "http://localhost:4566",
    "OLLAMA_URL": "http://localhost:11434",
    "OLLAMA_EMBED_MODEL": "mxbai-embed-large",
    "OLLAMA_CHAT_MODEL": "qwen3:4b-instruct",
    "MLFLOW_DISABLE_AGENT_HINT": "1",
}
for _k, _v in ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

SERVICES = {
    "postgres": (5433, "make up-db"), "valkey": (6380, "make up-db"), "qdrant": (6333, "make up-vector"),
    "redpanda": (19092, "make up-stream"), "s3": (8333, "make up-lake"), "iceberg": (8181, "make up-lake"),
    "mlflow": (5001, "make up-lake"), "keycloak": (8089, "make up-auth"), "openbao": (8200, "make up-auth"),
    "prometheus": (9090, "make up-obs"), "grafana": (3030, "make up-obs"), "loki": (3100, "make up-obs"),
    "tempo": (3200, "make up-obs"), "otlp": (4317, "make up-obs"), "floci": (4566, "make up-cloud"),
    "forgejo": (3002, "make up-git"), "ollama": (11434, "ollama serve  (and ollama pull mxbai-embed-large)"),
}


def ok(cond: object, msg: str) -> None:
    """Fails with a clean message (no traceback) if the condition does not hold."""
    if not cond:
        pytest.fail("\n\n  " + msg.strip().replace("\n", "\n  ") + "\n", pytrace=False)


def port_open(port: int, host: str = "localhost") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def need(*names: str) -> None:
    """Requires the services to be up; otherwise says exactly how to start them."""
    missing = [(n, SERVICES[n][1]) for n in names if not port_open(SERVICES[n][0])]
    if missing:
        hints = sorted({h for _, h in missing})
        ok(False, f"Missing services: {', '.join(n for n, _ in missing)}.\nStart them with:  " + "  ·  ".join(hints))


def unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def sh(cmd: list[str] | str, env: dict[str, str] | None = None, cwd: Path | None = None, timeout: int = 600, shell: bool = False) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=cwd or ROOT, env=full_env, capture_output=True, text=True, timeout=timeout, shell=shell, check=False)


def py(args: list[str], env: dict[str, str] | None = None, timeout: int = 600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return sh([sys.executable, *args], env={"PYTHONPATH": str(ROOT), **(env or {})}, timeout=timeout, cwd=cwd)


def tail(proc: subprocess.CompletedProcess, n: int = 12, width: int = 220) -> str:
    text = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return "\n".join(ln[:width] for ln in text[-n:])


def pg():
    import psycopg

    need("postgres")
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def wait_until(fn, timeout: float = 30.0, interval: float = 0.5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


# ------------------------------------------------------------------ Kafka / simulator
def kafka_topics(*names: str, partitions: int = 3) -> None:
    from confluent_kafka.admin import AdminClient, NewTopic

    need("redpanda")
    admin = AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
    for f in admin.create_topics([NewTopic(n, num_partitions=partitions, replication_factor=1) for n in names]).values():
        f.result(timeout=20)


def kafka_read_all(topic: str, timeout: float = 20.0) -> list[bytes]:
    """Reads a whole topic from the beginning (ephemeral group) until it reaches the end."""
    from confluent_kafka import Consumer, TopicPartition

    c = Consumer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"], "group.id": unique("reader"), "auto.offset.reset": "earliest", "enable.auto.commit": False})
    md = c.list_topics(topic, timeout=10)
    if topic not in md.topics or md.topics[topic].error:
        return []
    parts = list(md.topics[topic].partitions)
    ends = {p: c.get_watermark_offsets(TopicPartition(topic, p), timeout=10)[1] for p in parts}
    c.assign([TopicPartition(topic, p, 0) for p in parts])
    out, end = [], time.time() + timeout
    while time.time() < end and any(ends.values()):
        m = c.poll(0.5)
        if m is None or m.error():
            continue
        out.append(m.value())
        if m.offset() + 1 >= ends[m.partition()]:
            ends[m.partition()] = 0
    c.close()
    return out


def sim_burst(topic: str, n: int = 4000, seed: int = 7, start_hours: float = 0.0, inject: str | None = None) -> dict:
    """Runs the simulator in burst mode (container) and returns its manifest: exactly what it sent."""
    need("redpanda")
    cmd = ["docker", "compose", "--profile", "tools", "run", "--rm", "--build", "sim", "burst", "--n", str(n), "--topic", topic, "--seed", str(seed), "--start-hours", str(start_hours)]
    if inject:
        cmd += ["--inject", inject]
    r = sh(cmd, timeout=300)
    ok(r.returncode == 0, "Could not run the simulator (is Docker running?):\n" + tail(r))
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ Temporary Postgres with YOUR migrations
from contextlib import contextmanager  # noqa: E402


@contextmanager
def seeded_db():
    """Temporary database created with YOUR scripts/migrate.py and scripts/seed.py (module 1)."""
    import psycopg

    need("postgres")
    base = os.environ["DATABASE_URL"]
    admin = psycopg.connect(base.rsplit("/", 1)[0] + "/postgres", autocommit=True)
    name = unique("chk")
    admin.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + f"/{name}"
    try:
        for script in ("scripts/migrate.py", "scripts/seed.py"):
            ok((ROOT / script).exists(), f"Missing {script} (module 1).")
            r = py([script], env={"DATABASE_URL": url})
            ok(r.returncode == 0, f"{script} failed:\n{tail(r)}")
        yield url
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


# ------------------------------------------------------------------ Iceberg / Spark
def iceberg_catalog():
    from pyiceberg.catalog.rest import RestCatalog

    need("iceberg", "s3")
    return RestCatalog("lake", **{
        "uri": os.environ["ICEBERG_REST_URL"], "s3.endpoint": os.environ["S3_ENDPOINT"], "s3.access-key-id": os.environ["S3_ACCESS_KEY"],
        "s3.secret-access-key": os.environ["S3_SECRET_KEY"], "s3.path-style-access": "true", "s3.region": "us-east-1"})


def drop_namespace(ns: str) -> None:
    try:
        cat = iceberg_catalog()
        for ident in cat.list_tables(ns):
            cat.drop_table(ident)
        cat.drop_namespace(ns)
    except Exception:  # noqa: BLE001  (best-effort cleanup)
        pass


def spark_job(job: str, env: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Runs a Spark job exactly like `make spark JOB=...`."""
    need("redpanda", "iceberg", "s3")
    f = ROOT / "pipelines" / "spark" / job
    ok(f.exists(), f"Missing pipelines/spark/{job} (see guide/03_lakehouse.md).")
    return sh(["docker", "compose", "--profile", "tools", "run", "--rm", "--build", "spark", f"/jobs/{job}"], env=env, timeout=timeout)


def table_count(ns: str, table: str = "readings") -> int:
    cat = iceberg_catalog()
    try:
        return cat.load_table(f"{ns}.{table}").scan(selected_fields=("msg_id",)).to_arrow().num_rows
    except Exception as exc:  # noqa: BLE001
        ok(False, f"Cannot read the Iceberg table {ns}.{table}: {exc}")


# ------------------------------------------------------------------ service processes
import signal  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve(cmd: list[str], env: dict[str, str], health: str, timeout: float = 40.0, cwd: Path | None = None, ok_codes=(200,)):
    """Starts a service process, waits for its health endpoint and stops it at the end."""
    import httpx

    proc = subprocess.Popen(cmd, cwd=cwd or ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), **env}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        end = time.time() + timeout
        up = False
        while time.time() < end:
            if proc.poll() is not None:
                break
            try:
                if httpx.get(health, timeout=2).status_code in ok_codes:
                    up = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        if not up:
            out = ""
            if proc.poll() is not None:
                out = proc.stdout.read()[-1800:] if proc.stdout else ""
            ok(False, f"The service did not answer on {health} in time.\n{out}")
        yield proc
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
