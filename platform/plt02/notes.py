"""PLT02 work orders: a deterministic simulator of the second plant's CMMS, including the technicians' free-text notes.

Stdlib only. `generate_work_orders(seed, n)` always returns the same list for the same arguments (it only uses
`random.Random(seed).random()`, never `choice`/`shuffle`, whose behaviour changed between Python versions).

    import sys; sys.path.insert(0, "<repo root>/platform")      # `platform.plt02` cannot be imported (stdlib clash)
    from plt02.notes import generate_work_orders

This file is the PLANT, not a labelled-data tool: a work order has a hidden `label`, and the point of module 16 is to learn
that mapping from the ~300 labels the lead technician wrote down. Minting labels by calling this generator with other seeds
would defeat the exercise (and the checks score notes you have never seen). The mock CMMS must not expose `label`.

Time zone: `opened_at` / `closed_at` are NAIVE America/Santiago wall-clock strings "YYYY-MM-DD HH:MM", like the legacy system
stores them. Chile changes clocks, and two traps are planted on purpose for the connector module:
  * 2025-04-05 23:40 -> the clock falls back from 24:00 to 23:00: that work order closes at "23:20" (before it opened, as naive strings).
  * 2026-04-04 23:15 -> another work order opened in the same ambiguous hour (23:00-23:59 happens twice).
No timestamp falls in the hour that does not exist (2025-09-07 00:00-00:59). The generator does not use `zoneinfo`
(Windows needs the tzdata package for it); the offsets for 2025-2026 are a hard-coded table (UTC_TRANSITIONS).

CLI (used once by the author to build the public fixtures):
    python platform/plt02/notes.py sample [N]          print N notes with their labels
    python platform/plt02/notes.py stats               label distributions, duplicates, lengths
    python platform/plt02/notes.py build-fixtures      write platform/plt02/data/*.jsonl and checks/_plt02_heldout.jsonl
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:  # imported as plt02.notes (normal) ...
    from .schema import (ACTIONS, ASSET_TAGS, CAUSES, COMPONENTS, DATE_END, DATE_START, DEFAULT_N, DEFAULT_SEED,
                         FIELDS, LEAD_TECHNICIAN, N_HELDOUT, N_LABELLED, TECHNICIANS)
except ImportError:  # ... or run as a script / with platform/plt02 on sys.path
    from schema import (ACTIONS, ASSET_TAGS, CAUSES, COMPONENTS, DATE_END, DATE_START, DEFAULT_N, DEFAULT_SEED,  # type: ignore
                        FIELDS, LEAD_TECHNICIAN, N_HELDOUT, N_LABELLED, TECHNICIANS)

# ============================================================================================ small deterministic helpers


def _u(rng: random.Random) -> float:
    return rng.random()


def _pick(rng: random.Random, seq):
    return seq[min(len(seq) - 1, int(rng.random() * len(seq)))]


def _wpick(rng: random.Random, pairs):
    """pairs = [(item, weight), ...]"""
    total = sum(w for _, w in pairs)
    x = rng.random() * total
    for item, w in pairs:
        x -= w
        if x < 0:
            return item
    return pairs[-1][0]


def _h(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest(), 16)


# ============================================================================================ clock (Chile, hard-coded 2025-2026)
# UTC instants where America/Santiago changes offset (checked against tzdata): -3 (summer) -> -4 (winter) -> -3 -> -4 -> -3
UTC_TRANSITIONS = (datetime(2025, 4, 6, 3, 0), datetime(2025, 9, 7, 4, 0), datetime(2026, 4, 5, 3, 0), datetime(2026, 9, 6, 4, 0))


def _offset_hours_utc(utc: datetime) -> int:
    t1, t2, t3, t4 = UTC_TRANSITIONS
    if utc < t1 or t2 <= utc < t3 or utc >= t4:
        return -3
    return -4


def local_to_utc(local: datetime) -> datetime:
    """Naive local -> naive UTC. In the ambiguous hour it takes the first occurrence (fold=0)."""
    t1, t2, t3, t4 = UTC_TRANSITIONS
    for off in (3, 4):
        cand = local + timedelta(hours=off)
        if _offset_hours_utc(cand) == -off:
            return cand
    return local + timedelta(hours=4)  # non-existent local time: treat as winter time


def utc_to_local(utc: datetime) -> datetime:
    return utc + timedelta(hours=_offset_hours_utc(utc))


def _exists(local: datetime) -> bool:
    return not (datetime(2025, 9, 7, 0, 0) <= local < datetime(2025, 9, 7, 1, 0))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


# ============================================================================================ vocabulary
# Component words, ordered from fully spelled to heavily abbreviated (a technician's "home" word depends on how terse they are).
_COMP_WORDS = {
    "bearing": ["bearing", "drive end bearing", "bearings", "DE bearing", "DE brg", "NDE brg", "brg", "brgs"],
    "seal": ["mechanical seal", "shaft seal", "mech seal", "seal", "gland", "gland packing", "m/seal", "mseal"],
    "impeller": ["impeller", "impellor", "impeller vanes", "imp"],
    "coupling": ["coupling", "coupling insert", "coupling rubber", "spider", "cplg", "cpl"],
    "motor": ["motor", "electric motor", "drive motor", "motor fan", "mtr", "mtr fan"],
    "valve": ["valve", "suction valve", "discharge valve", "check valve", "gate valve", "suct valve", "disch valve", "vlv"],
}
# "other" depends on what the problem was
_OTHER_WORDS = {
    "cavitation": ["strainer", "suction strainer", "strainer basket", "basket"],
    "contamination": ["strainer", "suction strainer", "strainer basket", "basket"],
    "looseness": ["guard", "baseplate", "pipe support", "base plate", "hold down bolts"],
    "wear": ["gauge", "pressure gauge", "flow meter", "gauge glass"],
    "unknown": ["routine PM", "weekly check", "walkdown", "PM", "round", "routine round"],
}

_SYMPTOMS = {
    "bearing": ["vibes bad", "vibration high", "noisy", "growling", "running hot", "hot", "humming loud", "vibes up", "rough noise", "warm"],
    "seal": ["leaking", "dripping", "leak at shaft", "wet around shaft", "spraying", "weeping", "leaking bad"],
    "impeller": ["low flow", "low pressure", "vibes", "rattling", "no flow", "flow dropped", "amps high"],
    "coupling": ["clunking", "vibes", "knocking", "rubber dust", "shaking", "noise at coupling"],
    "motor": ["tripped", "hot", "smells burnt", "amps high", "wont start", "trip on overload", "buzzing"],
    "valve": ["wont close", "passing", "stuck", "leaking", "hammering", "cant throttle", "low flow"],
    "other": ["low flow", "gauge reads wrong", "alarm", "noise", "dp high", "low pressure"],
}

# Cause phrases a technician may write (only when the note states the cause).
_CAUSE_PHRASES = {
    "wear": ["worn", "worn out", "wornout", "end of life", "scored", "pitted", "damaged", "cracked"],
    "lubrication": ["no grease", "dry", "low oil", "lack of lube", "needs grease", "no lube", "oil low"],
    "misalignment": ["misaligned", "out of align", "align off", "misalign", "alignment out"],
    "cavitation": ["cavitation", "cavitating", "cav", "gravel noise", "cavitation at suction"],
    "overheating": ["overheated", "overtemp", "thermal trip", "overload trip", "over temp"],
    "contamination": ["dirt in oil", "water in oil", "debris", "sand", "fouled", "clogged", "blocked", "grit"],
    "imbalance": ["unbalanced", "imbalance", "out of balance", "off balance"],
    "looseness": ["loose", "loose bolts", "soft foot", "bolts loose", "loose hold down"],
}
_CONTAM_BY_COMP = {
    "bearing": ["dirt in oil", "water in oil", "contaminated grease", "grit in it"],
    "seal": ["grit", "debris at faces", "dirty product", "sand"],
    "impeller": ["clogged", "debris", "blocked", "rag stuck", "fouled"],
    "motor": ["dust", "fouled fan", "blocked fan cover", "dirty"],
    "valve": ["debris", "clogged", "fouled seat", "stuck w debris"],
    "other": ["clogged", "blocked", "fouled", "full of debris"],
    "coupling": ["dirt", "debris"],
}
_CAUSE_NEGATIONS = {  # "this was checked and is NOT the cause"
    "wear": ["no wear", "not worn"],
    "lubrication": ["lube ok", "grease ok", "oil level ok"],
    "misalignment": ["alignment ok", "align good", "alignment fine"],
    "cavitation": ["no cavitation", "not cav", "no cav noise"],
    "overheating": ["temp normal", "temp ok"],
    "contamination": ["oil clean", "no debris", "strainer clean"],
    "imbalance": ["balance ok"],
    "looseness": ["bolts tight", "no looseness", "bolts ok"],
}
_COMP_NEGATIONS = ["{c} ok", "{c} fine", "{c} good", "no issue w {c}", "{c} looks ok", "{c} is ok", "{c} not the problem", "checked {c} ok"]

# Action phrases: (text, implied_cause). A verb that names the cause ("realigned") lets the lead technician label the cause even if
# the note does not state it; the generator only picks verbs whose implied cause is None or the true cause.
_ACTION_PHRASES = {
    "replace": [("replaced", None), ("swapped", None), ("changed", None), ("r&r", None), ("fitted new", None), ("renewed", None), ("new one fitted", None)],
    "repair": [("rebuilt", None), ("repaired", None), ("repacked", None), ("rewound", None), ("welded", None), ("resealed", None), ("refurbished", None)],
    "adjust": [("realigned", "misalignment"), ("re-aligned", "misalignment"), ("tightened", "looseness"), ("retorqued", "looseness"),
               ("rebalanced", "imbalance"), ("balanced", "imbalance"), ("adjusted", None), ("trimmed", None), ("re-shimmed", None), ("set up", None)],
    "lubricate": [("greased", "lubrication"), ("regreased", "lubrication"), ("lubed", "lubrication"), ("added grease", "lubrication"),
                  ("topped up oil", "lubrication"), ("refilled oil", "lubrication"), ("oil change", None), ("flushed oil", None)],
    "clean": [("cleaned", None), ("flushed", None), ("cleared", None), ("blown out", None), ("descaled", None), ("cleaned out", None)],
    "inspect": [("inspected", None), ("checked", None), ("looked over", None), ("no fault found", None), ("nff", None), ("checked all ok", None)],
    "none": [("will monitor", None), ("monitor", None), ("parts ordered", None), ("waiting spares", None), ("pending", None),
             ("needs replacing, parts ordered", None), ("left running", None), ("to do next shift", None), ("deferred", None)],
}
_HIDDEN_CAUSE_WORDS = ["{c} bad", "{c} faulty", "{c} failed", "{c} issue", "bad {c}", "{c} u/s"]

# Shop-floor slang: the long tail. Each technician leans on a few of these (see Style.p_slang); a keyword list written from a
# handful of notes will miss most of them, a model that "knows English" does not.
_SLANG_COMP = {
    "bearing": ["roller", "DE housing", "ball race", "brng", "bering"], "seal": ["face", "carbon face", "stuffing box", "MS", "seal pack"],
    "impeller": ["wheel", "vanes", "runner", "impelr"], "coupling": ["rubber", "jaw", "flex", "lovejoy", "drive coupling"],
    "motor": ["windings", "electrics", "mtr unit", "motor unit"], "valve": ["gate", "NRV", "isolation", "non return", "butterfly"],
}
_SLANG_OTHER = {"cavitation": ["filter", "suction screen", "screen"], "contamination": ["filter", "suction screen", "screen"],
                "looseness": ["frame", "foundation bolts", "anchor bolts"], "wear": ["indicator", "transmitter", "sight glass"],
                "unknown": ["PM visit", "inspection round", "shift check", "look around"]}
_SLANG_CAUSE = {
    "wear": ["shot", "gone", "chewed", "had it", "toast", "eaten away"], "lubrication": ["starved of grease", "no gres", "bone dry", "ran dry"],
    "misalignment": ["out of line", "off center", "not in line", "shaft off"], "cavitation": ["sounds like gravel", "starved suction", "npsh problem", "cavit"],
    "overheating": ["burnt", "cooked", "hot trip", "burnt out"], "contamination": ["gunk", "muck", "silt", "mud", "slime"],
    "imbalance": ["wobble", "heavy side", "wobbling"], "looseness": ["wobbly", "slack", "working loose", "backed off"],
}
_SLANG_ACTION = {
    "replace": [("put new", None), ("installed new", None), ("exchanged", None), ("new fitted", None)],
    "repair": [("fixed", None), ("patched up", None), ("reworked", None), ("overhauled", None)],
    "adjust": [("tweaked", None), ("shimmed", None), ("re-set", None), ("snugged up", "looseness"), ("lined up", "misalignment")],
    "lubricate": [("oiled", "lubrication"), ("greased up", "lubrication"), ("gresed", "lubrication"), ("lube done", "lubrication")],
    "clean": [("hosed", None), ("rinsed", None), ("unblocked", None), ("cleared out", None), ("scraped", None)],
    "inspect": [("looked", None), ("eyeballed", None), ("walked it", None), ("all good on visit", None)],
    "none": [("will watch", None), ("keep an eye", None), ("on hold", None), ("next PM", None), ("no spares", None)],
}

# (component, cause, {action: weight}, scenario weight)
_SCENARIOS = [
    ("bearing", "wear", {"replace": .75, "none": .15, "repair": .10}, 11),
    ("bearing", "lubrication", {"lubricate": .85, "replace": .15}, 8),
    ("bearing", "contamination", {"replace": .6, "lubricate": .25, "clean": .15}, 4),
    ("bearing", "looseness", {"adjust": .9, "inspect": .1}, 2),
    ("bearing", "misalignment", {"adjust": .8, "replace": .2}, 3),
    ("bearing", "overheating", {"replace": .5, "lubricate": .5}, 2),
    ("seal", "wear", {"replace": .7, "repair": .2, "none": .1}, 12),
    ("seal", "contamination", {"replace": .5, "clean": .3, "repair": .2}, 4),
    ("seal", "misalignment", {"replace": .6, "adjust": .4}, 2),
    ("seal", "looseness", {"adjust": .9, "repair": .1}, 3),
    ("seal", "overheating", {"replace": 1.0}, 3),
    ("impeller", "cavitation", {"replace": .35, "repair": .15, "adjust": .2, "none": .3}, 6),
    ("impeller", "wear", {"replace": .6, "repair": .4}, 2),
    ("impeller", "contamination", {"clean": .9, "inspect": .1}, 3),
    ("impeller", "imbalance", {"adjust": .7, "replace": .3}, 4),
    ("coupling", "misalignment", {"adjust": .85, "replace": .15}, 4),
    ("coupling", "wear", {"replace": .9, "none": .1}, 3),
    ("coupling", "looseness", {"adjust": 1.0}, 1),
    ("motor", "overheating", {"clean": .4, "repair": .3, "inspect": .2, "replace": .1}, 4),
    ("motor", "wear", {"repair": .5, "replace": .5}, 3),
    ("motor", "lubrication", {"lubricate": 1.0}, 2),
    ("motor", "contamination", {"clean": 1.0}, 3),
    ("valve", "cavitation", {"adjust": .7, "none": .3}, 3),
    ("valve", "wear", {"replace": .5, "repair": .5}, 2),
    ("valve", "contamination", {"clean": .7, "replace": .3}, 2),
    ("valve", "looseness", {"adjust": .9, "repair": .1}, 2),
    ("other", "cavitation", {"clean": .9, "none": .1}, 3),
    ("other", "contamination", {"clean": 1.0}, 2),
    ("other", "looseness", {"adjust": 1.0}, 2),
    ("other", "wear", {"replace": .8, "repair": .2}, 1),
    ("other", "unknown", {"inspect": .85, "none": .15}, 5),
]

# Some pumps are lemons in a particular way (mild asset prior, as on a real site).
_ASSET_BIAS = {"B-03": ("seal", 3.0), "B-07": ("bearing", 3.0), "B-05": ("coupling", 2.5), "B-12": ("motor", 2.5), "B-08": ("valve", 2.5)}
_ASSET_CAVITATION_BIAS = {"B-10": 3.0}

_REF_STYLES = ["p{n}", "P-{nn}", "B{nn}", "B-{nn}", "pump {n}", "bomba {n}", "pmp{n}", "#{n}"]
_SEPS = [", ", ", ", "; ", " - ", ". ", " / ", " "]
_KEYBOARD = {"a": "sqw", "e": "wrd", "i": "uok", "o": "ipl", "u": "yij", "s": "adw", "d": "sfe", "r": "etf", "t": "ryg", "n": "bm", "m": "nk",
             "l": "ko", "k": "jl", "c": "xv", "p": "ol", "b": "vn", "g": "fh", "h": "gj", "y": "tu", "w": "qe", "v": "cb", "f": "dg"}


# ============================================================================================ technician styles
class Style:
    """How ONE technician writes. Derived from the technician id only (so it does not depend on the data seed)."""

    def __init__(self, tid: str):
        i = int(tid[1:])
        r = random.Random(7000 + i)
        self.tid = tid
        self.abbr = 0.1 + 0.85 * _u(r)                        # 0 = spells everything, 1 = abbreviates everything
        self.typo = [0.02, 0.03, 0.05, 0.08, 0.11, 0.15][min(5, int(_u(r) ** 1.3 * 6))]
        self.case = _wpick(r, [("lower", 4), ("upper", 1.5), ("sentence", 2), ("asis", 2.5)])
        self.sep = _pick(r, _SEPS)
        self.end = _wpick(r, [("", 6), (".", 2), (" done", 1)])
        self.p_ref = 0.35 + 0.65 * _u(r)
        self.p_sym = 0.35 + 0.6 * _u(r)
        self.p_num = 0.05 + 0.4 * _u(r)
        self.p_dis = 0.05 + 0.45 * _u(r)                      # mentions what was checked and is fine
        self.p_cause = 0.45 + 0.45 * _u(r)                    # states WHY it failed
        self.p_finding = 0.35 + 0.6 * _u(r)                   # writes a finding clause (otherwise just symptom + action)
        self.p_side = 0.04 + 0.1 * _u(r)                      # mentions a symptom of ANOTHER part
        self.p_comp_in_action = 0.3 + 0.6 * _u(r)
        self.p_slang = 0.10 + 0.55 * _u(r) ** 1.2                # leans on slang words
        self.result_first = _u(r) < 0.25                      # "swapped brg - was noisy, worn"
        self.home_cache: dict[str, int] = {}
        self.consistency = 0.55 + 0.4 * _u(r)

    def variant(self, rng: random.Random, key: str, options: list):
        """Pick an option: the technician's habitual one most of the time (habit depends on how terse they are)."""
        if key not in self.home_cache:
            u = (_h(f"{self.tid}|{key}") % 10000) / 10000.0
            self.home_cache[key] = min(len(options) - 1, int(((u + self.abbr) / 2.0) * len(options)))
        if _u(rng) < self.consistency:
            return options[min(self.home_cache[key], len(options) - 1)]
        return _pick(rng, options)

    def ref(self, rng: random.Random, asset_tag: str) -> str:
        n = int(asset_tag[2:])
        tpl = self.variant(rng, "ref", _REF_STYLES)
        return tpl.format(n=n, nn=f"{n:02d}")


