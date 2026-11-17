"""The data behind the mock legacy CMMS of plant PLT02.

You don't need to read this file: it is "the client's old maintenance system". What you DO need is in the module 15 guide
(the vendor's documented API) and in the interview with the client (`/client-plt02`): everything else you find by running
your connector against it, exactly as on a real job.

The rows come from `generate_work_orders` (platform/plt02/notes.py). The CMMS exports them the way an old system would and
it NEVER exposes the `label` of a row (the hidden ground truth of module 16).

Import note: `platform` is also the name of a standard-library module, so `from platform.plt02.notes import ...` can never
work. The generator is therefore loaded by file path.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

PLT02_DIR = Path(__file__).resolve().parents[1]
FMT = "%Y-%m-%d %H:%M"  # internal (sortable) form; the CMMS speaks DD/MM/YYYY HH:MM
LOCK_DAYS = 7  # a supervisor can correct a work order for 7 days; after that the CMMS locks it

# Rows the CMMS adds on top of the generator (the awkward real-life cases). The ids are far from the generator's.
PROBE_IDS = {
    "gap": "WO-900001",  # opened in the hour that does not exist (spring forward, 2025-09-07 00:30)
    "straddle": "WO-900002",  # opened and closed inside the repeated hour (fall back): the naive close is EARLIER than the open
    "fold": "WO-900003",  # opened in the repeated hour, closed next morning
    "typo": "WO-900004",  # closed days before it was opened (a typo): the close date cannot be right
    "unmapped": "WO-900005",  # a pump (B-13) that is not in anybody's asset register yet
}


def load_generator():
    """`generate_work_orders` from platform/plt02/notes.py, loaded by path (see the module docstring)."""
    key = "plt02_notes"
    if key not in sys.modules:
        path = PLT02_DIR / "notes.py"
        if not path.exists():
            raise RuntimeError(f"missing {path}: the CMMS needs generate_work_orders(seed, n) from platform/plt02/notes.py")
        added = str(PLT02_DIR) not in sys.path
        if added:
            sys.path.insert(0, str(PLT02_DIR))  # so that notes.py can import its sibling schema.py
        try:
            spec = importlib.util.spec_from_file_location(key, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[key] = mod
            spec.loader.exec_module(mod)
        finally:
            if added:
                sys.path.remove(str(PLT02_DIR))
    return sys.modules[key].generate_work_orders


def norm_tag(raw: str) -> str:
    """'b 03', ' B-03 ', 'B03' -> 'B-03'."""
    s = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    return f"{s[0]}-{s[1:]}" if len(s) > 1 else s


def dirty_tag(wo_id: str, tag: str) -> str:
    """How the operator typed the tag in this work order (deterministic per work order)."""
    h = int(hashlib.sha1(wo_id.encode()).hexdigest()[:8], 16) % 100
    if h < 4:
        return tag.lower()
    if h < 8:
        return tag + " "
    if h < 10:
        return tag.replace("-", "")
    if h < 12:
        return tag.replace("-", " ")
    return tag


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def to_dmy(s: str | None) -> str:
    return "" if not s else datetime.strptime(s, FMT).strftime("%d/%m/%Y %H:%M")


def from_dmy(s: str) -> str:
    return datetime.strptime(s.strip(), "%d/%m/%Y %H:%M").strftime(FMT)


def _plus(s: str, **kw) -> str:
    return (datetime.strptime(s, FMT) + timedelta(**kw)).strftime(FMT)


class Dataset:
    """All the work orders PLT02 will ever have, plus a virtual clock (`now`, naive local) that decides how much of it the CMMS shows."""

    def __init__(self, seed: int = 2, n: int = 1500, initial_fraction: float = 0.8, probes: bool = True):
        gen = load_generator()(seed=seed, n=n)
        self.rows: dict[str, dict] = {}
        for g in gen:  # `label` is deliberately NOT copied
            self.rows[g["wo_id"]] = {"wo_id": g["wo_id"], "tag": g["asset_tag"], "opened_at": g["opened_at"], "closed_at": g.get("closed_at"),
                                     "technician": g["technician"], "note": g["note"], "corrections": []}
        self._opened = sorted(r["opened_at"] for r in self.rows.values())
        last = self._opened[-1]
        if probes:
            for r in self._probe_rows(last):
                self.rows[r["wo_id"]] = {**r, "corrections": []}
        self.now = self.now_for_fraction(initial_fraction)

    @staticmethod
    def _probe_rows(last_opened: str) -> list[dict]:
        P = PROBE_IDS
        return [
            {"wo_id": P["gap"], "tag": "B-03", "opened_at": "2025-09-07 00:30", "closed_at": "2025-09-07 03:00", "technician": "T04",
             "note": "falla de presión en descarga B-03, revisión de válvula. Turno noche"},
            {"wo_id": P["straddle"], "tag": "B-05", "opened_at": "2026-04-04 23:50", "closed_at": "2026-04-04 23:20", "technician": "T09",
             "note": "ruido anormal en rodamiento, se engrasa y se deja en servicio"},
            {"wo_id": P["fold"], "tag": "B-08", "opened_at": "2026-04-04 23:30", "closed_at": "2026-04-05 08:00", "technician": "T12",
             "note": "sello mecánico con fuga, se cambia el sello. Piso húmedo"},
            {"wo_id": P["typo"], "tag": "B-02", "opened_at": "2026-06-10 09:00", "closed_at": "2026-06-01 10:30", "technician": "T15",
             "note": "vibración alta, se aprieta anclaje"},
            {"wo_id": P["unmapped"], "tag": "B-13", "opened_at": _plus(last_opened, hours=-3), "closed_at": _plus(last_opened, hours=-1), "technician": "T02",
             "note": "arranque bomba nueva B-13, vibración alta, se ajusta alineación"},
        ]

    # ---- virtual clock
    def now_for_fraction(self, f: float) -> str:
        if f >= 1:
            stamps = [r["opened_at"] for r in self.rows.values()] + [r["closed_at"] for r in self.rows.values() if r["closed_at"]]
            return _plus(max(stamps), minutes=1)
        return self._opened[max(0, int(f * len(self._opened)) - 1)]

    # ---- one work order as the CMMS shows it at `now`
    @staticmethod
    def _fields(row: dict, k: int) -> tuple[dict, str | None]:
        f = {"tag": row["tag"], "opened_at": row["opened_at"], "closed_at": row["closed_at"], "technician": row["technician"], "note": row["note"]}
        bump = None
        for c in row["corrections"][:k]:
            f.update(c["fields"])
            if c["bump"]:
                bump = c["at"]
        return f, bump

    def snapshot(self, row: dict, k: int | None = None) -> dict | None:
        k = len(row["corrections"]) if k is None else k
        f, bump = self._fields(row, k)
        if f["opened_at"] > self.now:
            return None
        closed = f["closed_at"] if f["closed_at"] and f["closed_at"] <= self.now else None
        modified = closed or f["opened_at"]
        if bump and bump > modified:
            modified = bump
        return {"wo_id": row["wo_id"], "tag": f["tag"], "opened_at": f["opened_at"], "closed_at": closed, "modified": modified,
                "technician": f["technician"], "note": f["note"], "rev": 1 + k}

    def visible(self) -> list[dict]:
        return [s for r in self.rows.values() if (s := self.snapshot(r))]

    # ---- corrections made by a supervisor (admin only: the fixture, not the API)
    def correct(self, wo_id: str, fields: dict, bump_modified: bool = False) -> dict:
        row = self.rows.get(wo_id)
        snap = self.snapshot(row) if row else None
        if not snap:
            raise KeyError(wo_id)
        if (snap["closed_at"] or snap["opened_at"]) < _plus(self.now, days=-LOCK_DAYS):
            raise PermissionError("locked: older than 7 days")
        allowed = {"tag", "closed_at", "note", "technician"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot correct {sorted(bad)}")
        row["corrections"].append({"fields": dict(fields), "bump": bool(bump_modified), "at": self.now})
        return self.snapshot(row)

    def close(self, wo_id: str) -> dict:
        """What the (RW-key-only) write endpoint does: it closes a work order 'now'."""
        row = self.rows.get(wo_id)
        if not row or not self.snapshot(row):
            raise KeyError(wo_id)
        row["corrections"].append({"fields": {"closed_at": self.now}, "bump": True, "at": self.now})
        return self.snapshot(row)

    # ---- the query behind GET /api/v1/workorders
    def query(self, since: str | None = None, asset: str | None = None, q: str | None = None, status: str | None = None,
              cursor: str | None = None, page_size: int = 100, cap: int = 200) -> tuple[list[dict], str | None]:
        rows = self.visible()
        if since:
            rows = [r for r in rows if r["modified"] >= since]  # inclusive, minute precision
        if asset:
            tag = norm_tag(asset)
            rows = [r for r in rows if norm_tag(r["tag"]) == tag]
        if q:
            qf = fold(q)
            rows = [r for r in rows if qf in fold(r["note"])]
        if status:
            rows = [r for r in rows if (r["closed_at"] is None) == (status.upper() == "OPEN")]
        rows.sort(key=lambda r: (r["modified"], r["wo_id"]))
        if cursor:
            m, w = decode_cursor(cursor)
            rows = [r for r in rows if (r["modified"], r["wo_id"]) > (m, w)]
        size = max(1, min(page_size, cap))
        page, rest = rows[:size], rows[size:]
        nxt = encode_cursor(page[-1]["modified"], page[-1]["wo_id"]) if rest else None
        return page, nxt

    def stale_copy(self, snap: dict) -> dict | None:
        """The previous revision of a corrected work order (what a duplicate in a page may carry)."""
        if snap["rev"] <= 1:
            return None
        return self.snapshot(self.rows[snap["wo_id"]], snap["rev"] - 2)


def encode_cursor(modified: str, wo_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([modified, wo_id]).encode()).decode()


def decode_cursor(c: str) -> tuple[str, str]:
    m, w = json.loads(base64.urlsafe_b64decode(c.encode()))
    return m, w


# The client's equipment list, as the CMMS exports it (fixed width, ISO-8859-1). B-13 is new and nobody told the platform team.
EQUIPMENT = [(f"B-{i:02d}", "BOMBA CENTRÍFUGA", "SALA-A" if i <= 6 else "SALA-B", "ACTIVO") for i in range(1, 13)] + [("B-13", "BOMBA CENTRÍFUGA NUEVA", "SALA-B", "ACTIVO")]


def equipment_fixed_width() -> str:
    lines = [f"{'EQUIP':<10}{'DESCRIPCION':<32}{'UBICACION':<12}{'ESTADO':<10}"]
    lines += [f"{t:<10}{d:<32}{loc:<12}{st:<10}" for t, d, loc, st in EQUIPMENT]
    return "\n".join(lines) + "\n"
