#!/bin/bash
# Local full-platform diagnostic loop for Vantav.
# Runs the Stripe billing + TikTok order webhook simulators against either a
# Docker Compose stack or a local Flask dev server.
#
# Usage:
#   ./scripts/local_test_sequence.sh
#
# With Docker (preferred):
#   docker compose -f docker-compose.local.yml up --build -d
#   ./scripts/local_test_sequence.sh
#
# Without Docker (falls back to Flask dev server):
#   ./scripts/local_test_sequence.sh

set -e

export STRIPE_WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-whsec_test_secret}"
export MERCHANT_ID="${MERCHANT_ID:-merchant_shawn_01}"
export SELECTED_TIER="${SELECTED_TIER:-Vantav Growth}"
export MERCHANT_EMAIL="${MERCHANT_EMAIL:-local@vantavcommerce.com}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_DB="$ROOT_DIR/.local_test.db"
export DATABASE_URL="sqlite:///$TEST_DB"
PORT="${PORT:-8000}"

# Remove any stale local test DB so the schema is created fresh.
rm -f "$TEST_DB"

# Use the Docker Compose frontend by default if it appears to be running.
if curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
    BASE_URL="http://localhost:$PORT"
    DOCKER_MODE=true
else
    DOCKER_MODE=false
fi

if [ "$DOCKER_MODE" = false ]; then
    echo "[TEST] No running container found on port $PORT; starting Flask dev server..."
    PORT="${PORT:-5000}"
    BASE_URL="http://localhost:$PORT"
    cd "$ROOT_DIR"
    FLASK_APP=app.py python -m flask run --host=127.0.0.1 --port="$PORT" &
    SERVER_PID=$!
    trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

    for i in {1..30}; do
        if curl -s "$BASE_URL/health" >/dev/null 2>&1; then
            echo "[TEST] Dev server is healthy."
            break
        fi
        sleep 1
    done
fi

echo "[TEST] Target: $BASE_URL"

echo "[TEST] Seeding local test merchant..."
BASE_URL="$BASE_URL" python "$SCRIPT_DIR/seed_local_merchant.py"

echo "[TEST] Running Stripe billing webhook simulation..."
BASE_URL="$BASE_URL" python "$SCRIPT_DIR/simulate_stripe_webhook.py"

echo "[TEST] Running TikTok Shop order webhook simulation..."
BASE_URL="$BASE_URL" python "$SCRIPT_DIR/test_tiktok_order_webhook.py"

echo "[TEST] Sequence complete."
