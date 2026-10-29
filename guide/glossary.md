# Glossary

If a word is missing, ask your mentor: `/why <word>`.

**A2A** — an emerging protocol for AI agents from different vendors to talk to each other. Worth keeping an eye on.
**ADR** — Architecture Decision Record: a short document that explains *why* an architecture decision was made.
**Agent (AI)** — a loop in which a model decides which tool to use, looks at the result and decides the next step.
**AppRole** — an OpenBao/Vault machine identity: `role_id` + `secret_id`.
**Argo CD** — a GitOps agent: it makes a Kubernetes cluster follow the state declared in a Git repository.
**Asset (data asset)** — in Dagster, a piece of data your pipeline produces (a table, a model, a file).
**Autoencoder** — a neural network that compresses and reconstructs its input; the reconstruction error is used to detect anomalies.
**Backstage** — a developer portal (service catalog, templates, documentation).
**Checkpoint (Spark)** — a record of the offsets a streaming job has processed, so it can resume without repeating or skipping.
**Chunking** — splitting documents into pieces before computing their embeddings.
**Collector (OpenTelemetry)** — a process that receives telemetry, processes it and sends it to its destinations.
**Conventional Commits** — a commit message convention (`feat:`, `fix:`…).
**Decision threshold** — the score above which the model raises an alert; it is chosen on validation according to the business cost. Challenge R5.
**Delta Lake** — Databricks' open table format (Parquet + a `_delta_log` transaction log); an alternative to Iceberg. Challenge R2.
**DLQ (dead-letter queue)** — a queue where what could not be processed is set aside, with its reason.
**Drift** — a change in the distribution of the data compared to the training data; it silently degrades the model. Challenge R6.
**Embedding** — a numeric vector that represents the meaning of a text.
**Eval** — a systematic measurement of an AI system's quality against a reference set.
**Fan-out** — publishing once and delivering to many subscribers (SNS → several SQS queues).
**GitOps** — the desired state lives in Git; an agent applies it and reverts any drift.
**Guardrail** — a control that limits what goes into, runs in and comes out of an AI system.
**HNSW** — an approximate nearest-neighbor index for fast vector search.
**IaC (infrastructure as code)** — describing infrastructure in versioned files (OpenTofu/Terraform).
**IAM** — identity and permission management in the cloud.
**Iceberg (Apache)** — an open table format on top of Parquet with transactions, snapshots and schema evolution.
**Idempotent consumer** — processing the same message twice gives the same result as processing it once.
**JWKS** — the set of public keys used to verify the signatures of an issuer's JWTs.
**JWT** — a signed token that carries identity and roles (`iss`, `aud`, `exp`, …).
**Keycloak** — an open-source identity server (OAuth 2.0 / OIDC).
**kind** — Kubernetes inside Docker, for development and CI.
**Kustomize** — customizing Kubernetes manifests with bases and *overlays*, without templates.
**Lakehouse** — data in cheap storage (S3) with database guarantees (transactions, schema).
**LangGraph** — a library to describe agents as state graphs.
**Least privilege** — granting only the permissions that are essential, and nothing more.
**Lock-in** — dependence on a vendor: what it would cost to leave. Challenge R9.
**MCP** — Model Context Protocol: a standard for an AI client to discover and use a server's tools.
**MERGE INTO** — “insert if it doesn't exist, update if it does” in one operation (the basis of idempotency in tables).
**Migration** — a numbered SQL file that takes the schema from one state to another; it is not edited after being applied.
**MLflow** — experiment tracking and model registry.
**OIDC** — OpenID Connect: an identity layer on top of OAuth 2.0.
**OpenBao** — an open-source secrets manager (fork of Vault).
**OpenTelemetry (OTel)** — the instrumentation standard for traces, metrics and logs.
**OpenTofu** — the open fork of Terraform.
**PKCE** — an OAuth extension that protects the code flow in public clients (browsers, mobile apps).
**Pod / Deployment / Service** — the unit, the replica controller and the stable address in Kubernetes.
**Polars** — a columnar, multi-threaded DataFrame library; in *lazy* mode it optimizes the whole query before running it. Challenge R3.
**Postmortem** — a blameless post-incident review: impact, root cause, fix and how to keep it from happening again. Challenge R8.
**PSI** — Population Stability Index: measures how much a distribution moved between two periods (> 0.25 is usually considered strong drift). Challenge R6.
**RAG** — retrieval-augmented generation: looking up relevant chunks and giving them to the model to answer.
**RBAC** — role-based access control.
**Readiness / Liveness** — probes: “can it take traffic?” / “does it need restarting?”.
**RED** — the Rate, Errors, Duration metrics.
**Regression test** — a test that fails with a known bug and passes without it; it stops the bug from coming back. Challenge R8.
**Runbook** — an operational guide to start, watch over and repair a system.
**SCD type 2** — *slowly changing dimension*: keeping one row per version of a piece of master data, with its validity interval. Challenge R4.
**Secret** — a credential that must never be in the code or in git.
**SHAP / contributions** — how much each feature contributed to the prediction of ONE row; it explains why an alert fires. Challenge R5.
**SLI / SLO** — a measurable indicator / a target on that indicator.
**SSE** — Server-Sent Events: the server pushes events over an open HTTP connection.
**STRIDE** — threat categories: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.
**Structured Streaming** — Spark's continuous processing, which treats a stream as an infinite table.
**Tempo / Loki** — Grafana's trace and log stores.
**Text-to-SQL** — translating natural-language questions into SQL queries with an LLM. Challenge R7.
**TTL** — how long a piece of data lives in a cache before it expires.
**UniForm** — a Delta option that lets Iceberg engines read the same table. Challenge R2.
**Valkey** — the open-source fork of Redis (same protocol).
