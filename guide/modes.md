# Three ways to do each module

You choose **how** to do each module or challenge, one at a time. Go deep where you want the skill, go fast where you only want to understand how it fits.
Nothing is locked: you can change the mode at any moment, and the same `make check-N` decides when the work is done in every mode.

## What you will choose between

| Mode | Who builds | What Claude does | What you read | You are done when |
|---|---|---|---|---|
| **solo** | You, alone | Nothing unless you ask. With `/hint N` you get the smallest nudge on the exact obstacle (a question first), never a list of steps. Environment help (Docker, ports, tools) is always available. | *What you will build*, *The contract* and *How you will know you are done*. The rest only if you want it. | The check is green **and** you answer 2–3 comprehension questions in your own words |
| **guided** (default) | You, with Claude as a mentor | Analogies, questions, graduated hints and a review. It does not write the solution unless you say "do it" or "write it". | The whole guide | The check is green **and** you answer 2–3 comprehension questions in your own words |
| **walkthrough** | Claude, live, in small pieces | Writes the module's code one piece at a time. Before each piece it says what it is for and what breaks without it; after each piece it gives you the command to run and waits. It does the tedious parts (boilerplate, config, wiring) by itself and **stops at the decisions that are the point of the module**, so you choose. | The whole guide, as Claude walks you through it | You run the check (green), answer 2–3 comprehension questions **and** do the guide's **Your turn (walkthrough mode)** alone. Claude reviews it; it never writes it |

If you never set a mode, every module is **guided**.

## How to set a mode

- In Claude Code: `/mode 5 solo`, `/mode R3 walkthrough`, `/mode all guided` (without arguments it shows what you have).
- In the terminal: `make mode N=5 MODE=solo` (also `N=all`), and `make modes` to see the mode of every module and challenge.
- The choice is saved in `modes.json` at the root of the repository. `make progress` shows each module's mode next to its status.
- Start a module with `/module N`: Claude reads the mode first and starts accordingly.

## The contract does not change

Whatever the mode, some things stay yours:

- **Your notes.** Decision records, memos, interview notes and written answers (`notes/*.md`) are written by you. In walkthrough Claude explains the numbers and the options, but the judgement is yours.
- **The checks.** They are never edited to make them pass, and the hidden evaluation files stay hidden.
- **Module 15.** The client interview (`/client-plt02`) is always yours. In walkthrough Claude builds the connector only from *your* interview notes: if a fact is missing from them, it will not guess it, and the check will show the gap so you can go back and interview again.
- **Challenge R8.** In walkthrough Claude demonstrates the method (reproduce, compare with the ticket's definition, write a failing test, fix) on **one** ticket that you pick. The rest of the bugs are yours to find.

## How long does it take?

The hours at the top of each guide (the ⏱ line) are for **guided**. These multipliers are **estimates**, not measurements; your own pace will vary:

| Mode | Time compared with the ⏱ hours |
|---|---|
| solo | about 1.3–1.5× (you do all of it, and nobody shortens your detours) |
| guided | 1× |
| walkthrough | about 0.4–0.6× (Claude does the typing; you keep the running, the decisions, the questions and the Your turn) |

For example, a module marked 5–7 h in guided is roughly 7–10 h in solo and 2–4 h in walkthrough.

## Which one should I pick?

- **solo** for the modules at the heart of the role you are aiming for, when you want to be able to do it on your own at an interview or on the job.
- **guided** when you want to learn it but would like a teammate nearby. This is the default for a reason.
- **walkthrough** for the modules around the edges: you need to know what the piece is, why it exists and how it connects, but you do not need to type it yourself.

You do not have to do every module in the same mode. A common mix: your core modules in solo or guided, the supporting ones in walkthrough. The suggestions per role are in [role_paths.md](role_paths.md).

## Switching

- **Solo and stuck on the same thing for about 45 minutes?** Switch that module to guided (`/mode N guided`). You keep what you built; Claude starts asking questions and giving graduated hints.
- **Walkthrough, and one part interests you?** Switch to guided for it, build that part yourself, and switch back.
- **Guided and it is going fast?** Try solo on the next module.

Changing the mode never resets your progress or your files.

## A note on usage

Walkthrough uses **more of your Claude usage** than the other modes, because Claude writes the code, explains each piece and waits for you after each one. If you have a tight usage limit, keep walkthrough for the modules where it saves you the most time and use solo or guided for the rest.

## How each mode ends

1. `make check-N` is green (every mode).
2. You answer 2–3 comprehension questions from the guide in your own words (every mode). A green check is not the same as understanding.
3. In walkthrough only: you do **Your turn (walkthrough mode)** alone and re-run the check. `/review N` looks at that change and at your notes.
