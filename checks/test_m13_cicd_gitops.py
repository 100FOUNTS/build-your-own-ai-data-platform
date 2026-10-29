"""Module 13 — CI/CD with GitHub Actions, GitOps with Argo CD and a service catalog (Backstage)."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from conftest import ROOT, need, ok, sh, tail, wait_until

WF = ROOT / ".github" / "workflows" / "ci.yml"


# ------------------------------------------------------------------ GitHub Actions
@pytest.fixture(scope="module")
def wf():
    ok(WF.exists(), "Missing .github/workflows/ci.yml (the continuous integration pipeline; see guide/13_cicd_gitops.md).")
    doc = yaml.safe_load(WF.read_text())
    doc["on"] = doc.get("on", doc.get(True))  # YAML 1.1 reads `on:` as True
    return doc


def test_workflow_secure_and_well_built(wf):
    jobs = wf["jobs"]
    ok(len(jobs) >= 5, f"Your pipeline has {len(jobs)} jobs and ≥ 5 are expected (lint, Python tests, web, infrastructure, Kubernetes manifests and image builds).")
    triggers = wf["on"]
    ok("pull_request" in triggers and "push" in triggers, "The workflow must run on `pull_request` (feedback before merging) and on `push` to main.")
    ok(wf.get("permissions", {}).get("contents") == "read", "Declare `permissions: contents: read` at workflow level: the CI token must have least privilege, not the (broad) default.")
    ok(wf.get("concurrency", {}).get("cancel-in-progress") is True, "Add `concurrency` with cancel-in-progress: if you push two commits in a row, the first one no longer matters.")
    for name, j in jobs.items():
        ok(j.get("timeout-minutes"), f"The job `{name}` has no timeout-minutes: a hung job burns CI minutes without limit.")
    text = WF.read_text()
    bad = re.findall(r"uses:\s*\S+@(main|master|latest)\b", text)
    ok(not bad, "Some actions are pinned to a branch (@main/@master/@latest): any change by the author gets into your pipeline without you reviewing it. Pin to a version (@v4) or a SHA.")
    ok("astral-sh/setup-uv" in text and "cache" in text, "Use the dependency cache (setup-uv with enable-cache, setup-node with cache): a slow pipeline is a pipeline people avoid.")


def test_images_build_in_parallel_and_only_if_everything_passes(wf):
    b = [j for j in wf["jobs"].values() if "docker" in json.dumps(j).lower() and "build" in json.dumps(j).lower() and j.get("strategy")]
    ok(b, "Missing an image build job with `strategy.matrix` (api, gateway, web): one definition, three parallel runs.")
    j = b[0]
    ok(set(j["strategy"]["matrix"].get("service", j["strategy"]["matrix"].get("svc", []))) >= {"api", "gateway", "web"}, "The matrix must cover api, gateway and web.")
    ok(j.get("needs") and len(j["needs"]) >= 3, "The image build must depend (`needs`) on lint and tests: there is no point building what doesn't pass the checks.")


def test_actionlint_finds_no_errors():
    r = sh(["docker", "run", "--rm", "-v", f"{ROOT}:/repo", "-w", "/repo", "rhysd/actionlint:1.7.12", "-color=false"], timeout=180)
    ok(r.returncode == 0, "actionlint (the workflow linter) finds errors:\n" + tail(r, 16))


def test_act_can_list_the_jobs():
    ok(shutil.which("act"), "Install act to run GitHub Actions locally: `brew install act`.")
    r = sh(["act", "-l", "-W", str(WF)], timeout=120)
    ok(r.returncode == 0 and "lint" in r.stdout.lower(), "`act -l` can't read your workflow:\n" + tail(r))


# ------------------------------------------------------------------ service catalog
def test_backstage_catalog_consistent():
    f = ROOT / "catalog-info.yaml"
    ok(f.exists(), "Missing catalog-info.yaml at the root (it describes your services for the Backstage internal portal).")
    docs = [d for d in yaml.safe_load_all(f.read_text()) if d]
    by_kind: dict[str, dict] = {}
    for d in docs:
        ok(d.get("apiVersion") == "backstage.io/v1alpha1", f"Invalid apiVersion in {d.get('metadata', {}).get('name')}: it must be backstage.io/v1alpha1.")
        ok(re.fullmatch(r"[a-zA-Z0-9]([-_.a-zA-Z0-9]{0,61}[a-zA-Z0-9])?", d["metadata"]["name"]), f"Invalid entity name: {d['metadata']['name']}")
        by_kind.setdefault(d["kind"], {})[d["metadata"]["name"]] = d
    comps = by_kind.get("Component", {})
    ok(len(comps) >= 4 and by_kind.get("API") and len(by_kind.get("Resource", {})) >= 3 and by_kind.get("System"), "Expected: 1 System, ≥ 4 Component, ≥ 1 API and ≥ 3 Resource (postgres, kafka…).")
    for n, c in comps.items():
        for field in ("type", "lifecycle", "owner"):
            ok(c["spec"].get(field), f"The component {n} needs spec.{field}: without an owner nobody knows who to call at 3 a.m.")
        ok(c["spec"].get("system"), f"The component {n} must belong to a System.")
    known = {(k.lower(), n) for k, v in by_kind.items() for n in v}

    def check_ref(ref: str, where: str):
        m = re.match(r"(?:(\w+):)?(?:[\w-]+/)?([\w.-]+)$", ref)
        kind, name = (m.group(1) or "component").lower(), m.group(2)
        ok((kind, name) in known, f"{where} references `{ref}`, which doesn't exist in the catalog (dangling reference).")

    for n, c in comps.items():
        for ref in c["spec"].get("dependsOn", []):
            check_ref(ref, f"{n}.dependsOn")
        for ref in c["spec"].get("providesApis", []) + c["spec"].get("consumesApis", []):
            check_ref(f"api:{ref}", f"{n} (APIs)")
    ok(any(c["spec"].get("providesApis") for c in comps.values()) and any(c["spec"].get("consumesApis") for c in comps.values()), "Model the API relationship: one component provides it (providesApis) and another consumes it (consumesApis).")
    api = next(iter(by_kind["API"].values()))
    ok(api["spec"].get("type") == "openapi" and api["spec"].get("definition"), "The API entity must be of type openapi and include its definition (spec.definition).")


# ------------------------------------------------------------------ GitOps (Argo CD)
def test_argo_application_well_formed():
    f = ROOT / "deploy" / "argocd" / "application.yaml"
    ok(f.exists(), "Missing deploy/argocd/application.yaml (the Argo CD “Application”).")
    app = yaml.safe_load(f.read_text())
    ok(app["kind"] == "Application" and app["apiVersion"].startswith("argoproj.io/"), "The resource must be an argoproj.io Application.")
    s = app["spec"]
    ok(s["source"]["repoURL"].endswith("/gitops/plant-deploy.git") and s["source"]["targetRevision"] == "main", "source.repoURL must point to the GitOps repo (http://forgejo:3000/gitops/plant-deploy.git, visible from inside the cluster) and targetRevision: main.")
    ok((ROOT / s["source"]["path"]).exists(), f"source.path `{s['source']['path']}` doesn't exist in your repository.")
    auto = s.get("syncPolicy", {}).get("automated", {})
    ok(auto.get("prune") is True and auto.get("selfHeal") is True, "syncPolicy.automated must have prune: true and selfHeal: true. selfHeal reverts manual changes: the repository is the single source of truth.")
    ok(s["destination"]["namespace"] == "plant", "The destination must be the plant namespace.")


@pytest.fixture(scope="module")
def gitops():
    need("forgejo")
    ok(shutil.which("kind") and shutil.which("kubectl"), "You need kind and kubectl.")
    ok("plant" in sh(["kind", "get", "clusters"]).stdout.split(), "The kind cluster “plant” doesn't exist: complete module 12 (`make kind-up`).")
    for svc in ("api", "gateway", "web"):
        ok(sh(["docker", "image", "inspect", f"plant-{svc}:dev"]).returncode == 0, f"Missing the image plant-{svc}:dev (module 12: build the images and load them into kind).")
        sh(["kind", "load", "docker-image", f"plant-{svc}:dev", "--name", "plant"], timeout=300)
    r = sh(["bash", "platform/tools/gitops_up.sh"], timeout=600)
    ok(r.returncode == 0, "Could not set up Argo CD + Forgejo:\n" + tail(r))
    sh(["bash", "platform/tools/k8s_secrets.sh"])
    p = sh(["bash", "platform/tools/gitops_push.sh"], timeout=120)
    ok(p.returncode == 0, "Could not publish deploy/k8s to the GitOps repository:\n" + tail(p))
    k = ["kubectl", "--context", "kind-plant"]
    sh([*k, "delete", "-f", "deploy/argocd/application.yaml", "--ignore-not-found", "--wait=true"], timeout=240)
    sh([*k, "delete", "namespace", "plant", "--ignore-not-found", "--wait=true"], timeout=240)
    sh(["bash", "platform/tools/k8s_secrets.sh"])
    a = sh([*k, "apply", "-f", "deploy/argocd/application.yaml"])
    ok(a.returncode == 0, "Could not apply the Application:\n" + tail(a))
    app = yaml.safe_load((ROOT / "deploy/argocd/application.yaml").read_text())["metadata"]["name"]
    yield {"k": k, "app": app}
    sh([*k, "delete", "-f", "deploy/argocd/application.yaml", "--ignore-not-found", "--wait=false"])


def status(g):
    r = sh([*g["k"], "-n", "argocd", "get", "application", g["app"], "-o", "json"])
    if r.returncode != 0:
        return {}
    return json.loads(r.stdout).get("status", {})


def test_argo_syncs_and_leaves_the_deployment_healthy(gitops):
    def done():
        st = status(gitops)
        return st.get("sync", {}).get("status") == "Synced" and st.get("health", {}).get("status") == "Healthy"

    good = wait_until(done, 360, 5)
    st = status(gitops)
    ok(good, f"Argo CD didn't get the application to Synced + Healthy within 6 minutes (sync={st.get('sync', {}).get('status')}, health={st.get('health', {}).get('status')}).\n"
       f"Conditions: {json.dumps(st.get('conditions', []))[:500]}\nCheck: can Argo reach http://forgejo:3000? does the Secret plant-secrets exist? are the images loaded into kind?")


def test_self_heal_reverts_manual_changes(gitops):
    k = gitops["k"]
    sh([*k, "-n", "plant", "scale", "deployment/plant-web", "--replicas=3"])
    def reverted():
        r = sh([*k, "-n", "plant", "get", "deployment", "plant-web", "-o", "jsonpath={.spec.replicas}"])
        return r.stdout.strip() == "1"

    ok(wait_until(reverted, 150, 5), "I scaled plant-web to 3 replicas by hand and Argo CD did NOT revert it within 150 s. With selfHeal the cluster goes back to the git state on its own: nobody “fixes” production with kubectl.")


def test_a_change_in_git_reaches_the_cluster(gitops):
    k = gitops["k"]
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "deploy", tmp / "deploy")
    gw = tmp / "deploy" / "k8s" / "base" / "gateway.yaml"
    txt = gw.read_text()
    ok("replicas: 1" in txt, "Cannot find `replicas: 1` in deploy/k8s/base/gateway.yaml (the check changes it to 2 through a commit).")
    gw.write_text(txt.replace("replicas: 1", "replicas: 2", 1))
    p = sh(["bash", "platform/tools/gitops_push.sh", str(tmp / "deploy")])
    ok(p.returncode == 0, "Could not publish the change:\n" + tail(p))
    sh([*k, "-n", "argocd", "annotate", "application", gitops["app"], "argocd.argoproj.io/refresh=hard", "--overwrite"])
    got = wait_until(lambda: sh([*k, "-n", "plant", "get", "deployment", "plant-gateway", "-o", "jsonpath={.spec.replicas}"]).stdout.strip() == "2", 240, 5)
    sh(["bash", "platform/tools/gitops_push.sh"])  # restores the original state
    sh([*k, "-n", "argocd", "annotate", "application", gitops["app"], "argocd.argoproj.io/refresh=hard", "--overwrite"])
    shutil.rmtree(tmp, ignore_errors=True)
    ok(got, "I made a commit that changes plant-gateway to 2 replicas and the cluster didn't apply it within 4 minutes. GitOps: a deployment is a `git push`.")
