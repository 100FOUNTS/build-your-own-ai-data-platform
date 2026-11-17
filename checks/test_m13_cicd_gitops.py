"""Module 13 — CI/CD with GitHub Actions (including supply-chain security), GitOps with Argo CD and a service catalog (Backstage)."""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml
from conftest import ROOT, free_port, need, ok, sh, tail, wait_until
from expected import M13_CANARY_OVERLAY, M13_KYVERNO_CHART, M13_LAB_CLUSTER, M13_ROLLOUTS_VERSION, M13_STUB_TAGS

WF = ROOT / ".github" / "workflows" / "ci.yml"
SC_SCRIPT = ROOT / "scripts" / "supply_chain.sh"
# Throwaway local OCI registry used by the dynamic supply-chain checks (pinned by tag AND digest, like everything else here).
REGISTRY_IMAGE = "registry:3.1.2@sha256:82f6ff4a7f982960c8aa51617fea3aabc485df6e55cce80d22deb1de36f1bc3c"
# Trivy releases that shipped an infostealer on 19-23 March 2026 (CVE-2026-33634, GHSA-69fq-xp46-6x23).
COMPROMISED_TRIVY = ("0.69.4", "0.69.5", "0.69.6")
FIX = ROOT / "checks" / "m13_fixtures"
CODE_SCAN_SCRIPT = ROOT / "scripts" / "code_scan.sh"


# ------------------------------------------------------------------ GitHub Actions
@pytest.fixture(scope="module")
def wf():
    ok(WF.exists(), "Missing .github/workflows/ci.yml (the continuous integration pipeline; see guide/13_cicd_gitops.md).")
    doc = yaml.safe_load(WF.read_text())
    doc["on"] = doc.get("on", doc.get(True))  # YAML 1.1 reads `on:` as True
    return doc


def test_workflow_secure_and_well_built(wf):
    jobs = wf["jobs"]
    ok(len(jobs) >= 6, f"Your pipeline has {len(jobs)} jobs and ≥ 6 are expected (lint, Python tests, web, infrastructure, Kubernetes manifests, image builds and the supply-chain job).")
    triggers = wf["on"]
    ok("pull_request" in triggers and "push" in triggers, "The workflow must run on `pull_request` (feedback before merging) and on `push` to main.")
    ok(wf.get("permissions", {}).get("contents") == "read", "Declare `permissions: contents: read` at workflow level: the CI token must have least privilege, not the (broad) default.")
    ok(wf.get("concurrency", {}).get("cancel-in-progress") is True, "Add `concurrency` with cancel-in-progress: if you push two commits in a row, the first one no longer matters.")
    for name, j in jobs.items():
        ok(j.get("timeout-minutes"), f"The job `{name}` has no timeout-minutes: a hung job burns CI minutes without limit.")
    text = WF.read_text()
    bad = re.findall(r"uses:\s*\S+@(main|master|latest)\b", text)
    ok(not bad, "Some actions are pinned to a branch (@main/@master/@latest): any change by the author gets into your pipeline without you reviewing it. Pin to a version, and third-party actions to a full commit SHA (see the supply-chain section of the guide).")
    ok("astral-sh/setup-uv" in text and "cache" in text, "Use the dependency cache (setup-uv with enable-cache, setup-node with cache): a slow pipeline is a pipeline people avoid.")


def test_images_build_in_parallel_and_only_if_everything_passes(wf):
    b = [j for j in wf["jobs"].values() if "docker" in json.dumps(j).lower() and "build" in json.dumps(j).lower() and j.get("strategy")]
    ok(b, "Missing an image build job with `strategy.matrix` (api, gateway, web): one definition, three parallel runs.")
    j = b[0]
    ok(set(j["strategy"]["matrix"].get("service", j["strategy"]["matrix"].get("svc", []))) >= {"api", "gateway", "web"}, "The matrix must cover api, gateway and web.")
    ok(j.get("needs") and len(j["needs"]) >= 3, "The image build must depend (`needs`) on lint and tests: there is no point building what doesn't pass the checks.")


# ------------------------------------------------------------------ supply chain (static)
def job_block(name: str) -> str:
    """Raw text of one job of the workflow (from `  <name>:` to the next job), so comments and quoting are kept as written."""
    lines = WF.read_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(rf"^  {re.escape(name)}:\s*$", ln)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^  [\w.-]+:\s*$", lines[i]) or re.match(r"^\S", lines[i])), len(lines))
    return "\n".join(lines[start:end])


def supply_chain_job(wf) -> tuple[str, str]:
    name = next((n for n in wf["jobs"] if "supply" in n.lower()), None)
    ok(name, "Missing the supply-chain job (a job whose id contains `supply`, e.g. `supply-chain`): build the API image, scan it, make an SBOM, sign it and verify the signature. See the supply-chain section of guide/13_cicd_gitops.md.")
    text = job_block(name)
    if SC_SCRIPT.exists() and "supply_chain" in text:
        text += "\n" + SC_SCRIPT.read_text()  # the job may delegate to the script: judge both together
    return name, text


TRIVY_LATEST = "Trivy is tracking `latest`: whatever is published next runs in your pipeline, with your secrets."


