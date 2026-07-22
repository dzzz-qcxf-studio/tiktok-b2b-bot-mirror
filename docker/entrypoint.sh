#!/bin/bash
set -e

echo "=== TikTok B2B Bot ==="
echo "Initializing database..."
cd /app
python -c "
from tiktok_bot_core.storage.database import init_db
init_db()
print('Database initialized')
"

echo "Starting API server..."
uvicorn tiktok_bot_api.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

# Wait for API to be ready
for i in $(seq 1 10); do
    if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
        echo "API ready"
        break
    fi
    sleep 1
done

echo "Starting nginx..."
nginx -g "daemon off;"
