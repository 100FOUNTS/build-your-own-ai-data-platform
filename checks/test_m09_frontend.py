"""Module 9 — Frontend: React + Next.js + TypeScript (SSR, accessibility, OIDC login, real time)."""

from __future__ import annotations

import json
import re
import shutil

import httpx
import psycopg
import pytest

from conftest import ROOT, free_port, kafka_topics, need, ok, seeded_db, serve, sh, tail, unique

WEB = ROOT / "services" / "web"


@pytest.fixture(scope="module")
def built():
    ok(shutil.which("node") and shutil.which("npm"), "You need Node.js ≥ 20.")
    ok((WEB / "package.json").exists() and (WEB / "package-lock.json").exists(), "Missing services/web/package.json and its package-lock.json (see guide/09_frontend.md).")
    ts = json.loads((WEB / "tsconfig.json").read_text()) if (WEB / "tsconfig.json").exists() else {}
    ok(ts.get("compilerOptions", {}).get("strict") is True, "tsconfig.json must have `\"strict\": true`.")
    pkg = json.loads((WEB / "package.json").read_text())
    ok({"build", "start", "test"} <= set(pkg.get("scripts", {})), "package.json needs the `build`, `start` and `test` scripts.")
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    ok("next" in deps and "react" in deps and "typescript" in deps, "The project must use next, react and typescript.")
    r = sh("npm ci --no-audit --no-fund && npm run build", cwd=WEB, shell=True, timeout=900)
    ok(r.returncode == 0, "`npm ci && npm run build` failed (in Next the build also type-checks TypeScript):\n" + tail(r, 18))
    return True


def test_your_component_tests(built):
    r = sh("npm test", cwd=WEB, shell=True, timeout=300)
    ok(r.returncode == 0, "`npm test` fails:\n" + tail(r, 14))
    m = re.search(r"Tests\s+(\d+) passed", r.stdout + r.stderr)
    ok(m and int(m.group(1)) >= 4, f"At least 4 passing tests are expected (there are {m.group(1) if m else 0}): test pure logic and components (render, empty states, missing data).")


@pytest.fixture(scope="module")
def app(built):
    need("postgres", "valkey", "redpanda")
    topic = unique("chk.web")
    kafka_topics(topic)
    with seeded_db() as url:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("insert into risk_scores (asset_id, feature_date, risk_score, model_version) values ('PLT01-P101','2026-09-29',0.20,'v1'),('PLT01-P101','2026-09-30',0.35,'v1'),('PLT01-P102','2026-09-30',0.90,'v1')")
        api_port, web_port, web_port2 = free_port(), free_port(), free_port()
        api_env = {"DATABASE_URL": url, "AUTH_DISABLED": "1", "RATE_LIMIT_PER_MIN": "1000", "KAFKA_TOPIC_ALERTS": topic}
        with serve(["uv", "run", "uvicorn", "services.api.app.main:app", "--port", str(api_port), "--log-level", "warning"], api_env, f"http://127.0.0.1:{api_port}/health"):
            base = {"API_URL": f"http://127.0.0.1:{api_port}", "AUTH_SECRET": "test-secret-for-the-checks-0123456789", "AUTH_URL": "http://localhost", "KEYCLOAK_ISSUER": "http://localhost:8089/realms/plant"}
            with serve(["npm", "start", "--", "-p", str(web_port)], {**base, "AUTH_DISABLED": "1"}, f"http://127.0.0.1:{web_port}/api/health", cwd=WEB, timeout=60):
                with serve(["npm", "start", "--", "-p", str(web_port2)], {**base, "AUTH_DISABLED": "0"}, f"http://127.0.0.1:{web_port2}/api/health", cwd=WEB, timeout=60):
                    yield {"open": f"http://127.0.0.1:{web_port}", "secured": f"http://127.0.0.1:{web_port2}"}


def test_fleet_page_rendered_on_the_server(app):
    html = re.sub(r"<!--.*?-->", "", httpx.get(app["open"] + "/", timeout=20).text)  # React separates texts with <!-- --> comments
    ok("<caption" in html and 'scope="col"' in html, "The table must be accessible: it has a <caption> and <th scope=\"col\"> headers. A screen reader depends on that.")
    ok('lang="en"' in html, "<html> must declare the language (lang=\"en\").")
    ids = re.findall(r"PLT01-P\d{3}", html)
    ok(ids and ids[0] == "PLT01-P102" and "PLT01-P101" in ids[:4], f"The fleet must already be rendered in the server HTML (SSR) and ordered by risk: P102 (0.90) first. First ids: {ids[:3]}")
    ok(re.search(r'data-level="high"[^>]*>[^<]*High', html) and 'data-level="low"' in html, "Each asset must show its risk level with an element that carries `data-level` (high/medium/low) and the text (High/Medium/Low), not just a color: color alone is not accessible.")
    ok("90%" in html, "The risk must be shown as a percentage (0.90 → 90%).")


def test_detail_page_and_404(app):
    r = httpx.get(app["open"] + "/assets/PLT01-P101", timeout=20)
    ok(r.status_code == 200 and 'role="img"' in r.text and "PLT01-P101" in r.text, "The asset detail must show its risk and an accessible chart (SVG with role=\"img\" and aria-label).")
    ok(httpx.get(app["open"] + "/assets/NO-SUCH-ASSET", timeout=20).status_code == 404, "A nonexistent asset must show the 404 page (notFound()), not a 500 error.")


def test_routes_require_a_session_and_redirect_to_login(app):
    r = httpx.get(app["secured"] + "/", follow_redirects=False, timeout=20)
    ok(r.status_code in (302, 303, 307) and "signin" in r.headers.get("location", "").lower(), f"Without a session, `/` must redirect to the OIDC login (it gave {r.status_code}, location={r.headers.get('location')!r}). Protect the routes with middleware.")
    ok(httpx.get(app["secured"] + "/api/health", timeout=20).status_code == 200, "/api/health must stay public.")
    ok(httpx.get(app["secured"] + "/api/auth/providers", timeout=20).status_code == 200, "The Auth.js providers endpoint (/api/auth/providers) must be available.")
    prov = httpx.get(app["secured"] + "/api/auth/providers", timeout=20).json()
    ok("keycloak" in prov, f"The login must use Keycloak as the OIDC provider (providers: {list(prov)}).")


def test_the_secret_does_not_reach_the_browser(app):
    html = httpx.get(app["open"] + "/", timeout=20).text + httpx.get(app["open"] + "/alerts", timeout=20).text
    ok("plant-api-secret" not in html and "AUTH_SECRET" not in html, "There are secrets in the HTML sent to the browser. Only NEXT_PUBLIC_* variables may reach the client; the access token is passed with care.")
