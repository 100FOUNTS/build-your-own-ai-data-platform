"""PLT02 work-order vocabulary: the enums every module of Part 2 agrees on.

Stdlib only. The plant (PLT02) is a second site with 20 technicians who write failure notes by hand.
Nothing here is a solution: it is the *contract* of the plant's data (what a label may contain).

How to import it (the folder is called `platform`, which clashes with the stdlib module of the same name,
so `platform.plt02` can NOT be imported; put `platform/` itself on the path instead):

    import sys; sys.path.insert(0, "<repo root>/platform")
    from plt02 import schema
"""

from __future__ import annotations

# ----------------------------------------------------------------------------------------------- plant
PLANT = "PLT02"
TIMEZONE = "America/Santiago"          # the legacy CMMS stores naive local wall-clock times in this zone
DATE_START = "2025-01-01"
DATE_END = "2026-06-30"
ASSET_TAGS = tuple(f"B-{i:02d}" for i in range(1, 13))        # "B" = bomba (pump): B-01 .. B-12
TECHNICIANS = tuple(f"T{i:02d}" for i in range(1, 21))        # T01 .. T20
LEAD_TECHNICIAN = "T01"                                       # the only person who has labelled notes

DEFAULT_SEED = 2
DEFAULT_N = 1500
N_LABELLED = 300                                              # notes the lead technician labelled by hand
N_HELDOUT = 150                                               # the most recent notes, hidden from you (the checks score them)

# ----------------------------------------------------------------------------------------------- labels
# What was found faulty or worked on (NOT what the symptom looks like). Same family as the repo's failure modes:
# bearing / seal are components, cavitation is a CAUSE (it damages the impeller or starves the pump through a valve/strainer).
COMPONENTS = ("bearing", "seal", "impeller", "coupling", "motor", "valve", "other")
# Why it happened, ONLY if the note says so (or the action verb says it: "realigned" -> misalignment). Otherwise "unknown".
CAUSES = ("wear", "lubrication", "misalignment", "cavitation", "overheating", "contamination", "imbalance", "looseness", "unknown")
# What was DONE in this work order. "none" = nothing done yet (monitor, parts ordered, deferred).
ACTIONS = ("replace", "repair", "adjust", "lubricate", "clean", "inspect", "none")

FIELDS = ("component", "cause", "action")
ENUMS = {"component": COMPONENTS, "cause": CAUSES, "action": ACTIONS}

# The repo's failure modes (platform/seeds/work_orders.csv, platform/sim): bearing | seal | cavitation.
def repo_failure_mode(label: dict) -> str | None:
    """Maps a PLT02 label to the PLT01 failure-mode vocabulary (None if it is none of the three)."""
    if label.get("cause") == "cavitation":
        return "cavitation"
    if label.get("component") in ("bearing", "seal"):
        return label["component"]
    return None


# ----------------------------------------------------------------------------------------------- the model contract
# Every model in modules 16-17 is asked EXACTLY this (system + user message) and must answer with ONE JSON object.
SYSTEM_PROMPT = (
    "You read maintenance notes that plant technicians wrote by hand (English, shorthand, typos). "
    "Reply with ONE JSON object and nothing else, with exactly the keys component, cause and action. "
    "component = the part that was found faulty or worked on, one of: " + ", ".join(COMPONENTS) + ". "
    "cause = why it failed, only if the note says so, one of: " + ", ".join(CAUSES) + " (unknown if not said). "
    "action = what was done, one of: " + ", ".join(ACTIONS) + " (none if nothing was done yet)."
)

# JSON Schema for the answer (used by constrained decoding in module 17, and by the checks to validate answers).
NOTE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "component": {"type": "string", "enum": list(COMPONENTS)},
        "cause": {"type": "string", "enum": list(CAUSES)},
        "action": {"type": "string", "enum": list(ACTIONS)},
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}


def valid_answer(obj: object) -> bool:
    """True iff `obj` is a dict with exactly the three keys and enum values (what 'JSON-valid' means in the checks)."""
    return (isinstance(obj, dict) and set(obj) == set(FIELDS)
            and all(isinstance(obj[f], str) and obj[f] in ENUMS[f] for f in FIELDS))
