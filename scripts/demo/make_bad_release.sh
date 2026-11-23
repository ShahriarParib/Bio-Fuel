#!/usr/bin/env bash
# Build a deliberately BROKEN release to demonstrate automatic rollback:
#   scripts/demo/make_bad_release.sh v0.9.9-bad BASE   (BASE = a good version, e.g. v1.0.0-rc1)
#   scripts/deploy.sh v0.9.9-bad --local                → health gate fails → rolls back to the last good version
# The bad api image is the good one with a startup that exits (like a release with a missing setting or a bad import).
set -euo pipefail
BAD=${1:-v0.9.9-bad}; BASE=${2:?usage: make_bad_release.sh BAD_VERSION GOOD_BASE_VERSION}
for s in engine intelligence frontend; do docker tag "fsip/$s:$BASE" "fsip/$s:$BAD"; done
docker build -q -t "fsip/api:$BAD" - <<EOF
FROM fsip/api:$BASE
ENV APP_VERSION=$BAD
CMD ["python", "-c", "import sys; print('FATAL: required setting FSIP_SIGNING_KEY missing', file=sys.stderr); sys.exit(3)"]
EOF
echo "built broken release $BAD (api exits on boot); deploy it with: scripts/deploy.sh $BAD --local"
