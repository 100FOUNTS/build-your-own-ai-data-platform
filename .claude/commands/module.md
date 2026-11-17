---
description: Starts module N in the learner's mode (solo, guided or walkthrough; default guided)
argument-hint: <module number 0-18 or challenge R1-R9>
---
The learner wants to start **module $ARGUMENTS**. Read the file in `guide/` whose name starts with that module's two-digit number (for example `guide/03_lakehouse.md` for 3). If it is a challenge (`R1`…`R9`), read `guide/challenges/RN_*.md`.

First read the module's **mode**: run `make modes` (or read `modes.json` at the repo root; a missing file or key means `guided`). Say it in one line and how to change it (`/mode $ARGUMENTS <solo|guided|walkthrough>`). Then, in the learner's language, follow the branch for that mode.
In every mode, check that the **services** the module needs are up (`make ps`) and help with the environment if needed (that part you may do for them).

## solo
Keep it to **3–4 lines**: what they will build, the contract (which files/tables/endpoints are expected), `make check-$ARGUMENTS`, and "use `/hint $ARGUMENTS` when you are stuck".
No quiz, no analogy, no step list, no questions. Stop there.

## guided (default)
1. In 5–6 lines, explain **what will be built, why this module exists and what it is for on a real job**, with an analogy. Say it in your own words; don't paste the guide. Mention which tools it practices.
2. Ask **2–3 questions** to see what they already know (depending on the module) and adapt the explanation.
3. Check the services (above).
4. Summarize the **contract** (which files/tables/endpoints are expected) and how it is checked (`make check-$ARGUMENTS`).
5. End by asking where they want to start. **Don't write solution code.**

## walkthrough
1. In 5–6 lines, explain **why the module exists and what it is for on a real job**, with an analogy, and which tools it practices.
2. Check the services (above).
3. List, in order, the **3–6 pieces** you will build (one line each: the piece and what it is for), and mark which decisions will be **theirs** (the ones that are the point of the module). Say that the guide's **Your turn (walkthrough mode)** and their decision notes (`notes/`) are theirs too.
4. **Build piece 1 only**: say what it is for and what breaks without it, write it, give the command to run it, and **stop and wait** for them to run it and tell you what they saw. Then continue piece by piece.
Boilerplate, config and wiring you do without asking; at a real decision you stop, lay out the options and let them choose.

Special case, **module 15** (every mode): it includes a client role-play. Tell the learner to run `/client-plt02` to interview the client; you must **not** play the client yourself and must **not** read `.claude/commands/client-plt02.md`, `checks/plt02_fixtures/` or `platform/plt02/cmms/`. Do not hint at what the client knows. In walkthrough, do not build any piece until their interview notes exist; then you build the connector only from **their own interview notes** (never guess a fact that is missing: the check will show the gap and they go back to interview).
