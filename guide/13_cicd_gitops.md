# Module 13 — CI/CD, supply-chain security, GitOps and a service catalog

⏱ 12–13 h (7–8 h of CI, supply chain, GitOps and catalog + ~5 h of code scanning, signature admission and canaries) · Services: `make kind-up up-git` (and module 12 done) · Check: `make check-13` (⏱ ~15 min the first time: it builds images, installs Argo CD and creates a second, throwaway kind cluster for Kyverno and Argo Rollouts; the dynamic parts need `brew install trivy syft cosign semgrep gitleaks kind helm`)
**Tools:** GitHub Actions (locally with **act**), **Trivy** (vulnerability scanner), **Syft** (SBOM), **cosign** (signatures), **Semgrep CE** (static analysis of your code), **Gitleaks** (leaked credentials), **Kyverno** (admission policies), **Argo CD** (GitOps), **Argo Rollouts** (canary releases), **Backstage** (internal portal), Git.
**Mode:** solo, guided or walkthrough — see [modes](modes.md) · set it with `/mode 13 <mode>`
**Memory:** Docker ≈ 8 GB is enough, but not with other modules running: the check starts a second kind cluster (~1.5 GB with the add-ons) and deletes it at the end. Look at `docker stats --no-stream` before you start.

## What you will build
1. **`.github/workflows/ci.yml`**: the continuous integration pipeline — lint, Python tests, frontend tests and build, infrastructure validation (OpenTofu), Kubernetes manifest validation, building the three images in parallel, a **`code-scan` job** and a **supply-chain job** for the API image.
2. **Code scanning**: **`scripts/code_scan.sh`** runs **Semgrep CE** (insecure code patterns; blocks on ERROR findings) and **Gitleaks** (leaked credentials; blocks on any leak), both pinned.
3. **Supply-chain security**: a `supply-chain` job plus **`scripts/supply_chain.sh`** that scan the image with Trivy (and block it if it has fixable critical vulnerabilities), produce an SBOM with Syft, and sign the image with cosign and verify the signature.
4. **Admission control**: a **Kyverno `ClusterPolicy`** in `deploy/policies/` that makes the cluster *refuse* the plant's images unless they carry a valid cosign signature for your public key. This is the half that makes signing worth doing.
5. **GitOps with Argo CD**: an `Application` that makes the cluster **follow a Git repository**. Deploying = `git push`.
6. **A canary release with Argo Rollouts**: `plant-api` as a `Rollout` (20% → analysis → 50% → 100%) whose `AnalysisTemplate` **aborts a bad version on its own**, delivered by Argo CD from a second overlay.
7. A Backstage **`catalog-info.yaml`**: the inventory of your systems, components, APIs and resources with their owners and dependencies.

