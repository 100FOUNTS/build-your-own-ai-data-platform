# Glossary

If a word is missing, ask your mentor: `/why <word>`.

**A2A** — an emerging protocol for AI agents from different vendors to talk to each other. Worth keeping an eye on.
**Adapter (LoRA)** — the small set of extra weights a LoRA fine-tune learns; it needs the same base model at inference, or can be fused into it. Modules 16-17.
**Admission controller** — a Kubernetes component that inspects every object before it is stored and can reject it (for example, a pod with an unsigned image). Module 13.
**ADR** — Architecture Decision Record: a short document that explains *why* an architecture decision was made.
**Agent (AI)** — a loop in which a model decides which tool to use, looks at the result and decides the next step.
**AnalysisTemplate** — in Argo Rollouts, the test a canary must pass at each step (a metric, a web check or a job); if it fails, the rollout is aborted and reverted. Module 13.
**Anti-corruption layer** — a boundary that translates a messy external system into your own clean model, so its quirks never spread. The connector of module 15.
**AppRole** — an OpenBao/Vault machine identity: `role_id` + `secret_id`.
**Argo CD** — a GitOps agent: it makes a Kubernetes cluster follow the state declared in a Git repository.
**Argo Rollouts** — a Kubernetes controller for progressive delivery: canary and blue-green releases with automatic analysis. Module 13.
**Asset (data asset)** — in Dagster, a piece of data your pipeline produces (a table, a model, a file).
**ATT** — average treatment effect on the treated: the mean effect of an intervention over the units (here pump-weeks) that actually received it. Module 18.
**Autoencoder** — a neural network that compresses and reconstructs its input; the reconstruction error is used to detect anomalies.
**Backoff with jitter** — waiting longer after each failed attempt (1x, 2x, 4x...) plus a random part so many clients do not retry at once; obey `Retry-After` when the server sends it. Module 15.
**Backstage** — a developer portal (service catalog, templates, documentation).
**Block bootstrap** — resampling whole blocks (days, asset-weeks) instead of rows, so the confidence interval respects that neighbouring rows are not independent. Challenge R5. Module 18 resamples whole pumps for the same reason.
**Burn rate** — how fast you are spending the error budget; 1× spends it exactly in the SLO window, 14.4× spends 2% of it in one hour. Module 10.
**Canary release** — sending a small share of traffic to the new version first and widening it only while it stays healthy. Module 13.
**Champion / challenger** — the model in production and a freshly trained candidate, scored on the same holdout; the candidate replaces the champion only if it is clearly better. Module 5.
**Chat template** — the exact way a model expects roles and turns to be wrapped in special tokens; training and serving must render it identically. Module 16.
**Checkpoint (Spark)** — a record of the offsets a streaming job has processed, so it can resume without repeating or skipping.
**Chunking** — splitting documents into pieces before computing their embeddings.
**CMMS** — computerized maintenance management system: where a plant records work orders and assets. PLT02's is a 15-year-old legacy one.
**Cohort (adoption cohort)** — the units that start the intervention in the same week; in a staggered rollout every cohort starts at a different time. Module 18.
**Collector (OpenTelemetry)** — a process that receives telemetry, processes it and sends it to its destinations.
**Compatibility mode (schema registry)** — the rule a new schema version must follow: BACKWARD (new readers can read old data), FORWARD, FULL, or their *transitive* variants. Challenge R1.
**Constrained decoding** — restricting, token by token, what a model may generate (a grammar or JSON Schema), so that invalid output is impossible. Module 17.
**Continuous batching** — a server decodes the active requests together and admits new ones between steps, which raises throughput. Module 17.
**Conventional Commits** — a commit message convention (`feat:`, `fix:`…).
**cosign** — a Sigstore tool that signs container images and verifies those signatures before deploying. Module 13.
**Data contract** — an explicit, versioned agreement on the shape and meaning of the data a producer sends; it is enforced at the entrance, not discovered downstream. Challenge R1.
**Decision threshold** — the score above which the model raises an alert; it is chosen on validation according to the business cost. Challenge R5.
**Delta Lake** — Databricks' open table format (Parquet + a `_delta_log` transaction log); an alternative to Iceberg. Challenge R2.
**Difference-in-differences (DiD)** — the change in the treated units minus the change in comparison units over the same period; it removes what is constant per unit and what is shared by everybody in a week. Module 18.
**DLQ (dead-letter queue)** — a queue where what could not be processed is set aside, with its reason.
**Drift** — a change in the distribution of the data compared to the training data; it silently degrades the model. Challenge R6.
**DST fold** — the wall-clock hour that happens twice when clocks go back (and the hour that does not exist when they go forward); a local time with no offset is ambiguous there. Python's `fold` attribute picks the occurrence. Module 15.
**Embedding** — a numeric vector that represents the meaning of a text.
**Error budget** — the share of failures an SLO allows (99% over 30 days = 1% bad requests); while there is budget left you can ship, when it runs out you stabilize. Module 10.
**Eval** — a systematic measurement of an AI system's quality against a reference set.
**Event study** — the effect by event time (weeks since adoption), including the weeks before it; the pre-adoption part is a test of the design. Module 18.
**Faithfulness** — an answer only says what its sources support: no invented numbers, no claims missing from the cited document. Module 6.
**Fan-out** — publishing once and delivering to many subscribers (SNS → several SQS queues).
**Forward-deployed engineer (FDE)** — an engineer embedded with a client who makes the product work in the client's real environment: discovery, integration, deployment, handover. Module 15.
**GBNF** — llama.cpp's grammar format; a JSON Schema is compiled into one. Module 17.
**GenAI semantic conventions** — OpenTelemetry's standard names for spans and attributes of AI calls (`gen_ai.request.model`, token usage, tool calls). Module 10.
**GGUF** — llama.cpp's single-file model format (weights, tokenizer, chat template, metadata). Module 17.
**Gitleaks** — a scanner that finds secrets (tokens, keys, passwords) committed to a Git repository. Module 13.
**GitOps** — the desired state lives in Git; an agent applies it and reverts any drift.
**Group-time ATT** — the effect of cohort g at week t estimated against units still untreated at t (Callaway–Sant'Anna idea); you average these cells as the question needs. Module 18.
**Guardrail** — a control that limits what goes into, runs in and comes out of an AI system.
**HNSW** — an approximate nearest-neighbor index for fast vector search.
**IaC (infrastructure as code)** — describing infrastructure in versioned files (OpenTofu/Terraform).
**IAM** — identity and permission management in the cloud.
**Iceberg (Apache)** — an open table format on top of Parquet with transactions, snapshots and schema evolution.
**Idempotent consumer** — processing the same message twice gives the same result as processing it once.
**Internal network (Docker)** — a network created with `internal: true`: containers reach each other and nothing else (no gateway, no DNS to the outside). Module 15.
**JWKS** — the set of public keys used to verify the signatures of an issuer's JWTs.
**JWT** — a signed token that carries identity and roles (`iss`, `aud`, `exp`, …).
**Keycloak** — an open-source identity server (OAuth 2.0 / OIDC).
**kind** — Kubernetes inside Docker, for development and CI.
**Kustomize** — customizing Kubernetes manifests with bases and *overlays*, without templates.
**Kyverno** — a Kubernetes policy engine written in YAML; `verifyImages` rules only admit images with a valid signature. Module 13.
**Lakehouse** — data in cheap storage (S3) with database guarantees (transactions, schema).
**LangGraph** — a library to describe agents as state graphs.
**Late-arriving data / look-back window** — rows that change after you copied them, without moving the source's "modified" stamp; you re-read a window as long as the source allows edits. Module 15.
**Least privilege** — granting only the permissions that are essential, and nothing more. For AI tools (module 15): a read-only key, no write tools, validated inputs, bounded outputs.
**llama.cpp / llama-server** — C/C++ inference engine and its OpenAI-compatible HTTP server; runs quantized models on CPU or Apple/other GPUs. Module 17.
**LLM-as-judge** — using a model to grade another model's answers; it has to be calibrated against human labels before you trust it. Module 6.
**Lock-in** — dependence on a vendor: what it would cost to leave. Challenge R9.
**LoRA** — low-rank adaptation: freeze the model and train small low-rank matrices on some layers; minutes of training, a few MB of adapter. Module 16.
**Macro-F1** — F1 per class averaged with equal weight, so rare classes count as much as common ones. Module 16.
**MCP** — Model Context Protocol: a standard for an AI client to discover and use a server's tools.
**MDE (minimum detectable effect)** — the smallest true effect a design would detect with the chosen power and significance level; computed before the data. Module 18.
**MERGE INTO** — “insert if it doesn't exist, update if it does” in one operation (the basis of idempotency in tables).
**Migration** — a numbered SQL file that takes the schema from one state to another; it is not edited after being applied.
**MLflow** — experiment tracking and model registry.
**Not-yet-treated** — units that will adopt later but are untreated now: valid controls until their turn. Module 18.
**OIDC** — OpenID Connect: an identity layer on top of OAuth 2.0.
**OpenBao** — an open-source secrets manager (fork of Vault).
**OpenTelemetry (OTel)** — the instrumentation standard for traces, metrics and logs.
**OpenTofu** — the open fork of Terraform.
**OWASP Top 10 (LLM / Agentic)** — the reference lists of the most common attacks on LLM applications and on AI agents (prompt injection, excessive agency…). Module 6.
**Parallel trends** — the assumption that, without the intervention, treated and comparison units would have moved together. Module 18.
**PKCE** — an OAuth extension that protects the code flow in public clients (browsers, mobile apps).
**Placebo lead (pre-trend test)** — an "effect" estimated for weeks BEFORE adoption, where it must be zero; if it is not, the comparison is contaminated. Module 18.
**Pod / Deployment / Service** — the unit, the replica controller and the stable address in Kubernetes.
**Polars** — a columnar, multi-threaded DataFrame library; in *lazy* mode it optimizes the whole query before running it. Challenge R3.
**Postmortem** — a blameless post-incident review: impact, root cause, fix and how to keep it from happening again. Challenge R8.
**Potential outcomes** — for each unit and period, the outcome with and without the intervention; we only ever observe one, which is why effects must be estimated. Module 18.
**Pre-registration** — writing the estimand, estimator, checks and decision rule down before looking at outcomes. Module 18.
**Progressive delivery** — releasing a change gradually (canary, feature flags) with automatic checks that can stop it. Module 13.
**PSI (population stability index)** — a number that measures how much a distribution moved between two periods (> 0.25 is usually considered strong drift); a common drift alarm. Module 5, challenge R6.
**Quantization (Q8_0, Q4_K_M)** — storing weights in fewer bits to shrink the model and read less memory per token, at some cost in accuracy. Module 17.
**Quarantine (data)** — parking a record you cannot place yet (and replaying it later), instead of dropping it or failing the whole run. Module 15.
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
**Slot (llama-server)** — one concurrent sequence; `-np N` makes N slots and splits the context among them. Module 17.
**SSE** — Server-Sent Events: the server pushes events over an open HTTP connection.
**Staggered adoption** — units adopt an intervention at different times instead of all at once. Module 18.
**STRIDE** — threat categories: Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege.
**Structured Streaming** — Spark's continuous processing, which treats a stream as an infinite table.
**Subject (schema registry)** — the name under which a schema's versions are stored, usually `<topic>-value`. Challenge R1.
**Supply chain (software)** — everything your image is built from (base images, packages, CI actions, scanners); attacking it compromises you without touching your code. Module 13.
**Tempo / Loki** — Grafana's trace and log stores.
**Text-to-SQL** — translating natural-language questions into SQL queries with an LLM. Challenge R7.
**Trivy** — a vulnerability scanner for images, file systems and IaC; pin it by version or digest (it was itself compromised in March 2026). Module 13.
**TTFT** — time to first token: queueing plus prompt processing before the answer starts streaming. Module 17.
**TTL** — how long a piece of data lives in a cache before it expires.
**TWFE (two-way fixed effects)** — a regression with unit and time dummies and a treatment dummy; under staggered adoption with dynamic or heterogeneous effects it puts negative weights on some cells and can be badly biased. Module 18.
**UniForm** — a Delta option that lets Iceberg engines read the same table. Challenge R2.
**Valkey** — the open-source fork of Redis (same protocol).
**Watermark** — the position a sync has reached (usually the newest source timestamp or id it has stored), so the next run asks only for what changed. Module 15.
