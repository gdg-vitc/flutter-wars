#!/usr/bin/env sh
# Verify a deployed Worker is healthy and ready.
#
# Usage: scripts/verify_deployment.sh https://flutter-wars-api-staging.<subdomain>.workers.dev
set -eu

BASE_URL="${1:?usage: verify_deployment.sh <base-url>}"

body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

for path in /health /ready; do
  code="$(curl -sS -o "$body_file" -w '%{http_code}' --max-time 20 "${BASE_URL}${path}")"
  printf '%s -> %s %s\n' "$path" "$code" "$(cat "$body_file")"
  if [ "$code" != "200" ]; then
    echo "FAILED: ${path} returned ${code}"
    exit 1
  fi
done

echo "deployment verification passed"
