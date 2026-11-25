#!/usr/bin/env bash
# The test a judge runs without knowing it: a fresh clone in an empty directory, nothing from our working copy.
#
#   bash scripts/clean_clone_test.sh            # clone from GitHub, build, verify, leave it running
#   KEEP=0 bash scripts/clean_clone_test.sh     # ... and tear it down afterwards
#
# It stops the development stack first: both use the `fsip` compose project name and the same ports, which is
# itself part of the test - whatever is cached in our working copy must not be what makes the clone work.
set -uo pipefail

CLEAN=${CLEAN_DIR:-/root/fsip-clean}
REPO=${REPO_URL:-https://github.com/ShahriarParib/Bio-Fuel.git}
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT="$HERE/docs/evidence/clean-clone.md"

fail() { echo; echo "CLEAN-CLONE FAILED: $*"; exit 1; }
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "stopping the development stack (same project name, same ports)"
(cd "$HERE" && docker compose --profile obs down 2>&1 | tail -3)

say "cloning from GitHub into an empty directory"
rm -rf "$CLEAN"
git clone --quiet "$REPO" "$CLEAN" || fail "git clone"
cd "$CLEAN"
COMMIT=$(git rev-parse --short HEAD)
FILES=$(git ls-files | wc -l)
echo "cloned $COMMIT on $(git rev-parse --abbrev-ref HEAD), $FILES files"

say "the only setup step the README gives"
[ -f .env.example ] || fail ".env.example is missing from the repo"
cp .env.example .env

say "checking nothing essential was left out of git"
for f in docker-compose.yml Makefile README.md frontend/package.json frontend/nginx.conf \
         services/engine/Dockerfile services/api/Dockerfile services/intelligence/Dockerfile \
         frontend/Dockerfile scripts/smoke_test.sh libs/fsip_common/pyproject.toml; do
  [ -f "$f" ] || fail "missing from the clone: $f"
done
echo "all expected files present"

say "docker compose up -d --build   (this is the whole install)"
START=$(date +%s)
docker compose up -d --build 2>&1 | tail -12 || fail "docker compose up"

say "waiting for the console"
READY=""
for i in $(seq 1 60); do
  if curl -fsS --max-time 5 http://localhost:3000/api/system/status >/dev/null 2>&1; then READY=$((i * 5)); break; fi
  sleep 5
done
[ -n "$READY" ] || { docker compose ps; docker compose logs --tail=40; fail "console never answered on :3000"; }
BUILD=$(( $(date +%s) - START ))
echo "console answered after ~${READY}s (${BUILD}s including the build)"

say "smoke test"
bash scripts/smoke_test.sh || fail "smoke test"

say "what a judge sees first"
python3 - <<'PY' > /tmp/clean_clone_first_look.txt
import json, urllib.request
s = json.load(urllib.request.urlopen("http://localhost:3000/api/system/status", timeout=10))
print(f"overall: {s['overall']} | engine mode: {s['engine_mode']} | tick: {s['data_tick']}")
for name, c in s["components"].items():
    print(f"  {name:<18} {c['status']}")
st = json.load(urllib.request.urlopen("http://localhost:3000/api/state", timeout=10))
print(f"stations: {len(st['stations'])} | depots: {len(st['depots'])} | routes: {len(st['routes'])}")
a = json.load(urllib.request.urlopen("http://localhost:3000/api/assistant", timeout=10))
print(f"assistant with no API key: {a['state']} - {a['detail']}")
PY
cat /tmp/clean_clone_first_look.txt

say "the pages the console serves"
PAGES=""
for p in / /overview /risk /decisions /alerts /health /control; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:3000$p")
  echo "  $code  $p"
  PAGES="$PAGES| \`$p\` | $code |
"
done

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<MD
# The clean-clone test

Generated $(date -u '+%Y-%m-%d %H:%M UTC') by \`scripts/clean_clone_test.sh\`.

Everything else in \`docs/evidence/\` was measured in a working copy that had an existing \`.env\`, cached images
and populated volumes. None of that is true on the machine a judge will use, so this is the one check that
matters most: **clone the repository into an empty directory and run the two commands the README gives.**

The development stack is stopped first, on purpose. It uses the same compose project name and the same ports, so
if the clone only worked because something of ours was already running, this test would say so.

| | |
|---|---|
| Source | \`$REPO\` |
| Commit | \`$COMMIT\` |
| Files in the clone | $FILES |
| Setup | \`cp .env.example .env\` |
| Install | \`docker compose up -d --build\` |
| Console answered after | **~${READY}s** (${BUILD}s including the image build) |

## Smoke test

\`\`\`
$(bash scripts/smoke_test.sh 2>&1 | tail -12)
\`\`\`

## First look

\`\`\`
$(cat /tmp/clean_clone_first_look.txt)
\`\`\`

Note the last line. With no \`GEMINI_API_KEY\` configured - which is what \`.env.example\` ships - the plain-language
assistant reports itself disabled and every explanation endpoint serves the platform's own written summaries. The
console does not show an error, and nothing else in the platform changes.

## Pages

| Path | Status |
|---|---|
$PAGES
## What this does not prove

The simulator starts **paused** (\`SIMULATOR_START_MODE=paused\` in \`.env.example\`), so tick 0 is the correct
starting state: a judge presses Run on the Control page, or \`curl -X POST localhost:8000/admin/run\`. The numbers
in the other evidence reports come from a running world and are reproduced with the scripts named in each.
MD

say "wrote $OUT"
if [ "${KEEP:-1}" = "0" ]; then
  say "tearing the clone down"
  docker compose down
  rm -rf "$CLEAN"
else
  echo "the clone is still running at $CLEAN (docker compose down there to stop it)"
fi
say "CLEAN CLONE OK"