_STYLES = {t: Style(t) for t in TECHNICIANS}
_TECH_WEIGHTS = [(t, 8.0 if t == LEAD_TECHNICIAN else 0.75 + 0.5 * ((_h("w" + t) % 1000) / 1000.0) * 4.0 / 4.0 + 3.6) for t in TECHNICIANS]


def _typo_word(rng: random.Random, w: str) -> str:
    if len(w) < 4 or any(c.isdigit() for c in w) or "/" in w or "&" in w:
        return w
    i = 1 + int(_u(rng) * (len(w) - 2))
    r = _u(rng)
    if r < 0.35:
        return w[:i] + w[i + 1:]                                   # dropped letter
    if r < 0.60:
        return w[:i] + w[i] + w[i:]                                # doubled letter
    if r < 0.85 and i + 1 < len(w):
        return w[:i] + w[i + 1] + w[i] + w[i + 2:]                 # swapped letters
    near = _KEYBOARD.get(w[i].lower())
    return w[:i] + (_pick(rng, near) if near else w[i]) + w[i + 1:]


def _apply_case(style: Style, text: str) -> str:
    if style.case == "lower":
        return text.lower()
    if style.case == "upper":
        return text.upper()
    if style.case == "sentence":
        return text[:1].upper() + text[1:].lower() if text else text
    return text


