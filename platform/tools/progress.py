"""Shows which module you are on. Skips the slow modules by default; use --all to run them too."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTS = {"postgres": 5433, "valkey": 6380, "redpanda": 19092, "s3": 8333, "iceberg": 8181, "mlflow": 5001, "keycloak": 8089, "openbao": 8200,
         "prometheus": 9090, "otlp": 4317, "floci": 4566, "forgejo": 3002, "ollama": 11434}
# (no., name, file, required services, slow)
MODULES = [
    (0, "Engineering foundations", "test_m00_base.py", [], False),
    (1, "Databases", "test_m01_databases.py", ["postgres", "valkey"], False),
    (2, "Streaming", "test_m02_streaming.py", ["postgres", "valkey", "redpanda"], False),
    (3, "Lakehouse", "test_m03_lakehouse.py", ["postgres", "redpanda", "s3", "iceberg"], False),
    (4, "ML", "test_m04_ml.py", ["postgres", "mlflow", "s3"], False),
    (5, "Orchestration", "test_m05_orchestration.py", ["postgres", "redpanda", "s3", "iceberg", "mlflow"], False),
    (6, "Generative AI", "test_m06_genai.py", ["postgres", "ollama"], False),
    (7, "Backend", "test_m07_backend.py", ["postgres", "valkey", "redpanda"], False),
    (8, "Security", "test_m08_security.py", ["postgres", "valkey", "redpanda", "keycloak", "openbao"], False),
    (9, "Frontend", "test_m09_frontend.py", ["postgres", "valkey", "redpanda"], False),
    (10, "Observability", "test_m10_observability.py", ["postgres", "valkey", "redpanda", "prometheus", "otlp"], False),
    (11, "Cloud (OpenTofu)", "test_m11_cloud.py", ["floci", "redpanda"], True),
    (12, "Containers and K8s", "test_m12_containers_k8s.py", ["redpanda"], True),
    (13, "CI/CD and GitOps", "test_m13_cicd_gitops.py", ["forgejo"], True),
    (14, "Integration", "test_m14_integration.py", ["postgres", "valkey", "redpanda", "keycloak", "floci"], False),
]


def up(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


def main() -> None:
    run_slow = "--all" in sys.argv
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    print("\nYour progress (missing services are listed; slow modules are skipped unless you pass --all)\n")
    first = None
    for n, name, file, needs, slow in MODULES:
        if not (ROOT / "checks" / file).exists():
            continue
        missing = [s for s in needs if not up(PORTS[s])]
        if missing:
            print(f"  ⏸   Module {n:>2}  {name:<22} needs: {', '.join(missing)}")
            continue
        if slow and not run_slow:
            print(f"  ⏱   Module {n:>2}  {name:<22} slow: run it with `make check-{n}`")
            continue
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", "pytest", f"checks/{file}", "-q", "-p", "no:warnings", "--tb=no"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        ok = r.returncode == 0
        if not ok and first is None:
            first = n
        print(f"  {'✅' if ok else '⬜'}  Module {n:>2}  {name:<22} ({time.time() - t0:4.0f} s)")
    print(f"\nNext to do: module {first} → guide/{first:02d}_*.md  and  make check-{first}\n" if first is not None else "\nNo pending modules among the ones that ran.\n")


if __name__ == "__main__":
    main()
