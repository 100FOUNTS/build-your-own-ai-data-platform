# Module 15 — A second plant: work as a forward-deployed engineer

⏱ 8–10 h · Services: `make up-db` and Docker (for the site box) · Check: `make check-15`
**Tools:** an interview (the `/client-plt02` role-play), HTTP + XML, PostgreSQL upserts, time zones, MCP, Docker Compose internal networks.
**Mode:** solo, guided or walkthrough — see [modes](modes.md) · set it with `/mode 15 <mode>`
**Part 2 — "The second plant".** The platform worked in PLT01, so the company bought a second plant. This module is the first of four: the next ones teach the assistant to read PLT02's handwriting (16, LoRA) and to run on the plant's own box (17, llama.cpp), and then to prove the rollout worked (18).

## What you will build
You do the job of a **forward-deployed engineer (FDE)**: the engineer who goes to the client with the product and makes it work *there*, in their mess, with their constraints. Four deliverables:

1. **Discovery.** You interview the maintenance lead of PLT02 (Claude plays her: `/client-plt02`) and write `notes/plt02_discovery.md`.
2. **A connector.** `connectors/plt02/sync.py`: an incremental, idempotent sync from the plant's legacy maintenance system (a CMMS) into the platform's `assets` and `work_orders`, plus the plant's asset register (`connectors/plt02/asset_map.csv`) and any migration it needs.
3. **An MCP server.** `connectors/plt02/mcp_server.py`: a **read-only, least-privilege** window onto that CMMS for the plant assistant.
4. **A site box.** `deploy/site/compose.site.yaml` (a stack whose network has **no way out**, proven offline) and `docs/runbook_plt02.md`, the short handover for the client's IT.

## Why this module exists
PLT02 differs from PLT01 in three ways that matter more than any algorithm:
- **A system that is not yours.** It runs a 15-year-old CMMS with a vendor that no longer exists: XML in a strange encoding, dates with no time zone, a paging scheme you must discover, an old server that falls over if you ask too fast.
- **A network that is not yours.** The internet is flaky and **the data must not leave the site**. Anything that quietly calls a cloud API is not a bug, it is a breach of contract.
- **Words that are not yours.** The 20 technicians write failure notes by hand, in free text with shop-floor shorthand (that is what modules 16 and 17 are for).

On a real job none of this is in the ticket. The ticket says "connect the platform to PLT02". The difference between a demo and something the client **keeps running** is made of facts that exist only in other people's heads: *"supervisors can fix a work order for a week, and the screen doesn't touch the modified stamp"*, *"the server is offline for the backup at 2 a.m."*, *"the union says no technician names"*, *"I can approve a pilot, but only the plant manager can approve anything that writes"*. Nobody will volunteer them: they are not hiding them, they just don't know you need them. An FDE's first skill is **asking**, and the second is turning what they said into code, a deployment and a document the client's one IT person can operate when you are gone. This module is those two skills, end to end.

## Concepts
- **Forward-deployed engineer.** Embedded with the client, accountable for the outcome and not only for code: discovery, integration, deployment, handover, and the *no* you say to scope that breaks a constraint.
- **Discovery interview.** Ask open questions first, then precise ones; confirm numbers; ask "who decides?" and "what would make you switch it off?". You can't ask about what you don't know exists: ask about *categories* (formats, time, identity, change, load, people, hardware, criteria, approval).
- **Anti-corruption layer.** The connector is the only place that knows the CMMS is weird. Everything on the platform side sees the canonical model (`assets`, `work_orders`). Don't let the old system's habits leak in.
- **Incremental sync with a watermark.** Remember how far you got (a *watermark*) and ask only for what changed since. The watermark lives **in the same database as the data**, so both move together or not at all.
- **Idempotency.** Running the sync twice must leave exactly the same data. That is what lets you retry, overlap and recover without thinking.
- **Overlap and late data.** A watermark is a promise about *the source's* clock. If the source can change old rows without moving that clock, you must look back. How far is a question for the client, not for you.
- **Revisions and duplicates.** When the same record can arrive twice, and in the wrong order, the source's own version number decides, never the arrival order.
- **Time zones and DST.** A timestamp with no offset is a *wall-clock time* in some zone. Two nights a year the wall clock lies: one hour never happens, another happens twice. Convert with a real zone database (`zoneinfo`), never with a fixed offset.
- **Transient failures.** 503 and 429 are *the server telling you something*: retry with **exponential backoff and jitter**, obey `Retry-After`, bound the number of attempts, and **fail loudly** when you give up. A truncated body is also a transient failure.
- **Quarantine.** A row you can't place (here: a pump nobody registered) is neither dropped nor allowed to stop the run: it is parked, reported, and replayed when the missing fact arrives.
- **MCP, least privilege.** A model's input is untrusted: whatever it sends to a tool can be a prompt injection. The tools must *be unable* to do harm (read-only key, no write tools, validated inputs, bounded outputs), not merely *promise* not to.
- **Internal network.** A Docker network with `internal: true` has no gateway: containers see each other and nothing else. Offline-by-construction beats "it just doesn't call out".
- **Handover runbook.** For one tired person with no context at 6 a.m.: how to see it is healthy, what to do for each failure you have actually seen, how to update it with no internet, who to call.

