#!/usr/bin/env bash
# Load test → docs/evidence/loadtest-report.md   (needs the core stack + `make obs-up` for the engine-isolation check)
#   bash loadtest/run_loadtest.sh            ~8 min
# 1. read load at 25..400 VUs against api x1 and api x3 (through nginx, like the browser) → find the knee (p95 > 200 ms)
# 2. spike 10 → 300 VUs with api x3
# 3. engine isolation: fsip_ticks_behind during every run (the api serves cached state; the engine must not notice)
# 4. rate limit with the NORMAL config: one client gets 429 + Retry-After, never 5xx
set -uo pipefail
cd "$(dirname "$0")/.."
# a function, not a string: the repo path may contain spaces
k6() { docker run --rm --user "$(id -u):$(id -g)" --network host -v "$PWD/loadtest/k6:/scripts"          -v "$PWD/loadtest/out:/out" grafana/k6:0.54.0 "$@"; }
LEVELS=${LEVELS:-"25 50 100 200 400"}
DUR=${DUR:-20s}
OUT=loadtest/out; rm -rf "$OUT"; mkdir -p "$OUT"
LT="docker compose -f docker-compose.yml -f loadtest/compose.loadtest.yml"
prom() { curl -s localhost:9090/api/v1/query --data-urlencode "query=$1" | jq -r '.data.result[0].value[1] // "n/a"'; }

curl -s -X POST localhost:8000/admin/run >/dev/null   # the simulator keeps ticking: the engine works during the test
for R in 1 3; do
  echo "== api x$R"
  $LT up -d --no-deps --scale api=$R api >/dev/null 2>&1
  sleep 15
  for V in $LEVELS; do
    k6 run --quiet -e VUS=$V -e DURATION=$DUR --summary-export /out/read-x$R-$V.json /scripts/dashboard_read.js >/dev/null 2>&1
    tb=$(prom "max_over_time(fsip_ticks_behind[30s])")
    jq -c --arg tb "$tb" '{p95: .metrics.http_req_duration["p(95)"], rps: .metrics.http_reqs.rate, failed: .metrics.http_req_failed.value, ticks_behind_max: $tb}' \
      "$OUT/read-x$R-$V.json" | sed "s/^/x$R $V VUs: /"
    echo "$tb" > "$OUT/tb-x$R-$V.txt"
  done
done
echo "== spike 10 -> 300 VUs (api x3)"
k6 run --quiet -e SPIKE=300 --summary-export /out/spike.json /scripts/spike.js >/dev/null 2>&1
prom "max_over_time(fsip_ticks_behind[70s])" > "$OUT/tb-spike.txt"
jq -c '{p95: .metrics.http_req_duration["p(95)"], rps: .metrics.http_reqs.rate, failed: .metrics.http_req_failed.value}' "$OUT/spike.json"

echo "== restore the normal api (x1, normal rate limit) and prove the limit"
docker compose up -d --no-deps --scale api=1 api >/dev/null 2>&1
sleep 15
k6 run --quiet --summary-export /out/ratelimit.json /scripts/ratelimit.js >/dev/null 2>&1
curl -s -D - -o /dev/null localhost:3000/api/state | grep -i "retry-after\|^HTTP" > "$OUT/ratelimit-headers.txt"
for i in $(seq 1 700); do curl -s -o /dev/null -w '%{http_code}\n' localhost:3000/api/state; done | sort | uniq -c > "$OUT/ratelimit-codes.txt"
curl -s -D - -o /dev/null localhost:3000/api/state | grep -i "retry-after\|^HTTP" >> "$OUT/ratelimit-headers.txt"
cat "$OUT/ratelimit-codes.txt"
.venv/bin/python loadtest/report.py
