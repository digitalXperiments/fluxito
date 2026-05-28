#!/usr/bin/env bash
#
# Fluxito Demo — Image Update Script
#
# This script is meant to run on your Mac (the demo host).
# It pulls the latest published image from GHCR and restarts the demo stack.
#
# Recommended usage:
#   - Manual: ./update-demo.sh
#   - Cron (every 15-30 min):
#       */20 * * * * cd ~/fluxito-demo && ./update-demo.sh >> ~/fluxito-demo/update.log 2>&1
#
# The script expects to be next to docker-compose.demo.yml and .env.demo.
#
# It will:
#   1. Pull the image (tagged via FLUXITO_IMAGE or default :demo)
#   2. Restart the stack with docker compose
#   3. Wait for /api/health to become healthy
#   4. Exit 0 on success, non-zero on failure (good for monitoring/cron alerts)
#
# Optional notification:
#   Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your shell or cron env
#   to get a message on failure (or always).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Allow overriding the image (useful for pinning to a specific sha during testing)
IMAGE="${FLUXITO_IMAGE:-ghcr.io/digitalxperiments/fluxito:demo}"

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] Starting Fluxito demo update"
echo "    Image: $IMAGE"
echo "    Compose file: docker-compose.demo.yml"

# 1. Pull latest image
echo "==> Pulling image..."
if ! docker compose -f docker-compose.demo.yml pull --quiet 2>&1; then
    echo "ERROR: docker compose pull failed"
    exit 1
fi

# 2. Restart stack (remove orphans in case services were renamed)
echo "==> Restarting stack..."
if ! docker compose -f docker-compose.demo.yml up -d --remove-orphans 2>&1; then
    echo "ERROR: docker compose up failed"
    exit 1
fi

# 3. Health check (up to ~60 seconds)
echo "==> Waiting for healthcheck (http://localhost:8010/api/health)..."
HEALTH_URL="http://localhost:8010/api/health"
MAX_ATTEMPTS=30
ATTEMPT=1
HEALTH_OK=0

while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    if curl -fsS --max-time 3 "$HEALTH_URL" > /dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    echo "    Attempt $ATTEMPT/$MAX_ATTEMPTS — not healthy yet, waiting 2s..."
    sleep 2
    ATTEMPT=$((ATTEMPT + 1))
done

if [ $HEALTH_OK -eq 1 ]; then
    echo "==> SUCCESS: Demo is healthy at $HEALTH_URL"
    echo "    $(date '+%Y-%m-%d %H:%M:%S') — update complete"

    # Optional: send success notification (uncomment if you want it)
    # if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    #     curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    #         -d chat_id="${TELEGRAM_CHAT_ID}" \
    #         -d text="✅ Fluxito demo updated and healthy" \
    #         -d disable_web_page_preview=true > /dev/null || true
    # fi

    exit 0
else
    echo "ERROR: Demo did not become healthy after $((MAX_ATTEMPTS * 2)) seconds"
    echo "       Check logs with: docker compose -f docker-compose.demo.yml logs --tail=100 app"

    # Send failure notification if Telegram credentials are available
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="🚨 Fluxito demo update FAILED — did not become healthy after ${MAX_ATTEMPTS} attempts" \
            -d disable_web_page_preview=true > /dev/null || true
    fi

    exit 1
fi