# ============================================================================================ one note
def _comp_word(rng: random.Random, style: Style, comp: str, cause: str) -> str:
    if _u(rng) < style.p_slang:
        pool = _SLANG_OTHER[cause] if comp == "other" else _SLANG_COMP[comp]
        return style.variant(rng, f"slang:{comp}", pool)
    words = _OTHER_WORDS[cause] if comp == "other" else _COMP_WORDS[comp]
    return style.variant(rng, f"comp:{comp}:{cause if comp == 'other' else ''}", words)


def _number(rng: random.Random) -> str:
    k = int(_u(rng) * 4)
    if k == 0:
        return f"{2 + _u(rng) * 12:.1f}mm/s"
    if k == 1:
        return f"{60 + int(_u(rng) * 50)}C"
    if k == 2:
        return f"{10 + int(_u(rng) * 60)}A"
    return f"{1 + int(_u(rng) * 6)}.{int(_u(rng) * 9)} bar"


def _make_note(rng: random.Random, style: Style, asset_tag: str, scen, action: str) -> tuple[str, dict]:
    comp, cause, _, _ = scen
    stated = _u(rng) < style.p_cause
    use_finding = _u(rng) < style.p_finding
    cw = _comp_word(rng, style, comp, cause)

    # action phrase: only verbs that do not contradict the true cause
    pool = _SLANG_ACTION[action] if _u(rng) < style.p_slang else _ACTION_PHRASES[action]
    allowed = [p for p in pool if p[1] in (None, cause)] or [p for p in _ACTION_PHRASES[action] if p[1] in (None, cause)]
    phrase, implied = style.variant(rng, f"act:{action}:{pool is not _ACTION_PHRASES[action]}", allowed)
    # the lead technician labels the cause from the note: stated, or implied by the verb; otherwise "unknown"
    label_cause = cause if (stated or implied == cause) else "unknown"
    if comp == "other" and cause == "unknown":
        label_cause, stated = "unknown", False

    force_comp_in_action = (not use_finding) or _u(rng) < style.p_comp_in_action
    # --- clauses
    ref = style.ref(rng, asset_tag) if _u(rng) < style.p_ref else None
    sym = None
    if _u(rng) < style.p_sym:
        sym = _pick(rng, _SYMPTOMS[comp])
        if _u(rng) < style.p_num:
            sym += " " + _number(rng)
    side = None
    if _u(rng) < style.p_side:
        c2 = _pick(rng, [c for c in COMPONENTS if c not in (comp, "other")])
        side = "also " + style.variant(rng, f"comp:{c2}:", _COMP_WORDS[c2]) + " " + _pick(rng, _SYMPTOMS[c2][:4])
    dis: list[str] = []
    if _u(rng) < style.p_dis:
        for _ in range(1 + int(_u(rng) < 0.25)):
            if _u(rng) < 0.6:
                c2 = _pick(rng, [c for c in COMPONENTS if c not in (comp, "other")])
                dis.append(_pick(rng, _COMP_NEGATIONS).format(c=style.variant(rng, f"comp:{c2}:", _COMP_WORDS[c2])))
            else:
                c2 = _pick(rng, [c for c in CAUSES if c not in (cause, "unknown")])
                dis.append(_pick(rng, _CAUSE_NEGATIONS[c2]))

    cp = None
    if stated:
        pool = _CONTAM_BY_COMP[comp] if cause == "contamination" else _CAUSE_PHRASES[cause]
        if _u(rng) < style.p_slang:
            pool = _SLANG_CAUSE[cause]
        cp = style.variant(rng, f"cause:{cause}:{comp if cause == 'contamination' else ''}", pool)

    finding = None
    if use_finding:
        if stated and cp:
            finding = _pick(rng, [f"{cw} {cp}", f"{cp} {cw}", f"{cw} is {cp}", f"found {cw} {cp}", f"{cw}: {cp}"])
        elif comp == "other" and cause == "unknown":
            finding = cw
        else:
            finding = _pick(rng, _HIDDEN_CAUSE_WORDS).format(c=cw)

    if action == "none":
        act = _pick(rng, [f"{phrase} {cw}", f"{cw} - {phrase}"]) if force_comp_in_action else phrase
    elif force_comp_in_action:
        act = _pick(rng, [f"{phrase} {cw}", f"{cw} {phrase}"])
    else:
        act = phrase
    # a note must always name the component somewhere (for "other/unknown" the routine word counts)
    if finding is None and act is phrase and comp != "other":
        act = f"{phrase} {cw}"

    why = finding or cp  # the finding clause already contains the cause phrase; otherwise the bare cause phrase ("worn", "no grease")
    if style.result_first:
        parts = [ref, act, ("was " + sym) if sym else None, why, side, *dis]
    else:
        parts = [ref, sym, side, *dis, why, act]
    parts = [p for p in parts if p]
    words_ok = []
    for p in parts:
        words_ok.append(" ".join(_typo_word(rng, w) if _u(rng) < style.typo else w for w in p.split(" ")))
    text = style.sep.join(words_ok) if style.sep != ". " else ". ".join(words_ok)
    text = _apply_case(style, text.strip()) + style.end
    return text, {"component": comp, "cause": label_cause, "action": action}


