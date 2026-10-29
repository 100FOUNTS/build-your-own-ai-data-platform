"""Module 12 — Containers and Kubernetes: small, secure images (Docker) and declarative deployment on a local cluster (kind)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

import httpx
import pytest
import yaml

from conftest import ROOT, free_port, need, ok, sh, tail, wait_until

SERVICES = {"api": {"max_mb": 700, "port": 8000, "health": "/health"}, "gateway": {"max_mb": 250, "port": 8010, "health": "/healthz"}, "web": {"max_mb": 400, "port": 3300, "health": "/api/health"}}
KUSTOM = ROOT / "deploy" / "k8s" / "overlays" / "dev"


# ------------------------------------------------------------------ Dockerfiles (static analysis)
def instructions(path):
    text = path.read_text().replace("\\\n", " ")
    return [(ln.split(None, 1)[0].upper(), ln.split(None, 1)[1] if len(ln.split(None, 1)) > 1 else "") for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


@pytest.mark.parametrize("svc", SERVICES)
def test_dockerfile_good_practices(svc):
    f = ROOT / "services" / svc / "Dockerfile"
    ok(f.exists(), f"Missing services/{svc}/Dockerfile (build context: the repo root; see guide/12_containers_k8s.md).")
    ins = instructions(f)
    froms = [a for k, a in ins if k == "FROM"]
    for a in froms:
        image = a.split()[0]
        ok(":" in image and not image.endswith(":latest") or " AS " in a.upper() and image in [x.split()[-1] for x in froms], f"The base image `{image}` is not pinned to a version: `:latest` or no tag makes two builds of the same commit give different results.")
    if svc in ("gateway", "web"):
        ok(len(froms) >= 2, f"{svc} should use a multi-stage build: compile with all the tools and copy only the result into a small final image.")
    users = [a.strip() for k, a in ins if k == "USER"]
    ok(users and users[-1] not in ("root", "0"), "The final container must run as a NON-root user (USER instruction): if it is compromised, the attacker is not root.")
    ok(any(k == "HEALTHCHECK" for k, _ in ins), "Missing HEALTHCHECK: without it Docker can't tell a live process from a working service.")
    ok(not any(k == "ADD" and a.startswith("http") for k, a in ins), "Don't use ADD with a URL: it downloads unverified content. Use COPY or a step with a checksum.")
    ok(any(k == "CMD" or k == "ENTRYPOINT" for k, _ in ins), "Missing CMD/ENTRYPOINT.")
    # layer cache: the dependency manifest is copied BEFORE the code
    copies = [a for k, a in ins if k == "COPY" and "--from" not in a]
    manifest = next((i for i, a in enumerate(copies) if re.search(r"requirements|package(-lock)?\.json|pyproject", a)), None)
    code = next((i for i, a in enumerate(copies) if re.search(r"services/(common|api/app|gateway/src|web/?\s)|\bsrc\b|\. \.|\./ ", a)), None)
    ok(manifest is not None and (code is None or manifest <= code), "Copy ONLY the dependency file first (requirements.txt / package*.json), install, and then copy the code. That way a code change doesn't reinstall every dependency (layer cache).")
    di = ROOT / ".dockerignore"
    ok(di.exists() and all(x in di.read_text() for x in (".git", "node_modules", "data")), "Missing .dockerignore at the root (with .git, node_modules, data, .venv…): without it you would send gigabytes of data to the Docker daemon on every build.")


# ------------------------------------------------------------------ build and run
@pytest.fixture(scope="module")
def images():
    need("redpanda")
    ok(shutil.which("docker"), "Docker is not available.")
    out = {}
    for svc in SERVICES:
        r = sh(["docker", "build", "-f", f"services/{svc}/Dockerfile", "-t", f"plant-{svc}:dev", "."], timeout=1500)
        ok(r.returncode == 0, f"`docker build` of {svc} failed:\n" + tail(r, 14))
        size = int(sh(["docker", "image", "inspect", f"plant-{svc}:dev", "--format", "{{.Size}}"]).stdout) / 1e6
        out[svc] = size
    return out


@pytest.mark.parametrize("svc", SERVICES)
def test_image_small_non_root_and_healthy(images, svc):
    cfg = SERVICES[svc]
    ok(images[svc] <= cfg["max_mb"], f"The {svc} image is {images[svc]:.0f} MB and the reasonable maximum is {cfg['max_mb']} MB. Check: multi-stage build? development dependencies? pip/npm cache inside the image?")
    port = free_port()
    name = f"chk-{svc}-{port}"
    run = sh(["docker", "run", "-d", "--rm", "--name", name, "-p", f"{port}:{cfg['port']}", "-e", "KAFKA_BOOTSTRAP=host.docker.internal:29092", "-e", "AUTH_SECRET=x" * 4, "-e", "DATABASE_URL=postgresql://x:x@host.docker.internal:5433/x", "-e", "REDIS_URL=redis://host.docker.internal:6380/0", "-e", "KEYCLOAK_URL=http://host.docker.internal:8089", f"plant-{svc}:dev"])
    ok(run.returncode == 0, "Could not start the container:\n" + tail(run))
    try:
        up = wait_until(lambda: _http(f"http://127.0.0.1:{port}{cfg['health']}"), 60)
        logs = sh(["docker", "logs", name]).stdout[-800:]
        ok(up, f"The {svc} container didn't answer on {cfg['health']} within 60 s. Last logs:\n{logs}")
        uid = sh(["docker", "exec", name, "id", "-u"]).stdout.strip()
        ok(uid not in ("", "0"), f"The process runs as root (uid {uid or '0'}).")
        health = wait_until(lambda: sh(["docker", "inspect", name, "--format", "{{.State.Health.Status}}"]).stdout.strip() == "healthy", 90)
        ok(health, "The HEALTHCHECK never reaches “healthy”: check that the health command works inside the image (does curl/wget/python exist?).")
    finally:
        sh(["docker", "stop", "-t", "2", name])


def _http(url):
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


# ------------------------------------------------------------------ Kubernetes manifests
@pytest.fixture(scope="module")
def manifests():
    ok(shutil.which("kubectl"), "Install kubectl.")
    r = sh(["kubectl", "kustomize", str(KUSTOM)])
    ok(r.returncode == 0, "`kubectl kustomize deploy/k8s/overlays/dev` failed (layout: base/ with the resources and overlays/dev with kustomization.yaml):\n" + tail(r))
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def test_production_manifests(manifests):
    deps = {d["metadata"]["name"]: d for d in manifests if d["kind"] == "Deployment"}
    ok({"plant-api", "plant-gateway", "plant-web"} <= set(deps), f"The Deployments plant-api, plant-gateway and plant-web are expected (there are {sorted(deps)}).")
    ok(any(d["kind"] == "Service" and d["metadata"]["name"] == "plant-api" for d in manifests), "Missing the Service plant-api.")
    ok(any(d["kind"] == "ConfigMap" for d in manifests), "Missing a ConfigMap with the non-secret configuration.")
    for name, d in deps.items():
        spec = d["spec"]["template"]["spec"]
        c = spec["containers"][0]
        ok(c.get("resources", {}).get("requests") and c["resources"].get("limits"), f"{name}: define resources.requests AND limits (without them, a pod with a memory leak takes down the node and the scheduler decides blindly).")
        ok(c.get("readinessProbe") and c.get("livenessProbe"), f"{name}: needs a readinessProbe (should it get traffic?) and a livenessProbe (should it be restarted?). They are different things.")
        sc, psc = c.get("securityContext", {}), spec.get("securityContext", {})
        ok((psc.get("runAsNonRoot") or sc.get("runAsNonRoot")) and sc.get("allowPrivilegeEscalation") is False, f"{name}: securityContext with runAsNonRoot: true and allowPrivilegeEscalation: false.")
        ok("drop" in sc.get("capabilities", {}) and "ALL" in sc["capabilities"]["drop"], f"{name}: drop all Linux capabilities (capabilities.drop: [ALL]) and add only the essential ones.")
        ok(not c["image"].endswith(":latest") and ":" in c["image"], f"{name}: the image {c['image']} must carry an explicit version tag.")
    ok(deps["plant-api"]["spec"]["replicas"] >= 2, "The API must have at least 2 replicas (if a pod goes down or is updated, the service keeps running).")
    ok(any(d["kind"] == "PodDisruptionBudget" for d in manifests), "Add a PodDisruptionBudget for the API: it prevents a node update from leaving it with no replicas.")
    secrets = [d for d in manifests if d["kind"] == "Secret" and (d.get("data") or d.get("stringData"))]
    ok(not secrets, "There is a Secret with values inside the repository manifests. Real Secrets are created outside git (kubectl create secret, platform/tools/k8s_secrets.sh) and only referenced here with secretKeyRef.")


# ------------------------------------------------------------------ kind cluster
def test_real_deployment_on_kind(images, manifests):
    if not shutil.which("kind"):
        ok(False, "Install kind: `brew install kind`.")
    clusters = sh(["kind", "get", "clusters"]).stdout.split()
    if "plant" not in clusters:
        r = sh(["kind", "create", "cluster", "--config", "platform/kind-config.yaml"], timeout=600)
        ok(r.returncode == 0, "Could not create the kind cluster:\n" + tail(r))
    ctx = ["--context", "kind-plant"]
    for svc in SERVICES:
        r = sh(["kind", "load", "docker-image", f"plant-{svc}:dev", "--name", "plant"], timeout=600)
        ok(r.returncode == 0, f"kind load of {svc} failed:\n" + tail(r))
    ok(sh(["bash", "platform/tools/k8s_secrets.sh"]).returncode == 0, "Could not create the Secret plant-secrets (platform/tools/k8s_secrets.sh).")
    d = sh(["kubectl", *ctx, "apply", "--dry-run=server", "-k", str(KUSTOM)])
    ok(d.returncode == 0, "The Kubernetes server rejects your manifests (dry-run=server):\n" + tail(d))
    a = sh(["kubectl", *ctx, "apply", "-k", str(KUSTOM)])
    ok(a.returncode == 0, "kubectl apply failed:\n" + tail(a))
    try:
        for dep in ("plant-api", "plant-gateway", "plant-web"):
            r = sh(["kubectl", *ctx, "-n", "plant", "rollout", "status", f"deployment/{dep}", "--timeout=240s"], timeout=300)
            if r.returncode != 0:
                ev = sh(["kubectl", *ctx, "-n", "plant", "get", "pods", "-o", "wide"]).stdout + sh(["kubectl", *ctx, "-n", "plant", "describe", "deployment", dep]).stdout[-1200:]
                ok(False, f"The deployment {dep} didn't become available within 4 minutes:\n{tail(r, 4)}\n{ev[-1500:]}")
        pods = json.loads(sh(["kubectl", *ctx, "-n", "plant", "get", "pods", "-o", "json"]).stdout)["items"]
        ok(all(c["restartCount"] == 0 for p in pods for c in p["status"].get("containerStatuses", [])), "Some pod restarted: the probes or the resources are badly tuned (liveness too aggressive?).")
        for svc, local in (("plant-api", 8000), ("plant-web", 3300)):
            port = free_port()
            pf = subprocess.Popen(["kubectl", *ctx, "-n", "plant", "port-forward", f"svc/{svc}", f"{port}:{local}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                path = "/health" if svc == "plant-api" else "/api/health"
                ok(wait_until(lambda: _http(f"http://127.0.0.1:{port}{path}"), 30), f"The Service {svc} doesn't answer {path} through port-forward.")
            finally:
                pf.terminate()
    finally:
        sh(["kubectl", *ctx, "delete", "-k", str(KUSTOM), "--wait=false"])
