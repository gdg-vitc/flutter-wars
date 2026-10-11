#!/usr/bin/env sh
# Build and deploy the Worker (server/cloudflare) to production.
#
# Requires Cloudflare auth (interactive `wrangler login`, or CLOUDFLARE_API_TOKEN
# + CLOUDFLARE_ACCOUNT_ID in the environment). This script only deploys the Worker;
# it does NOT run migrations: run scripts/migrate.sh first (docs/deployment.md).
#
# Usage: scripts/deploy.sh
set -eu

cd "$(dirname "$0")/../cloudflare"
./build.sh
exec uv run pywrangler deploy