# ============================================================================================ work orders
_DURATION_MIN = {"replace": (120, 600), "repair": (180, 720), "adjust": (30, 240), "lubricate": (15, 60), "clean": (30, 180),
                 "inspect": (15, 90), "none": (5, 45)}


def _scenario_weights(asset_tag: str):
    out = []
    for sc in _SCENARIOS:
        w = float(sc[3])
        bias = _ASSET_BIAS.get(asset_tag)
        if bias and sc[0] == bias[0]:
            w *= bias[1]
        if sc[1] == "cavitation" and asset_tag in _ASSET_CAVITATION_BIAS:
            w *= _ASSET_CAVITATION_BIAS[asset_tag]
        out.append((sc, w))
    return out


def generate_work_orders(seed: int = DEFAULT_SEED, n: int = DEFAULT_N) -> list[dict]:
    """The legacy CMMS of PLT02: `n` work orders, chronological, ids WO-000001...

    Keys: wo_id, asset_tag (B-01..B-12), opened_at, closed_at (None for ~4 %), technician (T01..T20), note (free text),
    label ({component, cause, action}; the ground truth, NOT something a CMMS stores).
    """
    rng = random.Random(seed)
    d0 = date.fromisoformat(DATE_START)
    ndays = (date.fromisoformat(DATE_END) - d0).days + 1
    rows = []
    for _ in range(n):
        tech = _wpick(rng, _TECH_WEIGHTS)
        style = _STYLES[tech]
        asset = _pick(rng, ASSET_TAGS)
        scen = _wpick(rng, _scenario_weights(asset))
        action = _wpick(rng, sorted(scen[2].items()))
        note, label = _make_note(rng, style, asset, scen, action)
        # when: weekdays busier; each technician works a fixed shift (day / evening / night)
        while True:
            day = int(_u(rng) * ndays)
            if (d0 + timedelta(days=day)).weekday() < 5 or _u(rng) < 0.6:
                break
        start_hour = (6, 14, 22)[int(tech[1:]) % 3]
        opened = datetime.combine(d0 + timedelta(days=day), datetime.min.time()) + timedelta(hours=start_hour + int(_u(rng) * 8), minutes=int(_u(rng) * 60))
        if not _exists(opened):
            opened += timedelta(hours=1)
        lo, hi = _DURATION_MIN[action]
        minutes = lo + int(_u(rng) * (hi - lo))
        if action == "none" and _u(rng) < 0.2:
            minutes += 1440 * (1 + int(_u(rng) * 4))
        keep_closed = _u(rng) >= 0.04
        rows.append({"asset_tag": asset, "opened": opened, "minutes": minutes, "closed": keep_closed, "technician": tech, "note": note, "label": label})

    # planted daylight-saving traps (two rows chosen deterministically; they must be closed)
    for idx, (when, mins) in zip((int(_u(rng) * n), int(_u(rng) * n)), ((datetime(2025, 4, 5, 23, 40), 40), (datetime(2026, 4, 4, 23, 15), 25))):
        rows[idx % n].update(opened=when, minutes=mins, closed=True)

    rows.sort(key=lambda r: r["opened"])
    out = []
    for i, r in enumerate(rows, 1):
        closed_at = _fmt(utc_to_local(local_to_utc(r["opened"]) + timedelta(minutes=r["minutes"]))) if r["closed"] else None
        out.append({"wo_id": f"WO-{i:06d}", "asset_tag": r["asset_tag"], "opened_at": _fmt(r["opened"]), "closed_at": closed_at,
                    "technician": r["technician"], "note": r["note"], "label": r["label"]})
    return out


