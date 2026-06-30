#!/usr/bin/env bash
#
# Deploy the product-compare app on the droplet.
#
# Invoked by .github/workflows/deploy.yml over SSH *after* the checkout has been
# fast-forwarded to origin/main. Idempotent — safe to re-run by hand:
#   bash /home/ubuntu/scripts/Product-comparison-/scripts/deploy.sh
#
# Note: .env is gitignored, so the git reset in the workflow never touches it.
set -euo pipefail

export HOME=/root
UV=/root/.local/bin/uv
REPO=/home/ubuntu/scripts/Product-comparison-

echo "==> Node sidecar deps (npm install)"
cd "$REPO"
npm install --no-audit --no-fund

echo "==> Python deps (uv sync)"
cd "$REPO/api"
"$UV" sync

echo "==> DB migrations (alembic upgrade head)"
"$UV" run alembic upgrade head

echo "==> Restart services"
systemctl restart compare-sidecar compare-api

echo "==> Health check: Node sidecar (port 3100)"
# The sidecar hosts every scraper/extractor. If it's down, the API still answers
# /health but EVERY price extraction silently fails — so gate the deploy on it too,
# else prod looks healthy while returning no matches.
sidecar_ok=0
for i in $(seq 1 20); do
  if curl -fsS -m 5 http://127.0.0.1:3100/health >/dev/null 2>&1; then
    echo "OK: sidecar healthy after ~$((i * 3))s"
    sidecar_ok=1
    break
  fi
  sleep 3
done
if [ "$sidecar_ok" -ne 1 ]; then
  echo "FAIL: Node sidecar (3100) did not become healthy in ~60s"
  journalctl -u compare-sidecar -n 40 --no-pager 2>/dev/null || tail -n 40 /var/log/compare-sidecar.log || true
  exit 1
fi

echo "==> Health check: API (port 8000)"
for i in $(seq 1 20); do
  if curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "OK: API healthy after ~$((i * 3))s"
    exit 0
  fi
  sleep 3
done

echo "FAIL: API did not become healthy in ~60s"
journalctl -u compare-api -n 40 --no-pager 2>/dev/null || tail -n 40 /var/log/compare-api.log || true
exit 1
