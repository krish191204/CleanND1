#!/usr/bin/env bash
# CleanND end-to-end demo script.
# Starts the backend, runs an ingest, labels a few reviews, retrains, and prints metrics.
#
# Usage: bash scripts/demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

# --- start backend (kill any existing one) ---
if lsof -ti:8000 >/dev/null 2>&1; then
    echo ">> killing existing server on :8000"
    lsof -ti:8000 | xargs -r kill -9
    sleep 1
fi

mkdir -p data
rm -f data/cleannd.db ml/artifacts/bot_classifier.joblib 2>/dev/null || true

echo ">> starting backend"
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning > /tmp/cleannd.log 2>&1 &
sleep 3

echo ">> health"
curl -s http://localhost:8000/api/health | python3 -m json.tool

echo ">> ingest 50 mock tweets"
curl -s -X POST "http://localhost:8000/api/ingest/mock?n=50&seed=42" | python3 -m json.tool

echo ">> feed (top 3)"
curl -s "http://localhost:8000/api/feed?limit=3" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for c in d['items'][:3]:
    print(f\"  [{c['credibility_level']:11s}] @{c['handle']:18s} {c['headline'][:80]}\")
"

echo ">> review queue (top 5)"
curl -s "http://localhost:8000/api/review/queue?limit=5" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'  stats: {d[\"stats\"]}')
for it in d['items'][:5]:
    text = it.get('snapshot', {}).get('clean', {}).get('clean_text', '')[:60]
    print(f'  bot={it[\"model_bot_score\"]:.2f} cred={it[\"model_credibility\"]:.2f} u={it[\"uncertainty_margin\"]:.2f}  {text}')
"

echo ">> label first 3 reviews"
for i in 1 2 3; do
    RID=$(curl -s "http://localhost:8000/api/review/queue?limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin)['items']; print(items[0]['id'] if items else '')")
    [ -z "$RID" ] && break
    if [ "$i" = "2" ]; then
        LABEL='rejected'
    else
        LABEL='approved'
    fi
    curl -s -X POST "http://localhost:8000/api/review/$RID/label" \
        -H 'Content-Type: application/json' \
        -d "{\"label\":\"$LABEL\",\"category\":\"tech\",\"labeler_id\":\"demo\"}" | python3 -m json.tool
done

echo ">> retrain"
curl -s -X POST "http://localhost:8000/api/ml/retrain" | python3 -m json.tool

sleep 4

echo ">> ml metrics"
curl -s "http://localhost:8000/api/ml/metrics" | python3 -m json.tool

echo ">> dashboard URL: http://localhost:8000/"
echo ">> Swagger docs:  http://localhost:8000/docs"
echo ">> server logs:   tail -f /tmp/cleannd.log"
