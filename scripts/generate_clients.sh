#!/usr/bin/env bash
# Generate the Python, TypeScript and Swift clients from the frozen OpenAPI v1.
# Requires Node (npx fetches @openapitools/openapi-generator-cli). Output is
# generated code: never hand-edit it; regenerate after any openapi/ change.
set -euo pipefail
cd "$(dirname "$0")/.."
SPEC=openapi/openapi-v1.json
GEN="npx --yes @openapitools/openapi-generator-cli@2.13.4"
$GEN generate -i "$SPEC" -g python -o clients/python --additional-properties=packageName=apgi_client
$GEN generate -i "$SPEC" -g typescript-fetch -o clients/typescript
$GEN generate -i "$SPEC" -g swift5 -o clients/swift
