---
description: Gives the NEXT hint for module N, from least to most help, without giving away the solution
argument-hint: <module number 0-18 or challenge R1-R9>
---
The learner is stuck on **module $ARGUMENTS**. First read the module's **mode** (`make modes`, or `modes.json`; missing = `guided`). Then read that module's guide (the file in `guide/` that starts with the two-digit number; for a challenge `RN`, `guide/challenges/RN_*.md`) and their current code.

1. Ask (if they didn't say) **what they have tried** and what result/error they got. If they already told you, don't repeat the question.
2. Diagnose the real obstacle (a missing concept, a misreading, a bug, an environment problem).
3. Give **a single hint**, at the lowest level that helps: first a question that points in the right direction; then the guide's hint; only as a last resort a minimal example with toy data (not the exact solution code).
   If the problem is the **environment** (Docker, ports, memory, tools), solve it directly: it is not part of the learning.
4. Ask them to try it and come back with the result. Don't solve the module.

## By mode
- **guided** (default): the steps above, as written.
- **solo**: the **smallest nudge** on the exact obstacle: a question first, one sentence, and nothing about the next steps. Never a step list. Escalate only if they come back still stuck, one notch at a time. Environment problems: solve them directly. If they have been stuck on the same thing for about 45 minutes, suggest switching that module to guided (`/mode $ARGUMENTS guided`).
- **walkthrough**: Claude wrote the module, so a hint is for the guide's **Your turn (walkthrough mode)** task, which is theirs: same graduated hints (question, then the guide's hint, then a toy example) and **never write the Your turn change for them**. If they ask for a hint on the part Claude is still building, just continue the walkthrough.
