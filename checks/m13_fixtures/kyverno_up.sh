#!/usr/bin/env bash
# Installs Kyverno (admission controller only, pinned) on a kind cluster. Idempotent.
# Usage: bash checks/m13_fixtures/kyverno_up.sh [kube-context]      (default: kind-plant)
# Only the admission controller is needed to verify image signatures; the background, cleanup and reports controllers are off to save memory.
# Registries on the kind network are plain HTTP, hence registryClient.allowInsecure (never do that against a real registry).
set -euo pipefail
CTX="${1:-kind-plant}"
KYVERNO_CHART="${KYVERNO_CHART:-3.9.2}"   # Kyverno v1.19.2
helm --kube-context "$CTX" upgrade --install kyverno kyverno --repo https://kyverno.github.io/kyverno/ --version "$KYVERNO_CHART" \
  --namespace kyverno --create-namespace --wait --timeout 300s \
  --set admissionController.replicas=1 \
  --set backgroundController.enabled=false --set cleanupController.enabled=false --set reportsController.enabled=false \
  --set features.registryClient.allowInsecure=true \
  --set crds.migration.enabled=false \
  --set admissionController.container.resources.limits.memory=384Mi
kubectl --context "$CTX" -n kyverno rollout status deployment/kyverno-admission-controller --timeout=300s
echo "Kyverno ${KYVERNO_CHART} ready on ${CTX}"
