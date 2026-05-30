#!/usr/bin/env bash
#
# Fluxito Production — Deploy / Update Script
#
# Runs ON the production Mac. Pulls the latest stable code from `main`,
# rebuilds the Docker stack from source, restarts, and health-checks.
#
# Usage:
#   ssh <prod-mac>
#   cd ~/fluxito
#   ./deploy/update.sh
#
# Optional: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to get a Telegram
# message if the deploy fails its health check.

set -euo pipefail

# Resolve repo root from this script's location (deploy/..), so the script
# works regardless of the caller's CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="deploy/docker-compose.prod.yml"
ENV_FILE="deploy/.env.prod"
HEALTH_URL="${HEALTH_URL:-http://localhost:8010/api/health}"

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] Fluxito production deploy"
echo "    Repo:    $REPO_ROOT"
echo "    Compose: $COMPOSE_FILE"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Copy deploy/.env.prod.example to it and fill in secrets."
    exit 1
fi

# 1. Pull latest stable code from main (fast-forward only — never auto-merge).
echo "==> Pulling origin/main (fast-forward only)..."
git checkout main
if ! git pull --ff-only origin main; then
    echo "ERROR: 'git pull --ff-only origin main' failed (local main diverged?)."
    echo "       Inspect with: git status && git log --oneline -5"
    exit 1
fi

# 2. Rebuild + restart the stack from source.
echo "==> Rebuilding and restarting stack..."
if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build --remove-orphans; then
    echo "ERROR: docker compose up --build failed"
    exit 1
fi

# 3. Health check (up to ~90s; a from-source build + boot can be slow).
echo "==> Waiting for health ($HEALTH_URL)..."
MAX_ATTEMPTS=45
ATTEMPT=1
HEALTH_OK=0
while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    echo "    Attempt $ATTEMPT/$MAX_ATTEMPTS — not healthy yet, waiting 2s..."
    sleep 2
    ATTEMPT=$((ATTEMPT + 1))
done

if [ $HEALTH_OK -eq 1 ]; then
    echo "==> SUCCESS: production healthy at $HEALTH_URL"
    echo "    $(date '+%Y-%m-%d %H:%M:%S') — deploy complete"
    exit 0
else
    echo "ERROR: not healthy after $((MAX_ATTEMPTS * 2))s"
    echo "       Logs: docker compose -f $COMPOSE_FILE logs --tail=200 app"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="🚨 Fluxito production deploy FAILED — not healthy after ${MAX_ATTEMPTS} attempts" \
            -d disable_web_page_preview=true >/dev/null || true
    fi
    exit 1
fi
