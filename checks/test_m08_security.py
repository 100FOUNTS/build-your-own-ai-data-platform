"""Module 8 — Security: OAuth2/OIDC with Keycloak (JWT + roles), secrets with OpenBao (AppRole) and a threat model."""

from __future__ import annotations

import base64
import json
import os
import re
import time

import httpx
import hvac
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import ROOT, free_port, kafka_topics, need, ok, py, seeded_db, serve, sh, tail, unique

KC = os.environ["KEYCLOAK_URL"] + "/realms/" + os.environ["KEYCLOAK_REALM"]


def token(user: str, client: str = "plant-cli") -> str:
    r = httpx.post(KC + "/protocol/openid-connect/token", data={"grant_type": "password", "client_id": client, "username": user, "password": user}, timeout=15)
    ok(r.status_code == 200, f"Keycloak didn't issue a token for {user}: {r.text[:120]}")
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def secured_api():
    need("postgres", "valkey", "redpanda", "keycloak")
    topic = unique("chk.sec")
    kafka_topics(topic)
    with seeded_db() as url:
        env = {"DATABASE_URL": url, "KAFKA_TOPIC_ALERTS": topic, "RATE_LIMIT_PER_MIN": "1000", "AUTH_DISABLED": "0"}
        port = free_port()
        with serve(["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(port), "--log-level", "warning"], env, f"http://127.0.0.1:{port}/health"):
            yield {"base": f"http://127.0.0.1:{port}", "url": url}


def call(api, method, path, tok=None, **kw):
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    return httpx.request(method, api["base"] + path, headers=h, timeout=15, **kw)


# ------------------------------------------------------------------ authentication
def test_no_token_is_401_but_health_is_public(secured_api):
    ok(call(secured_api, "GET", "/health").status_code == 200, "/health must stay public (load balancers and Kubernetes use it).")
    r = call(secured_api, "GET", "/assets")
    ok(r.status_code == 401, f"GET /assets without a token gave {r.status_code}; it must be 401.")
    ok("bearer" in r.headers.get("www-authenticate", "").lower(), "A 401 response must include the `WWW-Authenticate: Bearer` header.")


def test_real_keycloak_token_works(secured_api):
    r = call(secured_api, "GET", "/assets", token("ana"))
    ok(r.status_code == 200, f"With a valid Keycloak token GET /assets gave {r.status_code}: {r.text[:120]}. Check the issuer, the audience (`plant-api`) and the JWKS.")
    me = call(secured_api, "GET", "/me", token("bruno")).json()
    ok(me["username"] == "bruno" and "engineer" in me["roles"], f"/me must return the user and their roles read from the token (`realm_access.roles`): {me}")


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_forged_tokens_are_rejected(secured_api):
    real_token = token("carla")
    real = jwt.decode(real_token, options={"verify_signature": False})
    real_kid = jwt.get_unverified_header(real_token)["kid"]  # the attacker knows the kid: it is public
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad = {
        "without a signature (alg=none)": b64({"alg": "none", "typ": "JWT"}) + "." + b64(real) + ".",
        "signed with its own key": jwt.encode(real, forged_key, algorithm="RS256", headers={"kid": "made-up"}),
        "that is garbage": "this.is.not-a-jwt",
    }
    real_hs = jwt.encode(real, "shared-secret-of-32-characters-xxxx", algorithm="HS256", headers={"kid": real_kid})
    bad["signed with HS256"] = real_hs
    for why, t in bad.items():
        got = call(secured_api, "GET", "/assets", t).status_code
        ok(got == 401, f"A token {why} must give 401 and it gave {got}. A server that accepts forged tokens has no authentication.")


def test_token_for_another_audience_is_rejected(secured_api):
    r = httpx.post(KC + "/protocol/openid-connect/token", data={"grant_type": "client_credentials", "client_id": "plant-api", "client_secret": "plant-api-secret"}, timeout=15)
    claims = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    aud = claims.get("aud")
    ok("plant-api" not in (aud if isinstance(aud, list) else [aud]), "(the test token already carries the right audience; the check can't evaluate this case)")
    got = call(secured_api, "GET", "/assets", r.json()["access_token"]).status_code
    ok(got == 401, f"A valid Keycloak token issued for ANOTHER audience ({aud}) must be rejected with 401 and it gave {got}. Always check `aud`: otherwise a token stolen from another app works here.")


# ------------------------------------------------------------------ authorization (RBAC)
def test_roles_operator_engineer_admin(secured_api):
    wo = {"asset_id": "PLT01-P101", "wo_type": "INSPECTION", "description": "round"}
    ana, bruno, carla = token("ana"), token("bruno"), token("carla")
    ok(call(secured_api, "POST", "/work-orders", ana, json=wo).status_code == 403, "An OPERATOR can't create work orders: it must give 403 (authenticated but not allowed; different from 401).")
    created = call(secured_api, "POST", "/work-orders", bruno, json=wo)
    ok(created.status_code == 201, f"An ENGINEER can create work orders (it gave {created.status_code}).")
    wid = created.json()["wo_id"]
    ok(call(secured_api, "DELETE", f"/work-orders/{wid}", bruno).status_code == 403, "Deleting a work order is ADMIN only: the engineer must get 403.")
    ok(call(secured_api, "DELETE", f"/work-orders/{wid}", carla).status_code == 204, "The ADMIN must be able to delete (204).")
    ok(call(secured_api, "DELETE", "/work-orders/NO-SUCH-WO", carla).status_code == 404, "Deleting something that doesn't exist must give 404.")


# ------------------------------------------------------------------ gateway
def test_gateway_requires_a_token_on_the_sse_stream():
    need("redpanda", "keycloak")
    g = ROOT / "services" / "gateway"
    ok((g / "package.json").exists(), "Missing the gateway from module 7.")
    r = sh("npm ci --no-audit --no-fund && npm run build", cwd=g, shell=True, timeout=300)
    ok(r.returncode == 0, "The gateway doesn't build:\n" + tail(r))
    topic = unique("chk.gwsec")
    kafka_topics(topic)
    port = free_port()
    with serve(["node", "dist/index.js"], {"PORT": str(port), "KAFKA_TOPIC_ALERTS": topic, "AUTH_DISABLED": "0"}, f"http://127.0.0.1:{port}/healthz", cwd=g):
        base = f"http://127.0.0.1:{port}/events"
        ok(httpx.get(base, timeout=5).status_code == 401, "GET /events without a token must give 401.")
        ok(httpx.get(base, params={"access_token": "garbage"}, timeout=5).status_code == 401, "GET /events with an invalid token must give 401.")
        with httpx.stream("GET", base, params={"access_token": token("ana")}, timeout=10) as resp:
            ok(resp.status_code == 200, f"With a valid token /events must open the stream (it gave {resp.status_code}).")


# ------------------------------------------------------------------ OpenBao
@pytest.fixture(scope="module")
def bao(tmp_path_factory):
    need("openbao", "postgres")
    f = ROOT / "scripts" / "bao_setup.py"
    ok(f.exists(), "Missing scripts/bao_setup.py (stores the secret, creates the policy and the AppRole). See guide/08_security.md.")
    app, path = unique("chk_app"), unique("chk") + "/db"
    secrets_file = tmp_path_factory.mktemp("bao") / "secrets.json"
    env = {"BAO_APP_NAME": app, "BAO_SECRET_PATH": path, "SECRETS_FILE": str(secrets_file), "BAO_TOKEN": "root"}
    with seeded_db() as url:
        env["DATABASE_URL"] = url
        for _ in range(2):  # idempotent
            r = py(["scripts/bao_setup.py"], env=env)
            ok(r.returncode == 0, "scripts/bao_setup.py failed (can it run twice?):\n" + tail(r))
        creds = json.loads(secrets_file.read_text())
        ok({"BAO_ROLE_ID", "BAO_SECRET_ID"} <= set(creds), "The credentials file must contain BAO_ROLE_ID and BAO_SECRET_ID.")
        yield {"env": env, "creds": creds, "url": url, "path": path, "file": secrets_file}


def test_the_app_reads_its_secret_and_nothing_else(bao):
    c = hvac.Client(url=os.environ["OPENBAO_ADDR"])
    c.auth.approle.login(role_id=bao["creds"]["BAO_ROLE_ID"], secret_id=bao["creds"]["BAO_SECRET_ID"])
    ok(c.is_authenticated(), "Could not log in with the generated AppRole.")
    got = c.secrets.kv.v2.read_secret_version(path=bao["path"], mount_point="secret")["data"]["data"]["url"]
    ok(got == bao["url"], "The secret read with the AppRole doesn't match the stored one.")
    root = hvac.Client(url=os.environ["OPENBAO_ADDR"], token="root")
    root.secrets.kv.v2.create_or_update_secret(path="chk_other/secret", secret={"x": "1"}, mount_point="secret")
    try:
        c.secrets.kv.v2.read_secret_version(path="chk_other/secret", mount_point="secret")
        leaked = True
    except hvac.exceptions.Forbidden:
        leaked = False
    ok(not leaked, "LEAST PRIVILEGE: the API's AppRole could read a secret that is not its own. The policy must allow only `secret/data/<its path>` and only `read`.")
    try:
        c.secrets.kv.v2.create_or_update_secret(path=bao["path"], secret={"url": "hacked"}, mount_point="secret")
        wrote = True
    except hvac.exceptions.Forbidden:
        wrote = False
    ok(not wrote, "The application could WRITE its own secret: it must only have read capability.")


def test_the_api_starts_without_database_url_in_the_environment(bao):
    need("valkey", "redpanda")
    port = free_port()
    env = {"BAO_ROLE_ID": bao["creds"]["BAO_ROLE_ID"], "BAO_SECRET_ID": bao["creds"]["BAO_SECRET_ID"], "BAO_SECRET_PATH": bao["path"], "AUTH_DISABLED": "1", "RATE_LIMIT_PER_MIN": "1000", "KAFKA_TOPIC_ALERTS": unique("chk.x")}
    full = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    import subprocess

    from conftest import ROOT as R

    proc = subprocess.Popen(["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(port), "--log-level", "warning"], cwd=R, env={**full, **env, "PYTHONPATH": str(R)}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        end = time.time() + 40
        status = None
        while time.time() < end:
            try:
                status = httpx.get(f"http://127.0.0.1:{port}/assets", timeout=3).status_code
                if status:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        ok(status == 200, f"Without DATABASE_URL in the environment, but with OpenBao credentials, GET /assets gave {status}. The API must get the connection string from OpenBao (services/common/secrets.py).")
    finally:
        proc.terminate()
        proc.wait(10)


def test_secrets_are_not_in_the_code():
    rx = re.compile(r"plant:plant@|lakesecret|plant-api-secret|password\s*=\s*['\"](?!\s*['\"])[^'\"]{3,}", re.I)
    offenders = []
    for d in ("services", "pipelines", "ml", "genai", "scripts", "db", "deploy", "infra"):
        for f in (ROOT / d).rglob("*"):
            if f.is_file() and f.suffix in {".py", ".ts", ".js", ".yaml", ".yml", ".tf", ".sh", ".json", ".sql", ".md"} and "node_modules" not in f.parts and ".next" not in f.parts and "package-lock" not in f.name:
                try:
                    if rx.search(f.read_text()):
                        offenders.append(str(f.relative_to(ROOT)))
                except UnicodeDecodeError:
                    pass
    ok(not offenders, f"There are credentials written in the code: {offenders[:4]}.\nA secret in git stays in the history forever even if you delete it later. Read them from OpenBao or from the environment.")
    gi = (ROOT / ".gitignore").read_text()
    ok(".secrets.local" in gi and ".env" in gi, "Add `.secrets.local` and `.env` to .gitignore: the file with role_id/secret_id is never committed.")


# ------------------------------------------------------------------ threat model
def test_stride_threat_model():
    f = ROOT / "docs" / "threat-model.md"
    ok(f.exists(), "Missing docs/threat-model.md (STRIDE threat model; see guide/08_security.md).")
    text = f.read_text()
    for h in ("Scope", "Trust boundaries", "STRIDE", "Residual risks"):
        ok(h.lower() in text.lower(), f"The document must have a “{h}” section.")
    rows = [ln for ln in text.splitlines() if re.match(r"\|\s*T-\d+", ln)]
    ok(len(rows) >= 10, f"There are {len(rows)} threats in the table (rows “| T-01 | …”); at least 10 are expected.")
    cats = set()
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        ok(len(cells) >= 6, f"The row “{cells[0]}” must have 6 columns: ID | Component | Threat | STRIDE | Mitigation | Status.")
        ok(len(cells[4]) >= 20, f"The mitigation of {cells[0]} is too vague: say WHICH concrete control reduces it.")
        cats.add(cells[3].strip().upper()[:1])
    ok(set("STRIDE") <= cats, f"Your analysis covers the categories {sorted(cats)}; STRIDE has six: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service and Elevation of privilege.")
    ok(sum(1 for ln in rows if "implemented" in ln.lower()) >= 4, "At least 4 threats must be marked “Implemented” (with the real control you built in this course).")
    res = text.lower().split("residual risks")[-1]
    ok(len(re.findall(r"^\s*(\d+\.|-)\s+\S", res, re.M)) >= 3, "List at least 3 residual risks: what you did NOT solve and accept. A threat model without residual risks is a lie.")
