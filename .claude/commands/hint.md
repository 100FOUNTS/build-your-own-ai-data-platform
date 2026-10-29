---
description: Gives the NEXT hint for module N, from least to most help, without giving away the solution
argument-hint: <module number 0-14 or challenge R1-R9>
---
The learner is stuck on **module $ARGUMENTS**. Read that module's guide (the file in `guide/` that starts with the two-digit number; for a challenge `RN`, `guide/challenges/RN_*.md`) and their current code.

1. Ask (if they didn't say) **what they have tried** and what result/error they got. If they already told you, don't repeat the question.
2. Diagnose the real obstacle (a missing concept, a misreading, a bug, an environment problem).
3. Give **a single hint**, at the lowest level that helps: first a question that points in the right direction; then the guide's hint; only as a last resort a minimal example with toy data (not the exact solution code).
   If the problem is the **environment** (Docker, ports, memory, tools), solve it directly: it is not part of the learning.
4. Ask them to try it and come back with the result. Don't solve the module.
