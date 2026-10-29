#!/usr/bin/env bash
# Creates (or updates) the Secret `plant-secrets` in the cluster from your local environment. It is NEVER committed.
set -euo pipefail
NS="${1:-plant}"
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
kubectl -n "$NS" create secret generic plant-secrets \
  --from-literal=DATABASE_URL="${K8S_DATABASE_URL:-postgresql://plant:plant@host.docker.internal:5433/plant}" \
  --from-literal=AUTH_SECRET="${AUTH_SECRET:-dev-only-secret-change-me-0123456789abcdef}" \
  --dry-run=client -o yaml | kubectl apply -f -
