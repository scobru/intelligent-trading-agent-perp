#!/usr/bin/env bash
set -e

echo "========================================================"
echo " Starting Intelligent Trading Agent on CapRover / Docker"
echo "========================================================"

# 1. Start SynFutures Node.js Microservice in background on port 3100
echo "[1/4] Starting SynFutures Node.js microservice on port 3100..."
cd /app/synfutures-service
export SYNFUTURES_PORT=3100
export SYNFUTURES_SERVICE_URL="http://localhost:3100"
if [ -d "dist" ]; then
    node dist/index.js &
else
    npx ts-node src/index.ts &
fi
SERVICE_PID=$!
cd /app

# 2. Wait for synfutures-service /health endpoint
echo "[2/4] Waiting for SynFutures microservice on http://localhost:3100/health..."
MAX_TRIES=30
COUNT=0
until curl -s http://localhost:3100/health > /dev/null 2>&1 || [ $COUNT -eq $MAX_TRIES ]; do
    sleep 1
    COUNT=$((COUNT + 1))
done

if [ $COUNT -eq $MAX_TRIES ]; then
    echo "⚠️ Warning: synfutures-service did not respond in time, proceeding anyway..."
else
    echo "✅ SynFutures microservice is ready!"
fi

# 3. Start Web Dashboard on port 3000 (accessible via CapRover HTTP)
DASHBOARD_PORT="${PORT:-3000}"
echo "[3/4] Starting Web Dashboard on port ${DASHBOARD_PORT}..."
python dashboard.py &
DASHBOARD_PID=$!

# 3b. Start Telegram Bot Listener if configured
TELEGRAM_PID=""
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "📱 Starting Telegram Bot listener..."
    python telegram_bot.py &
    TELEGRAM_PID=$!
fi

# Cleanup on exit
cleanup() {
    echo "Stopping background services..."
    kill $SERVICE_PID 2>/dev/null || true
    kill $DASHBOARD_PID 2>/dev/null || true
    [ -n "$TELEGRAM_PID" ] && kill $TELEGRAM_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# 4. Execution loop for Python Trading Agent
INTERVAL="${INTERVAL_SECONDS:-900}"  # Default: 15 minutes (900 seconds)

echo "[4/4] Starting trading agent execution loop (Interval: ${INTERVAL}s)..."

if [ "${RUN_ONCE}" = "true" ]; then
    echo "Single run requested (RUN_ONCE=true)..."
    python main.py
    echo "Single execution finished. Keeping container alive for dashboard and microservice."
    wait $DASHBOARD_PID
else
    while true; do
        echo ""
        echo "⏰ [$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Running trading cycle..."
        python main.py || echo "⚠️ Warning: main.py exited with an error, retrying in next cycle."
        echo "💤 Sleeping for ${INTERVAL} seconds until next cycle..."
        sleep "${INTERVAL}" &
        wait $!
    done
fi
