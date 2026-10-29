#!/usr/bin/env bash
# Sets up GitOps locally (idempotent): Argo CD (core mode) on the kind cluster + the `plant-deploy` repository on Forgejo.
set -euo pipefail
ARGO_VERSION="${ARGO_VERSION:-v3.5.5}"
FORGEJO_CONTAINER="${FORGEJO_CONTAINER:-ai-data-platform-forgejo-1}"

kubectl config use-context kind-plant >/dev/null
kubectl get namespace argocd >/dev/null 2>&1 || kubectl create namespace argocd
if ! kubectl -n argocd get deployment argocd-repo-server >/dev/null 2>&1; then
  kubectl apply -n argocd --server-side --force-conflicts -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGO_VERSION}/manifests/core-install.yaml"
fi
kubectl -n argocd rollout status deployment/argocd-repo-server --timeout=300s
kubectl -n argocd rollout status deployment/argocd-redis --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s

# In "core" mode there is no Argo server to create the `default` project: we declare it ourselves.
kubectl apply -f - <<'YAML'
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata: {name: default, namespace: argocd}
spec:
  sourceRepos: ["*"]
  destinations: [{namespace: "*", server: "*"}]
  clusterResourceWhitelist: [{group: "*", kind: "*"}]
YAML

# Forgejo must be reachable from the pods as http://forgejo:3000 → it is attached to the kind network with that alias.
docker network inspect kind >/dev/null 2>&1
docker network connect --alias forgejo kind "$FORGEJO_CONTAINER" 2>/dev/null || true
docker exec -u git "$FORGEJO_CONTAINER" forgejo admin user create --admin --username gitops --password gitops-pass1 --email gitops@plant.test --must-change-password=false >/dev/null 2>&1 || true
curl -sf -u gitops:gitops-pass1 -X POST http://localhost:3002/api/v1/user/repos -H 'Content-Type: application/json' \
  -d '{"name":"plant-deploy","private":false,"default_branch":"main"}' >/dev/null 2>&1 || true
echo "GitOps ready: Argo CD ${ARGO_VERSION} + repo http://localhost:3002/gitops/plant-deploy"
