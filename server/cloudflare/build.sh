#!/usr/bin/env bash
# Copy the server app into the Worker bundle (wrangler does not follow symlinks).
# Usage: ./build.sh && uv run pywrangler deploy     (or: pywrangler dev)
set -euo pipefail
cd "$(dirname "$0")"
rm -rf src/app
mkdir -p src
cp -r ../app src/app
find src/app -name __pycache__ -prune -exec rm -rf {} +
