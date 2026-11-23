# Shared helpers for the resilience drills (06 §1). Source it; do not run it.
# Each drill: inject a real failure -> measure seconds until the platform DETECTS it (from /api/system/status, i.e.
# what an operator sees) -> check the mitigation -> measure seconds until it RECOVERS on its own -> append one row.
set -u
API=${API:-http://localhost:3000/api}
SIM=${SIM:-http://localhost:8000}
PROXY=${PROXY:-http://localhost:8001}
RESULTS=${RESULTS:-/tmp/fsip-resilience.tsv}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT" || exit 1

now() { date +%s.%N; }
since() { python3 -c "print(f'{$(now) - $1:.1f}')"; }
status() { curl -s --max-time 3 "$API/system/status"; }
st() { status | jq -r "$1" 2>/dev/null; }
state() { curl -s --max-time 3 "$API/state" | jq -r "$1" 2>/dev/null; }

# wait_until '<jq boolean expr on /api/system/status>' <timeout_s>  -> prints elapsed seconds, or "timeout"
wait_until() {
  local expr=$1 timeout=$2 t0
  t0=$(now)
  while :; do
    if [ "$(st "$expr")" = "true" ]; then since "$t0"; return 0; fi
    if [ "$(python3 -c "print(int($(now) - $t0 >= $timeout))")" = "1" ]; then echo "timeout"; return 1; fi
    sleep 0.5
  done
}

# fault <type> <seconds> [parameters-json]
fault() {
  local params=${3:-"{}"}
  curl -s -X POST "$SIM/admin/faults" -H 'content-type: application/json' \
    -d "{\"type\":\"$1\",\"duration_seconds\":$2,\"parameters\":$params}" >/dev/null
}
clear_faults() { curl -s -X POST "$SIM/admin/faults/clear" >/dev/null; }
sim_running() { curl -s -X POST "$SIM/admin/run" >/dev/null; }
allocation_failures() { state '.metrics.allocation_failures'; }
healthy='(.engine_mode == "NORMAL") and (.overall == "Healthy")'

# record <id> <failure> <detected_s> <mitigation> <recovered_s> <PASS|FAIL> [note]
record() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "${7:-}" >> "$RESULTS"
  printf '%-4s %-44s detect=%-7s recover=%-7s %s  %s\n' "$1" "$2" "$3" "$5" "$6" "$4"
}

# verdict <detected> <recovered> [extra_ok]
verdict() {
  if [ "$1" != "timeout" ] && [ "$2" != "timeout" ] && [ "${3:-1}" = "1" ]; then echo PASS; else echo FAIL; fi
}

# every drill starts from a healthy, running system
precondition() {
  clear_faults
  sim_running
  if ! wait_until "$healthy" 120 >/dev/null; then
    echo "precondition failed: system not healthy: $(st '.mode_reasons|join("; ")')"
    return 1
  fi
}

# sleep until a fault injected at <t0> for <dur> seconds has expired (recovery is measured from that moment)
wait_fault_end() {
  local left
  left=$(python3 -c "print(max(0.0, $1 + $2 - $(now)))")
  sleep "$left"
}
