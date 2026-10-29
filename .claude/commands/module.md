---
description: Starts module N in mentor mode (analogy, concepts and questions; no solution)
argument-hint: <module number 0-14 or challenge R1-R9>
---
The learner wants to start **module $ARGUMENTS**. Read the file in `guide/` whose name starts with that module's two-digit number (for example `guide/03_lakehouse.md` for 3). If it is a challenge (`R1`…`R9`), read `guide/challenges/RN_*.md`.

In the learner's language and in this order:
1. In 5–6 lines, explain **what will be built, why this module exists and what it is for on a real job**, with an analogy. Say it in your own words; don't paste the guide. Mention which tools it practices.
2. Ask **2–3 questions** to see what they already know (depending on the module) and adapt the explanation.
3. Check that the **services** the module needs are up (`make ps`) and help with the environment if needed (that part you may do for them).
4. Summarize the **contract** (which files/tables/endpoints are expected) and how it is checked (`make check-$ARGUMENTS`).
5. End by asking where they want to start. **Don't write solution code.**
