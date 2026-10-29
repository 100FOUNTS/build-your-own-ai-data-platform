# Module 12 — Containers and Kubernetes

⏱ 6–8 h · Services: `make up-stream` and Docker with ≥ 8 GB · Check: `make check-12` (⏱ ~3 min: builds 3 images and deploys to a cluster)
**Tools:** Docker, Kubernetes (here **kind**: “ephemeral, made for CI”; minikube or k3d are alternatives), Linux.

## What you will build
1. **Three Docker images**, secure and small — API (Python), gateway (Node) and web (Next.js) — with multi-stage *builds*, a non-root user and *healthchecks*.
2. The **Kubernetes manifests** (with Kustomize) to deploy the three services: Deployments, Services, ConfigMap, probes, resource limits, security context and a PodDisruptionBudget.
3. A **real deployment** on a local `kind` cluster called `plant`.

> A fair warning: *“Kubernetes is overkill for small teams; Docker and a managed service are enough to start with.”* You learn it here because it shows up in almost every platform job posting, but knowing when you **don't** need it.

## Why this module exists
“It works on my machine” ends with a container: the application travels with its exact dependencies. And Kubernetes answers the questions of running many containers: how many copies?,
what do I do if one goes down?, how do I update without cutting the service?, how do I limit how much memory it uses? Security starts here: an image that runs as root with a full shell is a gift to an attacker.

## Concepts
- **Image vs container**; **layers and cache**: every instruction is a layer. Copy **only** the dependency file first, install, and **then** copy the code: that way changing one line of code doesn't reinstall everything.
- **Multi-stage build**: you compile with all the tools in one stage and copy only the result into a minimal final image.
- **Non-root user**, **HEALTHCHECK**, **pinned base images** (`python:3.12-slim`, not `latest`), **`.dockerignore`**.
- **Pod, Deployment, Service, ConfigMap, Secret**. A Deployment keeps N replicas and does *rolling* updates.
- **Probes**: *readiness* (should it get traffic?) ≠ *liveness* (should it be restarted?). A liveness probe that is too aggressive kills healthy pods and creates a restart loop.
- **Requests and limits**: `requests` = what the scheduler reserves; `limits` = the ceiling before it gets killed (OOM). Without them, a leaking pod takes down the node.
- **securityContext**: `runAsNonRoot`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, read-only file system.
- **PodDisruptionBudget**: guarantees a minimum of available replicas during node maintenance.
- **Kustomize**: base + *overlays* per environment, no templates. **Secrets**: real values do **not** go into the repository; they are created in the cluster and *referenced* (`secretKeyRef`).