# ============================================================================================ who labelled what
def split_roles(rows: list[dict]) -> dict[str, list[str]]:
    """{"labelled": 300 wo_ids (the lead technician's hand labels), "heldout": the last N_HELDOUT work orders (hidden), "unlabelled": the rest}."""
    cut = len(rows) - N_HELDOUT
    heldout = [r["wo_id"] for r in rows[cut:]]
    earlier = [r["wo_id"] for r in rows[:cut]]
    ranked = sorted(earlier, key=lambda w: _h("lab|" + w))
    labelled = set(ranked[:N_LABELLED])
    return {"labelled": [w for w in earlier if w in labelled], "unlabelled": [w for w in earlier if w not in labelled], "heldout": heldout}


def lead_labels(rows: list[dict], seed: int = DEFAULT_SEED, noise: float = 0.04) -> dict[str, dict]:
    """The lead technician's labels for the labelled notes: right most of the time, ~4 % of the notes have one field slipped."""
    rng = random.Random(seed + 11)
    roles = split_roles(rows)
    by_id = {r["wo_id"]: r for r in rows}
    out = {}
    for wid in roles["labelled"]:
        lab = dict(by_id[wid]["label"])
        if _u(rng) < noise:
            f = _pick(rng, FIELDS)
            enum = {"component": COMPONENTS, "cause": CAUSES, "action": ACTIONS}[f]
            lab[f] = _pick(rng, [v for v in enum if v != lab[f]])
        out[wid] = lab
    return out


