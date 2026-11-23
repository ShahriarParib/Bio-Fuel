#!/usr/bin/env bash
# Deploy a release with a health gate and AUTOMATIC ROLLBACK (brief §13: Package → Deploy → Health Check → Running).
#
#   scripts/deploy.sh v1.0.0              images from GHCR (ghcr.io/<owner>/fsip-<svc>:v1.0.0), retagged locally
#   scripts/deploy.sh v1.0.0 --local      images already built on this machine (scripts/release_local.sh v1.0.0)
#
# Steps: resolve images → remember the running version → roll out the app services (engine, api, intelligence,
# frontend; data stores and the simulator are not touched) → health gate → record the version, or roll back to the
# previous good version and fail. History: deploy/versions.env (one line per attempt; machine state, not in git).
set -uo pipefail
VERSION=${1:?usage: deploy.sh VERSION [--local]}
SOURCE=ghcr; [ "${2:-}" = "--local" ] && SOURCE=local
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
OWNER=${GHCR_OWNER:-shahriarparib}
APPS=(engine api intelligence frontend)
HISTORY=deploy/versions.env
GATE_TIMEOUT=${GATE_TIMEOUT:-120}
mkdir -p deploy; touch "$HISTORY"
log() { printf '%s [deploy] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

resolve_images() {  # make fsip/<svc>:<v> exist locally
  local v=$1
  for s in "${APPS[@]}"; do
    if [ "$SOURCE" = ghcr ]; then
      docker pull -q "ghcr.io/$OWNER/fsip-$s:$v" >/dev/null || { log "cannot pull ghcr.io/$OWNER/fsip-$s:$v"; return 1; }
      docker tag "ghcr.io/$OWNER/fsip-$s:$v" "fsip/$s:$v"
    else
      docker image inspect "fsip/$s:$v" >/dev/null 2>&1 || { log "image fsip/$s:$v not found (build: scripts/release_local.sh $v)"; return 1; }
    fi
  done
}

roll_out() {  # recreate only the app services on version $1
  APP_VERSION=$1 docker compose up -d --no-build --no-deps "${APPS[@]}" 2>&1 | grep -E "Recreated|Started|Error" || true
}

# Health gate: every app container running (not restarting), engine ready, api answers with the NEW version,
# system status not Down, and the smoke test passes. All within GATE_TIMEOUT seconds.
health_gate() {
  local v=$1 t0=$SECONDS why=""
  while (( SECONDS - t0 < GATE_TIMEOUT )); do
    why=""
    for s in "${APPS[@]}"; do
      st=$(docker compose ps --format '{{.Service}} {{.State}}' "$s" 2>/dev/null | awk '{print $2}' | head -1)
      [ "$st" = "running" ] || why="$s is ${st:-missing}"
      # a crash loop will not heal by waiting: fail fast (containers were just recreated, so the count starts at 0)
      restarts=$(docker inspect -f '{{.RestartCount}}' $(docker compose ps -aq "$s" 2>/dev/null | head -1) 2>/dev/null || echo 0)
      if [ "${restarts:-0}" -ge 3 ]; then
        log "health gate FAILED for $v after $((SECONDS - t0)) s: $s crash-looping ($restarts restarts)"; return 1
      fi
    done
    if [ -z "$why" ]; then
      ver=$(curl -s --max-time 3 http://localhost:3000/api/ping | jq -r '.version // empty' 2>/dev/null)
      ready=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:8100/health/ready)
      overall=$(curl -s --max-time 5 http://localhost:3000/api/system/status | jq -r '.overall // empty' 2>/dev/null)
      if [ "$ver" != "$v" ]; then why="api reports version '${ver:-none}'"
      elif [ "$ready" != "200" ]; then why="engine not ready ($ready)"
      elif [ -z "$overall" ] || [ "$overall" = "Down" ]; then why="system status '${overall:-none}'"
      elif bash scripts/smoke_test.sh >/dev/null 2>&1; then
        log "health gate PASSED for $v after $((SECONDS - t0)) s (system $overall)"; return 0
      else why="smoke test failing"; fi
    fi
    sleep 3
  done
  log "health gate FAILED for $v after ${GATE_TIMEOUT} s: $why"
  return 1
}

previous=$(awk '$2 == "ok" {v=$1} END {print v}' "$HISTORY")
current=$(docker compose ps --format '{{.Image}}' api 2>/dev/null | head -1 | sed 's/.*://')
log "deploying $VERSION (source: $SOURCE); running now: ${current:-nothing}; last good: ${previous:-none}"

if ! resolve_images "$VERSION"; then
  log "ABORTED before touching anything: images for $VERSION are not available"; exit 2
fi

roll_out "$VERSION"
if health_gate "$VERSION"; then
  echo "$VERSION ok $(date -u +%FT%TZ)" >> "$HISTORY"
  log "DEPLOYED $VERSION"
  exit 0
fi

echo "$VERSION failed $(date -u +%FT%TZ)" >> "$HISTORY"
docker compose logs --tail=15 api engine 2>/dev/null | tail -20 | sed 's/^/    /'
target=${previous:-dev}
log "ROLLING BACK to $target"
SOURCE=local roll_out "$target"
if health_gate "$target"; then
  echo "$target ok $(date -u +%FT%TZ) rollback-from=$VERSION" >> "$HISTORY"
  log "ROLLED BACK: $target is running and healthy; $VERSION was rejected"
else
  log "ROLLBACK ALSO FAILED: manual intervention needed (see docs/runbook.md)"
fi
exit 1
