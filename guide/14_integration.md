# Module 14 — Final integration: operating what you built

⏱ 3–4 h · Services: `make up-db up-stream up-auth up-cloud` · Check: `make check-14`

## What you will build
What turns a set of services into a platform that **other people can operate**:
1. **`docs/runbook.md`**: the document for whoever is on call.
2. **`docs/adr/`**: ≥ 4 *Architecture Decision Records* that explain **why** you made the big decisions.
3. An **end-to-end test**: an alert is born in the API, goes through Kafka and arrives live in the browser and in the cloud (SNS → SQS).

## Why this module exists
The code is not the product: the product is the **system working and operable**. At 3 a.m. nobody remembers why you picked Iceberg; the ADR does. Nobody knows what to do when the Kafka lag grows; the
runbook does. And the services can each pass their tests separately and still **not work together** (a different topic name, a token with another audience): that is what the integration test is for.

## Concepts
- **Runbook**: a concrete operational guide: how to start, how to know it is healthy, **what to do for each common incident** (symptom → step-by-step diagnosis → fix), how to restore.
- **SLO**: a measurable reliability target (“99.5% of requests without a 5xx over 30 days”); without a number it can neither be missed nor measured.
- **ADR**: a short, immutable document: context, decision, consequences, alternatives. When the decision changes, another ADR is written that supersedes the previous one.
- **Integration / smoke test**: a real user scenario that crosses several services.

## The contract
- **`docs/runbook.md`** with sections *Overview* (with a **mermaid** diagram), *Startup*, *SLOs* (≥ 3 with a numeric target), *Incidents* (≥ 5 cases `### title`, each with “Symptom:” and concrete steps), *Restore*; it mentions the API, gateway, Kafka and Postgres.
- **`docs/adr/NNNN-title.md`** (≥ 4), each with *Context*, *Decision*, *Consequences* and *Alternatives*, longer than a few lines.
- The end-to-end test (the check runs it with your services): an **operator** can't create an alert (403); an **engineer** can (201) → it is stored in Postgres → it reaches the **SSE gateway** through Kafka (a client connected with the operator's token) in < 20 s → the **notifier** publishes it to SNS and it reaches SQS;
  and without a token, both the API and the gateway answer 401.

## Your task
1. Go through the whole platform **from cold**: `make down`, start what is needed, run the pipeline and the API. Write down every step that failed or wasn't documented — that is your startup runbook.
2. For each incident: **cause it** (stop Keycloak, fill the DLQ, stop the consumer) and write down what you see in Grafana, what you do and how long it takes you to recover. Write only what you checked.
3. Define 3–4 realistic SLOs with the exact metric that measures them (you already have `http_server_duration_milliseconds` in Prometheus).
4. Write 4 ADRs about the decisions a new teammate would question most (table format, event bus, the database for the gold layer, vector search, authentication…). Include the **real** alternatives you ruled out and why.
5. Run the full demo and follow the trace of one alert from start to finish in Grafana/Tempo.

## How you will know you are done
`make check-14` reviews the structure and quality of your documentation, and then **starts your API and your gateway with real authentication**, creates a queue and a topic in local AWS, runs your notifier and walks through the alert scenario end to end, including the denial cases.

## Comprehension questions
1. Pick an incident from your runbook: in which Grafana panel do you see it first? Which alert warns you?
2. Which ADR would you rewrite if the data volume grew 100×? What is the first symptom that would warn you?
3. Of everything you built, what is the most fragile? How do you know (evidence, not intuition)?
4. If you could add **one** more automatic test, which one would prevent the most likely incident?
5. Which platform data should you not leave in a log, a trace or a panel? Where did you check?

## Hints
<details><summary>A good incident</summary>

“**Symptom:** alert `ApiHighErrorRate`. **Diagnosis:** 1) the “5xx errors” panel → which route? 2) the trace of a failed request in Tempo → which span fails? 3) if it is SQL: `pg_isready`. **Fix:** … **Verification:** the 5xx rate drops to 0 within 5 minutes.” Concrete, ordered, checkable.
</details>
<details><summary>A good ADR</summary>

It ends with “**This gets worse…**”. An ADR without costs isn't honest. And it links to the measurement that would show the decision stopped being a good one.
</details>

## Common traps
- A runbook that says “check the logs”.
- ADRs written months later, from memory, that justify instead of deciding.
- SLOs without a time window, or that can't be measured with what you have.
- Testing only isolated services.

## On a real job
Incident drills (*game days*), blameless *post-mortems*, error budgets that hold back deployments, on-call rotations and a service catalog (module 13) that every alert points to.

**When you finish**: write your retrospective in `notes/` and move on to the [industry challenges](challenges/README.md) on your path
(**R8**, legacy code, is the practice closest to your first month on a job).
