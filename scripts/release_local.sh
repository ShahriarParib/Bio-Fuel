#!/usr/bin/env bash
# Build the app images on this machine as release VERSION (what CI does before pushing to GHCR).
#   scripts/release_local.sh v1.0.0-rc1   → fsip/{engine,api,intelligence,frontend}:v1.0.0-rc1
set -euo pipefail
VERSION=${1:?usage: release_local.sh VERSION}
cd "$(dirname "$0")/.."
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
APP_VERSION=$VERSION GIT_SHA=$SHA docker compose build --build-arg APP_VERSION="$VERSION" --build-arg GIT_SHA="$SHA" \
  engine api intelligence frontend
echo "built fsip/{engine,api,intelligence,frontend}:$VERSION ($SHA)"
