# Module 13 — CI/CD, GitOps and a service catalog

⏱ 5–6 h · Services: `make kind-up up-git` (and module 12 done) · Check: `make check-13` (⏱ ~2 min + installing Argo CD the first time)
**Tools:** GitHub Actions (locally with **act**), **Argo CD** (GitOps), **Backstage** (internal portal), Git.

## What you will build
1. **`.github/workflows/ci.yml`**: the continuous integration pipeline — lint, Python tests, frontend tests and build, infrastructure validation (OpenTofu), Kubernetes manifest validation and building the three images in parallel.
2. **GitOps with Argo CD**: an `Application` that makes the cluster **follow a Git repository**. Deploying = `git push`.
3. A Backstage **`catalog-info.yaml`**: the inventory of your systems, components, APIs and resources with their owners and dependencies.

## Why this module exists
So far you deployed with `kubectl apply` from your terminal. That neither scales nor can be audited: who changed what?, which version is running today?, how do I roll back? The modern answer:
- **CI** checks **every change** automatically before it is integrated (the same `ruff` and the same tests, always).
- **GitOps** declares the cluster's desired state **in Git**; an agent (Argo CD) applies it and **reverts any manual change**. Git is the source of truth: auditing, review and *rollback* for free (`git revert`).
- **Backstage** answers “which services exist, who owns them and what do they depend on?” — vital when there are hundreds.

## Concepts
- **Workflow, job, step, matrix, needs**: the structure of GitHub Actions. `needs` orders jobs; `matrix` repeats a job with different values.
- **Least privilege in CI**: `permissions: contents: read`; actions **pinned to a version** (not `@main`); `timeout-minutes`; `concurrency` with `cancel-in-progress`.
- **Dependency cache** (uv, npm, Docker layers): people avoid a slow CI.
- **GitOps**: *pull* instead of *push* — the cluster pulls its state from Git. **Sync, prune and self-heal**.
- **Argo CD Application**: `source` (repo + path + revision) and `destination` (cluster + namespace). The deployment repository is usually **different** from the code one.
- **Backstage**: entities `System`, `Component`, `API`, `Resource`, `Group`; relations `dependsOn`, `providesApis`, `consumesApis`; everything with an `owner`.

## The contract
**CI** — `.github/workflows/ci.yml`:
- `on:` `pull_request` and `push` (to `main`); `permissions: contents: read`; `concurrency` with `cancel-in-progress: true`.
- **≥ 5 jobs**, all with `timeout-minutes`; no action pinned to `@main/@master/@latest`; a dependency cache (e.g. `astral-sh/setup-uv` with `enable-cache`).
- An image build job with **`strategy.matrix`** (api, gateway, web) and **`needs`** on ≥ 3 previous jobs (you don't build what doesn't pass).
- It must pass **actionlint** (the workflow linter) and `act -l` must be able to list it.

