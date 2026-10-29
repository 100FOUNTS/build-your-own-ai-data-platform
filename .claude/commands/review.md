---
description: Reviews module N like a senior teammate: runs the check, reads the code and asks questions
argument-hint: <module number 0-14 or challenge R1-R9>
---
Review the learner's **module $ARGUMENTS**.

1. Make sure the module's services are up (`make ps`) and run `make check-$ARGUMENTS` (for a challenge `RN`: `make challenge-N` and the guide `guide/challenges/RN_*.md`). Interpret the result.
2. Read what they wrote. Evaluate it against the “Common traps” and “Comprehension questions” sections of the module guide.
3. Return the review in this format (in the learner's language, friendly but honest):
   - **What is good** (concrete, not generic).
   - **What is wrong or fragile**, with the why and what would happen in production. **Point out the problem, don't write the fix.**
   - **What the check doesn't verify** but is worth looking at (clarity, edge cases, security, operability).
   - **2–3 comprehension questions** for them to answer in their own words.
4. If the check passes and they answered well, say so clearly and suggest the next module according to their path (`guide/role_paths.md`). If not, what to reinforce before moving on.

Rubric per module — **0** readable commits, a hook that really blocks, a diagnosis script that fails when it should · **1** rules in the database (not only in the API), an index justified with EXPLAIN, atomic Redis operations ·
**2** produce→flush→commit order, a DLQ with reason and original, dedupe outside the process · **3** idempotency, partitioning, UTC time zone, no default credentials, meaningful dbt tests ·
**4** a time split with a gap, the right metric, a champion with a measurable criterion, UPSERT · **5** declared dependencies, errors not silenced, isolation by environment ·
**6** evaluate before improving, conceptual guardrails (not phrases), writes with human approval, step limits ·
**7** correct status codes, `extra=forbid`, cache with TTL, real readiness, strict TypeScript · **8** validate signature+issuer+audience+expiry, 401≠403, least privilege, no secrets in git, honest threats ·
**9** SSR, accessibility, token only on the server, component tests · **10** correlated signals, alerts with `for` and a summary, dashboards as code ·
**11** idempotent IaC, no IAM wildcards, names with the environment, “at least once” · **12** small non-root images, layer cache, distinct probes, requests/limits, no secrets in git ·
**13** CI with least privilege and pinned versions, GitOps with selfHeal, a catalog without dangling references · **14** a concrete, tested runbook, honest ADRs with real alternatives.
Challenges — **R1** translation at the boundary, declared (not guessed) version, range after converting, dedupe across formats · **R2** MERGE on a key, `date` partition in UTC, time travel, another engine reads it ·
**R3** exact equality before speed, lazy end to end, a repeatable benchmark · **R4** a justified strategy, no spurious versions, tests on the dimension ·
**R5** threshold chosen on validation, metrics recomputable from the artifacts, explanations per alert, honesty with few failures · **R6** a threshold calibrated with data, looking at WHAT it flags, telling real degradation from noise ·
**R7** validation on the tree, a least-privilege connection, an enforced limit, one retry · **R8** reproduce before fixing, a test that fails first, contract intact, a blameless postmortem (don't reveal bugs they didn't find) ·
**R9** the role of each piece (not just names), what doesn't carry over, cost with a method, a verifiable plan, lock-in.
