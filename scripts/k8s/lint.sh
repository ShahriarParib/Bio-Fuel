#!/usr/bin/env bash
# Chart validation that does not need a cluster: helm lint, a full render, and a server-side-free
# schema check of every rendered object. Run this before any install.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
CHART=deploy/helm/fsip
OUT="${TMPDIR:-/tmp}/fsip-rendered.yaml"

echo "== helm lint"
helm lint "$CHART"

echo "== helm template (default values)"
helm template fsip "$CHART" --namespace fsip > "$OUT"
grep -c '^kind:' "$OUT" | xargs echo "rendered objects:"

echo "== helm template (registry values: a real cluster pulling from GHCR)"
helm template fsip "$CHART" --namespace fsip \
  --set image.registry=ghcr.io/shahriarparib --set image.pullPolicy=IfNotPresent \
  --set api.autoscaling.enabled=false >/dev/null

echo "== python yaml parse of every rendered document"
python3 - "$OUT" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")) if d]
for d in docs:
    assert d.get("kind") and d.get("metadata", {}).get("name"), d
    # every one of our containers must declare resources, or one pod can starve the node
    spec = d.get("spec", {}).get("template", {}).get("spec", {})
    for c in spec.get("containers", []):
        assert c.get("resources", {}).get("limits"), f'{d["metadata"]["name"]}/{c["name"]} has no limits'
engines = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-engine")]
assert len(engines) == 1 and engines[0]["spec"]["replicas"] == 1, "the engine must be a single replica"
assert engines[0]["spec"]["strategy"]["type"] == "Recreate", "the engine must never roll two pods at once"
print(f"ok: {len(docs)} documents, invariants hold")
PY