## The contract
**Dockerfiles** in `services/{api,gateway,web}/Dockerfile`, with the **build context at the repo root** (`docker build -f services/api/Dockerfile .`) and a root `.dockerignore` (with `.git`, `node_modules`, `data`, `.venv`…):
- base image with a version tag; `gateway` and `web` with **≥ 2 stages**; a non-root `USER`; `HEALTHCHECK`; no `ADD <url>`; dependencies copied **before** the code.
- **Maximum size**: api ≤ 700 MB · gateway ≤ 250 MB · web ≤ 400 MB. For the API create `services/api/requirements.txt` with only what it needs (don't install PyTorch in the API).
- They start, answer their `health` (api `/health` :8000 · gateway `/healthz` :8010 · web `/api/health` :3300) and their HEALTHCHECK reaches *healthy*; the process doesn't run with uid 0.
- Image tags for Kubernetes: `plant-api:dev`, `plant-gateway:dev`, `plant-web:dev`.

**Kubernetes** (`deploy/k8s/`): `base/` (+ `kustomization.yaml`) and `overlays/dev/kustomization.yaml`. Namespace `plant`. Resources `plant-api` (≥ **2 replicas**), `plant-gateway`, `plant-web` (Deployment + Service), a `ConfigMap` with the non-secret configuration and a `PodDisruptionBudget` for the API.
Every container: `requests` **and** `limits`; `readinessProbe` **and** `livenessProbe`; `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`; an image with a version tag.
**No `Secret` with values in the repo**: use `platform/tools/k8s_secrets.sh` (it creates `plant-secrets` in the cluster) and `secretKeyRef`. From the pods, the services on your machine are reachable as `host.docker.internal` (Kafka on port **29092**).

## Your task
1. Write the **API** `Dockerfile` first. Build it (`docker build …`), measure the size (`docker images`), start it and check `docker ps` (healthy?) and `docker exec <id> id`.
2. Optimize: can you get below 700 MB? Look at the output of `docker history`. Change one line of code and rebuild: which layers are reused?
3. Gateway (multi-stage Node with `npm ci` and `npm prune --omit=dev`) and web (Next's `output: "standalone"`).
4. Manifests: start with the API. `kubectl kustomize deploy/k8s/overlays/dev` must render without errors.
5. `make kind-up`, `kind load docker-image plant-api:dev --name plant` (and the other two), `bash platform/tools/k8s_secrets.sh`, `kubectl apply -k deploy/k8s/overlays/dev`.
6. Watch: `kubectl -n plant get pods -w`, `describe`, `logs`. `kubectl delete pod` one of the API replicas: does the service go down? `kubectl rollout restart deployment/plant-api`: is there an outage?
7. `kubectl -n plant port-forward svc/plant-api 8000:8000` and try `/health`.

## How you will know you are done
`make check-12` analyzes your Dockerfiles, **builds** them, measures sizes, starts each container to test its health and its user, renders your manifests and checks **production good practices**, validates them on the Kubernetes server (`--dry-run=server`)
and **deploys them on kind**, waiting for each Deployment to be available, with no restarts, and reachable through `port-forward`.

## Comprehension questions
1. You change one line of `main.py`. Why does your build take 5 s and not 3 minutes? (layers) What would happen if the `COPY . .` came before installing dependencies?
2. Why doesn't the final Next.js image include the whole `node_modules`? What does `standalone` bring?
3. A `livenessProbe` points to `/health` and that route queries the database. The database goes down for 30 s. What happens to your pods? How should it be designed? (readiness ≠ liveness)
4. What is the difference between memory `requests` and `limits`? What happens to a pod that goes over the limit? And to one that goes over the CPU limit?
5. Why aren't `Secret`s committed? What alternatives are there to get them into the cluster (sealed-secrets, external-secrets + OpenBao)?
6. When would you NOT use Kubernetes for this platform?

## Hints
<details><summary>Python Dockerfile</summary>

Stage 1: `python -m venv /opt/venv` + `pip install -r requirements.txt`. Stage 2: `COPY --from=build /opt/venv /opt/venv`, `USER 10001`, `HEALTHCHECK` with `python -c "urllib.request.urlopen(…)"` (the slim image has no curl).
</details>
<details><summary>Heavy images</summary>

`PIP_NO_CACHE_DIR=1`, don't install the whole project's dependencies (`pyproject.toml` includes PyTorch and Dagster), use `--no-install-recommends` with `apt-get`, and copy only the folders the service needs.
</details>
<details><summary>Pods in CrashLoopBackOff</summary>

`kubectl -n plant describe pod <p>` (events) and `kubectl -n plant logs <p> --previous`. Typical causes: probes that are too demanding (raise `failureThreshold`/`initialDelaySeconds`), a read-only file system without an `emptyDir` for `/tmp`, an image not loaded into kind (`ErrImagePull`: run `kind load docker-image`).
</details>
<details><summary>The pods can't reach Kafka</summary>

From a pod, `localhost` is the pod itself. Use `host.docker.internal:29092` (Redpanda's `k8s` listener in this environment).
</details>

## Common traps
- `FROM python:latest`, or installing compilers and leaving them in the final image.
- `COPY . .` first (it breaks the cache) and no `.dockerignore` (you send `data/` to Docker).
- Running as root “because that's how it works”.
- No `resources` (the greediest pod wins) or memory `limits` that are too tight (OOMKilled).
- `imagePullPolicy: Always` with local images: Kubernetes tries to download them from the internet.
- Putting a `Secret` with the password in the repository.

## On a real job
An image registry with vulnerability scanning (Trivy), signatures, an *Ingress* with TLS, HPA (autoscaling), `NetworkPolicy`, Helm, a managed service (EKS/GKE/AKS) and quota limits per namespace.
**Courses:** Uplimit's free DevOps course (Docker, Kubernetes, CI/CD, Terraform) and the containers part of *Full Stack Open*.

→ Next: [`13_cicd_gitops.md`](13_cicd_gitops.md)
