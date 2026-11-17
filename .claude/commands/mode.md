---
description: Shows or sets the learning mode (solo, guided, walkthrough) for a module or challenge
argument-hint: <module 0-18 | challenge R1-R9 | all> <solo|guided|walkthrough> (empty = show every mode)
---
The learner's input is: `$ARGUMENTS`

- If it is **empty**: run `make modes` and show the result. Add one line: `/mode N <solo|guided|walkthrough>` changes it, and `guide/modes.md` explains the three modes.
- Otherwise it should be `<N|RN|all> <mode>` (for example `5 solo`, `R3 walkthrough`, `all guided`). Run `make mode N=<N> MODE=<mode>` (`all` sets the default for every module without its own setting).
  If the command reports a bad key or mode, show the error and the valid options; do not edit `modes.json` by hand.
- Then say in **at most 3 lines** what changes for that module (or for all of them):
  - **solo**: you build it alone; Claude stays quiet unless asked (`/hint N` gives the smallest nudge); done = check green + 2–3 questions answered in your own words.
  - **guided**: you build it with Claude as a mentor: analogies, questions, graduated hints, review; Claude does not write the solution.
  - **walkthrough**: Claude builds it live in small pieces and explains each one; you choose at the real decisions, write your own notes, then do the guide's **Your turn** alone; done = check green + questions + Your turn.
- Do not start the module. If they want to, they run `/module N`.
