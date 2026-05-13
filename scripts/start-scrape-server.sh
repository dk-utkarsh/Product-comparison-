#!/usr/bin/env bash
# Start the Node scrape sidecar that hosts every TS scraper behind
# localhost:3100. Run this in its own terminal alongside `uvicorn`.

set -euo pipefail

cd "$(dirname "$0")/.."
exec npx tsx api/bridges/scrape-server.ts
