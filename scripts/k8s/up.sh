#!/usr/bin/env bash
# Bring the whole platform up on a local single-node Kubernetes cluster (kind).
#
# Why this exists: docker compose proves the platform runs; Kubernetes proves it survives operationally --
# a pod that dies is replaced without a human, and the api scales out on its own when traffic arrives.
#
#   bash scripts/k8s/up.sh            # build if needed, create cluster, install, wait until healthy
#   SKIP_BUILD=1 bash scripts/k8s/up.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

export PATH="$HOME/.local/bin:$PATH"
CLUSTER=fsip
RELEASE=fsip
NS=fsip
TAG="${APP_VERSION:-v1.0.0-rc1}"
SERVICES=(engine api intelligence frontend)
METRICS_SERVER_VERSION=v0.7.2

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. images
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  missing=()
  for s in "${SERVICES[@]}"; do
    docker image inspect "fsip/$s:$TAG" >/dev/null 2>&1 || missing+=("$s")
  done
  if (( ${#missing[@]} )); then
    say "building images (${missing[*]}) at $TAG"
    APP_VERSION="$TAG" docker compose build "${missing[@]}"
  else
    say "images for $TAG already built"
  fi
fi

# ---------------------------------------------------------------- 2. cluster
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  say "kind cluster '$CLUSTER' already exists"
else
  say "creating kind cluster '$CLUSTER'"
  kind create cluster --config deploy/kind/cluster.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

# The kubelet asks for a serving certificate (serverTLSBootstrap) and nobody signs it automatically in kind.
# Approving it is what lets metrics-server scrape over TLS instead of needing --kubelet-insecure-tls.
say "approving kubelet serving certificates"
kubectl get csr -o name 2>/dev/null | xargs -r -n1 kubectl certificate approve 2>/dev/null || true

# ---------------------------------------------------------------- 3. metrics-server (the HPA's data source)
if kubectl -n kube-system get deploy metrics-server >/dev/null 2>&1; then
  say "metrics-server already installed"
else
  say "installing metrics-server $METRICS_SERVER_VERSION"
  kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml"
fi
kubectl -n kube-system rollout status deploy/metrics-server --timeout=180s

# ---------------------------------------------------------------- 4. load our images into the node
say "loading images into the cluster"
for s in "${SERVICES[@]}"; do
  kind load docker-image "fsip/$s:$TAG" --name "$CLUSTER"
done
# the simulator is pulled from Docker Hub by the node, but preloading it keeps the demo offline-safe
SIM_IMAGE=$(awk '/^simulator:/{f=1} f&&/image:/{print $2; exit}' deploy/helm/fsip/values.yaml)
if docker image inspect "$SIM_IMAGE" >/dev/null 2>&1; then
  kind load docker-image "$SIM_IMAGE" --name "$CLUSTER"
fi

# ---------------------------------------------------------------- 5. install
say "helm upgrade --install $RELEASE"
helm upgrade --install "$RELEASE" deploy/helm/fsip \
  --namespace "$NS" --create-namespace \
  --set image.tag="$TAG" \
  --wait --timeout 420s

# ---------------------------------------------------------------- 6. prove it
say "rollout status"
for d in simulator redis postgres engine intelligence api frontend; do
  kind=deploy; [[ $d == redis || $d == postgres ]] && kind=statefulset
  kubectl -n "$NS" rollout status "$kind/$RELEASE-$d" --timeout=240s
done

say "pods"
kubectl -n "$NS" get pods -o wide

say "console: http://localhost:3080   simulator admin: http://localhost:3800"
