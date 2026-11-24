#!/usr/bin/env bash
# Tear the local Kubernetes cluster down. `bash scripts/k8s/down.sh keep` uninstalls the release but keeps
# the cluster (faster re-install); with no argument the whole kind cluster is deleted.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
if [[ "${1:-}" == "keep" ]]; then
  helm uninstall fsip --namespace fsip || true
  kubectl delete namespace fsip --ignore-not-found
  echo "release removed; cluster 'fsip' still running"
else
  kind delete cluster --name fsip
fi
