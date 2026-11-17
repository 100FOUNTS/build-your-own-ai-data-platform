"""Stand-in for the plant API used by the module 13 checks (canary analysis).

`/health` always answers 200 (so the pod is Ready, which is all a readiness probe can see).
Every other path answers like a healthy API in mode `good` and with HTTP 500 in mode `bad`:
a regression that only shows up on real requests.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ.get("STUB_MODE", "good")
GOOD = {"status": "ok", "openapi": "3.1.0", "info": {"title": "Plant reliability API", "version": "1.0.0"}, "paths": {}}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/health":
            self._send(200, {"status": "ok"})
        elif MODE == "bad":
            self._send(500, {"detail": "internal error"})
        else:
            self._send(200, GOOD)

    def log_message(self, *args) -> None:
        pass


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
