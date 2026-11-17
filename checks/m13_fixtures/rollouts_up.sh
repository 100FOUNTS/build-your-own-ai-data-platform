#!/usr/bin/env bash
# Installs the Argo Rollouts controller + CRDs (pinned) on a kind cluster. Idempotent.
# Usage: bash checks/m13_fixtures/rollouts_up.sh [kube-context]      (default: kind-plant)
set -euo pipefail
CTX="${1:-kind-plant}"
ROLLOUTS_VERSION="${ROLLOUTS_VERSION:-v1.10.1}"
kubectl --context "$CTX" get namespace argo-rollouts >/dev/null 2>&1 || kubectl --context "$CTX" create namespace argo-rollouts
kubectl --context "$CTX" apply -n argo-rollouts --server-side --force-conflicts -f "https://github.com/argoproj/argo-rollouts/releases/download/${ROLLOUTS_VERSION}/install.yaml"
kubectl --context "$CTX" -n argo-rollouts rollout status deployment/argo-rollouts --timeout=300s
echo "Argo Rollouts ${ROLLOUTS_VERSION} ready on ${CTX}"
