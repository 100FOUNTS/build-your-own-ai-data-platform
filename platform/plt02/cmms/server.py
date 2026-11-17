"""Mock legacy CMMS of plant PLT02 ("the client's old maintenance system"), a platform fixture.

You don't need to read this file; the guide (module 15) has the vendor's documented API, and the interview has the rest.

    python platform/plt02/cmms/server.py --port 8702

Documented API (all GET, header `X-Api-Key`; dates are DD/MM/YYYY HH:MM, local plant time):
    /api/v1/equipment                    fixed-width list of equipment
    /api/v1/workorders                   XML; filters: modified_since, asset, q, status; paging: page_size, cursor
    /api/v1/workorders/{id}              one work order (XML)
    POST /api/v1/workorders/{id}/close   needs the read-write key

Fixture knobs (not part of the CMMS; used by the checks and by you to practise). Environment at start-up:
    CMMS_SEED, CMMS_N, CMMS_INITIAL_FRACTION, CMMS_FAULTS (JSON), CMMS_RO_KEY, CMMS_RW_KEY
and, while running, POST JSON to /_admin/{reset,faults,advance,correct} and GET /_admin/{stats,truth}.
Faults: p503, p429, p_truncate, retry_after_s, fail_first_n, fail_first_status, down_after, dup_rate, page_cap, latency_ms.
Everything is deterministic for a given seed and sequence of requests.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data as D  # noqa: E402

DEFAULT_FAULTS = {"p503": 0.05, "p429": 0.05, "p_truncate": 0.02, "retry_after_s": 1, "fail_first_n": 0, "fail_first_status": 503, "down_after": None, "dup_rate": 0.03,
                  "page_cap": 200, "latency_ms": 0}
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.ro = os.environ.get("CMMS_RO_KEY", "ro-demo-key")
        self.rw = os.environ.get("CMMS_RW_KEY", "rw-demo-key")
        self.reset(int(os.environ.get("CMMS_SEED", "2")), int(os.environ.get("CMMS_N", "1500")),
                   float(os.environ.get("CMMS_INITIAL_FRACTION", "0.8")), json.loads(os.environ.get("CMMS_FAULTS", "{}") or "{}"))

    def reset(self, seed: int, n: int, fraction: float, faults: dict | None = None, probes: bool = True):
        with self.lock:
            self.seed, self.n = seed, n
            self.ds = D.Dataset(seed, n, fraction, probes)
            self.faults = {**DEFAULT_FAULTS, **(faults or {})}
            self.log: list[dict] = []
            self.rows_served = 0
            self.writes = {"applied": 0, "denied": 0}
            self.fault_n = 0

    def set_faults(self, f: dict):
        with self.lock:
            self.faults.update(f)
            self.fault_n = 0


ST = State()


def render_workorders(snaps: list[dict], nxt: str | None, now: str) -> bytes:
    out = ['<?xml version="1.0" encoding="ISO-8859-1"?>', f"<WORKORDERS generated={quoteattr(D.to_dmy(now))} tz=\"LOCAL\">"]
    for s in snaps:
        tag = D.dirty_tag(s["wo_id"], s["tag"])
        out.append(
            f"<WO id={quoteattr(s['wo_id'])} rev=\"{s['rev']}\"><EQUIP>{escape(tag)}</EQUIP><TYPE>CM</TYPE><OPENED>{D.to_dmy(s['opened_at'])}</OPENED>"
            f"<CLOSED>{D.to_dmy(s['closed_at'])}</CLOSED><MODIFIED>{D.to_dmy(s['modified'])}</MODIFIED><TECH>{escape(s['technician'])}</TECH>"
            f"<STATUS>{'ABIERTA' if s['closed_at'] is None else 'CERRADA'}</STATUS><NOTE>{escape(CTRL.sub(' ', s['note']))}</NOTE></WO>")
    out.append(f"<PAGE count=\"{len(snaps)}\"" + (f" next={quoteattr(nxt)}" if nxt else "") + "/>")
    out.append("</WORKORDERS>")
    return "\n".join(out).encode("iso-8859-1", errors="xmlcharrefreplace")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence
        pass

    # ---- helpers
    def _send(self, code: int, body: bytes = b"", ctype: str = "text/xml", headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)  # no charset on purpose, like the real thing
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _role(self) -> str:
        k = self.headers.get("X-Api-Key", "")
        return "rw" if k == ST.rw else "ro" if k == ST.ro else "none"

    def _entry(self, path: str, q: dict, status: int, role: str, rows: int = 0, fault: str = ""):
        with ST.lock:
            ST.log.append({"t": time.time(), "method": self.command, "path": path, "query": {k: v[0] for k, v in q.items()}, "status": status, "key": role,
                           "rows": rows, "fault": fault})
            ST.log = ST.log[-20000:]

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # ---- dispatch
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, b"ok", "text/plain")
        if u.path.startswith("/_admin/"):
            return self._admin_get(u.path)
        self._api(u.path, q)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path.startswith("/_admin/"):
            return self._admin_post(u.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        role = self._role()
        m = re.fullmatch(r"/api/v1/workorders/([^/]+)/close", u.path)
        if role == "none":
            self._entry(u.path, q, 401, role)
            return self._send(401, b"<ERROR code=\"401\"/>")
        if role != "rw" or not m or self.command != "POST":
            with ST.lock:
                ST.writes["denied"] += 1
            self._entry(u.path, q, 403, role)
            return self._send(403, b"<ERROR code=\"403\">read-only key</ERROR>")
        with ST.lock:
            try:
                ST.ds.close(m.group(1))
                ST.writes["applied"] += 1
                code = 200
            except KeyError:
                code = 404
        self._entry(u.path, q, code, role)
        self._send(code, b"<OK/>")

    do_PUT = do_DELETE = do_PATCH = do_POST  # any other verb is a write attempt

    # ---- the API
    def _fault(self, path: str, q: dict, role: str) -> bool:
        """Returns True when the request was answered with a fault."""
        with ST.lock:
            f = dict(ST.faults)
            ST.fault_n += 1
            n = ST.fault_n
            seed = ST.seed
        if f["latency_ms"]:
            time.sleep(f["latency_ms"] / 1000)
        r = random.Random(f"{seed}:{n}").random()
        first = n <= f["fail_first_n"]
        if (first and f["fail_first_status"] == 429) or (not first and f["p503"] <= r < f["p503"] + f["p429"]):
            self._entry(path, q, 429, role, fault="429")
            self._send(429, b"Too Many Requests", "text/plain", {"Retry-After": f["retry_after_s"]})
            return True
        if first or (f["down_after"] is not None and n > f["down_after"]) or r < f["p503"]:
            self._entry(path, q, 503, role, fault="503")
            self._send(503, b"<html><body>Service Unavailable</body></html>", "text/html")
            return True
        return False

    def _api(self, path: str, q: dict):
        role = self._role()
        if not path.startswith("/api/v1/"):
            return self._send(404, b"<ERROR code=\"404\"/>")
        if role == "none":
            self._entry(path, q, 401, role)
            return self._send(401, b"<ERROR code=\"401\">missing or wrong X-Api-Key</ERROR>")
        if self._fault(path, q, role):
            return
        g = lambda k, d=None: q.get(k, [d])[0]  # noqa: E731
        if path == "/api/v1/equipment":
            self._entry(path, q, 200, role, rows=len(D.EQUIPMENT))
            return self._send(200, D.equipment_fixed_width().encode("iso-8859-1"), "text/plain")
        try:
            if path == "/api/v1/workorders":
                since = D.from_dmy(g("modified_since")) if g("modified_since") else None
                with ST.lock:
                    f = ST.faults
                    page, nxt = ST.ds.query(since, g("asset"), g("q"), g("status"), g("cursor"), int(g("page_size", 100)), f["page_cap"])
                    out = list(page)
                    if f["dup_rate"] > 0:
                        rnd = random.Random(f"{ST.seed}:d:{ST.fault_n}")
                        before, after = [], []
                        for s in page:
                            stale = ST.ds.stale_copy(s)
                            if stale:  # a corrected work order: its old version arrives before or after the new one
                                (before if rnd.random() < 0.5 else after).append(stale)
                            elif rnd.random() < f["dup_rate"]:
                                after.append(s)
                        if g("cursor") and page:
                            before.append(page[0])  # the page boundary overlaps by one row
                        out = before + out + after
                    body = render_workorders(out, nxt, ST.ds.now)
                    ST.rows_served += len(out)
                    cut = f["p_truncate"] and random.Random(f"{ST.seed}:t:{ST.fault_n}").random() < f["p_truncate"]
                if cut:
                    body = body[: max(40, int(len(body) * 0.6))]
                self._entry(path, q, 200, role, rows=len(out), fault="truncated" if cut else "")
                return self._send(200, body)
            m = re.fullmatch(r"/api/v1/workorders/([^/]+)", path)
            if m:
                with ST.lock:
                    row = ST.ds.rows.get(m.group(1))
                    snap = ST.ds.snapshot(row) if row else None
                    body = render_workorders([snap], None, ST.ds.now) if snap else b""
                    ST.rows_served += 1 if snap else 0
                if not snap:
                    self._entry(path, q, 404, role)
                    return self._send(404, b"<ERROR code=\"404\">work order not found</ERROR>")
                self._entry(path, q, 200, role, rows=1)
                return self._send(200, body)
        except (ValueError, KeyError, json.JSONDecodeError):
            self._entry(path, q, 400, role)
            return self._send(400, b"<ERROR code=\"400\">bad parameter</ERROR>")
        self._entry(path, q, 404, role)
        self._send(404, b"<ERROR code=\"404\"/>")

    # ---- the fixture's own admin interface (never logged as API traffic)
    def _admin_get(self, path: str):
        if not self.headers.get("X-Fixture"):  # someone who is not the checks is poking at the fixture: leave a trace
            self._entry(path, {}, 200, self._role())
        with ST.lock:
            if path == "/_admin/stats":
                return self._json({"log": ST.log, "rows_served": ST.rows_served, "writes": ST.writes, "now": ST.ds.now, "faults": ST.faults})
            if path == "/_admin/truth":
                return self._json({"now": ST.ds.now, "rows": sorted(ST.ds.visible(), key=lambda r: r["wo_id"]), "probes": D.PROBE_IDS})
        self._json({"error": "unknown"}, 404)

    def _admin_post(self, path: str):
        body = self._body()
        if not self.headers.get("X-Fixture"):
            self._entry(path, {}, 200, self._role())
        with ST.lock:
            if path == "/_admin/reset":
                ST.reset(int(body.get("seed", 2)), int(body.get("n", 1500)), float(body.get("initial_fraction", 0.8)), body.get("faults"), body.get("probes", True))
                return self._json({"now": ST.ds.now, "visible": len(ST.ds.visible())})
            if path == "/_admin/faults":
                ST.set_faults(body)
                return self._json(ST.faults)
            if path == "/_admin/advance":
                ST.ds.now = body["to"] if "to" in body else ST.ds.now_for_fraction(float(body["fraction"]))
                return self._json({"now": ST.ds.now, "visible": len(ST.ds.visible())})
            if path == "/_admin/correct":
                try:
                    snap = ST.ds.correct(body["wo_id"], body["fields"], bool(body.get("bump_modified", False)))
                except PermissionError as e:
                    return self._json({"error": str(e)}, 409)
                except (KeyError, ValueError) as e:
                    return self._json({"error": str(e)}, 400)
                return self._json(snap)
        self._json({"error": "unknown"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("CMMS_HOST", "0.0.0.0"))  # noqa: S104
    ap.add_argument("--port", type=int, default=int(os.environ.get("CMMS_PORT", "8702")))
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"mock CMMS PLT02 on {a.host}:{a.port} (visible work orders: {len(ST.ds.visible())}, clock {ST.ds.now})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