**GitOps** — `deploy/argocd/application.yaml`: `kind: Application`; `repoURL: http://forgejo:3000/gitops/plant-deploy.git` (that is how the cluster's pods see it), `targetRevision: main`, a `path` that exists in your repo (`deploy/k8s/overlays/dev`),
`destination.namespace: plant`, `syncPolicy.automated` with **`prune: true` and `selfHeal: true`**. Helper scripts: `make gitops-up` (installs Argo CD on kind and prepares the repo in Forgejo) and `make gitops-push` (publishes `deploy/k8s`).

**Catalog** — `catalog-info.yaml` (several YAML documents): 1 `System`, ≥ 4 `Component` (each with `type`, `lifecycle`, `owner`, `system`), ≥ 1 `API` of type `openapi` with its definition, ≥ 3 `Resource` (postgres, kafka…),
`dependsOn` / `providesApis` / `consumesApis` relations **with no dangling references**.

## Your task
1. **CI**: write the workflow job by job. Test each one locally with `act -j lint` (it needs Docker; the first time it downloads a ~1 GB image) or at least `act -l` and `actionlint`.
2. Think: which part of the CI **can't** run without services (Kafka, Postgres)? Leave it out or mark it; a fast CI relies on tests that need no infrastructure.
3. **GitOps**: `make kind-up up-git gitops-up gitops-push`; apply your `Application` (`kubectl apply -f deploy/argocd/application.yaml`) and watch Argo create the pods (`kubectl -n argocd get application`).
4. **Test self-healing**: `kubectl -n plant scale deployment/plant-web --replicas=3` and watch Argo bring it back to 1. Then change the replicas in `deploy/k8s`, `make gitops-push` and see the change reach the cluster **without touching kubectl**.
5. **Catalog**: describe your system the way the platform team would. Draw the dependency graph in `notes/` and compare it with your YAML.

## How you will know you are done
`make check-13` validates your workflow (security and structure rules, **actionlint** and `act -l`), your catalog (schema and references) and your `Application`; and then **runs it for real**: it installs Argo CD if missing, publishes your `deploy/k8s` to the local Git, applies your Application and waits for `Synced` + `Healthy`;
**it scales a Deployment by hand and checks that Argo reverts it**; it makes a commit that changes the gateway replicas and checks that the cluster applies it.

## Comprehension questions
1. What is the difference between “CI that deploys with `kubectl` using cluster credentials” and *pull* GitOps? Which credentials does the CI no longer need?
2. A teammate runs `kubectl edit deployment` in production to fix something urgent. What happens with `selfHeal`? Is it a system failure or a virtue? How is a legitimate urgent fix done?
3. Why is the deployment repository different from the code one? What problem does it avoid? (hint: CI loops and permissions)
4. Why are GitHub actions pinned to a version (or a SHA)? Describe the supply-chain attack.
5. Which part of `catalog-info.yaml` helps someone on call at 3 a.m.?
6. What would it take for the CI to update only the image tag in the deployment repository after a successful build? (This closes the CI → GitOps loop.)

## Hints
<details><summary>Minimal workflow structure</summary>

`on: {push: {branches: [main]}, pull_request: {}}` · `permissions: {contents: read}` · `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}` · jobs `lint`, `test-python`, `web`, `iac`, `k8s-manifests` and `build-images` with `needs: [...]` and `strategy.matrix.service: [api, gateway, web]`.
</details>
<details><summary>Which actions to use</summary>

`actions/checkout@v4`, `astral-sh/setup-uv@v5`, `actions/setup-node@v4` (with `cache: npm`), `opentofu/setup-opentofu@v1`, `docker/setup-buildx-action@v3` and `docker/build-push-action@v6` (with `push: false`).
</details>
<details><summary>Argo doesn't sync</summary>

`kubectl -n argocd get application plant -o yaml` and look at `status.conditions`. Causes: a missing `default` project (in *core* mode you have to create it; the script already does), a repo the cluster can't reach (`http://forgejo:3000`), images not loaded into kind, a missing `plant-secrets` Secret.
</details>
<details><summary>Refreshing by hand</summary>

`kubectl -n argocd annotate application plant argocd.argoproj.io/refresh=hard --overwrite` forces Argo to read Git now (by default it polls every 3 minutes).
</details>

## Common traps
- Default `permissions` (too broad) and `@main` actions.
- A CI that needs production secrets to *test*.
- Jobs without `timeout-minutes` that hang for 6 hours.
- Mixing code and deployment manifests in the same repository without thinking about it (every code commit triggers deployments).
- `selfHeal` turned off: the cluster and Git drift apart silently.
- A catalog with names in `dependsOn` that don't exist.

## On a real job
Image signatures, vulnerability scanning and SBOMs in CI, environments (dev/staging/prod) with *overlays* and promotion by *pull request*, Argo Rollouts (canaries), notifications and a real Backstage with templates to create new services with one click.
**Courses:** Uplimit's DevOps course and the CI/CD part of *Full Stack Open*.

→ Next: [`14_integration.md`](14_integration.md)
