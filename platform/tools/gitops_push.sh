#!/usr/bin/env bash
# Publishes the deploy/ folder to the GitOps repository (Forgejo), branch main. Usage: gitops_push.sh [deploy_folder]
set -euo pipefail
SRC="${1:-deploy}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/deploy"
cp -R "$SRC/k8s" "$TMP/deploy/k8s"
cd "$TMP"
git init -q -b main
git config user.email "gitops@plant.test"; git config user.name "gitops"
git add -A && git commit -q -m "deploy: desired state of the platform"
git push -q --force "http://gitops:gitops-pass1@localhost:3002/gitops/plant-deploy.git" main
echo "published to Forgejo (branch main)"