def _learner_row(r: dict, label: dict | None = None) -> dict:
    d = {"wo_id": r["wo_id"], "asset_tag": r["asset_tag"], "technician": r["technician"], "opened_at": r["opened_at"], "note": r["note"]}
    if label is not None:
        d["label"] = label
    return d


def build_fixtures(root: Path) -> dict:
    rows = generate_work_orders()
    roles = split_roles(rows)
    by_id = {r["wo_id"]: r for r in rows}
    lead = lead_labels(rows)
    data = root / "platform" / "plt02" / "data"
    data.mkdir(parents=True, exist_ok=True)

    def dump(path: Path, items: list[dict]) -> None:
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items))

    dump(data / "labelled.jsonl", [_learner_row(by_id[w], lead[w]) for w in roles["labelled"]])
    dump(data / "unlabelled.jsonl", [_learner_row(by_id[w]) for w in roles["unlabelled"]])
    dump(root / "checks" / "_plt02_heldout.jsonl", [_learner_row(by_id[w], by_id[w]["label"]) for w in roles["heldout"]])
    return {k: len(v) for k, v in roles.items()}


# ============================================================================================ CLI
def _stats(rows: list[dict]) -> None:
    from collections import Counter
    print("component", dict(Counter(r["label"]["component"] for r in rows)))
    print("cause    ", dict(Counter(r["label"]["cause"] for r in rows)))
    print("action   ", dict(Counter(r["label"]["action"] for r in rows)))
    lens = sorted(len(r["note"]) for r in rows)
    print("note length p5/p50/p95/max", lens[len(lens) // 20], lens[len(lens) // 2], lens[-len(lens) // 20], lens[-1])
    print("closed_at None:", sum(r["closed_at"] is None for r in rows), "| distinct notes:", len({r['note'].lower() for r in rows}), "of", len(rows))
    roles = split_roles(rows)
    by_id = {r["wo_id"]: r for r in rows}
    train = {by_id[w]["note"].lower().strip() for w in roles["labelled"]}
    held = [by_id[w]["note"].lower().strip() for w in roles["heldout"]]
    print("held-out notes that also appear verbatim among the 300 labelled:", sum(h in train for h in held), "of", len(held))
    print("held-out component", dict(Counter(by_id[w]["label"]["component"] for w in roles["heldout"])))
    print("held-out cause    ", dict(Counter(by_id[w]["label"]["cause"] for w in roles["heldout"])))
    print("held-out action   ", dict(Counter(by_id[w]["label"]["action"] for w in roles["heldout"])))
    print("planted:", [(r["wo_id"], r["opened_at"], r["closed_at"]) for r in rows if r["opened_at"] in ("2025-04-05 23:40", "2026-04-04 23:15")])


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "sample"
    if cmd == "sample":
        k = int(argv[2]) if len(argv) > 2 else 25
        for r in generate_work_orders()[:k]:
            print(f"{r['wo_id']} {r['asset_tag']} {r['technician']} | {r['note']!r:70} -> {r['label']}")
    elif cmd == "stats":
        _stats(generate_work_orders())
    elif cmd == "build-fixtures":
        root = Path(__file__).resolve().parents[2]
        print(build_fixtures(root))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