## Why this module exists
So far you deployed with `kubectl apply` from your terminal. That neither scales nor can be audited: who changed what?, which version is running today?, how do I roll back? The modern answer:
- **CI** checks **every change** automatically before it is integrated (the same `ruff` and the same tests, always).
- **Supply-chain security** answers “can I trust what I am about to run?”. Your pipeline downloads and executes other people's code (actions, base images, scanners) **with your secrets in its environment**, so the pipeline itself is an attack target. In **March 2026** it happened to the most popular open-source vulnerability scanner: attackers with stolen credentials published a malicious **Trivy v0.69.4**, moved 76 of the 77 version tags of `aquasecurity/trivy-action` (and every tag of `setup-trivy`) to malicious commits, and later pushed bad images to Docker Hub (**CVE-2026-33634**, added to CISA's Known Exploited Vulnerabilities catalog on 26 March). The payload was an infostealer that read CI runner memory, SSH keys, cloud and Kubernetes credentials and `.env` files. Pipelines that referenced a tag or `latest` during the window ran it; pipelines that pinned a **commit SHA** or an image **digest** did not. A security tool was the attack vector, which is why this module makes you pin it. ([advisory GHSA-69fq-xp46-6x23](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23))
- **Code scanning** answers “did *we* introduce the problem?”. Trivy and Syft look at what you *ship* (packages); **Semgrep** reads *your code* for patterns that are almost always bugs (`shell=True`, disabled TLS verification…) and **Gitleaks** looks for credentials that were committed. A leaked key is not fixed by deleting the line: it lives in the history until it is rotated, so the only cheap moment to stop it is before the merge.
- **Admission control** closes the loop of signing. A signature nobody checks is decoration; the place to check it is the cluster's front door, because *anything* can ask the API server for a pod (a teammate, a compromised CI, a stale manifest). **Kyverno** is a webhook that sees every pod before it exists and can refuse it, or rewrite it.
- **Progressive delivery** answers “how do I find out that version N+1 is bad *before* everybody has it?”. A rolling update only checks that pods start; a **canary** gives the new version a slice of the traffic and lets a measurement decide whether to continue. If a human has to be watching a dashboard, it will fail at 3 a.m.; so the measurement is code (**Argo Rollouts**' `AnalysisTemplate`) and the abort is automatic.
- **GitOps** declares the cluster's desired state **in Git**; an agent (Argo CD) applies it and **reverts any manual change**. Git is the source of truth: auditing, review and *rollback* for free (`git revert`).
- **Backstage** answers “which services exist, who owns them and what do they depend on?” — vital when there are hundreds.

## Concepts
- **Workflow, job, step, matrix, needs**: the structure of GitHub Actions. `needs` orders jobs; `matrix` repeats a job with different values.
- **Least privilege in CI**: `permissions: contents: read`; actions **pinned** (never `@main`; third-party ones by commit SHA); `timeout-minutes`; `concurrency` with `cancel-in-progress`.
- **Pinning by digest (or SHA)**: a tag (`@v4`, `:1.2.3`, `:latest`) is a *label* the owner can move; a commit SHA or an image digest (`@sha256:…`) is a *fingerprint of the content* and cannot. You pin the exact thing you reviewed, and you update it on purpose (a pull request), not by surprise. Put the human-readable version in a comment next to the SHA.
- **SBOM** (software bill of materials): the machine-readable ingredient list of an image (OS packages, Python libraries, versions), in a standard format (**CycloneDX** or **SPDX**). When the next big CVE is announced, you search your SBOMs instead of guessing which services are affected.
- **Vulnerability scanning and gating**: a scanner matches the SBOM's packages against vulnerability databases. A **gate** turns the report into a decision: fail the job on `CRITICAL` vulnerabilities **that already have a fix** (`--ignore-unfixed`), because those are the ones you can act on today.
- **Signature**: a cryptographic statement “the owner of this key vouches for this exact **digest**”. **cosign** stores it in the registry next to the image. **Verifying** it before deploying is what makes it useful; signing is the cheap half. Signatures belong to digests, not tags, because anyone with push rights can re-point a tag.
- **Provenance and SLSA**: provenance records *where, when and how* an artifact was built (which commit, which workflow, which builder). **SLSA** (“salsa”) is the framework of levels that grades how tamper-resistant that story is. Here you sign with a key you hold; on real GitHub you would add *keyless* signing (the workflow's identity, short-lived certificates) and signed provenance attestations.
- **SAST and secret scanning**: static analysis (Semgrep *rules* have a **severity**; you choose which severities block) and pattern + entropy matching of credentials (Gitleaks). Both need *triage*: fix what is yours, exclude on purpose what is not (provided code, generated files) and write down why. A scanner that is red from day one gets turned off.
- **Admission webhook**: the API server asks an external service “may I store this object?” before persisting it. *Mutating* webhooks can change it, *validating* ones accept or refuse. Kyverno's `verifyImages` does both: it verifies the signature and **rewrites `image:tag` to `image@sha256:…`** of the content it verified, so the tag cannot be re-pointed between the check and the pull. `Enforce` refuses; `Audit` only reports.
- **Canary, steps and analysis**: `setWeight` (how much goes to the new version), `pause` (time, or a human), `analysis` (a check that can fail). A **Rollout** replaces a Deployment; a *stable* and a *canary* **Service** let you address “only the canary” (exact traffic percentages need an Ingress or a mesh; without one the weight is the ratio of pods). An **AnalysisRun** is one execution of an **AnalysisTemplate**; a failed run **aborts** the Rollout and traffic stays on the stable version. Readiness only tells you a pod *started*; the analysis has to look at something readiness cannot see.
- **Which metric?** The production-grade analysis is a *Prometheus* query (error ratio, latency percentile of the canary). Here the Prometheus on your machine (`make up-obs`) scrapes the Docker Compose services, **not pods inside kind**, and an in-cluster Prometheus would cost more than 1 GB of a ~8 GB budget. So the exercise uses Argo Rollouts' `web` provider (an HTTP probe of the canary Service, made by the controller) or a `job` smoke test. The mechanism (analysis → abort) is identical; only where the number comes from changes. See “On a real job”.
- **Dependency cache** (uv, npm, Docker layers): people avoid a slow CI.
- **GitOps**: *pull* instead of *push* — the cluster pulls its state from Git. **Sync, prune and self-heal**.
- **Argo CD Application**: `source` (repo + path + revision) and `destination` (cluster + namespace). The deployment repository is usually **different** from the code one.
- **Backstage**: entities `System`, `Component`, `API`, `Resource`, `Group`; relations `dependsOn`, `providesApis`, `consumesApis`; everything with an `owner`.

## The contract
**CI** — `.github/workflows/ci.yml`:
- `on:` `pull_request` and `push` (to `main`); `permissions: contents: read`; `concurrency` with `cancel-in-progress: true`.
- **≥ 6 jobs**, all with `timeout-minutes`; no action pinned to `@main/@master/@latest`; a dependency cache (e.g. `astral-sh/setup-uv` with `enable-cache`).
- **Every action that is not from the `actions/` organization is pinned to a full 40-character commit SHA** (`owner/repo@<sha> # v1.2.3`).
- An image build job with **`strategy.matrix`** (api, gateway, web) and **`needs`** on ≥ 3 previous jobs (you don't build what doesn't pass).
- It must pass **actionlint** (the workflow linter) and `act -l` must be able to list it.

**Supply chain** — a job whose id contains `supply` (e.g. `supply-chain`) in `ci.yml`, plus `scripts/supply_chain.sh`. Together they must:
- **build** the API image (`services/api/Dockerfile`);
- **scan it with Trivy**, which is **pinned to an exact version or digest** (not `latest`, and not one of the malicious March 2026 releases). The job **fails on `CRITICAL` vulnerabilities that have a fix**;
- generate an **SBOM with Syft** (CycloneDX or SPDX JSON) and **keep it as a build artifact** (`actions/upload-artifact`);
- **sign the image with cosign and then verify the signature**. The private key comes from a **secret** (`${{ secrets.… }}`) or an environment variable and **never lives in the repository**; the signature is made on the image **digest** in a registry.

**`scripts/supply_chain.sh <image>`** — the same chain as a script, so that CI and the check run exactly the same thing. `<image>` is a local Docker image (e.g. `plant-api:dev`). Configuration comes only from the environment:

| Variable | Meaning |
|---|---|
| `REGISTRY` | `host:port` of an already running OCI registry (the check starts a throwaway one; you may use `registry:3` yourself) |
| `OUT_DIR` | where to write the results |
| `COSIGN_PRIVATE_KEY` | the content of the private key (from a secret, never from a file in the repo) |
| `COSIGN_PASSWORD` | the key's password |

Behaviour: it writes **`$OUT_DIR/sbom.json`** (SBOM of the image) and **`$OUT_DIR/trivy.json`** (the Trivy report, written even when the gate fails); if the scan finds fixable `CRITICAL` vulnerabilities it exits non-zero and **publishes and signs nothing**; otherwise it pushes the image to `$REGISTRY/<name>:<tag>` (same name and tag as the argument), signs the pushed **digest**, verifies the signature and exits 0. Without a key it must fail. It is a script you can run anywhere: no GitHub-only syntax inside it.

**Code scan** — a job whose id contains `scan` (e.g. `code-scan`, separate from `supply-chain`) plus **`scripts/code_scan.sh [dir]`** (the same chain as a script, like the supply chain; `OUT_DIR` says where reports go). Together they must:
- run **Semgrep CE** with a public ruleset (`--config p/python`, `p/default`… or rules you vendor), **pinned to an exact version** (`pip install semgrep==X.Y.Z` or `semgrep/semgrep:X.Y.Z`), failing (`--error`) on **ERROR-severity** findings only (`--severity ERROR`: warnings are reported, not blocking);
- run **Gitleaks** (the `gitleaks` binary or its image, **pinned**, *not* `gitleaks-action`: it needs a paid licence for organizations) and fail on **any** leak;
- exit non-zero if either fails, **without** stopping at the first one (you want both reports), and not swallow failures (`|| true`, `continue-on-error`, `--exit-code 0`).

Run from the root of `git ls-files -co --exclude-standard` (what a CI checkout sees) as `bash scripts/code_scan.sh .`, the script must **pass on your own repository** and **fail** when a fake credential or `subprocess.run(cmd, shell=True)` / `requests.get(url, verify=False)` is added. Which paths you exclude (the provided `platform/`, `checks/`, the deliberately buggy `legacy/`) is your decision; document why in the script.

**Admission** — a Kyverno `ClusterPolicy` (`apiVersion: kyverno.io/v1`) in `deploy/policies/` (not in `deploy/k8s`: Argo's main Application must not need Kyverno's CRDs). One `verifyImages` rule that:
- matches `Pod`, in **`Enforce`** mode;
- covers the plant's three images (`plant-api`, `plant-gateway`, `plant-web`) **from any registry** and **only those** (the check also verifies that `registry.k8s.io/pause` is left alone);
- trusts **exactly your cosign public key**, written inline (`keys.publicKeys`, the content of `cosign.pub`; never the private key);
- keeps `required`, `verifyDigest` and `mutateDigest` on, so the admitted pod runs `image@sha256:<verified digest>`.

The check puts a pinned Kyverno (admission controller only) in its own throwaway kind cluster, swaps your public key for a fresh test key, and asks the API server (`--dry-run=server`) to admit pods. **The image it uses is the one your own `scripts/supply_chain.sh` signed**: so the signature your script writes must be one Kyverno can read.

**GitOps** — `deploy/argocd/application.yaml`: `kind: Application`; `repoURL: http://forgejo:3000/gitops/plant-deploy.git` (that is how the cluster's pods see it), `targetRevision: main`, a `path` that exists in your repo (`deploy/k8s/overlays/dev`),
`destination.namespace: plant`, `syncPolicy.automated` with **`prune: true` and `selfHeal: true`**. Helper scripts: `make gitops-up` (installs Argo CD on kind and prepares the repo in Forgejo) and `make gitops-push` (publishes `deploy/k8s`).

**Canary** — `deploy/k8s/overlays/canary/` (Kustomize) must render, with `kubectl kustomize`:
- a **`Rollout` named `plant-api`** (`argoproj.io/v1alpha1`) **instead of** the `plant-api` Deployment (replicas ≥ 2, the same resources, probes and security context as the Deployment had, an image with a version tag);
- `strategy.canary` with `stableService` and `canaryService` (two Services that select the API pods), **≥ 2 increasing `setWeight` steps below 100** (e.g. 20 → 50, then 100 implicitly), a **`pause`** and an **`analysis`** that references your template;
- an **`AnalysisTemplate`** with a `web` (HTTP probe of the canary Service) or `job` provider, a condition, an `interval` and a finite `count`. It must **not probe `/health`** (readiness already does): the check's *bad* stand-in version is Ready but answers HTTP 500 to every other path, and must be aborted; its *good* version answers 200 on every path with a small JSON document that has the same `openapi` and `info.title` fields as the real API's `/openapi.json` (and `status: ok`), and must be promoted. Judge the canary on what such an answer contains or on whether the request succeeds, nothing more specific. (The real API's data endpoints need a token and a database, which is why the probe is a path that does not.)

Argo CD delivers it: an `Application` in `deploy/argocd/` (for instance `application-canary.yaml`) whose `source.path` is that overlay, same GitOps repository and `automated` with `prune` and `selfHeal`, and a **`destination.namespace` equal to the one your overlay renders** (different from the main Application's, or the two would fight over `plant-api`).

**Catalog** — `catalog-info.yaml` (several YAML documents): 1 `System`, ≥ 4 `Component` (each with `type`, `lifecycle`, `owner`, `system`), ≥ 1 `API` of type `openapi` with its definition, ≥ 3 `Resource` (postgres, kafka…),
`dependsOn` / `providesApis` / `consumesApis` relations **with no dangling references**.

## Your task
1. **CI**: write the workflow job by job. Test each one locally with `act -j lint` (it needs Docker; the first time it downloads a ~1 GB image) or at least `act -l` and `actionlint`.
2. Think: which part of the CI **can't** run without services (Kafka, Postgres)? Leave it out or mark it; a fast CI relies on tests that need no infrastructure.
3. **Pin your actions.** Look up the commit behind each tag you use and write `@<full sha> # vX.Y.Z`. Notice how many of your actions are third-party. (Updating pins by hand does not scale: how would a bot like Renovate or Dependabot help?)
4. **Supply chain, by hand first**: `brew install trivy syft cosign`. Take your module 12 API image and, in a terminal: scan it (`trivy image`), list its contents (`syft`), start a throwaway registry, push the image, generate a key pair (**outside the repo**), sign the digest, verify it. Then check that `cosign verify` **fails** for an image you never signed.
5. Turn that into `scripts/supply_chain.sh` following the contract, and try it on an image you know is vulnerable (an old base image is enough): it must fail, leave its report and publish nothing.
6. Add the **`supply-chain` job**, with Trivy, Syft and cosign installed at pinned versions. Run it with `act -j supply-chain`, giving the key as a secret on the command line or from a file that is **not** in the repo (`act --help` tells you how). Expect to meet four `act`-specific snags (see the hint “Running the jobs with `act`”): they are not bugs in your workflow.
7. **Code scan, by hand first**: `brew install semgrep gitleaks`. Run `semgrep scan --config p/python .` and `gitleaks dir .` on the repository and read **every** finding: which are yours, which belong to the provided plant, the checks or `legacy/`? Write a throwaway file with `subprocess.run(cmd, shell=True)` and another with a fake `ghp_…` token and see each tool catch it. What is the exit code when it does?
8. Turn it into `scripts/code_scan.sh` and the **`code-scan` job** (both tools pinned, exclusions on purpose and explained). The script must pass on your repo and fail on the files from step 7. Run `act -j code-scan`.
9. **GitOps**: `make kind-up up-git gitops-up gitops-push`; apply your `Application` (`kubectl apply -f deploy/argocd/application.yaml`) and watch Argo create the pods (`kubectl -n argocd get application`).
10. **Test self-healing**: `kubectl -n plant scale deployment/plant-web --replicas=3` and watch Argo bring it back to 1. Then change the replicas in `deploy/k8s`, `make gitops-push` and see the change reach the cluster **without touching kubectl**.
11. **Admission**: install Kyverno on your kind cluster (`make kyverno-up`, or `bash checks/m13_fixtures/kyverno_up.sh`). Start a registry on the **kind network** (`docker run -d --network kind --name plant-registry registry:3.1.2@sha256:82f6ff4a…`), push `plant-api` there, sign the digest with your key, and try to run a pod with that image **and** with one nobody signed *before* you write any policy: both start. Now write `deploy/policies/` and watch the second one be refused with a message that names your policy. Then re-point the signed tag to different content and try again.
12. **Canary**: install the controller (`make rollouts-up`, or `bash checks/m13_fixtures/rollouts_up.sh`). Turn `plant-api` into a `Rollout` in a new `deploy/k8s/overlays/canary` (build it from `../../base` and *replace* the Deployment: what does Kustomize offer for deleting a resource?). Add the stable and canary Services, the steps, and look at it live: `kubectl -n <ns> get rollout plant-api -w` (or the `kubectl argo rollouts get rollout plant-api -w` plugin: `brew install argoproj/tap/kubectl-argo-rollouts`).
13. **An analysis that can say no**: write the `AnalysisTemplate`. Roll out a good version (`kubectl set image`-style patch of the Rollout) and watch it grow to 100%. Then roll out one that answers HTTP 500 on a real path while `/health` stays green (a two-line change in a copy of the API is enough) and watch the Rollout **abort by itself** and traffic stay on the stable version. Then ask: what if the probe *errors* instead of returning a bad number?
14. **The canary through Argo CD**: add the second `Application` (its own namespace), `make gitops-push`, and watch Argo create the Rollout. Remember Argo prunes what is not in Git: you do not run the Rollout by hand any more.
15. **Catalog**: describe your system the way the platform team would. Draw the dependency graph in `notes/` and compare it with your YAML.

## How you will know you are done
`make check-13` validates your workflow (security and structure rules, SHA pinning, the supply-chain and code-scan jobs, **actionlint** and `act -l`), that **no private key is in the repository**, your catalog (schema and references), your `Application`, your Kyverno policy (scope, mode, key) and your canary manifests (steps, services, analysis, hardening).

Then it **runs the supply chain for real** (when `trivy`, `syft` and `cosign` are installed; otherwise that part is skipped with an install message, but the module is not done without it). It starts a throwaway registry, builds your API image, generates a **fresh key pair** and calls `scripts/supply_chain.sh` with it. It checks that:
- the SBOM lists the API's real dependencies **and** the operating-system packages of the image, compared against its own Syft run;
- the Trivy report is a scan of *that* image (same layers), made by a Trivy that is not a compromised release;
- `cosign verify --key <public key>` **passes** on the digest you pushed, and **fails** with another key, on an image nobody signed, and on a tag that was re-pointed to different content;
- an image with known fixable `CRITICAL` vulnerabilities **fails the gate** and reaches no registry, and the script refuses to run without a key.

**Code scan, for real** (when `semgrep` and `gitleaks` are installed): it copies what a CI checkout would contain, runs `scripts/code_scan.sh` and requires **exit 0 on your own tree**; then it plants a fake GitHub token, and separately `shell=True` and `verify=False` patterns, and requires a **non-zero exit** for each; then it removes them and requires exit 0 again (so the failures were caused by what it planted).

Then it **runs GitOps for real**: it installs Argo CD if missing, publishes your `deploy/k8s` to the local Git, applies your Application and waits for `Synced` + `Healthy`;
**it scales a Deployment by hand and checks that Argo reverts it**; it makes a commit that changes the gateway replicas and checks that the cluster applies it.
Finally it **deploys your canary through Argo CD**: it installs the Rollouts controller on `plant`, applies your canary `Application` and waits for `Synced` + `Healthy` with a Healthy `Rollout`.

For the last two features it creates its **own throwaway kind cluster** (`chk-m13`) and deletes it at the end:
- **Canary, for real**: it installs Argo Rollouts (pinned) and deploys *your rendered manifests* with a stand-in API image (the real one needs your database). It rolls out a **good** version and requires it to reach 100%; then a **bad** one (Ready on `/health`, HTTP 500 everywhere else) and requires that your analysis **aborts the Rollout on its own**, that an `AnalysisRun` failed, and that the stable ReplicaSet and Service are untouched.
- **Admission, for real**: it installs Kyverno (pinned), applies *your* policy with a fresh public key swapped in, and uses **the image your `supply_chain.sh` signed**: it must be **admitted and pinned to its digest**; an unsigned image, a signed tag **re-pointed** to unsigned content and an image signed by **another key** must be **refused by Kyverno**; and an image that is not the plant's must be left alone.

The first run downloads the vulnerability database, the Kyverno and Rollouts images and the kind node image (needs network).

## Your turn (walkthrough mode)
On your own, add a named step to `.github/workflows/ci.yml` that validates `deploy/k8s/overlays/canary` with `kubectl kustomize`, before image builds.
Keep the existing job dependencies and pinned actions; the rendered output must contain a `Rollout` for `plant-api`.
Run `actionlint` and `act -l`, then `kubectl kustomize deploy/k8s/overlays/canary`; inspect the job listing and rendered kind/name pair.
Then re-run `make check-13`.

## Comprehension questions
1. What is the difference between “CI that deploys with `kubectl` using cluster credentials” and *pull* GitOps? Which credentials does the CI no longer need?
2. A teammate runs `kubectl edit deployment` in production to fix something urgent. What happens with `selfHeal`? Is it a system failure or a virtue? How is a legitimate urgent fix done?
3. Why is the deployment repository different from the code one? What problem does it avoid? (hint: CI loops and permissions)
4. In March 2026 attackers moved the tags of a popular scanner's GitHub Action to malicious code. Why did a pipeline that used a version tag get hit while one that used a commit SHA did not? What does a pipeline that ran it stand to lose, and what do you do afterwards?
5. What does an SBOM let you answer on the morning a new critical CVE is announced? Why is it not enough to scan only at build time?
6. Signing proves *who* vouches for an image, not that it is *good*. What attacks does a signature stop, and which ones does it not (think of a compromised build, a leaked key)? How does SLSA provenance add to it?
7. Why is the signature attached to a **digest** and not to a tag? What would you deploy by in Kubernetes, and what would enforce that only signed images are admitted?
8. Why gate only on `CRITICAL` vulnerabilities **with a fix**? What happens to a team whose gate blocks on everything?
9. Which part of `catalog-info.yaml` helps someone on call at 3 a.m.?
10. What would it take for the CI to update only the image tag in the deployment repository after a successful build? (This closes the CI → GitOps loop.)
11. Semgrep and Gitleaks look for different things. Give one problem each would catch that the other never could. Why is deleting a committed key in the next commit *not* a fix? What do you do instead?
12. Your `code_scan.sh` is red on day one because of code you did not write. What are the options (fix, exclude, baseline)? What does each cost, and why is “turn the job off” the worst one?
13. Kyverno refuses an unsigned image **at admission**, yet your CI already verified the signature. What does the second check protect against that the first one cannot? What happens to the cluster if Kyverno itself is down (`failurePolicy`)? What is the risk of making the policy match *every* image?
14. Why does `mutateDigest` matter even though the signature was verified? Describe the attack it closes (hint: time of check vs time of use, and a moving tag).
15. A canary at 20% gets 20% of the traffic for 5 minutes and nobody is watching. What has to be true of the analysis for that to be safe? Why does probing `/health` tell you nothing the readiness probe didn't?
16. Your analysis probe starts returning errors (HTTP 500) instead of a number. Should the Rollout continue, pause or abort? What does the default of Argo Rollouts do, and why is “fail open” dangerous for a release gate?
17. In production you would use a Prometheus metric. Write (in words) the query you would judge the canary by, and say how you would compare it with the stable version instead of with an absolute threshold. Why was it not used here?
18. Which of the following does a signature policy **not** stop: a stale `latest` tag, an image built by a compromised CI that holds the key, a pod that mounts the host filesystem? Which other Kyverno policy would you add first?

## Hints
<details><summary>Minimal workflow structure</summary>

`on: {push: {branches: [main]}, pull_request: {}}` · `permissions: {contents: read}` · `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}` · jobs `lint`, `test-python`, `web`, `iac`, `k8s-manifests` and `build-images` with `needs: [...]` and `strategy.matrix.service: [api, gateway, web]`.
</details>
<details><summary>Which actions to use</summary>

`actions/checkout`, `astral-sh/setup-uv`, `actions/setup-node` (with `cache: npm`), `opentofu/setup-opentofu`, `docker/setup-buildx-action` and `docker/build-push-action` (with `push: false`). Use each project's latest release and **pin it by SHA** (see the next hint).
</details>
<details><summary>Finding the commit SHA behind a tag</summary>

Level 1: the project's *Releases* page shows the tag; the tag points to a commit. Level 2: `git ls-remote https://github.com/OWNER/REPO 'refs/tags/vX.Y.Z*'` — an *annotated* tag lists two lines, and the one ending in `^{}` is the commit you want. Level 3: the `gh api` of the repository's git refs gives the same. Write the version in a comment on the same line so a human (or a bot) can read it.
</details>
<details><summary>Supply-chain job: which tools and how to install them pinned</summary>

You need `trivy`, `syft` and `cosign` on the runner. Each of the three projects publishes an installer action and release binaries; Trivy and Syft also publish container images. Whichever way you pick, the **tool version** is an input you must set to an exact release, and the **action** that installs it is pinned by SHA. Remember what the attack hit: *both* the action tags *and* the `latest` version.
</details>
<details><summary>The SBOM, the gate and the digest</summary>

Level 1: Syft reads an image straight from the Docker daemon and can write CycloneDX JSON to a file. Level 2: Trivy's `--severity`, `--ignore-unfixed` and `--exit-code` flags are the gate; `--format json --output` writes the report. Mind the order: if the scan fails the script, will the report still exist? Level 3: after `docker push`, ask the registry (or `docker buildx imagetools inspect`) for the digest, then sign `registry/name@sha256:…`, never the tag.
</details>
<details><summary>cosign: keys, secrets and the public transparency log</summary>

Level 1: `cosign generate-key-pair` makes `cosign.key` + `cosign.pub`. Keep them **outside** the repo (or git-ignore them); the public key can be derived from the private one, so CI only needs the private key as a secret. Level 2: cosign can read keys from environment variables (`--key env://NAME`); the password comes from `COSIGN_PASSWORD`. Level 3: by default cosign also publishes a record of every signature in the **public** Sigstore transparency log. For experiments against a local registry you don't want that, and recent cosign (v3) changed the flags that turn it off: read `cosign sign --help`. When verifying a signature that is not in the log you have to say so explicitly (`cosign verify --help`). Keep in mind that the signature will later be read by the admission controller (Kyverno, below), not only by `cosign verify`: where and in which layout cosign stores it matters then.
</details>
<details><summary>`act` and the local registry</summary>

Inside `act` the job runs in a container: `localhost` there is **not** your machine. Think about where the registry runs and how the job reaches it (a `services:` container, or a Docker network option for `act`). Also, `act` needs the key as a secret: `act -s NAME` reads it from your environment, so you never write it in a file of the repo.
</details>
<details><summary>Running the jobs with `act`: four snags that are not your bug (tested with act 0.2.89 on Apple silicon)</summary>

1. **`-j` runs the `needs` too.** `act -j supply-chain` also runs `lint` and `test-python` first, and `uv sync` then downloads PyTorch and CUDA wheels (several GB) into the job container. To run one job alone, point `-W` at a copy of the workflow without that job's `needs:` line (a throwaway file **outside** the repo).
2. **The runner image.** The first run asks for an image size and writes `~/.actrc`; the “medium” image is `-P ubuntu-latest=ghcr.io/catthehacker/ubuntu:act-latest` (note the spelling: `catthehacker`; add `--container-architecture linux/arm64` on Apple silicon so it is not emulated). It lacks `envsubst`, which `sigstore/cosign-installer` needs and which a real GitHub runner has: build a 2-line image `FROM` it with `gettext-base` installed (`docker build -t act-runner:local -`) and run with `-P ubuntu-latest=act-runner:local --pull=false`.
3. **Port 5000 is taken on macOS** (AirPlay Receiver) and the registry never starts: publish it on another port (the guide's `REGISTRY` is just `host:port`). The job container sees the host's published ports because `act` uses the host network. Also a container started by a *step* survives the job under `act` (the real runner is thrown away): finish the job with an `if: always()` step that removes it, or the next run fails with “port already allocated”.
4. **`actions/upload-artifact` fails** against `act`'s older local artifact server (`unknown field "mime_type"`). `act` sets `ACT=true`: guard the step with `if: ${{ !env.ACT }}`. Everything before it ran, which is what you wanted to see.
</details>
<details><summary>Code scan: rulesets, exclusions, installing the tools pinned</summary>

Level 1: `semgrep scan --help` lists `--config`, `--severity`, `--error`, `--exclude` and `--metrics`; by default findings do **not** change the exit code. `gitleaks dir --help` (scan a folder) and `gitleaks git --help` (scan history) exit 1 when they find something. Level 2: public rulesets such as `p/python`, `p/default` and `p/dockerfile` are downloaded at run time (they move!), so the *tool* is pinned but the *rules* are not: that is a conscious trade-off, or you vendor the rules into the repo. Which path would you exclude, and where do you write down why: `--exclude`, a `.semgrepignore`, or a `.gitleaks.toml` with an `[allowlist]` and `[extend] useDefault = true`? Level 3: Semgrep is a Python package (`pip install semgrep==X.Y.Z` after `actions/setup-python`); Gitleaks is a Go binary: download its release tarball **and compare its SHA-256** with a value you wrote down (the runner may be x64 and your laptop arm64: two values), or use its container image pinned by digest. Run both tools before deciding on the exit code, so one report does not hide the other, and keep the exit status of each. Test the script the way CI calls it (`bash scripts/code_scan.sh .` from the root of a clean checkout, not from your working folder full of `.venv` and `data/`): path patterns in exclusions behave differently for `.` and for an absolute path.
</details>
<details><summary>Kyverno: registry, signature layout, transparency log</summary>

Level 1: install it with `make kyverno-up`; `kubectl -n kyverno get pods` must show one admission controller. Test policies with `kubectl apply --dry-run=server -f pod.yaml`: the webhooks run, nothing is created. Level 2: the cluster must resolve and reach the registry: start it on the `kind` Docker network with a name (`docker run --network kind --name …`) and use `name:5000/plant-api:tag` in the pod (push from your machine through the published port; both names are the same registry). Kyverno here is installed with `registryClient.allowInsecure` because the registry speaks plain HTTP: never on a real registry. Level 3, the two classic walls: **(a)** *“no matching signatures: signature not found in transparency log”* — you signed without uploading to the public log (on purpose), so the policy has to say it does not require one (look at `rekor` and `ctlog` under the `keys` entry of the Kyverno `verifyImages` reference). **(b)** *“no signatures found”* although `cosign verify` passes — cosign v3 stores signatures in a new bundle layout by default and this Kyverno version reads the classic `sha256-….sig` layout: read `cosign sign --help` for the switch (and remember your CI and the check sign with the same command, so fix `scripts/supply_chain.sh`). A first verification can also be slow: raise `webhookTimeoutSeconds` if the first pod times out.
</details>
<details><summary>Kustomize: replacing the Deployment with a Rollout</summary>

Level 1: an overlay can list the base as a resource *and* add new resources; to remove one from the base use a `patches` entry with `$patch: delete`. Level 2: the Rollout's `template` is a Deployment's pod template: copy it, do not rewrite it, and keep every hardening line of module 12. `kubectl kustomize deploy/k8s/overlays/canary` must show a `Rollout` and no `Deployment/plant-api`. Level 3: the overlay can set `namespace:` for everything it renders (it also renames the `Namespace` object); your canary Application's `destination.namespace` and the namespace you use in the analysis URL must be that one.
</details>
<details><summary>Argo Rollouts: steps, services and the analysis that can fail</summary>

Level 1: `steps` are executed in order: `- setWeight: 20`, `- pause: {duration: 10s}` (or `pause: {}`: waits for a human, `kubectl argo rollouts promote`), `- analysis: {templates: [{templateName: …}]}`. The very first revision of a Rollout is not a canary (nothing to compare with): it goes straight to 100%; roll out a **second** revision to see the steps. Level 2: `stableService` and `canaryService` must exist; the controller edits their selectors (`rollouts-pod-template-hash`). The `web` provider **runs in the controller's pod**, in the `argo-rollouts` namespace, so a short Service name does not resolve: use the full `svc.cluster.local` name. `jsonPath` and `successCondition` read the JSON answer (`result.…`); a non-2xx answer is not a *failed measurement* but an **error**. Level 3: a probe that gets HTTP 500 is not the same thing as a probe that returns a bad value: look at the `AnalysisRun` of your own bad version (`kubectl -n <ns> get analysisrun -o yaml`) and see whether each measurement counts as *Failed* or as an *Error*, and what `failureLimit`, `consecutiveErrorLimit` and `count` do with each. Remember what `/health` is for: probe a path whose answer a regression could change while the pod is still Ready.
</details>
<details><summary>Argo CD and the canary overlay</summary>

The canary needs the Rollouts CRDs on the cluster before Argo can sync it (`make rollouts-up`). Its pods need `plant-secrets` in the **canary namespace** too: Secrets never live in Git, so create it there (`bash platform/tools/k8s_secrets.sh <namespace>`). If Argo ever shows the Services as `OutOfSync` or flapping, look at who edits their selectors: the Rollouts controller does (`rollouts-pod-template-hash`), and `ignoreDifferences` on `/spec/selector` is the tool for a field two controllers share. (With the pinned versions of the check, Argo CD stayed `Synced`; it may not happen to you.)
</details>
<details><summary>Argo doesn't sync</summary>

`kubectl -n argocd get application plant -o yaml` and look at `status.conditions`. Causes: a missing `default` project (in *core* mode you have to create it; the script already does), a repo the cluster can't reach (`http://forgejo:3000`), images not loaded into kind, a missing `plant-secrets` Secret.
</details>
<details><summary>Refreshing by hand</summary>

`kubectl -n argocd annotate application plant argocd.argoproj.io/refresh=hard --overwrite` forces Argo to read Git now (by default it polls every 3 minutes).
</details>

## Common traps
- Default `permissions` (too broad) and `@main` actions.
- Pinning a **tag** and believing it is pinned; or pinning the action but leaving the *tool* it installs on `latest`.
- Signing the **tag** instead of the digest; signing and never verifying; committing the private key “just for the exercise”.
- A scan that prints a report but cannot fail the job; a gate that blocks on every unfixable `LOW` until everyone turns it off.
- A CI that needs production secrets to *test*.
- Jobs without `timeout-minutes` that hang for 6 hours.
- Mixing code and deployment manifests in the same repository without thinking about it (every code commit triggers deployments).
- `selfHeal` turned off: the cluster and Git drift apart silently.
- A catalog with names in `dependsOn` that don't exist.
- A scanner pinned in version but run with a ruleset that moves, or a scan ending in `|| true`; excluding a whole directory “to get green” without writing why.
- A Kyverno policy in `Audit` mode (it only reports), one that matches every image (it blocks the system pods and gets deleted), or one that verifies the signature but does not pin the digest.
- Signing in a layout the admission controller cannot read: everything looks fine in CI (`cosign verify` passes) and the cluster refuses your own images.
- A canary whose analysis probes what readiness already probes; an analysis whose probe errors count as “nothing wrong”; a canary that is never given a second revision and therefore never runs.

## On a real job
*Keyless* signing with the workflow's own identity (Sigstore) instead of a long-lived key, signed **SLSA provenance** attestations that the admission policy also verifies (who built it, from which commit), Kyverno's newer CEL-based **`ImageValidatingPolicy`** (the `ClusterPolicy` you wrote is deprecated upstream), policies beyond signatures (no root, no host mounts, resource limits), a bot that proposes SHA-pin updates, continuous re-scanning of stored SBOMs (so yesterday's “clean” image is flagged when tomorrow's CVE lands), VEX statements to record which vulnerabilities do not affect you, **SARIF** upload of Semgrep results to a code-scanning dashboard, a ruleset you vendor and review instead of one that moves, scanning the *history* (not only the tree) for leaked credentials and rotating whatever turns up, environments (dev/staging/prod) with *overlays* and promotion by *pull request*, canaries judged by a **Prometheus** analysis (error ratio and latency of the canary compared with the stable version, via a `ServiceMonitor`) with **exact traffic weights** through an Ingress or a mesh, rollout notifications, and a real Backstage with templates to create new services with one click.
**Courses:** Uplimit's DevOps course and the CI/CD part of *Full Stack Open*.

→ Next: [`14_integration.md`](14_integration.md)