## The CMMS you will connect to
It runs as a platform fixture, like the plant simulator: `make plt02-cmms` (or `python platform/plt02/cmms/server.py --port 8702`), or as a container (`platform/plt02/cmms/Dockerfile`, build context `platform/plt02`). It is **deterministic** (same data on every start) and a bit hostile on purpose. You don't need to read its code: that is "the client's old system", and the point is to learn to work with something you can only observe. What follows is the vendor's documentation (all that exists, 11 pages long, and not always right):

- Authentication: header `X-Api-Key`. The fixture accepts the **read-only** key `ro-demo-key` and a **read-write** key `rw-demo-key`.
- `GET /api/v1/equipment` — the equipment list.
- `GET /api/v1/workorders` — work orders as XML. Filters: `modified_since` (`DD/MM/YYYY HH:MM`, local plant time), `asset` (a tag), `q` (text in the note), `status` (`OPEN`/`CLOSED`). Paging: `page_size` and `cursor` (take the `next` attribute of the `PAGE` element).
- `GET /api/v1/workorders/{id}` — one work order.
- It is the vendor's wrapper: there are other write endpoints (not documented here, and not yours to use).
- Every response has a `generated` time and `rev` on each work order.

You can watch what it does: `curl -H 'X-Api-Key: ro-demo-key' 'http://localhost:8702/api/v1/workorders?page_size=3'`. It starts with ~80 % of the history (the rest "happens" later, driven by the checks) and, like the real thing, sometimes answers 503 or 429. You may poke at it with the fixture's knobs (documented at the top of `platform/plt02/cmms/server.py`), but your code may never depend on them.

## The contract

### Discovery: `notes/plt02_discovery.md`
Markdown with these sections (headings containing the words in bold): **Client and stakeholders**, **Current system**, **Data quirks**, **Constraints**, **Success criteria**, **Approval and sign-off**, **Scope of the first release**, **Risks and open questions** (≥ 2 bullets). Write **what the client told you**, with the numbers, not what you expect. The check does not read your mind: it looks for the facts that only the interview reveals, section by section (the data facts under *Data quirks*, the site facts under *Constraints*, the numbers under *Success criteria*, who signs what under *Approval*), and tells you which **topics** are still missing, never the answers.

### The register: `connectors/plt02/asset_map.csv`
Columns `tag,asset_id,model,criticality,install_date,rated_flow_m3h,rated_kw`. One row per pump the client's register lists. It maps the **CMMS tag** to the **platform asset id** (`PLT02-Pxxx`) and holds the master data the CMMS does not have. The source is the client's sheet, not a pattern.

### The connector: `connectors/plt02/sync.py`
`python -m connectors.plt02.sync` runs **one** sync and exits `0` on success, **non-zero** if it could not finish. Configuration only through the environment:

| Variable | Meaning |
|---|---|
| `CMMS_URL`, `CMMS_API_KEY` | where the CMMS is, and the **read-only** key |
| `DATABASE_URL` | the platform database (the watermark and any other state live **here**) |
| `ASSET_MAP` | path of the register CSV (default `connectors/plt02/asset_map.csv`) |
| `SYNC_BACKOFF_BASE_S` | first backoff delay in seconds (default `0.5`; the check sets `0.01`) |
| `SYNC_MAX_RETRIES` | maximum retries per request (default `6`) |