def trivy_problem(text: str) -> str | None:
    """Returns why Trivy is NOT pinned to an exact version/digest, or None if it is."""
    low = text.lower()
    bad = [v for v in COMPROMISED_TRIVY if re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", low)]
    if bad:
        return f"You pin Trivy {bad[0]}, one of the malicious releases of March 2026 (CVE-2026-33634). Use a release published outside that incident."
    lines = text.splitlines()
    semver = r"v?\d+\.\d+\.\d+\b"
    found = False
    for i, ln in enumerate(lines):
        low_ln = ln.lower()
        if re.search(r"trivy[^\n]*@(main|master|latest)\b|trivy[^\n]*\blatest\b", low_ln):
            return TRIVY_LATEST
        image = re.search(r"(?:aquasec|aquasecurity)/trivy:(\S+)", low_ln)
        if image:
            found = found or "@sha256:" in image.group(1) or bool(re.match(semver, image.group(1)))
        elif re.search(r"uses:\s*aquasecurity/(setup-trivy|trivy-action)@", low_ln):
            step = []  # the `with:` of THIS step only (up to the next `- ` item)
            for nxt in lines[i + 1:]:
                if re.match(r"\s*- ", nxt):
                    break
                step.append(nxt)
            m = re.search(r"(?<![\w-])version:\s*['\"]?([\w.-]+)", "\n".join(step))
            if m and m.group(1).lower() == "latest":
                return TRIVY_LATEST
            found = found or bool(m and re.fullmatch(semver, m.group(1)))
        elif "trivy" in low_ln and "uses:" not in low_ln and re.search(semver, low_ln):
            found = True
    if found:
        return None
    return "Trivy is not pinned to an exact version or digest (an image tag like `aquasec/trivy:X.Y.Z`, `@sha256:…`, or the `version:` input of the setup action). A moving Trivy is exactly what was attacked in March 2026."


def test_third_party_actions_pinned_by_full_sha(wf):
    refs = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", WF.read_text(), flags=re.M)
    loose = []
    for r in refs:
        if r.startswith("./") or (r.startswith("docker://") and "@sha256:" in r):
            continue
        if r.split("/")[0] != "actions" and not re.search(r"@[0-9a-f]{40}$", r):
            loose.append(r)
    ok(not loose, "These third-party actions are not pinned to a full 40-character commit SHA: " + ", ".join(loose) + ".\n"
       "A tag can be moved by whoever controls the repository (in March 2026 attackers moved 76 of the 77 tags of trivy-action to malicious code). A commit SHA cannot.")


def test_supply_chain_job_covers_the_whole_chain(wf):
    name, text = supply_chain_job(wf)
    low = text.lower()
    ok(re.search(r"docker build|docker/build-push-action|buildx build", low) and "services/api" in low, f"`{name}` must build the API image itself (services/api/Dockerfile): you can only scan and sign what you just built.")
    ok("trivy" in low, f"`{name}` doesn't scan the image with Trivy.")
    problem = trivy_problem(text)
    ok(problem is None, problem or "")
    ok(re.search(r"exit-code['\"]?\s*[:=]?\s*['\"]?1\b", low), "The Trivy scan must FAIL the job when it finds something (its exit code must be 1): a scan that only prints a report blocks nothing.")
    ok("critical" in low and "ignore-unfixed" in low, "Gate on CRITICAL vulnerabilities that already have a fix (`--ignore-unfixed`): you can act on those; blocking on what nobody has patched only teaches the team to ignore the gate.")
    ok("syft" in low and re.search(r"cyclonedx|spdx", low), "Generate an SBOM with Syft in a standard format (CycloneDX or SPDX JSON): it is the ingredient list that lets you answer “are we affected?” the day a CVE like CVE-2026-33634 lands.")
    ok("actions/upload-artifact" in text, "Keep the SBOM (and the scan report) as a build artifact with actions/upload-artifact: an SBOM nobody can retrieve is useless.")
    ok(re.search(r"cosign\s+sign\b", low) and re.search(r"cosign\s+verify\b", low), "Sign the image with cosign and then VERIFY the signature in the same job: a signature nobody verifies protects nothing.")
    ok("--key" in low and "${{ secrets." in low, "Sign with a key that comes from a secret (`${{ secrets.… }}`) and pass it to cosign with --key: the private key must never be a file in the repository.")


def test_signing_key_never_lives_in_the_repo():
    skip_dirs = {".git", ".venv", "node_modules", "data", "checks", ".ruff_cache", ".pytest_cache", "__pycache__", ".next", "notebooks"}
    marker = re.compile(r"BEGIN (?:ENCRYPTED )?(?:SIGSTORE |RSA |EC |OPENSSH )?PRIVATE KEY")
    leaks = []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for f in files:
            p = Path(dirpath) / f
            if f.endswith((".key", ".pem")):
                if sh(["git", "check-ignore", "-q", str(p)]).returncode != 0:
                    leaks.append(str(p.relative_to(ROOT)))
                continue
            try:
                if p.stat().st_size < 2_000_000 and marker.search(p.read_text(errors="ignore")):
                    leaks.append(str(p.relative_to(ROOT)))
            except OSError:
                pass
    ok(not leaks, "A private key is inside the repository (or in a file git would commit): " + ", ".join(leaks) + ".\n"
       "Whoever can read the repo could sign malicious images as you. Keep the key outside the repo (or git-ignored) and give it to CI as a secret.")


def test_actionlint_finds_no_errors():
    r = sh(["docker", "run", "--rm", "-v", f"{ROOT}:/repo", "-w", "/repo", "rhysd/actionlint:1.7.12", "-color=false"], timeout=180)
    ok(r.returncode == 0, "actionlint (the workflow linter) finds errors:\n" + tail(r, 16))


def test_act_can_list_the_jobs():
    ok(shutil.which("act"), "Install act to run GitHub Actions locally: `brew install act`.")
    r = sh(["act", "-l", "-W", str(WF)], timeout=120)
    ok(r.returncode == 0 and "lint" in r.stdout.lower(), "`act -l` can't read your workflow:\n" + tail(r))


# ------------------------------------------------------------------ supply chain (dynamic: real tools, local registry)
INSTALL_MSG = "The dynamic supply-chain checks need trivy, syft and cosign (and Docker): `brew install trivy syft cosign`. This part is skipped until they are installed, but the module is not done without it."
ACCEPT = "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json"
API_TAG = "plant-api:m13-check"


@pytest.fixture(scope="module")
def tools():
    missing = [t for t in ("trivy", "syft", "cosign", "docker") if not shutil.which(t)]
    if missing:
        pytest.skip(f"missing: {', '.join(missing)}. {INSTALL_MSG}")
    ok(sh(["docker", "info"]).returncode == 0, "Docker is not running (the supply-chain checks start a throwaway registry and build images).")
    ok(SC_SCRIPT.exists(), "Missing scripts/supply_chain.sh. The check calls `scripts/supply_chain.sh <image>` with REGISTRY, OUT_DIR, COSIGN_PRIVATE_KEY and COSIGN_PASSWORD in the environment (see the contract in guide/13_cicd_gitops.md).")


@pytest.fixture(scope="module")
def registry(tools):
    port = free_port()
    name = f"chk-m13-registry-{port}"
    r = sh(["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:5000", REGISTRY_IMAGE], timeout=300)
    ok(r.returncode == 0, "Could not start the throwaway registry:\n" + tail(r))
    addr = f"localhost:{port}"

    def up():
        try:
            return httpx.get(f"http://{addr}/v2/", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    try:
        ok(wait_until(up, 30), "The throwaway registry did not answer in time.")
        yield addr
    finally:
        sh(["docker", "stop", "-t", "2", name])


@pytest.fixture(scope="module")
def keys(tools):
    d = Path(tempfile.mkdtemp(prefix="chk-m13-keys-"))
    env = {"COSIGN_PASSWORD": "chk-" + os.urandom(6).hex()}
    for prefix in ("main", "other"):
        r = sh(["cosign", "generate-key-pair", "--output-key-prefix", prefix], env=env, cwd=d)
        ok(r.returncode == 0, "cosign could not generate the check's key pair:\n" + tail(r))
    yield {"priv": (d / "main.key").read_text(), "pub": str(d / "main.pub"), "other_pub": str(d / "other.pub"), "other_key": str(d / "other.key"),
           "pub_pem": (d / "main.pub").read_text(), "password": env["COSIGN_PASSWORD"]}
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def api_image(tools):
    ok((ROOT / "services" / "api" / "Dockerfile").exists(), "Missing services/api/Dockerfile (module 12): the supply-chain job works on the API image.")
    r = sh(["docker", "build", "--pull", "-f", "services/api/Dockerfile", "-t", API_TAG, "."], timeout=1500)
    ok(r.returncode == 0, "`docker build` of the API image failed:\n" + tail(r, 14))
    return API_TAG


def run_script(image: str, registry: str, out: Path, key: str | None, password: str = "") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COSIGN_")}
    env.update({"REGISTRY": registry, "OUT_DIR": str(out), "COSIGN_PASSWORD": password})
    if key is not None:
        env["COSIGN_PRIVATE_KEY"] = key
    return subprocess.run(["bash", str(SC_SCRIPT), image], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900, check=False)


def digest_in_registry(registry: str, name: str, tag: str) -> str | None:
    try:
        r = httpx.head(f"http://{registry}/v2/{name}/manifests/{tag}", headers={"Accept": ACCEPT}, timeout=10)
    except httpx.HTTPError:
        return None
    return r.headers.get("docker-content-digest") if r.status_code == 200 else None


def fixable_criticals(image: str) -> list[str]:
    """Independent truth: a fresh Trivy run, CRITICAL with a fix available."""
    r = sh(["trivy", "image", "--quiet", "--format", "json", "--severity", "CRITICAL", "--ignore-unfixed", image], timeout=600)
    ok(r.returncode == 0, "The check's own Trivy scan failed (does it have network access to download the vulnerability DB?):\n" + tail(r))
    return [f"{v['VulnerabilityID']} ({v['PkgName']} → {v['FixedVersion']})" for res in json.loads(r.stdout).get("Results", []) for v in res.get("Vulnerabilities") or [] if v.get("FixedVersion")]


@pytest.fixture(scope="module")
def chain(registry, keys, api_image):
    out = Path(tempfile.mkdtemp(prefix="chk-m13-out-"))
    proc = run_script(api_image, registry, out, keys["priv"], keys["password"])
    name, tag = api_image.split(":")
    yield {"proc": proc, "out": out, "registry": registry, "name": name, "tag": tag, "digest": digest_in_registry(registry, name, tag), "image": api_image}
    shutil.rmtree(out, ignore_errors=True)


def signed(chain):
    """The checks below need the script to have succeeded on the API image."""
    p = chain["proc"]
    truth = fixable_criticals(chain["image"])
    ok(p.returncode == 0 or truth, "scripts/supply_chain.sh failed on the API image although the image has no fixable CRITICAL vulnerabilities:\n" + tail(p, 14))
    ok(p.returncode == 0, "The API image has fixable CRITICAL vulnerabilities, so the gate rightly blocks it: " + "; ".join(truth[:3]) + ".\nFix the image (newer base image? `docker build --pull`; packages you don't need?) and run again.")
    ok(chain["digest"], "The script finished but the image is not in the registry: it must push `<REGISTRY>/<image name>:<tag>` before signing (a signature lives next to the image).")
    return chain


def test_gate_blocks_a_vulnerable_image_and_publishes_nothing(registry, keys):
    # An old, end-of-life base on purpose: it has fixable CRITICAL vulnerabilities and always will.
    img = "plant-vulnerable:m13-check"
    b = subprocess.run(["docker", "build", "-q", "-t", img, "-"], input='FROM alpine:3.10.0\nCMD ["true"]\n', capture_output=True, text=True, timeout=600, check=False)
    ok(b.returncode == 0, "The check could not build its deliberately vulnerable test image:\n" + tail(b))
    out = Path(tempfile.mkdtemp(prefix="chk-m13-vuln-"))
    try:
        ok(fixable_criticals(img), "Check bug: the deliberately vulnerable image has no fixable CRITICAL vulnerabilities any more.")
        p = run_script(img, registry, out, keys["priv"], keys["password"])
        ok(p.returncode != 0, "I fed the script an image with known fixable CRITICAL vulnerabilities and it exited 0. The scan must fail the job: that is the whole point of a gate.")
        report = out / "trivy.json"
        ok(report.exists(), "The gate failed but left no `trivy.json` in OUT_DIR: whoever has to fix it needs the report (write it before failing).")
        found = [v for res in json.loads(report.read_text()).get("Results", []) for v in res.get("Vulnerabilities") or [] if v.get("Severity") == "CRITICAL" and v.get("FixedVersion")]
        ok(found, "trivy.json doesn't list the fixable CRITICAL vulnerabilities of the image: is it really a scan of the image you were given?")
        tags = httpx.get(f"http://{registry}/v2/plant-vulnerable/tags/list", timeout=10)
        listed = (tags.json().get("tags") or []) if tags.status_code == 200 else []
        ok(not listed, f"The image failed the gate but it still reached the registry ({listed}): nothing that fails the scan may be published.")
    finally:
        shutil.rmtree(out, ignore_errors=True)
        sh(["docker", "rmi", "-f", img])


def test_script_refuses_to_run_without_a_signing_key(registry, api_image):
    out = Path(tempfile.mkdtemp(prefix="chk-m13-nokey-"))
    try:
        p = run_script(api_image, registry, out, None)
        ok(p.returncode != 0, "I ran the script with no COSIGN_PRIVATE_KEY and it exited 0: an unsigned image must never look like a successful supply-chain run.")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_script_scans_publishes_and_verifies_the_api_image(chain):
    c = signed(chain)
    ok("verif" in (c["proc"].stdout + c["proc"].stderr).lower(), "The script should verify the signature it just made (cosign verify prints what it checked): a pipeline that signs without verifying proves nothing.")


def test_sbom_lists_the_real_packages(chain):
    c = signed(chain)
    f = c["out"] / "sbom.json"
    ok(f.exists(), "Missing OUT_DIR/sbom.json (the SBOM of the image, CycloneDX or SPDX JSON).")
    try:
        sbom = json.loads(f.read_text())
    except ValueError:
        ok(False, "sbom.json is not valid JSON.")
    if sbom.get("bomFormat") == "CycloneDX":
        comps = sbom.get("components") or []
        names = {x["name"] for x in comps if x.get("name")}
        purls = [x.get("purl", "") for x in comps]
    elif str(sbom.get("spdxVersion", "")).startswith("SPDX-"):
        pk = sbom.get("packages") or []
        names = {x["name"] for x in pk if x.get("name")}
        purls = [r["referenceLocator"] for x in pk for r in x.get("externalRefs", []) if r.get("referenceType") == "purl"]
    else:
        ok(False, "sbom.json is neither CycloneDX (`bomFormat`) nor SPDX (`spdxVersion`) JSON: use a standard format so other tools can read it.")

    def norm(n: str) -> str:
        return re.sub(r"[-_.]+", "-", n.lower())

    have = {norm(n) for n in names}
    req = ROOT / "services" / "api" / "requirements.txt"
    ok(req.exists(), "Missing services/api/requirements.txt (module 12).")
    wanted = {norm(m.group(1)) for ln in req.read_text().splitlines() if not ln.lstrip().startswith(("#", "-")) and (m := re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", ln))}
    missing = sorted(wanted - have)
    ok(wanted and not missing, f"The SBOM doesn't list the API's own dependencies ({', '.join(missing[:6])}…): an SBOM must describe what is INSIDE the image, not a guess.")
    os_pkgs = [p for p in purls if p.startswith(("pkg:deb/", "pkg:apk/", "pkg:rpm/"))]
    ok(len(os_pkgs) >= 10, f"The SBOM has only {len(os_pkgs)} operating-system packages: it looks like a scan of a folder, not of the image. Most CVEs live in the base image's packages, so they must be in the list.")
    r = sh(["syft", c["image"], "-o", "json", "-q"], timeout=600)
    ok(r.returncode == 0, "The check's own syft run failed:\n" + tail(r))
    truth = {norm(a["name"]) for a in json.loads(r.stdout).get("artifacts", [])}
    lost = truth - have
    ok(len(lost) <= 0.15 * len(truth), f"Your SBOM misses {len(lost)} of the {len(truth)} packages that are really in the image (e.g. {', '.join(sorted(lost)[:5])}).")


def test_trivy_report_is_a_real_scan_of_this_image(chain):
    c = signed(chain)
    f = c["out"] / "trivy.json"
    ok(f.exists(), "Missing OUT_DIR/trivy.json (the Trivy report in JSON: keep it as evidence of what was scanned).")
    try:
        rep = json.loads(f.read_text())
    except ValueError:
        ok(False, "trivy.json is not valid JSON (--format json --output …).")
    ok(rep.get("ArtifactType") == "container_image" and "plant-api" in rep.get("ArtifactName", ""), f"trivy.json is not a scan of the API image (ArtifactName: {rep.get('ArtifactName')!r}, ArtifactType: {rep.get('ArtifactType')!r}).")
    ok(rep.get("Metadata", {}).get("OS", {}).get("Family"), "trivy.json has no operating-system metadata: the scan didn't look inside the image.")
    layers = json.loads(sh(["docker", "image", "inspect", c["image"], "--format", "{{json .RootFS.Layers}}"]).stdout)
    ok(rep["Metadata"].get("DiffIDs") == layers, "The layers in trivy.json are not the layers of the image you built: you scanned a different (older? other?) image than the one you published.")
    used = rep.get("Trivy", {}).get("Version", "")
    ok(used and used not in COMPROMISED_TRIVY, f"The report says it was produced by Trivy {used or '?'}: that release is one of the malicious ones of March 2026 (CVE-2026-33634).")
    left = [v["VulnerabilityID"] for res in rep.get("Results", []) for v in res.get("Vulnerabilities") or [] if v.get("Severity") == "CRITICAL" and v.get("FixedVersion")]
    ok(not left, f"The report still lists fixable CRITICAL vulnerabilities ({', '.join(left[:3])}) yet the job passed.")


def cosign_verify(pub: str, ref: str) -> subprocess.CompletedProcess:
    return sh(["cosign", "verify", "--key", pub, "--insecure-ignore-tlog=true", ref], timeout=120)


def test_signature_verifies_with_the_public_key(chain, keys):
    c = signed(chain)
    ref = f"{c['registry']}/{c['name']}@{c['digest']}"
    v = cosign_verify(keys["pub"], ref)
    ok(v.returncode == 0, f"`cosign verify --key <public key> {ref}` fails: the image is not signed with the key given in COSIGN_PRIVATE_KEY (or the signature isn't in the registry next to the image).\n" + tail(v))
    payload = json.loads(v.stdout[v.stdout.index("["):])
    ok(payload[0]["critical"]["image"]["docker-manifest-digest"] == c["digest"], "The signature is valid but it covers a different digest than the image in the registry.")


def new_bundle_problem(c: dict) -> str | None:
    """Why the signature is NOT in the classic layout Kyverno 1.19 reads (cosign v3 writes a new bundle by default), or None."""
    hexd = c["digest"].split(":", 1)[1]
    try:
        tags = httpx.get(f"http://{c['registry']}/v2/{c['name']}/tags/list", timeout=10).json().get("tags") or []
        ref = httpx.get(f"http://{c['registry']}/v2/{c['name']}/referrers/{c['digest']}", headers={"Accept": "application/vnd.oci.image.index.v1+json"}, timeout=10)
        referrers = ref.json().get("manifests", []) if ref.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None  # cannot tell: the other checks will show what is wrong
    if f"sha256-{hexd}.sig" in tags:
        return None
    if f"sha256-{hexd}" in tags or any("sigstore" in m.get("artifactType", "") for m in referrers):
        return ("The image was signed in cosign v3's NEW BUNDLE FORMAT (an OCI referrer, no `sha256-<digest>.sig` tag). `cosign verify` accepts it, but Kyverno 1.19 cannot read it: "
                "the cluster would refuse your own image with “no signatures found”.\nRe-sign with `--new-bundle-format=false` in the `cosign sign` command of scripts/supply_chain.sh (and `--use-signing-config=false`, which cosign v3 needs to allow it).")
    return None


def test_signature_is_in_the_layout_kyverno_reads(chain):
    c = signed(chain)
    problem = new_bundle_problem(c)
    ok(problem is None, problem or "")


def test_verification_fails_with_another_key(chain, keys):
    c = signed(chain)
    v = cosign_verify(keys["other_pub"], f"{c['registry']}/{c['name']}@{c['digest']}")
    ok(v.returncode != 0, "A different public key verified the signature: the signature is not tied to your key.")


def test_verification_fails_for_unsigned_and_retagged_images(chain, keys):
    c = signed(chain)
    reg, name = c["registry"], c["name"]
    # 1. a tampered image that was never signed
    b = subprocess.run(["docker", "build", "-q", "-t", f"{reg}/{name}:evil", "-"], input=f"FROM {c['image']}\nLABEL evil=1\n", capture_output=True, text=True, timeout=300, check=False)
    ok(b.returncode == 0, "The check could not build its tampered image:\n" + tail(b))
    sh(["docker", "push", "-q", f"{reg}/{name}:evil"])
    evil_digest = digest_in_registry(reg, name, "evil")
    ok(evil_digest and cosign_verify(keys["pub"], f"{reg}/{name}@{evil_digest}").returncode != 0, "cosign verify PASSED on an image nobody signed: signatures must be checked against the exact content.")
    # 2. someone re-points the signed tag to different content: verifying the tag must now fail
    sh(["docker", "tag", f"{reg}/{name}:evil", f"{reg}/{name}:{c['tag']}"])
    sh(["docker", "push", "-q", f"{reg}/{name}:{c['tag']}"])
    ok(digest_in_registry(reg, name, c["tag"]) == evil_digest, "Check bug: could not re-point the tag in the registry.")
    ok(cosign_verify(keys["pub"], f"{reg}/{name}:{c['tag']}").returncode != 0, "cosign verify PASSED on a tag that was re-pointed to a different image. This is why you deploy by DIGEST: a tag is a label anyone with push rights can move.")
    ok(cosign_verify(keys["pub"], f"{reg}/{name}@{c['digest']}").returncode == 0, "The original digest stopped verifying after the retag: the signature should belong to the digest, not to the tag.")
    sh(["docker", "rmi", "-f", f"{reg}/{name}:evil", f"{reg}/{name}:{c['tag']}"])


# ------------------------------------------------------------------ code scan: Semgrep CE (SAST) + Gitleaks (secrets)
def code_scan_job(wf) -> tuple[str, str]:
    name = next((n for n in wf["jobs"] if "scan" in n.lower() and "supply" not in n.lower()), None)
    ok(name, "Missing the code-scan job (a job whose id contains `scan`, e.g. `code-scan`, separate from `supply-chain`): it runs Semgrep (static analysis of your code) and Gitleaks (leaked secrets). See guide/13_cicd_gitops.md.")
    text = job_block(name)
    if CODE_SCAN_SCRIPT.exists() and "code_scan" in text:
        text += "\n" + CODE_SCAN_SCRIPT.read_text()  # the job may delegate to the script: judge both together
    return name, text


def semgrep_pin_problem(text: str) -> str | None:
    semver = r"\d+\.\d+\.\d+"
    if re.search(r"semgrep/semgrep:(latest|develop|canary)\b", text, flags=re.I):
        return "Semgrep is tracking a moving image tag."
    if re.search(rf"semgrep/semgrep:v?{semver}\b", text, flags=re.I) or re.search(rf"semgrep\s*==\s*{semver}\b", text, flags=re.I):
        return None
    return "Semgrep is not pinned to an exact version (`pip install semgrep==X.Y.Z`, or `semgrep/semgrep:X.Y.Z[@sha256:…]`)."


def gitleaks_pin_problem(text: str) -> str | None:
    semver = r"v?\d+\.\d+\.\d+"
    if re.search(r"gitleaks/gitleaks-action", text, flags=re.I):
        return "gitleaks-action needs a paid licence (`GITLEAKS_LICENSE`) for repositories of an organization, and an action is one more thing to trust: run the gitleaks binary or its container image, pinned."
    if re.search(r"gitleaks/gitleaks:(latest|master|main)\b", text, flags=re.I) or re.search(r"gitleaks[^\n]*/releases/latest", text, flags=re.I):
        return "Gitleaks is tracking `latest`."
    if (re.search(rf"gitleaks/gitleaks:{semver}\b", text, flags=re.I) or re.search(rf"gitleaks/gitleaks/releases/download/{semver}/", text, flags=re.I)
            or re.search(rf"gitleaks[^\n]*{semver}[^\n]*(tar\.gz|zip|\.deb)", text, flags=re.I) or re.search(r"gitleaks/gitleaks@sha256:[0-9a-f]{64}", text, flags=re.I)):
        return None
    return "Gitleaks is not pinned to an exact release (its release URL with `vX.Y.Z`, or `gitleaks/gitleaks:vX.Y.Z[@sha256:…]`)."


def test_code_scan_job_runs_semgrep_and_gitleaks_pinned_and_blocking(wf):
    name, text = code_scan_job(wf)
    low = text.lower()
    ok("semgrep" in low and "gitleaks" in low, f"`{name}` must run both scanners: Semgrep (insecure code patterns) and Gitleaks (secrets). They look for different things; neither replaces the other.")
    ok(CODE_SCAN_SCRIPT.exists(), "Missing scripts/code_scan.sh: like the supply chain, the scans must be a script CI and the check run identically (`bash scripts/code_scan.sh .` from the root of a checkout; see the contract in the guide).")
    problem = semgrep_pin_problem(text)
    ok(problem is None, (problem or "") + "\nA scanner that runs in your pipeline reads your code and your secrets: exactly what the March 2026 Trivy incident abused. Pin it like you pinned Trivy.")
    problem = gitleaks_pin_problem(text)
    ok(problem is None, (problem or "") + "\nA scanner that runs in your pipeline reads your code and your secrets: pin it like you pinned Trivy.")
    ok(re.search(r"--config\b|--config=", text) and re.search(r"--severity[ =]['\"]?ERROR\b", text) and re.search(r"--error\b", text),
       "Semgrep must (1) use a ruleset (`--config …`), (2) report only ERROR-severity findings as blocking (`--severity ERROR`) and (3) exit non-zero when it finds some (`--error`): by default Semgrep prints findings and exits 0, so it blocks nothing.")
    ok(not re.search(r"(semgrep|gitleaks)[^\n]*\|\|\s*(true|:)\s*$", text, flags=re.M | re.I) and not re.search(r"--exit-code[ =]0\b", low) and not re.search(r"continue-on-error:\s*true", text),
       "A scan whose failure is swallowed (`|| true`, `--exit-code 0`, `continue-on-error`) is a report nobody reads. Semgrep must fail on ERROR findings and Gitleaks on ANY leak.")


@pytest.fixture(scope="module")
def scan_tools():
    missing = [t for t in ("semgrep", "gitleaks") if not shutil.which(t)]
    if missing:
        pytest.skip(f"missing: {', '.join(missing)}. The dynamic code-scan checks need them (`brew install semgrep gitleaks`). This part is skipped until they are installed, but the module is not done without it.")
    ok(CODE_SCAN_SCRIPT.exists(), "Missing scripts/code_scan.sh. The check calls `bash scripts/code_scan.sh .` from the root of a checkout, with OUT_DIR in the environment, and expects exit 0 on a clean tree and non-zero otherwise (see the contract in guide/13_cicd_gitops.md).")


@pytest.fixture(scope="module")
def checkout_copy(scan_tools):
    """What a CI runner would see: every file git tracks or would track (no .venv, data, node_modules…), in a scratch directory."""
    files = sh(["git", "ls-files", "-co", "--exclude-standard", "-z"]).stdout.split("\0")
    d = Path(tempfile.mkdtemp(prefix="chk-m13-scan-"))
    for f in filter(None, files):
        src = ROOT / f
        if src.is_file() and not src.is_symlink() and src.stat().st_size < 3_000_000:
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, d / f)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def run_code_scan(target: Path) -> subprocess.CompletedProcess:
    """Exactly what the CI job does: from the root of the checkout, `bash scripts/code_scan.sh .`."""
    out = Path(tempfile.mkdtemp(prefix="chk-m13-scan-out-"))
    try:
        env = {**os.environ, "OUT_DIR": str(out)}
        return subprocess.run(["bash", "scripts/code_scan.sh", "."], cwd=target, env=env, capture_output=True, text=True, timeout=600, check=False)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def fake_token() -> str:
    import secrets
    import string

    return "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))


def test_code_scan_passes_on_your_own_tree_and_catches_what_it_should(checkout_copy):
    d = checkout_copy
    base = run_code_scan(d)
    ok(base.returncode == 0, "`scripts/code_scan.sh` fails on a clean checkout of your own repository, so CI would be red from day one (a gate that always fails gets turned off):\n" + tail(base, 14)
       + "\nTriage the findings: fix what is yours; exclude on purpose (and say why) what is not (the provided plant, the checks, the deliberately buggy R8 `legacy/`).")
    leak = d / "services" / "m13_planted_leak.py"
    leak.parent.mkdir(exist_ok=True)
    leak.write_text(f'GITHUB_TOKEN = "{fake_token()}"\n')
    p = run_code_scan(d)
    leak.unlink()
    ok(p.returncode != 0, "I planted a (fake) GitHub token in services/ and `scripts/code_scan.sh` still exited 0. Gitleaks must fail on ANY leak: a secret in the history is a secret that must be rotated, so you want to stop it before the merge.")
    bad = d / "services" / "m13_planted_insecure.py"
    bad.write_text("import subprocess\nimport requests\n\n\ndef run(cmd):\n    subprocess.run(cmd, shell=True)\n\n\ndef fetch(url):\n    return requests.get(url, verify=False)\n")
    p = run_code_scan(d)
    bad.unlink()
    ok(p.returncode != 0, "I planted `subprocess.run(cmd, shell=True)` and `requests.get(url, verify=False)` (ERROR-severity patterns in the public Semgrep rulesets) and `scripts/code_scan.sh` still exited 0. Does your ruleset include Python rules? Does Semgrep exit non-zero on findings?")
    again = run_code_scan(d)
    ok(again.returncode == 0, "After removing the planted files the scan still fails: the failures above may have been caused by something else, not by what I planted:\n" + tail(again, 8))


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


# ------------------------------------------------------------------ canary releases with Argo Rollouts (static)
def kustomize(path: str) -> list[dict]:
    ok(shutil.which("kubectl"), "You need kubectl (it renders your Kustomize overlay).")
    ok((ROOT / path).exists(), f"Missing {path}: the canary overlay (Kustomize) that makes plant-api a Rollout. See guide/13_cicd_gitops.md.")
    r = sh(["kubectl", "kustomize", str(ROOT / path)])
    ok(r.returncode == 0, f"`kubectl kustomize {path}` fails:\n" + tail(r))
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def canary_parts() -> dict:
    docs = kustomize(M13_CANARY_OVERLAY)
    ros = [d for d in docs if d["kind"] == "Rollout" and d["metadata"]["name"] == "plant-api"]
    ok(ros, f"`kubectl kustomize {M13_CANARY_OVERLAY}` renders no Rollout named plant-api (kind: Rollout, apiVersion argoproj.io/v1alpha1).")
    ok(not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "plant-api" for d in docs),
       "The canary overlay still renders a Deployment named plant-api next to the Rollout: two controllers would fight over the same pods. The Rollout replaces the Deployment.")
    return {"docs": docs, "rollout": ros[0], "services": {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"},
            "templates": {d["metadata"]["name"]: d for d in docs if d["kind"] == "AnalysisTemplate"}}


def step_kind(s: dict) -> str:
    return next(iter(s), "")


def test_canary_rollout_is_well_formed():
    c = canary_parts()
    ro = c["rollout"]
    ok(str(ro.get("apiVersion", "")).startswith("argoproj.io/"), "The Rollout must be an argoproj.io resource.")
    spec = ro["spec"]
    canary = spec.get("strategy", {}).get("canary")
    ok(canary, "The Rollout needs `spec.strategy.canary` (the other strategy is blueGreen): a canary sends a slice of the traffic to the new version first.")
    steps = canary.get("steps") or []
    weights = [s["setWeight"] for s in steps if "setWeight" in s]
    ok(len(weights) >= 2 and weights == sorted(set(weights)) and weights[0] <= 50 and weights[-1] < 100,
       f"The canary steps need ≥ 2 increasing `setWeight` values below 100 (the last 100% is implicit, e.g. 20 → 50 → 100); yours are {weights or 'none'}. A first step that is too big is not a canary.")
    ok(any("pause" in s for s in steps), "Add a `pause` step: the pause is the time (or the human) that watches the canary before it grows.")
    ok(any("analysis" in s for s in steps) or canary.get("analysis"),
       "A canary without analysis is a rolling update with extra steps: nothing decides to stop it. Add an `analysis` step (or a background `strategy.canary.analysis`) that references your AnalysisTemplate.")
    stable, can = canary.get("stableService"), canary.get("canaryService")
    ok(stable and can and stable != can, "Set `stableService` and `canaryService` (two different Services): the analysis needs an address that reaches ONLY the canary pods.")
    sel = spec["selector"]["matchLabels"]
    for s in (stable, can):
        ok(s in c["services"], f"`{s}` is not rendered as a Service by the overlay (Rollouts patches the selector of both Services, they must exist).")
        ok(all(c["services"][s]["spec"].get("selector", {}).get(k) == v for k, v in sel.items()), f"The Service `{s}` must select the Rollout's pods (same labels as spec.selector).")
    ok(int(spec.get("replicas", 1)) >= 2, "Keep ≥ 2 replicas for the API (module 12): with 1 replica a 20% canary is the whole service.")
    pod = spec["template"]["spec"]
    for ctr in pod["containers"]:
        n = ctr["name"]
        res = ctr.get("resources", {})
        ok(res.get("requests") and res.get("limits"), f"Rollout container `{n}`: it needs resource requests AND limits, exactly as the Deployment it replaces had (module 12).")
        ok(ctr.get("readinessProbe") and ctr.get("livenessProbe"), f"Rollout container `{n}`: it needs readiness AND liveness probes. A canary is promoted when its pods are Ready: without readiness there is nothing to wait for.")
        sc = {**pod.get("securityContext", {}), **ctr.get("securityContext", {})}
        ok(sc.get("runAsNonRoot") is True and ctr.get("securityContext", {}).get("allowPrivilegeEscalation") is False and "ALL" in ctr.get("securityContext", {}).get("capabilities", {}).get("drop", []),
           f"Rollout container `{n}` lost the security hardening of module 12 (runAsNonRoot, allowPrivilegeEscalation: false, capabilities.drop: [ALL]): moving to a Rollout must not weaken the pod.")
        img = ctr["image"]
        ok(":" in img.split("/")[-1] and not img.endswith(":latest") or "@sha256:" in img, f"Rollout container `{n}` uses image `{img}`: a version tag or a digest is required (never `latest`).")


def analysis_refs(rollout: dict) -> list[str]:
    canary = rollout["spec"]["strategy"]["canary"]
    refs = [t["templateName"] for s in canary.get("steps", []) if "analysis" in s for t in s["analysis"].get("templates", []) if "templateName" in t]
    refs += [t["templateName"] for t in (canary.get("analysis") or {}).get("templates", []) if "templateName" in t]
    return refs


def test_analysis_template_judges_the_canary_with_a_real_probe():
    c = canary_parts()
    refs = analysis_refs(c["rollout"])
    ok(refs, "The Rollout's analysis must reference an AnalysisTemplate by `templateName`.")
    for r in refs:
        ok(r in c["templates"], f"The Rollout references the AnalysisTemplate `{r}` but the overlay does not render it (kind: AnalysisTemplate).")
    t = c["templates"][refs[0]]
    metrics = t["spec"].get("metrics") or []
    ok(metrics, "The AnalysisTemplate has no metrics: an analysis with nothing to measure always passes.")
    for m in metrics:
        prov = m.get("provider", {})
        kind = next(iter(prov), None)
        ok(kind in ("web", "job"),
           f"Metric `{m.get('name')}` uses the provider `{kind}`. Here the canary is judged by an HTTP probe of the canary Service (`web`) or a smoke-test Job (`job`): the Prometheus on your machine does not scrape pods inside kind, so a `prometheus` metric cannot be evaluated by this check (it is the production-grade version: see the guide).")
        if kind == "web":
            ok(m.get("successCondition") or m.get("failureCondition"), f"Metric `{m['name']}` needs a `successCondition` (or `failureCondition`): without a condition nothing decides pass or fail.")
            ok(prov["web"].get("url"), f"Metric `{m['name']}`: the web provider needs a `url`.")
            ok(not prov["web"]["url"].split("?")[0].rstrip("/").endswith("/health"),
               f"Metric `{m['name']}` probes `/health`: that is exactly what the readiness probe already checks, so the analysis can never say anything new. Probe something a regression could break while the pod is still Ready.")
        ok(m.get("interval") and (m.get("count") or kind == "job"), f"Metric `{m['name']}` needs `interval` and a finite `count`: an analysis that never ends keeps the Rollout paused forever.")


def argo_canary_applications() -> list[tuple[Path, dict]]:
    found = []
    for p in sorted((ROOT / "deploy" / "argocd").glob("*.y*ml")):
        for d in yaml.safe_load_all(p.read_text()):
            if isinstance(d, dict) and d.get("kind") == "Application" and (ROOT / d["spec"]["source"]["path"]).exists():
                path = d["spec"]["source"]["path"]
                r = sh(["kubectl", "kustomize", str(ROOT / path)])
                if r.returncode == 0 and re.search(r"^kind: Rollout$", r.stdout, flags=re.M):
                    found.append((p, d))
    return found


def test_argo_cd_application_delivers_the_canary_overlay():
    apps = argo_canary_applications()
    ok(apps, f"No Argo CD Application in deploy/argocd/ points (`source.path`) at a Kustomize path that renders a Rollout (your `{M13_CANARY_OVERLAY}`): the canary must be delivered through GitOps like everything else, not applied by hand.")
    p, app = apps[0]
    s = app["spec"]
    ok(s["source"]["repoURL"].endswith("/gitops/plant-deploy.git") and s["source"]["targetRevision"] == "main", f"{p.name}: repoURL must be the GitOps repository (http://forgejo:3000/gitops/plant-deploy.git) and targetRevision main.")
    auto = s.get("syncPolicy", {}).get("automated", {})
    ok(auto.get("prune") is True and auto.get("selfHeal") is True, f"{p.name}: syncPolicy.automated needs prune and selfHeal, like the main Application.")
    ns = canary_parts()["rollout"]["metadata"].get("namespace")
    ok(s["destination"]["namespace"] == ns, f"{p.name}: destination.namespace is `{s['destination']['namespace']}` but your overlay renders the Rollout in `{ns}`.")
    main = yaml.safe_load((ROOT / "deploy" / "argocd" / "application.yaml").read_text())
    ok(main["spec"]["destination"]["namespace"] != s["destination"]["namespace"] or main["spec"]["source"]["path"] == s["source"]["path"],
       "The canary Application and the main one deploy into the same namespace from different paths: two Applications would fight over plant-api and the Services (Argo CD shows them as shared resources, flapping). Use another namespace for the canary, or point ONE Application at the overlay.")


# ------------------------------------------------------------------ the throwaway lab cluster (Rollouts + Kyverno)
LAB_CTX = f"kind-{M13_LAB_CLUSTER}"
LAB_MEM = ("This check creates its own small kind cluster (`" + M13_LAB_CLUSTER + "`, ~1.5 GB of RAM with the add-ons) and deletes it at the end: "
           "with Docker below 8 GB stop what you don't need (`make down` of other modules).")


def no_warnings(proc: subprocess.CompletedProcess, n: int = 8) -> str:
    """Like tail(), without the (long, deprecation) warnings kubectl prints for Kyverno's legacy policy types."""
    proc = subprocess.CompletedProcess(proc.args, proc.returncode, "\n".join(ln for ln in (proc.stdout or "").splitlines() if not ln.startswith("Warning:")),
                                       "\n".join(ln for ln in (proc.stderr or "").splitlines() if not ln.startswith("Warning:")))
    return tail(proc, n, 600)


def kc(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return sh(["kubectl", "--context", LAB_CTX, *args], timeout=timeout)


def kapply(docs: list[dict]) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", "--context", LAB_CTX, "apply", "-f", "-"], input=yaml.safe_dump_all(docs), capture_output=True, text=True, timeout=120, check=False)


@pytest.fixture(scope="module")
def lab():
    for t in ("docker", "kind", "kubectl", "helm"):
        ok(shutil.which(t), f"You need `{t}` for the canary and admission checks (`brew install kind kubectl helm`).")
    ok(sh(["docker", "info"]).returncode == 0, "Docker is not running.")
    sh(["kind", "delete", "cluster", "--name", M13_LAB_CLUSTER], timeout=180)
    before = sh(["kubectl", "config", "current-context"]).stdout.strip()  # kind create switches the context: put it back at the end
    r = sh(["kind", "create", "cluster", "--name", M13_LAB_CLUSTER, "--wait", "180s"], timeout=900)
    ok(r.returncode == 0, "Could not create the throwaway kind cluster.\n" + LAB_MEM + "\n" + tail(r))
    try:
        yield LAB_CTX
    finally:
        sh(["kind", "delete", "cluster", "--name", M13_LAB_CLUSTER], timeout=180)
        if before and before != LAB_CTX:
            sh(["kubectl", "config", "use-context", before])


@pytest.fixture(scope="module")
def stubs(lab):
    """Stand-ins for the API (checks/m13_fixtures/stub_api): Ready on /health always; `bad` answers 500 on every other path."""
    d = FIX / "stub_api"
    for key, mode in (("good", "good"), ("bad", "bad")):
        r = sh(["docker", "build", "-q", "--build-arg", f"MODE={mode}", "-t", M13_STUB_TAGS[key], str(d)], timeout=300)
        ok(r.returncode == 0, "Could not build the check's stand-in API image:\n" + tail(r))
    sh(["docker", "tag", M13_STUB_TAGS["good"], M13_STUB_TAGS["good2"]])
    for tag in M13_STUB_TAGS.values():
        r = sh(["kind", "load", "docker-image", tag, "--name", M13_LAB_CLUSTER], timeout=300)
        ok(r.returncode == 0, "Could not load the stand-in image into the lab cluster:\n" + tail(r))
    return M13_STUB_TAGS


@pytest.fixture(scope="module")
def rollouts_lab(lab):
    r = sh(["bash", str(FIX / "rollouts_up.sh"), lab], env={"ROLLOUTS_VERSION": M13_ROLLOUTS_VERSION}, timeout=600)
    ok(r.returncode == 0, "Could not install Argo Rollouts in the lab cluster (needs network):\n" + tail(r))
    return lab


@pytest.fixture(scope="module")
def canary_manifests():
    return canary_parts()  # first fixture of canary_lab: fail on a missing overlay BEFORE creating any cluster


@pytest.fixture(scope="module")
def canary_lab(canary_manifests, rollouts_lab, stubs):
    """Deploys YOUR rendered canary manifests (Rollout, Services, AnalysisTemplate…) with the stand-in image, in the namespace your overlay chose."""
    c = canary_manifests
    ro = c["rollout"]
    ns = ro["metadata"].get("namespace", "default")
    pod = ro["spec"]["template"]["spec"]
    for ctr in pod["containers"]:
        ctr["image"] = stubs["good"]
        ctr["imagePullPolicy"] = "IfNotPresent"
    if ns != "default":
        kc("create", "namespace", ns)
    secrets: dict[str, set[str]] = {}
    for ctr in pod["containers"]:
        for e in ctr.get("env", []):
            ref = e.get("valueFrom", {}).get("secretKeyRef")
            if ref:
                secrets.setdefault(ref["name"], set()).add(ref["key"])
    for name, keys in secrets.items():
        kc("-n", ns, "create", "secret", "generic", name, *[f"--from-literal={k}=check-dummy" for k in keys])
    docs = [d for d in c["docs"] if d["kind"] in ("Rollout", "AnalysisTemplate", "Service", "ConfigMap")]
    r = kapply(docs)
    ok(r.returncode == 0, "The lab cluster (with the Argo Rollouts CRDs) rejects your rendered manifests:\n" + tail(r))
    g = {"ns": ns, "stub": stubs}
    first = wait_until(lambda: (rollout(g).get("status", {}).get("phase") == "Healthy") or None, 240, 3)
    ok(first, "The first version of your Rollout never became Healthy in the lab cluster (stand-in API, answers /health):\n" + describe_rollout(g))
    yield g


def rollout(g: dict) -> dict:
    r = kc("-n", g["ns"], "get", "rollout", "plant-api", "-o", "json")
    return json.loads(r.stdout) if r.returncode == 0 else {}


def describe_rollout(g: dict) -> str:
    ro = rollout(g)
    st = ro.get("status", {})
    pods = kc("-n", g["ns"], "get", "pods", "--no-headers").stdout
    return f"phase={st.get('phase')} message={st.get('message')!r} conditions={json.dumps(st.get('conditions', []))[:400]}\n{pods}"


def set_image(g: dict, image: str) -> None:
    ro = rollout(g)
    idx = 0
    patch = json.dumps([{"op": "replace", "path": f"/spec/template/spec/containers/{idx}/image", "value": image}])
    r = kc("-n", g["ns"], "patch", "rollout", "plant-api", "--type=json", "-p", patch)
    ok(r.returncode == 0, "Could not roll out a new version:\n" + tail(r))
    _ = ro


def promote_if_waiting(ro: dict, g: dict) -> None:
    """A `pause: {}` with no duration waits for a human: this is the human."""
    st, steps = ro.get("status", {}), ro["spec"]["strategy"]["canary"].get("steps", [])
    i = st.get("currentStepIndex")
    if st.get("phase") == "Paused" and st.get("pauseConditions") and i is not None and i < len(steps) and "pause" in steps[i] and not steps[i]["pause"].get("duration"):
        kc("-n", g["ns"], "patch", "rollout", "plant-api", "--subresource=status", "--type=merge", "-p", '{"status":{"pauseConditions":null}}')


def drive(g: dict, old_stable: str, timeout: int = 420) -> tuple[str, dict]:
    end = time.time() + timeout
    ro: dict = {}
    while time.time() < end:
        ro = rollout(g)
        st = ro.get("status", {})
        if st.get("abort") or st.get("phase") == "Degraded":
            return "aborted", ro
        if st.get("phase") == "Healthy" and st.get("currentPodHash") not in (None, old_stable) and st.get("currentPodHash") == st.get("stableRS"):
            return "promoted", ro
        if ro:
            promote_if_waiting(ro, g)
        time.sleep(3)
    return "timeout", ro


def test_a_good_version_is_promoted_to_100_percent(canary_lab):
    g = canary_lab
    old = rollout(g)["status"]["stableRS"]
    set_image(g, g["stub"]["good2"])
    outcome, ro = drive(g, old)
    ok(outcome != "aborted", "A healthy new version (answers 200 everywhere) was ABORTED by your canary: an analysis that cries wolf teaches the team to override it. What does it measure, and does the stand-in good API (JSON answer with the same `openapi`/`info.title` as the real one) satisfy it?\n" + describe_rollout(g))
    ok(outcome == "promoted", "A healthy new version never reached 100% within 7 minutes (steps, pauses and analysis counts too long?).\n" + describe_rollout(g))


def test_a_bad_version_is_aborted_automatically(canary_lab):
    g = canary_lab
    old = rollout(g)["status"]["stableRS"]
    set_image(g, g["stub"]["bad"])  # Ready on /health, HTTP 500 on everything else: the regression readiness cannot see
    outcome, ro = drive(g, old)
    ok(outcome != "promoted", "A BAD version reached 100% of the traffic. Its pods are Ready (readiness only looks at /health) but it answers HTTP 500 to every other request.\n"
       "The analysis must fail on that: what does your probe look at? What does Argo Rollouts do when the probe itself gets an error instead of a number (`consecutiveErrorLimit`, `failureLimit`)?")
    ok(outcome == "aborted", "The bad version neither completed nor aborted within 7 minutes: is the analysis finite (`count`)?\n" + describe_rollout(g))
    st = ro["status"]
    ok(st.get("stableRS") == old, "The Rollout aborted but the stable ReplicaSet changed: traffic must stay on the previous good version.")
    runs = json.loads(kc("-n", g["ns"], "get", "analysisrun", "-o", "json").stdout or '{"items": []}')["items"]
    ok(any(r.get("status", {}).get("phase") in ("Failed", "Error") for r in runs), "The Rollout aborted but no AnalysisRun failed: it was not your analysis that stopped it (progress deadline?).\n" + describe_rollout(g))
    svc = json.loads(kc("-n", g["ns"], "get", "service", ro["spec"]["strategy"]["canary"]["stableService"], "-o", "json").stdout)
    ok(svc["spec"]["selector"].get("rollouts-pod-template-hash") == old, "After the abort the stable Service no longer selects the previous good ReplicaSet.")


# ------------------------------------------------------------------ Kyverno: only signed plant images are admitted
def policy_docs() -> list[tuple[Path, dict]]:
    found = []
    for p in sorted((ROOT / "deploy").rglob("*.y*ml")):
        try:
            docs = list(yaml.safe_load_all(p.read_text()))
        except yaml.YAMLError:
            continue
        found += [(p, d) for d in docs if isinstance(d, dict) and d.get("kind") == "ClusterPolicy"]
    return found


def verify_entries(policy: dict) -> list[dict]:
    return [v for rule in policy.get("spec", {}).get("rules", []) for v in rule.get("verifyImages", [])]


def public_keys(node) -> list[str]:
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += [v] if k == "publicKeys" and isinstance(v, str) else public_keys(v)
    elif isinstance(node, list):
        for v in node:
            out += public_keys(v)
    return out


def test_kyverno_policy_is_well_formed():
    pols = [(p, d) for p, d in policy_docs() if verify_entries(d)]
    ok(pols, "No Kyverno ClusterPolicy with a `verifyImages` rule under deploy/ (e.g. deploy/policies/): this is what makes the cluster REFUSE unsigned images at admission. See guide/13_cicd_gitops.md.")
    p, pol = pols[0]
    ok(str(pol.get("apiVersion", "")).startswith("kyverno.io/"), f"{p.name}: apiVersion must be kyverno.io/v1.")
    ok(pol["spec"].get("validationFailureAction") == "Enforce" or any(v.get("failureAction") == "Enforce" for v in verify_entries(pol)),
       f"{p.name}: the policy must be in `Enforce` mode. In `Audit` mode an unsigned image is admitted and only reported: the cluster would trust everything.")
    kinds = [k for r in pol["spec"]["rules"] for blk in (r.get("match", {}).get("any", []) + r.get("match", {}).get("all", []) + ([r["match"]] if "resources" in r.get("match", {}) else [])) for k in blk.get("resources", {}).get("kinds", [])]
    ok("Pod" in kinds, f"{p.name}: match `Pod`. Whatever creates the pods (a Deployment, a Rollout, a CronJob) the Pod is the last place to refuse an image.")
    for v in verify_entries(pol):
        refs = v.get("imageReferences") or ([v["image"]] if v.get("image") else [])
        ok(refs, f"{p.name}: every verifyImages entry needs `imageReferences`.")
        for reg in ("kind-registry:5000", "localhost:5050", "ghcr.io/acme"):
            for svc in ("plant-api", "plant-gateway", "plant-web"):
                ok(any(fnmatch.fnmatch(f"{reg}/{svc}:1.2.3", pat) for pat in refs), f"{p.name}: the policy does not match `{reg}/{svc}:1.2.3`. All the plant's images (plant-api, plant-gateway, plant-web, whatever registry hosts them) must need a signature.")
        ok(not any(fnmatch.fnmatch(other, pat) for pat in refs for other in ("registry.k8s.io/pause:3.9", "docker.io/library/nginx:1.27")),
           f"{p.name}: the policy matches images that are not the plant's (e.g. `registry.k8s.io/pause`). Scope it to the plant's images: a policy that blocks the cluster's own system pods gets deleted in the first outage.")
        ok(v.get("required", True) is not False and v.get("verifyDigest", True) is not False, f"{p.name}: `required` and `verifyDigest` must stay on: they are what makes a missing or digest-less signature a rejection.")
        ok(v.get("mutateDigest", True) is not False, f"{p.name}: keep `mutateDigest` on: Kyverno rewrites `image:tag` to `image@sha256:…` of the VERIFIED content, so nobody can re-point the tag between the check and the pull.")
    keys = public_keys(pol)
    ok(keys, f"{p.name}: put your cosign PUBLIC key inline (`attestors[].entries[].keys.publicKeys`, the content of cosign.pub). The check swaps it for a fresh test key, but it must be there and be a public key.")
    for k in keys:
        body = "".join(ln for ln in k.strip().splitlines() if not ln.startswith("-----"))
        try:
            der = base64.b64decode(body, validate=True)
        except ValueError:
            der = b""
        ok(k.strip().startswith("-----BEGIN PUBLIC KEY-----") and "PRIVATE" not in k and len(der) >= 80, f"{p.name}: `publicKeys` is not a PEM public key (`-----BEGIN PUBLIC KEY-----`, as `cosign generate-key-pair` writes in cosign.pub). Never the private key.")


@pytest.fixture(scope="module")
def kyverno_lab(lab):
    r = sh(["bash", str(FIX / "kyverno_up.sh"), lab], env={"KYVERNO_CHART": M13_KYVERNO_CHART}, timeout=900)
    ok(r.returncode == 0, "Could not install Kyverno in the lab cluster (needs network):\n" + tail(r))
    return lab


def with_key(policy: dict, pub_pem: str) -> dict:
    def walk(n):
        if isinstance(n, dict):
            return {k: (pub_pem if k == "publicKeys" and isinstance(v, str) else walk(v)) for k, v in n.items()}
        return [walk(v) for v in n] if isinstance(n, list) else n

    pol = walk(policy)
    pol["metadata"] = {"name": pol["metadata"]["name"]}
    return pol


@pytest.fixture(scope="module")
def learner_policy():
    pols = [d for _, d in policy_docs() if verify_entries(d)]
    ok(pols, "No Kyverno ClusterPolicy with a `verifyImages` rule under deploy/ (e.g. deploy/policies/): the admission checks have nothing to apply.")
    return pols[0]


@pytest.fixture(scope="module")
def chain_signed(chain):
    c = signed(chain)  # the image YOUR script signed; resolved before any cluster is created
    problem = new_bundle_problem(c)  # fail early, with the real reason, instead of after installing Kyverno
    ok(problem is None, problem or "")
    return c


@pytest.fixture(scope="module")
def admission(learner_policy, chain_signed, keys, registry, kyverno_lab):
    """Your policy (with the check's public key swapped in) in a cluster that can pull from the throwaway registry the supply chain used."""
    c = chain_signed
    name = f"chk-m13-registry-{registry.rsplit(':', 1)[1]}"
    sh(["docker", "network", "connect", "--alias", "chk-m13-registry", "kind", name])
    pols = [learner_policy]
    r = kapply([with_key(pols[0], keys["pub_pem"])])
    ok(r.returncode == 0, "The lab cluster rejects your ClusterPolicy:\n" + no_warnings(r))
    ok(wait_until(lambda: kc("get", "clusterpolicy", pols[0]["metadata"]["name"], "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}").stdout == "True", 120, 3),
       "Kyverno never reported your ClusterPolicy as Ready:\n" + kc("get", "clusterpolicy", "-o", "yaml").stdout[-900:])
    kc("create", "namespace", "chk-m13-kv")
    reg = c["registry"]
    return {"c": c, "keys": keys, "host": "chk-m13-registry:5000", "local": reg}


def pod(image: str, name: str) -> dict:
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": "chk-m13-kv"}, "spec": {"containers": [{"name": "c", "image": image}]}}


def try_admit(image: str, name: str = "probe") -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", "--context", LAB_CTX, "apply", "--dry-run=server", "-o", "json", "-f", "-"], input=yaml.safe_dump(pod(image, name)), capture_output=True, text=True, timeout=120, check=False)


def copy_tag(a: dict, src_digest: str, tag: str) -> None:
    r = sh(["docker", "buildx", "imagetools", "create", "--tag", f"{a['local']}/plant-api:{tag}", f"{a['local']}/plant-api@{src_digest}"], timeout=120)
    ok(r.returncode == 0, "Check bug: could not create a tag in the throwaway registry:\n" + tail(r))


def test_kyverno_admits_the_image_your_supply_chain_signed(admission):
    a = admission
    copy_tag(a, a["c"]["digest"], "kv-signed")
    r = try_admit(f"{a['host']}/plant-api:kv-signed")
    ok(r.returncode == 0, "Kyverno REFUSED the image that scripts/supply_chain.sh signed with the key in your policy (the check swapped in the matching public key):\n" + no_warnings(r, 8)
       + "\n`cosign verify` accepts that signature, so it exists: is it stored in a layout this Kyverno version reads (cosign v3 changed it; see `cosign sign --help`)? Is the transparency log required although the signature was never uploaded to one?")
    img = json.loads(r.stdout)["spec"]["containers"][0]["image"]
    ok(img.endswith("@" + a["c"]["digest"]), f"The admitted pod still runs `{img}`: Kyverno must pin the verified digest (`mutateDigest`), or the tag can be re-pointed after the check.")


def test_kyverno_refuses_unsigned_retagged_and_wrongly_signed_images(admission):
    a, k = admission, admission["keys"]
    # 1. tampered, never signed
    b = subprocess.run(["docker", "build", "-q", "-t", f"{a['local']}/plant-api:kv-evil", "-"], input=f"FROM {a['c']['image']}\nLABEL evil=2\n", capture_output=True, text=True, timeout=300, check=False)
    ok(b.returncode == 0, "The check could not build its tampered image:\n" + tail(b))
    sh(["docker", "push", "-q", f"{a['local']}/plant-api:kv-evil"])
    evil = digest_in_registry(a["local"], "plant-api", "kv-evil")
    r = try_admit(f"{a['host']}/plant-api:kv-evil")
    ok(r.returncode != 0, "Kyverno ADMITTED an image nobody signed. Is the policy in Enforce mode and does it match the image name?")
    ok("kyverno" in (r.stdout + r.stderr).lower() or "blocked" in (r.stdout + r.stderr).lower(), "The pod was refused, but not by Kyverno:\n" + tail(r, 6))
    # 2. the signed tag is re-pointed to different content
    copy_tag(a, a["c"]["digest"], "kv-moving")
    ok(try_admit(f"{a['host']}/plant-api:kv-moving").returncode == 0, "Kyverno refused the signed image (see the previous test), so the re-pointed-tag case cannot be evaluated: fix that first.")
    sh(["docker", "tag", f"{a['local']}/plant-api:kv-evil", f"{a['local']}/plant-api:kv-moving"])
    sh(["docker", "push", "-q", f"{a['local']}/plant-api:kv-moving"])
    ok(try_admit(f"{a['host']}/plant-api:kv-moving").returncode != 0, "Kyverno admitted a tag that was re-pointed to unsigned content: the signature belongs to the digest, and the digest behind the tag changed.")
    # 3. signed, but by somebody else
    env = {"COSIGN_PASSWORD": k["password"]}
    s = sh(["cosign", "sign", "--yes", "--key", k["other_key"], "--use-signing-config=false", "--tlog-upload=false", "--new-bundle-format=false", f"{a['local']}/plant-api@{evil}"], env=env, timeout=120)
    ok(s.returncode == 0, "Check bug: could not sign with the other key:\n" + tail(s))
    ok(try_admit(f"{a['host']}/plant-api:kv-evil").returncode != 0, "Kyverno admitted an image signed with a DIFFERENT key: the policy must trust exactly your public key.")


def test_kyverno_leaves_other_images_alone(admission):
    r = try_admit("registry.k8s.io/pause:3.9")
    ok(r.returncode == 0, "Kyverno refused an image that is not the plant's (registry.k8s.io/pause): a policy that is too broad blocks system pods and gets deleted in the first outage.\n" + tail(r, 6))


# ------------------------------------------------------------------ the canary through the real Argo CD
def test_argo_cd_deploys_and_keeps_the_canary_rollout_healthy(gitops):
    apps = argo_canary_applications()
    ok(apps, "No Argo CD Application points at your canary overlay (see test_argo_cd_application_delivers_the_canary_overlay).")
    p, app = apps[0]
    k, name, ns = gitops["k"], app["metadata"]["name"], app["spec"]["destination"]["namespace"]
    r = sh(["bash", str(FIX / "rollouts_up.sh"), "kind-plant"], env={"ROLLOUTS_VERSION": M13_ROLLOUTS_VERSION}, timeout=600)
    ok(r.returncode == 0, "Could not install Argo Rollouts on the `plant` cluster (needs network):\n" + tail(r))
    sh([*k, "create", "namespace", ns])
    sh(["kubectl", "config", "use-context", "kind-plant"])  # the lab cluster may have taken the default context
    sh(["bash", "platform/tools/k8s_secrets.sh", ns])  # the pods need plant-secrets in their namespace (it never lives in Git)
    try:
        a = sh([*k, "apply", "-f", str(p)])
        ok(a.returncode == 0, f"Could not apply {p.name}:\n" + tail(a))

        def st():
            r = sh([*k, "-n", "argocd", "get", "application", name, "-o", "json"])
            return json.loads(r.stdout).get("status", {}) if r.returncode == 0 else {}

        def done():
            s = st()
            return s.get("sync", {}).get("status") == "Synced" and s.get("health", {}).get("status") == "Healthy"

        good = wait_until(done, 420, 5)
        s = st()
        ok(good, f"Argo CD did not get `{name}` to Synced + Healthy within 7 minutes (sync={s.get('sync', {}).get('status')}, health={s.get('health', {}).get('status')}).\n"
           f"Conditions: {json.dumps(s.get('conditions', []))[:500]}\nCheck: does the path exist in the GitOps repo (`make gitops-push`)? are the Rollouts CRDs installed? is plant-secrets in `{ns}`?")
        ro = sh([*k, "-n", ns, "get", "rollout", "plant-api", "-o", "jsonpath={.status.phase}"])
        ok(ro.stdout.strip() == "Healthy", f"Argo CD reports the application Healthy but the Rollout plant-api in `{ns}` is `{ro.stdout.strip() or 'missing'}`.")
    finally:
        sh([*k, "delete", "-f", str(p), "--ignore-not-found", "--wait=false"])
        sh([*k, "delete", "namespace", ns, "--ignore-not-found", "--wait=false"])
