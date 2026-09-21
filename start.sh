#!/usr/bin/env bash
set -e

echo "========================================================"
echo " Starting Intelligent Trading Agent on CapRover / Docker"
echo "========================================================"

# 1. Start SynFutures Node.js Microservice in background
echo "[1/3] Starting SynFutures Node.js microservice..."
cd /app/synfutures-service
if [ -d "dist" ]; then
    node dist/index.js &
else
    npx ts-node src/index.ts &
fi
SERVICE_PID=$!
cd /app

# 2. Wait for synfutures-service /health endpoint
echo "[2/3] Waiting for SynFutures microservice on http://localhost:3100/health..."
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

# Cleanup on exit
cleanup() {
    echo "Stopping background services..."
    kill $SERVICE_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# 3. Execution loop for Python Trading Agent
INTERVAL="${INTERVAL_SECONDS:-900}"  # Default: 15 minutes (900 seconds)

echo "[3/3] Starting trading agent execution loop (Interval: ${INTERVAL}s)..."

if [ "${RUN_ONCE}" = "true" ]; then
    echo "Single run requested (RUN_ONCE=true)..."
    python main.py
    echo "Single execution finished. Keeping container alive for microservice / health checks."
    wait $SERVICE_PID
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