What it writes, in the platform's own tables (extend the schema with **your own numbered migration**, never by editing an applied one):
- `assets`: the 12 register pumps, `plant = 'PLT02'`. Never touches PLT01's.
- `work_orders`: `wo_id = 'PLT02-' + the CMMS id` (two plants, one table: ids must not collide); `asset_id` from the register; `wo_type = 'CORRECTIVE'`; `opened_at`/`closed_at` as the **correct UTC instants**; `failure_mode = 'unclassified'` until module 16 gives you one (a corrective order requires one, and *unknown* is honest); `cost_usd = 0`, `downtime_hours = 0` (the CMMS doesn't hold them); `description` = the technician's note, exactly as written (accents included).
- It must satisfy every constraint of module 1: the platform's rules are the judge of your mapping.
- **Not the technician.** No technician identifier may be stored anywhere in the platform database.

Behaviours the check exercises (the *how* is yours; the *why* is the client's):
- full load through every page; the second run asks for **what changed**, not for everything; a run with nothing new changes nothing;
- rows that appear later, rows that **change** after you copied them, and the same row arriving twice or out of order;
- 503, 429 (obey `Retry-After`), truncated answers, a server that goes down halfway: bounded retries, no corruption, **no silent partial success**, and after recovery nothing missing;
- an unknown pump: the run goes on, the row is not lost, and appears once the pump is in the register (`ASSET_MAP`);
- impossible or ambiguous times, and a close date that cannot be true.

### The MCP server: `connectors/plt02/mcp_server.py`
`python -m connectors.plt02.mcp_server` (stdio, like module 6) with `CMMS_URL` and `CMMS_API_KEY` in the environment. Tools:
- `list_assets()`
- `get_asset_history(asset_tag, limit=5)`: the most recent work orders of one pump, newest first. Tags as the technicians type them (`B-07`, `b07`) must work.
- `get_work_order(wo_id)`
- `search_work_orders(query, limit=10)`: text in the notes.

Rules: **read-only** (no tool that writes, nothing but `GET`, only the read-only key); inputs validated before they become part of a request; outputs bounded (the assistant runs a small local model: *never more than 20 work orders in one answer*); timestamps that say their zone; clear messages for unknown things, not exceptions; and the answers contain what the client allows them to contain.

### The site box: `deploy/site/compose.site.yaml` and `docs/runbook_plt02.md`
A Compose file with the services **`db`** (Postgres, user `plant`, database `plant`), **`cmms`** (the mock, built from `platform/plt02`) and **`sync`** (your connector, able to run `python -m connectors.plt02.sync` with its own environment), in which:
- **every** service is attached **only** to networks that are `internal: true`; no `network_mode: host`; no port published to the LAN;
- images are pinned (no `:latest`), because the box can't pull;
- every service restarts by itself;
- no secret that gives write access is configured anywhere.

`docs/runbook_plt02.md`: sections *Overview*, *Start / stop*, *Health checks*, *Common failures* (≥ 3: symptom, cause, action), *Backup and restore*, *Update* (with no internet), *Contacts and escalation*, *Sign-off*. 250 to 1800 words. Concrete commands, real names, the CMMS's busy windows, and where the approval boundaries are.

## Your task
1. **Prepare the interview** (20 min). Write the questions you want answered, *by category*, before you talk to anyone. What would hurt you most if you found it out in week 6? Then start: `/client-plt02`. She is busy, answers only what you ask, and gives you roughly 20 minutes (a second short call if you need it). Take notes **during** the call, in your words. `/client-plt02 debrief` tells you which **areas** you haven't asked about (no answers).
2. **Write `notes/plt02_discovery.md`.** Separate what the client **said** from what you **assume**. Anything you assume goes to *Risks and open questions*.
3. **Look at the CMMS before you code.** `curl` it. Page through it by hand. Decode a note and look at the bytes. Find three things that the vendor's documentation does not say, and compare them with your notes: which did the client warn you about, and which did you find alone?
4. **Design on paper** (`notes/`): what is the watermark, where does it live, what are you going to do when the same work order arrives twice, when it changes while you are not looking, when the clock changes? What is the order of operations that makes a crash in the middle harmless?
5. **Write the register** `asset_map.csv` from the client's sheet and the migration for your state tables. Check each row against what she told you, not against what looks plausible.
6. **Write `sync.py`**, one behaviour at a time, running it against the fixture after each (and with `make check-15` for the full proof). Start with the happy path, then break it: stop the fixture in the middle, restart it, make it slow.
7. **Prove the time handling** with a few cases of your own, not only with the check: a work order opened in the hour that does not exist, one in the hour that happens twice, one that straddles it. Ask the client's rule for each one (you wrote it down, didn't you?).
8. **Write the MCP server.** Then attack it as if you were a model that had been prompt-injected: what is the nastiest thing you can make it send to the CMMS? Make that impossible.
9. **Build the site box**: write the compose file, the connector image, bring it up, and **prove** it works offline: try to reach the internet from inside it (it must fail), then run the sync (it must work). Keep the commands you used: they go in the runbook.
10. **Write the runbook** for Rodrigo. Then read it as Rodrigo at 6 a.m. with a failing box: could you act on every line?
11. **Present to the client**: `/client-plt02 proposal` with your plan (scope, what is read-only, what you will measure, how it goes live). Does she sign? What exactly would she sign today, and what would need someone else?

## How you will know you are done
`make check-15`:
- reads your **discovery notes** and **runbook** for the facts and the structure (it tells you which *topics* are missing, never the answers);
- checks your **register** against the client's sheet;
- starts the mock CMMS as a process and a temporary database built from **your migrations**, then runs **your sync** through full loads, incremental runs, supervisors' late corrections with stale duplicates, a flaky server (503, 429, truncated bodies), a `Retry-After` it can measure, an outage in the middle of a run, an unknown pump, and daylight-saving edge cases, comparing the database with the truth computed independently from the generator;
- starts **your MCP server** with a real MCP client, calls every tool, throws hostile inputs at it and then reads what the CMMS saw: methods, keys, paths, parameters;
- reads your **compose file** (`docker compose config`) for internal networks, pinned images, restart policies and ports, and (it is marked slow: `make progress-all`) **builds and starts the site box and tries to escape**: it must reach the CMMS and must not reach 1.1.1.1 or resolve a public name, and the sync inside it must work.

## Your turn (walkthrough mode)
On your own, add one rehearsed recovery verification to `docs/runbook_plt02.md`, using only your own interview notes and the site stack you built.
Run `docker compose -f deploy/site/compose.site.yaml run --rm sync python -m connectors.plt02.sync` twice with your documented site configuration.
Before and after the second run, run `docker compose -f deploy/site/compose.site.yaml exec -T db psql -U plant -d plant -c 'SELECT (SELECT count(*) FROM assets) AS assets, (SELECT count(*) FROM work_orders) AS work_orders;'`; with no source changes, both counts must stay unchanged.
Document the exact comparison command and what a healthy result looks like; take unanswered client questions back to the interview. Then re-run `make check-15`.

## Comprehension questions
1. Your sync worked for two weeks and then the plant manager says two work orders show the wrong close time. Which three questions do you ask before you open the code?
2. Why does the watermark live in the same database as the data? What breaks if it lives in a file next to the script? And if you move it forward before the last page is stored?
3. The CMMS supports `modified_since`. Why is that not enough here? How did you decide how far to look back, and what does it cost you?
4. Explain the two nights a year when a local timestamp with no offset is wrong, and the rule the client gave you for each. Why is "convert with the offset of that day" not an answer?
5. A work order arrives twice with different revisions, the old one *after* the new. Which one do you keep and why not "the last one I saw"?
6. Your MCP server is read-only by construction. List the *three layers* that make it so (not "I don't write code that writes"), and say which one would still protect you if the other two had a bug.
7. A colleague proposes to make the site box call a hosted LLM "only for the hard notes". Which sentence of the client's answers kills that, and what would you do instead?
8. Who can approve what at PLT02? Describe the path from pilot to production and what evidence each step needs.
9. Rodrigo is on site one day a week. Which three sentences of your runbook would he most need, and which would he never read?
10. Which fact in your notes would have cost you the most if you had not asked about it? Which area of the interview did you skip, and what is the first thing that could go wrong because of that?

## Hints
<details><summary>I don't know what to ask</summary>

Go category by category: *the system* (what is it, how old, who supports it), *the export* (format, encoding, dates), *time* (zone, clock changes), *identity* (are the pump names in the CMMS the platform's names? who keeps the register? what is new?), *change* (can old records be edited? for how long? what moves when they are?), *load* (how fast may you ask? when is it busy?), *people* (who writes the notes? what may you show about them?), *site* (hardware, network, who touches it), *success* (a number, a test, a deadline) and *approval* (who says yes to what). "Is there anything else I should know?" is a good last question and a poor first one.
</details>
<details><summary>The text I read has strange characters</summary>

Look at the **first line of the XML** and at the HTTP headers. Who declares the encoding? Who is allowed to ignore it? Give your XML parser **bytes**, not a string you already decoded with a guess.
</details>
<details><summary>The second sync loads everything again / misses a correction</summary>

Two different problems with one root: what does "modified" mean *to the source*? Compare what the client said about corrections with what the filter actually sees. A watermark is only as good as the clock it trusts.
</details>
<details><summary>An old revision overwrote a new one</summary>

Order of arrival is not order of truth. What field of each record says which version it is? Could the database enforce "only if newer" in the same statement that writes?
</details>
<details><summary>My times are right except twice a year</summary>

`datetime` has a `tzinfo` and a `fold`. Which instant does a wall-clock time with no offset denote when the clock went back? When it jumped forward? What does your library do by default, and is that what the client said?
</details>
<details><summary>My sync "works" but exits 0 after the server died</summary>

What does your retry loop do when it runs out of attempts? Who gets to know? And what had you already stored, and what had you remembered?
</details>
<details><summary>The MCP check says something reached the CMMS that shouldn't</summary>

Print what your server sends. Which of your parameters did the *model* choose? Where does a string from the model become part of a path or a query? Validate on the way in, with an allow-list, not a block-list.
</details>
<details><summary>My compose file validates but the check says a service can reach the internet</summary>

Which network does a service join when you don't say? Where does a published port come from? What does `internal: true` change in the network's own routing?
</details>
<details><summary>Building the sync image needs the internet but the box has none</summary>

Distinguish *build time* from *run time*. Where do you build? How does the result get onto a machine that can't pull? (The runbook's *Update* section is exactly this.)
</details>

## Common traps
- Starting to code before the interview (and coding what you imagine the CMMS is).
- Notes that record what you *expect*, not what you were *told*. A fact without a source is a guess.
- Decoding the response as UTF-8, or letting a library guess the encoding from the HTTP header.
- Treating the naive timestamps as UTC, or as the *current* offset of the zone, or fixing the offset at -04:00 / -03:00.
- A watermark that is a timestamp of *your* clock, a file, or something you advance before the data is safely stored.
- `INSERT` instead of an upsert, or an upsert that lets an older revision overwrite a newer one.
- Retrying everything forever (or nothing at all); sleeping a fixed second; ignoring `Retry-After`; swallowing the final error and exiting 0.
- Dropping the row you can't map, or inventing an asset for it.
- Storing the whole raw record "just in case": you stored the technician.
- An MCP tool that takes a free string and pastes it into a URL; a tool called `update_…` "that the model won't use".
- A site compose that is internal on paper but still has a network without `internal: true`, a published port, or an image that needs a pull.
- A runbook that says "check the logs".

## On a real job
Discovery is a standing activity, not a phase: every integration has a second round of facts after the first data arrives, and a good FDE writes them back into the notes. You would add a reconciliation job (the CMMS's counts vs the platform's, per day), alerts on stale watermarks, a dead-letter table you review weekly, schema-drift detection on the export, secrets from a vault on site, a signed offline update bundle (images + checksums + a rollback), a read replica or a nightly export when the old system must not be touched in working hours at all, and a proper acceptance test with the client's own questions.
You would also say **no** sometimes: to a write path before the right person has signed, to a "quick" cloud call, to a promise of 99 % before the client has told you what 99 % of what. And you would leave behind a one-page document and an alert that fires before the client has to call you.
**Courses:** *Site Reliability Engineering* (the Google SRE book, chapters on handing over and on incident response) and the HTTP/XML/time-zone sections of any serious data-engineering course; for the interview, *The Mom Test* (Rob Fitzpatrick) is the best short read there is.

→ Next: [`16_llm_finetuning.md`](16_llm_finetuning.md)
