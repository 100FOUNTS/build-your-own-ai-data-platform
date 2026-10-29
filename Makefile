# Exercise shortcuts. Requires uv and Docker Desktop (recommended: 12 GB of memory in Settings → Resources).
UV ?= uv
include platform.env
export
export PYTHONPATH := $(CURDIR)
DC = docker compose
.PHONY: help setup fix-venv up-db up-vector up-stream up-lake up-auth up-obs up-cloud up-git down ps kind-up kind-down gitops-up gitops-push history topics sim spark progress progress-all check bonus challenges challenge-% sim-v2 doctor migrate consumer dbt train dagster ingest evals api gateway web

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_%-]+:.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Install Python dependencies (uv)
	$(UV) sync
	-chflags -R nohidden .venv

fix-venv:  ## macOS: fixes "No module named ..." after a uv sync
	chflags -R nohidden .venv

# ------------------------------------------------------------------ platform (by profile)
up-db:  ## Postgres+pgvector and Valkey (Redis)
	$(DC) --profile db up -d --wait postgres valkey
up-vector:  ## Qdrant (vector store comparison, module 6)
	$(DC) --profile vector up -d --wait qdrant
up-stream:  ## Redpanda (Kafka) + web console on :8088
	$(DC) --profile stream up -d --wait redpanda redpanda-console
up-lake:  ## SeaweedFS (S3), Iceberg catalog and MLflow on :5001
	$(DC) --profile lake up -d --wait postgres seaweedfs iceberg-rest
	$(DC) --profile lake up s3-init
	$(DC) --profile lake up -d --build --wait mlflow
up-auth:  ## Keycloak (:8089) and OpenBao (:8200)
	$(DC) --profile auth up -d --wait keycloak openbao
up-obs:  ## OpenTelemetry Collector, Prometheus :9090, Grafana :3030, Loki, Tempo
	$(DC) --profile obs up -d --wait
up-cloud:  ## Floci: local AWS on :4566
	$(DC) --profile cloud up -d --wait floci
kind-up:  ## Local Kubernetes cluster (kind) named “plant”
	kind create cluster --config platform/kind-config.yaml || true
kind-down:  ## Delete the kind cluster
	kind delete cluster --name plant
gitops-up:  ## Argo CD (core) on kind + plant-deploy repo on Forgejo (requires kind-up and up-git)
	bash platform/tools/gitops_up.sh
gitops-push:  ## Publish deploy/k8s to the GitOps repo
	bash platform/tools/gitops_push.sh
up-git:  ## Forgejo (local Git for GitOps) on :3002
	$(DC) --profile git up -d --wait forgejo
down:  ## Stop everything (keeps the data in the volumes)
	$(DC) --profile all --profile tools down --remove-orphans
ps:  ## What is running
	$(DC) --profile all ps

# ------------------------------------------------------------------ starting data
history:  ## Generate 90 days of history (Parquet) in data/history
	$(UV) run python platform/tools/make_history.py
topics:  ## Create the raw topic plant.readings.raw
	$(DC) --profile stream exec redpanda rpk topic create plant.readings.raw -p 3 || true
sim:  ## Live simulator (accelerated): publishes readings to Kafka. Ctrl+C to stop
	$(DC) --profile tools run --rm --build sim live
spark:  ## Run a Spark job:  make spark JOB=pipelines/spark/my_job.py
	$(DC) --profile tools run --rm --build spark /jobs/$(notdir $(JOB))

# ------------------------------------------------------------------ checks
progress:  ## Summary of green modules (make progress-all includes the slow ones)
	$(UV) run python platform/tools/progress.py
progress-all:
	$(UV) run python platform/tools/progress.py --all
check-%:  ## Check one module: make check-3
	$(UV) run pytest checks/test_m$$(printf '%02d' $*)_*.py
check:  ## All checks (modules you haven't built yet fail with a clear message)
	$(UV) run pytest checks
bonus:  ## Optional extra exercises
	$(UV) run pytest checks -m bonus
challenge-%:  ## Check one industry challenge: make challenge-3  (guide/challenges/)
	$(UV) run pytest checks/test_r$$(printf '%02d' $*)_*.py -m challenge -s
challenges:  ## All industry challenges
	$(UV) run pytest checks/test_r*.py -m challenge
sim-v2:  ## Challenge 1: burst from the new gateway (v1+v2 mixed) on plant.readings.raw
	$(UV) run python checks/vendor_v2.py --n 3000

# ------------------------------------------------------------------ shortcuts for your services and pipelines
doctor:  ## Environment diagnosis (module 0)
	bash scripts/doctor.sh
migrate:  ## Apply your migrations and seeds to the local database
	$(UV) run python scripts/migrate.py && $(UV) run python scripts/seed.py
consumer:  ## Module 2: your Kafka consumer
	$(UV) run python -m pipelines.stream.consumer
dbt:  ## Module 3: dbt build (silver + gold)
	$(UV) run dbt build --project-dir pipelines/dbt --profiles-dir pipelines/dbt
train:  ## Module 4: train and publish the risk
	$(UV) run python -m ml.train
dagster:  ## Module 5: Dagster UI on http://localhost:3400
	$(UV) run dagster dev -m pipelines.dagster.definitions -p 3400
ingest:  ## Module 6: index the manuals in pgvector
	$(UV) run python -m genai.rag ingest
evals:  ## Module 6: assistant evaluations
	$(UV) run python -m genai.evals.run
api:  ## Module 7: API on http://localhost:8000/docs (AUTH_DISABLED=1 for development)
	AUTH_DISABLED=$${AUTH_DISABLED:-1} $(UV) run uvicorn services.api.app.main:app --reload --port 8000
gateway:  ## Module 7: Node.js gateway on :8010
	cd services/gateway && npm run build && AUTH_DISABLED=$${AUTH_DISABLED:-1} npm start
web:  ## Module 9: Next.js on http://localhost:3300
	cd services/web && AUTH_DISABLED=$${AUTH_DISABLED:-1} npm run dev
