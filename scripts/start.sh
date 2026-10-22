#!/usr/bin/env bash
# One command to start everything: preflight, backend (port 8000) and frontend (port 5173). Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .venv/bin/activate ] && source .venv/bin/activate
mkdir -p data/logs
export OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_CONTEXT_LENGTH=8192

# start Ollama if it is not already answering (only when the default route is Ollama)
if ! curl -s --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && command -v ollama >/dev/null; then
  echo "starting ollama serve (log: data/logs/ollama.log)"
  nohup ollama serve > data/logs/ollama.log 2>&1 &
  sleep 3
fi

python scripts/preflight.py || { echo; echo "Not starting: fix the problems above."; exit 1; }

pids=()
cleanup() { echo; echo "stopping..."; kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup INT TERM EXIT
uvicorn backend.app.main:app --port 8000 > data/logs/backend.log 2>&1 & pids+=($!)
( cd frontend && npm run dev -- --port 5173 --strictPort > ../data/logs/frontend.log 2>&1 ) & pids+=($!)
for i in $(seq 1 30); do curl -s --max-time 1 localhost:8000/api/v1/health >/dev/null 2>&1 && break; sleep 1; done
echo
echo "  App:      http://localhost:5173"
echo "  API:      http://localhost:8000/api/v1/health"
echo "  Logs:     data/logs/ (contain no transcript text)"
echo "  Stop:     Ctrl+C"
wait
