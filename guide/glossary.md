# Glossary

If a word is missing, ask your mentor: `/why <word>`.

**A2A** — an emerging protocol for AI agents from different vendors to talk to each other. Worth keeping an eye on.
**Admission controller** — a Kubernetes component that inspects every object before it is stored and can reject it (for example, a pod with an unsigned image). Module 13.
**ADR** — Architecture Decision Record: a short document that explains *why* an architecture decision was made.
**Agent (AI)** — a loop in which a model decides which tool to use, looks at the result and decides the next step.
**AnalysisTemplate** — in Argo Rollouts, the test a canary must pass at each step (a metric, a web check or a job); if it fails, the rollout is aborted and reverted. Module 13.
**AppRole** — an OpenBao/Vault machine identity: `role_id` + `secret_id`.
**Argo CD** — a GitOps agent: it makes a Kubernetes cluster follow the state declared in a Git repository.
**Argo Rollouts** — a Kubernetes controller for progressive delivery: canary and blue-green releases with automatic analysis. Module 13.
**Asset (data asset)** — in Dagster, a piece of data your pipeline produces (a table, a model, a file).
**Autoencoder** — a neural network that compresses and reconstructs its input; the reconstruction error is used to detect anomalies.
**Backstage** — a developer portal (service catalog, templates, documentation).
**Block bootstrap** — resampling whole blocks (days, asset-weeks) instead of rows, so the confidence interval respects that neighbouring rows are not independent. Challenge R5.
**Burn rate** — how fast you are spending the error budget; 1× spends it exactly in the SLO window, 14.4× spends 2% of it in one hour. Module 10.
**Canary release** — sending a small share of traffic to the new version first and widening it only while it stays healthy. Module 13.
**Champion / challenger** — the model in production and a freshly trained candidate, scored on the same holdout; the candidate replaces the champion only if it is clearly better. Module 5.
**Checkpoint (Spark)** — a record of the offsets a streaming job has processed, so it can resume without repeating or skipping.
**Chunking** — splitting documents into pieces before computing their embeddings.
**Collector (OpenTelemetry)** — a process that receives telemetry, processes it and sends it to its destinations.
**Compatibility mode (schema registry)** — the rule a new schema version must follow: BACKWARD (new readers can read old data), FORWARD, FULL, or their *transitive* variants. Challenge R1.
**Conventional Commits** — a commit message convention (`feat:`, `fix:`…).
**cosign** — a Sigstore tool that signs container images and verifies those signatures before deploying. Module 13.
**Data contract** — an explicit, versioned agreement on the shape and meaning of the data a producer sends; it is enforced at the entrance, not discovered downstream. Challenge R1.
**Decision threshold** — the score above which the model raises an alert; it is chosen on validation according to the business cost. Challenge R5.
**Delta Lake** — Databricks' open table format (Parquet + a `_delta_log` transaction log); an alternative to Iceberg. Challenge R2.
**DLQ (dead-letter queue)** — a queue where what could not be processed is set aside, with its reason.
**Drift** — a change in the distribution of the data compared to the training data; it silently degrades the model. Challenge R6.
**Embedding** — a numeric vector that represents the meaning of a text.
**Error budget** — the share of failures an SLO allows (99% over 30 days = 1% bad requests); while there is budget left you can ship, when it runs out you stabilize. Module 10.
**Eval** — a systematic measurement of an AI system's quality against a reference set.
**Faithfulness** — an answer only says what its sources support: no invented numbers, no claims missing from the cited document. Module 6.
**Fan-out** — publishing once and delivering to many subscribers (SNS → several SQS queues).
**GenAI semantic conventions** — OpenTelemetry's standard names for spans and attributes of AI calls (`gen_ai.request.model`, token usage, tool calls). Module 10.
**Gitleaks** — a scanner that finds secrets (tokens, keys, passwords) committed to a Git repository. Module 13.
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
**Kyverno** — a Kubernetes policy engine written in YAML; `verifyImages` rules only admit images with a valid signature. Module 13.
**Lakehouse** — data in cheap storage (S3) with database guarantees (transactions, schema).
**LangGraph** — a library to describe agents as state graphs.
**Least privilege** — granting only the permissions that are essential, and nothing more.
**LLM-as-judge** — using a model to grade another model's answers; it has to be calibrated against human labels before you trust it. Module 6.
**Lock-in** — dependence on a vendor: what it would cost to leave. Challenge R9.
**MCP** — Model Context Protocol: a standard for an AI client to discover and use a server's tools.
**MERGE INTO** — “insert if it doesn't exist, update if it does” in one operation (the basis of idempotency in tables).
**Migration** — a numbered SQL file that takes the schema from one state to another; it is not edited after being applied.
**MLflow** — experiment tracking and model registry.
**OIDC** — OpenID Connect: an identity layer on top of OAuth 2.0.
**OpenBao** — an open-source secrets manager (fork of Vault).
**OpenTelemetry (OTel)** — the instrumentation standard for traces, metrics and logs.
**OpenTofu** — the open fork of Terraform.
**OWASP Top 10 (LLM / Agentic)** — the reference lists of the most common attacks on LLM applications and on AI agents (prompt injection, excessive agency…). Module 6.
**PKCE** — an OAuth extension that protects the code flow in public clients (browsers, mobile apps).
**Pod / Deployment / Service** — the unit, the replica controller and the stable address in Kubernetes.
**Polars** — a columnar, multi-threaded DataFrame library; in *lazy* mode it optimizes the whole query before running it. Challenge R3.
**Postmortem** — a blameless post-incident review: impact, root cause, fix and how to keep it from happening again. Challenge R8.
**Progressive delivery** — releasing a change gradually (canary, feature flags) with automatic checks that can stop it. Module 13.
**PSI** — Population Stability Index: measures how much a distribution moved between two periods (> 0.25 is usually considered strong drift). Challenge R6.
**PSI (population stability index)** — a number that measures how much a feature's distribution moved between two periods; a common drift alarm. Module 5.
**RAG** — retrieval-augmented generation: looking up relevant chunks and giving them to the model to answer.
**RBAC** — role-based access control.
**Readiness / Liveness** — probes: “can it take traffic?” / “does it need restarting?”.
**RED** — the Rate, Errors, Duration metrics.
**Regression test** — a test that fails with a known bug and passes without it; it stops the bug from coming back. Challenge R8.
**Runbook** — an operational guide to start, watch over and repair a system.
**SAST (static application security testing)** — finding vulnerable patterns by reading the code without running it (for example with Semgrep). Module 13.
**SBOM (software bill of materials)** — the list of every package inside an image, so you can tell in minutes whether a new vulnerability affects you. Module 13.
**SCD type 2** — *slowly changing dimension*: keeping one row per version of a piece of master data, with its validity interval. Challenge R4.
**Schema Registry** — a service that stores the versions of each topic's schema and refuses incompatible changes; Redpanda ships one built in. Challenge R1.
**Secret** — a credential that must never be in the code or in git.
**Semgrep** — an open-source static analysis tool with rules for insecure patterns in many languages. Module 13.
**SHAP / contributions** — how much each feature contributed to the prediction of ONE row; it explains why an alert fires. Challenge R5.
**SLI / SLO** — a measurable indicator / a target on that indicator.
**SSE** — Server-Sent Events: the server pushes events over an open HTTP connection.
**STRIDE** — threat categories: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.
**Structured Streaming** — Spark's continuous processing, which treats a stream as an infinite table.
**Subject (schema registry)** — the name under which a schema's versions are stored, usually `<topic>-value`. Challenge R1.
**Supply chain (software)** — everything your image is built from (base images, packages, CI actions, scanners); attacking it compromises you without touching your code. Module 13.
**Tempo / Loki** — Grafana's trace and log stores.
**Text-to-SQL** — translating natural-language questions into SQL queries with an LLM. Challenge R7.
**Trivy** — a vulnerability scanner for images, file systems and IaC; pin it by version or digest (it was itself compromised in March 2026). Module 13.
**TTL** — how long a piece of data lives in a cache before it expires.
**UniForm** — a Delta option that lets Iceberg engines read the same table. Challenge R2.
**Valkey** — the open-source fork of Redis (same protocol).
