"""Module 15 — The second plant: work as a forward-deployed engineer.

Discovery notes and a runbook (read for facts only an interview reveals), a connector from a legacy CMMS (a deliberately awkward mock, platform/plt02/cmms)
into the platform's work_orders/assets, a read-only MCP server, and a site box that works with no network egress.

The checks recompute the truth from the generator behind the mock CMMS; they never look at your code, only at what it leaves in Postgres and at what it sends to the CMMS.
Reading checks/plt02_fixtures/ or the CMMS fixture to find the answers defeats the purpose of the module: the facts are in the client's head.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from conftest import ROOT, free_port, ok, py, seeded_db, serve, sh, tail

SERVER = ROOT / "platform" / "plt02" / "cmms" / "server.py"
FIXTURES = ROOT / "checks" / "plt02_fixtures"
SANTIAGO = ZoneInfo("America/Santiago")
RO, RW = "chk-ro-key", "chk-rw-key"
NO_FAULTS = {"p503": 0, "p429": 0, "p_truncate": 0, "dup_rate": 0, "fail_first_n": 0, "fail_first_status": 503, "down_after": None, "latency_ms": 0, "retry_after_s": 0}
FLAKY = {**NO_FAULTS, "p503": 0.2, "p429": 0.1, "p_truncate": 0.1, "dup_rate": 0.05}
CONNECTOR = ROOT / "connectors" / "plt02"
SITE = ROOT / "deploy" / "site" / "compose.site.yaml"


# ------------------------------------------------------------------ the mock CMMS as a process
class Cmms:
    def __init__(self, port: int):
        self.url = f"http://127.0.0.1:{port}"
        self.http = httpx.Client(base_url=self.url, headers={"X-Fixture": "1"}, timeout=30)

    def post(self, path: str, **body):
        r = self.http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def stats(self) -> dict:
        return self.http.get("/_admin/stats").json()

    def truth(self) -> dict:
        return self.http.get("/_admin/truth").json()

    def api_log(self, start: int = 0) -> list[dict]:
        return self.stats()["log"][start:]


@contextmanager
def cmms(fraction: float = 0.8, faults: dict | None = None):
    ok((ROOT / "platform" / "plt02" / "notes.py").exists(), "Missing platform/plt02/notes.py: the CMMS fixture needs it (it ships with the platform; did you delete it?).")
    port = free_port()
    env = {"CMMS_HOST": "127.0.0.1", "CMMS_PORT": str(port), "CMMS_RO_KEY": RO, "CMMS_RW_KEY": RW}
    with serve([sys.executable, str(SERVER), "--port", str(port)], env, f"http://127.0.0.1:{port}/health"):
        c = Cmms(port)
        c.post("/_admin/reset", seed=2, n=1500, initial_fraction=fraction, faults={**NO_FAULTS, **(faults or {})})
        yield c


# ------------------------------------------------------------------ truth
def asset_truth() -> dict[str, dict]:
    with open(FIXTURES / "assets_truth.csv", newline="") as f:
        return {r["tag"]: r for r in csv.DictReader(f)}


def local_to_utc(s: str, fold: int = 0) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=SANTIAGO, fold=fold).astimezone(UTC)


def squash(s: str) -> str:
    return " ".join(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s or "").split())


def expected(truth_rows: list[dict], amap: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for t in truth_rows:
        a = amap.get(t["tag"])
        if not a:
            continue
        o = local_to_utc(t["opened_at"])
        c = None
        if t["closed_at"]:
            c = local_to_utc(t["closed_at"])
            if c < o:  # the repeated hour: the close is on the second pass; otherwise it is a typo and the close date is dropped
                c2 = local_to_utc(t["closed_at"], 1)
                c = c2 if c2 >= o else None
        out[f"PLT02-{t['wo_id']}"] = {"asset_id": a["asset_id"], "opened_at": o, "closed_at": c, "note": squash(t["note"])}
    return out


def db_rows(url: str) -> dict[str, dict]:
    with psycopg.connect(url) as con:
        rows = con.execute("SELECT wo_id, asset_id, wo_type, opened_at, closed_at, failure_mode, description FROM work_orders WHERE wo_id LIKE %s", ("PLT02-%",)).fetchall()
    return {r[0]: {"asset_id": r[1], "wo_type": r[2], "opened_at": r[3], "closed_at": r[4], "failure_mode": r[5], "note": squash(r[6])} for r in rows}


def diff(got: dict[str, dict], want: dict[str, dict]) -> list[str]:
    problems = []
    missing, extra = sorted(set(want) - set(got)), sorted(set(got) - set(want))
    if missing:
        problems.append(f"{len(missing)} work orders missing (e.g. {missing[:3]})")
    if extra:
        problems.append(f"{len(extra)} work orders that should not be there (e.g. {extra[:3]})")
    bad = {"asset_id": [], "opened_at": [], "closed_at": [], "note": []}
    for k in set(got) & set(want):
        for f in bad:
            if got[k][f] != want[k][f]:
                bad[f].append(k)
    for f, ids in bad.items():
        if ids:
            k = sorted(ids)[0]
            problems.append(f"{len(ids)} work orders with the wrong {f} (e.g. {k}: got {got[k][f]!r}, expected {want[k][f]!r})")
    return problems


def sync_env(c: Cmms, url: str, **extra) -> dict[str, str]:
    return {"CMMS_URL": c.url, "CMMS_API_KEY": RO, "DATABASE_URL": url, "SYNC_BACKOFF_BASE_S": "0.01", "SYNC_MAX_RETRIES": "8", **extra}


def run_sync(c: Cmms, url: str, timeout: int = 240, **extra):
    ok((CONNECTOR / "sync.py").exists(), "Missing connectors/plt02/sync.py (guide/15_second_plant_fde.md, the connector contract).")
    return py(["-m", "connectors.plt02.sync"], env=sync_env(c, url, **extra), timeout=timeout)


def assert_synced(c: Cmms, url: str, amap: dict[str, dict] | None = None, what: str = "after the sync"):
    want = expected(c.truth()["rows"], amap or asset_truth())
    problems = diff(db_rows(url), want)
    ok(not problems, f"The platform does not match the CMMS {what}:\n- " + "\n- ".join(problems))


# ------------------------------------------------------------------ 1. discovery notes
SECTIONS = {
    "stakeholders": ("stakeholder",), "system": ("current system", "legacy"), "quirks": ("quirk",), "constraints": ("constraint",),
    "success": ("success", "acceptance"), "approval": ("approval", "sign-off", "sign off"), "scope": ("scope",), "risks": ("risk", "open question"),
}
# (key, what the fact is about, regexes that must ALL match). Category names say what to ask about, never the answer.
FACTS = [
    ("encoding", "the character encoding of the export", [r"latin[- ]?1|iso[- ]?8859|cp-?1252|windows[- ]?1252"]),
    ("datefmt", "the date format the CMMS writes", [r"dd/mm/y|day[- /]first|day[- /]month|dd-mm|european (date|format)|\b\d{2}/\d{2}/\d{4}\b"]),
    ("naive", "what the timestamps do (and do not) carry", [r"naive|no (utc )?(offset|time ?zone|tz|zone)|without (an? )?(offset|time ?zone)|local (plant )?time", r"santiago|chile"]),
    ("dst", "daylight saving behaviour", [r"daylight|dst\b|clock (change|goes|go)|repeated hour|twice|does not exist|doesn.t exist|skipped hour|ambiguous|repeat(s|ed)?\b|falls? back|spring(s)? forward|summer time|time change|horario de verano"]),
    ("window", "how long supervisors can correct closed work orders", [r"\b7[- ]?days?\b|seven[- ]days?|one[- ]week|a week|\b8[- ]?days?"]),
    ("modstamp", "what a correction does to the modified stamp", [r"(modified|timestamp|stamp).{0,120}(not|n't|never|without).{0,80}(updat|chang|bump|touch|move)|(not|n't|never).{0,60}(updat|touch|bump|chang|refresh).{0,60}(modified|timestamp|stamp)|(modified|timestamp|stamp).{0,60}(stay|remain)s?\b.{0,30}(same|unchanged|frozen)|(modified|timestamp|stamp).{0,60}unchanged"]),
    ("rev", "duplicates and revision numbers", [r"\brev\b|revision|duplicat"]),
    ("pagecap", "the page-size limit", [r"\b200\b"]),
    ("load", "rate limits and busy windows", [r"429|rate[- ]?limit|throttl|5 requests|shift change|05:45|13:45|21:45|backup"]),
    ("tags", "how asset tags are typed", [r"lower[- ]?case|trailing|b03|b 03|typed|whitespace|spaces"]),
    ("swap", "which two pumps are not what their number suggests", [r"b-?0?9\b.{0,80}b-?10|b-?10.{0,80}b-?0?9\b|p209|p210|\b9\b.{0,20}\b10\b.{0,60}(swap|switch|revers|mixed|exchang)"]),
    ("newpump", "a pump nobody has registered", [r"b[- ]?13|p213"]),
    ("type", "what the TYPE field is worth", [r"type.{0,80}(always|default|useless|worthless|unreliable|not (used|meaningful|reliable)|\bcm\b|ignore|meaningless)|always.{0,10}\bcm\b"]),
    ("privacy", "the rule about the technicians", [r"union|sindicato", r"technician|\btechs?\b"]),
    ("offline", "where the data may go", [r"leav(e|es|ing) the (site|plant|premises)|never leaves?|stays? on[- ]?(site|prem)|on[- ]prem|offline|no egress|air-?gap|no cloud|no internet|without (the )?internet"]),
    ("hardware", "the site machine", [r"16 ?(gb|gib|gigs?)\b"]),
    ("gpu", "the machine's accelerator", [r"no (dedicated )?gpu|without a (dedicated )?gpu|cpu[- ]only|only (a )?cpu|gpu[- ]less|(no|without|not have|n.t have)( a)?( dedicated)? (graphics|gpu)|no graphics"]),
    ("usb", "how updates reach the box", [r"usb|stick|sneaker"]),
    ("autonomy", "unattended operation", [r"14[- ]days?|(2|two)[- ]weeks?|fortnight|power (cut|outage|loss|failure)|blackout|\bups\b"]),
    ("latency", "the response-time target", [r"\b5[- ]?(s|secs?|seconds?)\b|five[- ]seconds?"]),
    ("accuracy", "the accuracy target", [r"\b90 ?(%|percent|per ?cent)|0\.9\b|nine out of ten|9 out of 10|9/10"]),
    ("freshness", "the freshness target", [r"30[- ]?min|half[- ]an[- ]hour|thirty minutes"]),
    ("fabrication", "what must never happen in an answer", [r"invent|fabricat|hallucinat|made[- ]up|mak(e|es|ing) (things |stuff |anything |it |them )?up\b|fake"]),
    ("signer", "who signs", [r"valdés|valdes|plant manager"]),
    ("shadow", "what happens before production", [r"shadow|parallel (run|period)"]),
    ("pilot", "the first, limited step", [r"pilot"]),
    ("readonly", "what the first phase may do to the CMMS", [r"read[- ]only|no writes?|nothing writes|(does not|doesn.t|must not|never|not) writes?|writes nothing|only reads?"]),
    ("cap20", "how much the assistant may show at once", [r"\b20 (work[- ]?orders?|wos?|rows|items|results|records|entries)|(at most|up to|no more than|not more than|max(imum)?( of)?|limit(ed)?( to| of)?|cap(ped)?( at)?) ?20\b"]),
]
SECTION_FACTS = {
    "quirks": ({"encoding", "datefmt", "naive", "dst", "window", "modstamp", "rev", "pagecap", "load", "tags", "swap", "newpump", "type", "privacy", "cap20"}, 7),
    "constraints": ({"offline", "hardware", "gpu", "usb", "autonomy"}, 3),
    "success": ({"latency", "accuracy", "freshness", "fabrication", "privacy"}, 3),
    "approval": ({"signer", "shadow", "pilot", "readonly"}, 3),
}


def split_sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^#{1,3}\s+(.+)$", text)
    return [(parts[i].strip().lower(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def find_section(secs: list[tuple[str, str]], keys: tuple[str, ...]) -> str | None:
    for title, body in secs:
        if any(k in title for k in keys):
            return body
    return None


def matches(fact, text: str) -> bool:
    return all(re.search(rx, text, re.I | re.S) for rx in fact[2])


def notes_problems(path: Path) -> list[str]:
    if not path.exists():
        return ["Missing notes/plt02_discovery.md. Interview the client with /client-plt02 and write down what you learn."]
    text = path.read_text()
    secs = split_sections(text)
    problems, bodies = [], {}
    for name, keys in SECTIONS.items():
        body = find_section(secs, keys)
        if body is None:
            problems.append(f"No section about: {name} (a heading containing one of {list(keys)}).")
        elif len(body.split()) < 25:
            problems.append(f"The section about {name} is too thin ({len(body.split())} words): write what you actually learnt.")
        bodies[name] = body or ""
    facts = {f[0]: f for f in FACTS}
    for sec, (keys, minimum) in SECTION_FACTS.items():
        found = {k for k in keys if matches(facts[k], bodies[sec])}
        if len(found) < minimum:
            missing = [facts[k][1] for k in sorted(keys - found)]
            problems.append(f"The section about {sec} has {len(found)} of the {minimum} facts expected. Not covered yet: {', '.join(missing[:6])}.")
    total = {k for k in facts if matches(facts[k], text)}
    if len(total) < 21:
        missing = [facts[k][1] for k in facts if k not in total]
        problems.append(f"Your notes hold {len(total)} of the {len(FACTS)} facts the client can give you; at least 21 are expected. Topics still missing: {', '.join(missing[:8])}.")
    risks = bodies.get("risks", "")
    if len(re.findall(r"(?m)^\s*(?:[-*]|\d+\.)\s+\S", risks)) < 2:
        problems.append("Risks and open questions: list at least 2 bullets. A discovery that ends with no open questions was not honest.")
    return problems


def test_discovery_notes_hold_what_only_an_interview_reveals():
    problems = notes_problems(ROOT / "notes" / "plt02_discovery.md")
    ok(not problems, "notes/plt02_discovery.md is not done:\n- " + "\n- ".join(problems) + "\n\nThe missing facts are in the head of the client: run /client-plt02 and ask. (Reading the fixtures does not count: you would not know what you did not ask.)")


# ------------------------------------------------------------------ 2. the asset register
def test_asset_map_matches_what_the_client_said():
    f = CONNECTOR / "asset_map.csv"
    ok(f.exists(), "Missing connectors/plt02/asset_map.csv (columns: tag,asset_id,model,criticality,install_date,rated_flow_m3h,rated_kw). The sheet the client gave you is the source.")
    with open(f, newline="") as fh:
        rows = {r["tag"].strip().upper(): r for r in csv.DictReader(fh)}
    truth, bad = asset_truth(), []
    for tag, t in truth.items():
        r = rows.get(tag)
        if not r:
            bad.append(f"{tag}: missing")
            continue
        for col in ("asset_id", "model", "criticality", "install_date"):
            if r.get(col, "").strip() != t[col]:
                bad.append(f"{tag}.{col}: {r.get(col)!r}")
        for col in ("rated_flow_m3h", "rated_kw"):
            try:
                if float(r.get(col, "")) != float(t[col]):
                    bad.append(f"{tag}.{col}: {r.get(col)!r}")
            except ValueError:
                bad.append(f"{tag}.{col}: {r.get(col)!r}")
    ok(not bad, "asset_map.csv differs from the client's register in " + str(len(bad)) + " places: " + "; ".join(bad[:6]) + ".\nWhere did each value come from? Anything you copied from a pattern instead of from the client is a guess.")


# ------------------------------------------------------------------ fixture guard
def test_the_cmms_fixture_never_exposes_the_labels():
    """Guards the fixture itself (not your work): the CMMS must not leak the hidden ground truth of module 16."""
    with cmms(fraction=1.0) as c:
        tags, cursor = set(), None
        for _ in range(20):
            r = httpx.get(c.url + "/api/v1/workorders", params={"page_size": 200, **({"cursor": cursor} if cursor else {})}, headers={"X-Api-Key": RO}, timeout=30)
            if r.status_code != 200:
                continue
            import xml.etree.ElementTree as ET

            root = ET.fromstring(r.content)
            tags |= {e.tag for e in root.iter()}
            nxt = root.find("PAGE")
            cursor = nxt.get("next") if nxt is not None else None
            if not cursor:
                break
        ok(tags <= {"WORKORDERS", "WO", "EQUIP", "TYPE", "OPENED", "CLOSED", "MODIFIED", "TECH", "STATUS", "NOTE", "PAGE"}, f"The CMMS exports unexpected fields: {sorted(tags)}")
        ok(all("label" not in row for row in c.truth()["rows"]), "The CMMS truth must never contain `label`.")


# ------------------------------------------------------------------ 3. the connector
@pytest.fixture(scope="module")
def first_load():
    with cmms(fraction=1.0, faults=FLAKY) as c, seeded_db() as url:
        with psycopg.connect(url) as con:
            before = con.execute("SELECT count(*) FROM work_orders").fetchone()[0]
            assets_before = con.execute("SELECT count(*) FROM assets").fetchone()[0]
        r = run_sync(c, url)
        ok(r.returncode == 0, "The first sync failed (the CMMS is flaky: 503, 429 and truncated answers are part of the job):\n" + tail(r))
        yield {"c": c, "url": url, "before": before, "assets_before": assets_before, "truth": c.truth(), "log": c.api_log()}


def test_full_load_maps_the_canonical_model(first_load):
    f = first_load
    want = expected(f["truth"]["rows"], asset_truth())
    ok(len(want) > 1000, "The fixture produced fewer rows than expected; is platform/plt02/notes.py the generator of 1500 work orders?")
    got = db_rows(f["url"])
    problems = diff(got, want)
    ok(not problems, "After the first sync the platform does not match the CMMS:\n- " + "\n- ".join(problems))
    ok(all(g["wo_type"] == "CORRECTIVE" and g["failure_mode"] for g in got.values()),
       "Every PLT02 work order must satisfy the platform's own rules (the CMMS TYPE is useless, so what are they all in practice, and what does a corrective one require?).")
    ok(sum(1 for r in f["log"] if r["path"] == "/api/v1/workorders" and "cursor" in r["query"] and r["status"] == 200) >= 5,
       "You must page through the whole export with the cursor the CMMS gives (it never returns more than 200 rows per page).")


def test_times_are_correct_across_daylight_saving(first_load):
    f = first_load
    probes, got = f["truth"]["probes"], db_rows(f["url"])
    want = expected(f["truth"]["rows"], asset_truth())

    def pair(key):
        k = f"PLT02-{probes[key]}"
        return got.get(k), want[k]

    g, w = pair("gap")
    ok(g and g["opened_at"] == w["opened_at"], "A work order opened in the hour that does not exist (September, 00:30) has the wrong instant. How do you turn a wall-clock time with no offset into UTC?")
    g, w = pair("fold")
    ok(g and g["opened_at"] == w["opened_at"], "A work order opened in the hour that happens twice (first Saturday of April, 23:30) has the wrong instant: which of the two did the client say to take?")
    g, w = pair("straddle")
    ok(g and g["closed_at"] == w["closed_at"] and g["closed_at"] is not None,
       "A work order opened 23:50 and closed 23:20 on the night the clock goes back must close 30 minutes later, on the second pass of the hour. A close before the open breaks the platform's own CHECK: what did the client say it means?")
    g, w = pair("typo")
    ok(g is not None, "A work order with an impossible close date (closed days before it was opened) was dropped. What did the client want done with it?")
    ok(g["closed_at"] is None, "A work order closed days before it was opened must be kept WITHOUT a close date (the client's rule), not with a wrong one.")


def test_accents_survive_the_encoding(first_load):
    f = first_load
    got = db_rows(f["url"])
    k = f"PLT02-{f['truth']['probes']['gap']}"
    ok("presión" in got[k]["note"] and "válvula" in got[k]["note"],
       f"The note of {k} lost its accents ({got[k]['note']!r}). The bytes on the wire are not UTF-8: who tells you what they are, and where is it declared?")
    ok(not any("Ã" in g["note"] or "�" in g["note"] for g in got.values()), "Some notes contain mojibake (Ã…) or replacement characters.")


def test_assets_are_loaded_from_the_register(first_load):
    f = first_load
    truth = asset_truth()
    with psycopg.connect(f["url"]) as con:
        rows = con.execute("SELECT asset_id, plant, model, criticality, install_date::text, rated_flow_m3h::float, rated_kw::float FROM assets WHERE plant = 'PLT02'").fetchall()
        n_all = con.execute("SELECT count(*) FROM assets").fetchone()[0]
    got = {r[0]: r for r in rows}
    ok(set(got) == {t["asset_id"] for t in truth.values()}, f"The PLT02 assets must be exactly the 12 pumps of the register (plant = 'PLT02'); found {sorted(got)}.")
    for t in truth.values():
        r = got[t["asset_id"]]
        ok((r[2], r[3], r[4], r[5], r[6]) == (t["model"], t["criticality"], t["install_date"], float(t["rated_flow_m3h"]), float(t["rated_kw"])), f"Asset {t['asset_id']} differs from the register: {r}")
    ok(n_all == f["assets_before"] + 12, "The sync must not touch the assets of PLT01.")
    with psycopg.connect(f["url"]) as con:
        n = con.execute("SELECT count(*) FROM work_orders WHERE wo_id NOT LIKE %s", ("PLT02-%",)).fetchone()[0]
    ok(n == f["before"], "The sync changed or deleted work orders of PLT01. Two plants share one table: how do two systems' ids avoid colliding?")


def test_an_unknown_asset_does_not_stop_the_sync(first_load):
    f = first_load
    got = db_rows(f["url"])
    ok(f"PLT02-{f['truth']['probes']['unmapped']}" not in got, "A work order of a pump that is not in the register (B-13) was loaded: to which asset? The platform needs a real one; inventing it is worse than waiting.")
    ok(len(got) > 1000, "The sync stopped at the unknown tag or lost everything else.")


def test_no_technician_reaches_the_platform(first_load):
    f = first_load
    pat = re.compile(r'^T(0[1-9]|1[0-9]|20)$')
    found = []
    with psycopg.connect(f["url"]) as con:
        cols = con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND data_type IN ('text','character varying','character','jsonb','json')").fetchall()
        for table, col, dtype in cols:
            if (table, col) == ("work_orders", "description"):
                continue
            rows = con.execute(f'SELECT "{col}"::text FROM "{table}" WHERE "{col}" IS NOT NULL').fetchall()
            for (v,) in rows:
                if pat.match(v.strip()) or (dtype in ("jsonb", "json") and re.search(r'"T(0[1-9]|1[0-9]|20)"|technician|tech"', v, re.I)):
                    found.append(f"{table}.{col}")
                    break
    ok(not found, f"Technician identifiers reached the platform database ({sorted(set(found))}). The agreement with the union says they must not: which fields of the CMMS do you actually need?")


# ------------------------------------------------------------------ incremental, corrections, faults
def test_sync_is_incremental_and_idempotent():
    with cmms(fraction=0.8, faults=NO_FAULTS) as c, seeded_db() as url:
        r = run_sync(c, url)
        ok(r.returncode == 0, "First sync failed:\n" + tail(r))
        t1 = c.truth()
        max_mod1 = max(x["modified"] for x in t1["rows"])
        assert_synced(c, url, what="after the first sync")
        c.post("/_admin/advance", fraction=1.0)
        n_log = len(c.api_log())
        served_before = c.stats()["rows_served"]
        r = run_sync(c, url)
        ok(r.returncode == 0, "Second sync failed:\n" + tail(r))
        wo_reqs = [e for e in c.api_log(n_log) if e["path"] == "/api/v1/workorders"]
        first = wo_reqs[0]["query"] if wo_reqs else {}
        ok("modified_since" in first, "The second sync asked for everything again. A sync that re-reads the whole system on every run does not scale: what do you remember between runs, and where?")
        since = datetime.strptime(first["modified_since"], "%d/%m/%Y %H:%M")
        newest = datetime.strptime(max_mod1, "%Y-%m-%d %H:%M")
        ok(since >= newest - timedelta(days=14), f"The second sync starts from {since}, much older than the newest change it had seen ({newest}): that is a full reload in disguise.")
        ok(since.year > 2000, "modified_since must be a real date.")
        assert_synced(c, url, what="after the second sync")
        # a third run with nothing new changes nothing
        with psycopg.connect(url) as con:
            snap = con.execute("SELECT wo_id, asset_id, opened_at, closed_at, description FROM work_orders WHERE wo_id LIKE %s ORDER BY 1", ("PLT02-%",)).fetchall()
        r = run_sync(c, url)
        ok(r.returncode == 0, "Third sync failed:\n" + tail(r))
        with psycopg.connect(url) as con:
            snap2 = con.execute("SELECT wo_id, asset_id, opened_at, closed_at, description FROM work_orders WHERE wo_id LIKE %s ORDER BY 1", ("PLT02-%",)).fetchall()
        ok(snap == snap2, "Running the sync again with nothing new changed the data: it must be idempotent.")
        ok(c.stats()["rows_served"] - served_before < 2 * len(t1["rows"]), "The syncs read far too much from the CMMS.")


def test_late_corrections_and_stale_duplicates_are_applied():
    with cmms(fraction=0.8, faults=NO_FAULTS) as c, seeded_db() as url:
        ok(run_sync(c, url).returncode == 0, "First sync failed.")
        c.post("/_admin/advance", fraction=1.0)
        ok(run_sync(c, url).returncode == 0, "Second sync failed.")
        t = c.truth()
        now = datetime.strptime(t["now"], "%Y-%m-%d %H:%M")
        recent = [r for r in t["rows"] if r["closed_at"] and datetime.strptime(r["closed_at"], "%Y-%m-%d %H:%M") <= now - timedelta(hours=2) and r["closed_at"] >= (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M")
                  and r["tag"] in asset_truth() and r["wo_id"] not in t["probes"].values()]
        ok(len(recent) >= 5, "The fixture has too few recent closed work orders to correct (does platform/plt02/notes.py span at least a few weeks?).")
        a, b, d, e = recent[:4]
        new_close = (datetime.strptime(a["closed_at"], "%Y-%m-%d %H:%M") + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        other = "B-04" if b["tag"] != "B-04" else "B-05"
        c.post("/_admin/correct", wo_id=a["wo_id"], fields={"closed_at": new_close})
        c.post("/_admin/correct", wo_id=b["wo_id"], fields={"note": "corregido por supervisor: cambio de sello y alineación"})
        c.post("/_admin/correct", wo_id=d["wo_id"], fields={"tag": other})
        c.post("/_admin/correct", wo_id=e["wo_id"], fields={"note": "primera corrección"})
        c.post("/_admin/correct", wo_id=e["wo_id"], fields={"note": "segunda corrección ñandú"})
        c.post("/_admin/faults", **{**NO_FAULTS, "dup_rate": 0.3})  # old revisions now arrive before and after the new ones
        r = run_sync(c, url)
        ok(r.returncode == 0, "Sync after the corrections failed:\n" + tail(r))
        assert_synced(c, url, what="after the supervisors' corrections (they edited closed work orders: closing time, note, pump; the CMMS's `modified` stamp did not move)")
        r = run_sync(c, url)
        ok(r.returncode == 0, "Sync with stale duplicates failed:\n" + tail(r))
        assert_synced(c, url, what="after a sync in which old revisions of corrected work orders arrived after the new ones (which copy is the truth?)")


def test_the_sync_survives_503_429_and_truncated_answers():
    with cmms(fraction=1.0, faults={**NO_FAULTS, "p503": 0.2, "p429": 0.1, "p_truncate": 0.1}) as c, seeded_db() as url:
        r = run_sync(c, url)
        ok(r.returncode == 0, "The sync gave up on a flaky CMMS (503, 429 and answers cut in half). Which of those are worth retrying, how often, and how long do you wait?\n" + tail(r))
        log = c.api_log()
        faults = [e for e in log if e["fault"]]
        ok(len(faults) >= 3, "The fixture did not inject faults; is the CMMS fixture intact?")
        assert_synced(c, url, what="after a sync full of retries")


def test_the_sync_honours_retry_after():
    with cmms(fraction=0.8, faults={**NO_FAULTS, "fail_first_n": 1, "fail_first_status": 429, "retry_after_s": 1}) as c, seeded_db() as url:
        r = run_sync(c, url, SYNC_BACKOFF_BASE_S="0.01")
        ok(r.returncode == 0, "Sync failed:\n" + tail(r))
        log = c.api_log()
        i = next((k for k, e in enumerate(log) if e["status"] == 429), None)
        ok(i is not None and i + 1 < len(log), "The fixture did not answer 429 first.")
        gap = log[i + 1]["t"] - log[i]["t"]
        ok(gap >= 0.9, f"After a 429 with 'Retry-After: 1' the connector asked again after {gap:.2f} s. The server told you how long to wait: listen to it.")


def test_a_mid_run_outage_fails_loudly_and_loses_nothing():
    with cmms(fraction=1.0, faults={**NO_FAULTS, "down_after": 3}) as c, seeded_db() as url:
        t0 = time.time()
        r = run_sync(c, url, timeout=120, SYNC_MAX_RETRIES="2")
        ok(r.returncode != 0, "The CMMS went down in the middle of the run and the sync still exited 0. A silent partial sync is the worst outcome: nobody knows the data is stale.")
        ok(time.time() - t0 < 100, "The sync hangs when the CMMS is down: retries must be bounded.")
        c.post("/_admin/faults", **NO_FAULTS)
        r = run_sync(c, url)
        ok(r.returncode == 0, "After the CMMS came back the sync still failed:\n" + tail(r))
        assert_synced(c, url, what="after recovering from an outage in the middle of the run (the watermark must not have moved past data you did not get)")


def test_a_work_order_of_an_unknown_pump_is_replayed_once_registered(tmp_path):
    with cmms(fraction=1.0, faults=NO_FAULTS) as c, seeded_db() as url:
        ok(run_sync(c, url).returncode == 0, "First sync failed.")
        src = CONNECTOR / "asset_map.csv"
        ok(src.exists(), "Missing asset_map.csv.")
        extended = tmp_path / "asset_map.csv"
        text = src.read_text().rstrip("\n")
        extended.write_text(text + "\nB-13,PLT02-P213,CP-350,B,2026-05-01,250.0,45.0\n")
        r = run_sync(c, url, ASSET_MAP=str(extended))
        ok(r.returncode == 0, "Sync with the extended register failed:\n" + tail(r))
        amap = {**asset_truth(), "B-13": {"asset_id": "PLT02-P213"}}
        assert_synced(c, url, amap, what="once B-13 is in the register (the work orders you could not place earlier must not be lost; ASSET_MAP points at the register)")


# ------------------------------------------------------------------ 4. the MCP server
def text_of(res) -> str:
    return " ".join(c.text for c in res.content if getattr(c, "text", None))


def has_text(out: str, s: str) -> bool:
    return s in out or json.dumps(s)[1:-1] in out


def test_mcp_server_is_read_only_least_privilege_and_useful():
    ok((CONNECTOR / "mcp_server.py").exists(), "Missing connectors/plt02/mcp_server.py (stdio MCP server over the CMMS).")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with cmms(fraction=1.0, faults={**NO_FAULTS, "p503": 0.15, "dup_rate": 0.05}) as c:
        truth = c.truth()
        rows = truth["rows"]
        b07 = sorted((r for r in rows if r["tag"] == "B-07"), key=lambda r: r["opened_at"], reverse=True)
        b01 = [r for r in rows if r["tag"] == "B-01"]
        gap_row = next(r for r in rows if r["wo_id"] == truth["probes"]["gap"])
        words: dict[str, set] = {}
        for r in rows:
            for w in set(re.findall(r"[a-záéíóúñ]{5,}", r["note"].lower())):
                words.setdefault(w, set()).add(r["wo_id"])
        rare = next((w for w, ids in sorted(words.items()) if 2 <= len(ids) <= 12), None)
        outputs: dict[str, str] = {}

        async def go():
            params = StdioServerParameters(command=sys.executable, args=["-m", "connectors.plt02.mcp_server"], cwd=str(ROOT),
                                           env={**os.environ, "PYTHONPATH": str(ROOT), "CMMS_URL": c.url, "CMMS_API_KEY": RO})
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = (await s.list_tools()).tools

                    async def call(name, **args):
                        res = await s.call_tool(name, args)
                        outputs[f"{name}:{sorted(args.items())}"] = text_of(res)
                        return res

                    await call("list_assets")
                    await call("get_asset_history", asset_tag="B-07", limit=3)
                    await call("get_asset_history", asset_tag=" b07 ", limit=3)
                    await call("get_asset_history", asset_tag="B-01", limit=500)
                    await call("get_work_order", wo_id=gap_row["wo_id"])
                    await call("get_work_order", wo_id="WO-999999")
                    if rare:
                        await call("search_work_orders", query=rare, limit=20)
                    for bad in ("B-01&modified_since=01/01/1990 00:00", "../_admin/stats", "B-01/../../_admin/truth", "x" * 5000):
                        await call("get_asset_history", asset_tag=bad, limit=5)
                    for bad in ("WO-000001/close", "../../_admin/truth", "WO-000001?x=1"):
                        await call("get_work_order", wo_id=bad)
                    await call("search_work_orders", query="x" * 5000, limit=5)
                    after = await call("get_work_order", wo_id=gap_row["wo_id"])  # still alive after the hostile inputs
                    return tools, after

        tools, after = asyncio.run(asyncio.wait_for(go(), 180))
        names = {t.name for t in tools}
        ok({"list_assets", "get_asset_history", "get_work_order", "search_work_orders"} <= names, f"The MCP server exposes {sorted(names)}; list_assets, get_asset_history, get_work_order or search_work_orders is missing.")
        writers = [n for n in names if re.search(r"create|update|delete|close|write|modify|edit|assign|insert|remove|post", n, re.I) or {"set", "put"} & set(n.lower().split("_"))]  # token match: "get_asset_history" is not a "set_"
        ok(not writers, f"The assistant gets tools that sound like writes: {writers}. Phase 1 writes nothing to the CMMS: what is the least the assistant needs?")
        vals = list(outputs.values())
        # list of pumps, decoded properly
        ok("B-07" in vals[0] and "Ã" not in vals[0] and re.search(r"CENTR(Í|\\u00cd|\\u00CD)FUGA", vals[0]), f"list_assets: the equipment list is wrong or garbled: {vals[0][:160]!r}")
        # last 3 failures of B-07, newest first
        ids = [r["wo_id"] for r in b07[:3]]
        pos = [vals[1].find(i) for i in ids]
        ok(all(p >= 0 for p in pos) and pos == sorted(pos), f"get_asset_history('B-07', 3) must return the 3 most recent work orders of B-07, newest first: {ids}. Got: {vals[1][:200]!r}")
        ok(b07[3]["wo_id"] not in vals[1], "get_asset_history returned more work orders than the limit.")
        ok(all(i in vals[2] for i in ids), "The asset tag ' b07 ' (how the technicians type it) must find B-07.")
        n_ids = len(set(re.findall(r"WO-\d{6}", vals[3])))
        ok(0 < n_ids <= 20 or len(b01) <= 20, f"A request for 500 work orders returned {n_ids}. The local model has a small context: never more than 20 at once.")
        ok(has_text(vals[4], gap_row["note"]), f"get_work_order returned a different note than the CMMS holds ({vals[4][:200]!r}): accents?")
        ok(re.search(r"([+-]\d{2}:?\d{2}|\bZ\b|UTC|Santiago)", vals[4]), "Timestamps without an offset or a zone are ambiguous (twice a year they are wrong): say which zone they are in.")
        ok(re.search(r"not found|no existe|unknown|no such", vals[5], re.I), f"An unknown work order must give a clear message, not silence: {vals[5][:100]!r}")
        if rare:
            want_ids = words[rare]
            ok(want_ids <= set(re.findall(r"WO-\d{6}", vals[6])), f"search_work_orders('{rare}') missed work orders whose note contains it ({sorted(want_ids)[:4]}…).")
        ok("presión" in text_of(after) or "presi" in text_of(after) or "\\u00f3" in text_of(after), "The server did not survive the hostile inputs.")
        # privacy
        alltext = " ".join(vals)
        ok(not re.search(r"\bT(0[1-9]|1[0-9]|20)\b", alltext) and not re.search(r"tech(nician)?\W{0,3}\s*[:=]", alltext, re.I), "Technician identifiers appear in the assistant's answers. The union agreement forbids it: do you need that field at all?")
        # what the CMMS saw
        stats = c.stats()
        log = stats["log"]
        ok(log, "The MCP server never reached the CMMS.")
        ok(all(e["method"] == "GET" for e in log) and stats["writes"] == {"applied": 0, "denied": 0}, f"The MCP server sent something other than GET to the CMMS ({[e['method'] for e in log if e['method'] != 'GET'][:3]}; writes: {stats['writes']}).")
        ok(all(e["key"] == "ro" for e in log), "The MCP server used a key that is not the read-only one. Least privilege: the assistant never needs to be able to write.")
        allowed = {"/api/v1/equipment", "/api/v1/workorders"}
        stray = [e["path"] for e in log if e["path"] not in allowed and not re.fullmatch(r"/api/v1/workorders/WO-\d{6}", e["path"])]
        ok(not stray, f"The MCP server reached paths it should never reach: {stray[:3]}. Whatever the model sends is untrusted input: validate it before it becomes part of a URL.")
        keys = {k for e in log for k in e["query"]}
        ok(keys <= {"asset", "q", "page_size", "cursor", "modified_since", "status"}, f"Unexpected query parameters reached the CMMS: {sorted(keys - {'asset', 'q', 'page_size', 'cursor', 'modified_since', 'status'})}.")
        bad_asset = [e["query"]["asset"] for e in log if "asset" in e["query"] and not re.fullmatch(r"\s*[A-Za-z][\s-]?\d{1,3}\s*", e["query"]["asset"])]
        ok(not bad_asset, f"A malformed asset tag reached the CMMS ({(bad_asset or [''])[0]!r}). Validate before you build the request.")
        ok(all(int(e["query"].get("page_size", 1)) <= 200 for e in log), "page_size above what the CMMS allows.")


# ------------------------------------------------------------------ 5. the site box
def compose_config() -> dict:
    ok(SITE.exists(), "Missing deploy/site/compose.site.yaml: the compose file of the site box (guide/15_second_plant_fde.md).")
    ok(shutil.which("docker"), "Docker is not installed or not on the PATH.")
    r = sh(["docker", "compose", "-f", str(SITE), "config", "--format", "json"], env={"SITE_DB_PASSWORD": "chk-pass", "CMMS_RO_KEY": RO}, timeout=60)
    ok(r.returncode == 0, "deploy/site/compose.site.yaml is not a valid compose file (or needs variables with no default):\n" + tail(r))
    return json.loads(r.stdout)


def test_site_compose_has_no_egress_by_construction():
    cfg = compose_config()
    services, nets = cfg.get("services", {}), cfg.get("networks", {})
    ok({"db", "cmms", "sync"} <= set(services), f"The site box needs the services db, cmms and sync (found {sorted(services)}).")
    for name, s in services.items():
        used = set((s.get("networks") or {}).keys()) or {"default"}
        outside = [n for n in used if not nets.get(n, {}).get("internal")]
        ok(not outside, f"Service '{name}' is attached to a network that is not `internal: true` ({outside}): it could reach the internet. A site where data must not leave is not a place for 'it just never calls out'.")
        ok(s.get("network_mode") not in ("host", "bridge"), f"Service '{name}' uses network_mode {s.get('network_mode')}: that bypasses the internal network.")
        for p in s.get("ports") or []:
            ok(p.get("host_ip") in ("127.0.0.1", "::1"), f"Service '{name}' publishes a port to the whole LAN. On the site box only what must be reachable, and only locally, is published.")
        img = s.get("image", "")
        if img or not s.get("build"):
            ok(img and not img.endswith(":latest") and ":" in img.rsplit("/", 1)[-1], f"Service '{name}' has an unpinned image ({img!r}): the box cannot pull, so what runs must be exactly what you built and carried.")
        ok(s.get("restart") in ("unless-stopped", "always"), f"Service '{name}' has no restart policy. There is no UPS at PLT02 and a power cut happens about once a month: nobody will be there to start it.")
    envs = json.dumps(services["sync"].get("environment", {}))
    ok("rw-demo-key" not in envs and RW not in envs, "The sync is configured with the read-write key. Least privilege.")


@pytest.mark.slow
def test_the_site_box_works_offline(tmp_path):
    compose_config()
    ok(sh(["docker", "info"], timeout=30).returncode == 0, "The Docker daemon is not running.")
    project = "plt02-site-chk"
    base = ["docker", "compose", "-f", str(SITE), "-p", project]
    env = {"SITE_DB_PASSWORD": "chk-pass", "CMMS_RO_KEY": "ro-demo-key"}
    try:
        r = sh([*base, "up", "-d", "--build", "--wait", "--wait-timeout", "240"], env=env, timeout=1200)
        ok(r.returncode == 0, "The site stack does not start:\n" + tail(r, 25))
        probe = "import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=4)"
        r = sh([*base, "run", "--rm", "-T", "--no-deps", "sync", "python", "-c", probe, "cmms", "8702"], env=env, timeout=120)
        ok(r.returncode == 0, "Positive control failed: the sync container cannot reach the CMMS inside the site network, so the egress test below would prove nothing.\n" + tail(r))
        r = sh([*base, "run", "--rm", "-T", "--no-deps", "sync", "python", "-c", probe, "1.1.1.1", "443"], env=env, timeout=120)
        ok(r.returncode != 0, "A container of the site box could open a connection to 1.1.1.1:443. The network must have no way out.")
        r = sh([*base, "run", "--rm", "-T", "--no-deps", "sync", "python", "-c", "import socket; socket.getaddrinfo('example.com', 443)"], env=env, timeout=120)
        ok(r.returncode != 0, "A container of the site box resolved example.com. The network must have no way out.")
        import importlib.util

        spec = importlib.util.spec_from_file_location("plt02_cmms_data", ROOT / "platform" / "plt02" / "cmms" / "data.py")
        D = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(D)
        ds = D.Dataset(2, 1500, 0.8, True)
        amap = asset_truth()
        want = sum(1 for r in ds.visible() if D.norm_tag(r["tag"]) in amap)

        def count():
            q = sh([*base, "exec", "-T", "db", "psql", "-U", "plant", "-d", "plant", "-tAc", "select count(*) from work_orders where wo_id like 'PLT02-%'"], env=env, timeout=60)
            try:
                return int(q.stdout.strip())
            except ValueError:
                return -1

        end = time.time() + 90
        while time.time() < end and count() != want:
            time.sleep(3)
        if count() != want:  # the unattended loop may be waiting out a busy window: a human runs it once
            r = sh([*base, "run", "--rm", "-T", "sync", "python", "-m", "connectors.plt02.sync"], env=env, timeout=300)
            ok(r.returncode == 0, "Running the sync inside the offline site box failed:\n" + tail(r))
        got = count()
        ok(got == want, f"Inside the offline site box the platform holds {got} PLT02 work orders; the CMMS shows {want}. Does the stack really work with no network?")
    finally:
        sh([*base, "down", "-v", "--remove-orphans"], env=env, timeout=300)


# ------------------------------------------------------------------ 6. the handover runbook
RUNBOOK_SECTIONS = {
    "overview": ("overview", "summary", "what this is"), "start": ("start", "stop"), "health": ("health",), "failures": ("failure", "incident", "troubleshoot", "problem", "issue"),
    "backup": ("backup", "restore"), "update": ("updat", "upgrade"), "contacts": ("contact", "escalation"), "signoff": ("sign-off", "sign off", "approval"),
}


def runbook_problems(path: Path) -> list[str]:
    if not path.exists():
        return ["Missing docs/runbook_plt02.md: the handover for the client's IT (one person, one day a week on site)."]
    text = path.read_text()
    secs = split_sections(text)
    problems = [f"No section for: {n} (a heading containing one of {list(k)})." for n, k in RUNBOOK_SECTIONS.items() if find_section(secs, k) is None]
    words = len(text.split())
    if not 250 <= words <= 1800:
        problems.append(f"The runbook has {words} words: a handover for one busy person is short (250 to 1800) and concrete.")
    if len(re.findall(r"(?m)^\s*\|.+\|\s*$|^#{3,4}\s", find_section(secs, RUNBOOK_SECTIONS["failures"]) or "")) < 4:
        problems.append("Common failures: describe at least 3 failures (a table row or a ### heading each) with symptom, cause and action.")
    needs = [
        (r"05:45|13:45|21:45|02:00|shift change|backup window|busy window", "the CMMS's busy windows (when the sync must stay away) and what a skipped run looks like"),
        (r"usb|docker save|docker load|stick", "how an update reaches a box with no internet"),
        (r"internal|egress|no internet|offline", "how to verify the box has no way out"),
        (r"restart|power (cut|outage|loss|failure)|reboot|blackout", "what happens after a power cut"),
        (r"docker( compose)? .*(ps|logs|stats|inspect)|psql|pg_isready|curl .*health", "an actual command to check health"),
        (r"rodrigo|camila|valdés|valdes|plant manager", "who to call (names, not 'the team')"),
        (r"read[- ]only|valdés|valdes|plant manager|shadow", "the approval boundary: what needs whose signature"),
    ]
    problems += [f"The runbook does not say: {what}." for rx, what in needs if not re.search(rx, text, re.I)]
    return problems


def test_handover_runbook_is_concrete():
    problems = runbook_problems(ROOT / "docs" / "runbook_plt02.md")
    ok(not problems, "docs/runbook_plt02.md is not ready for the client's IT:\n- " + "\n- ".join(problems))
