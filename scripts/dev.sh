#!/usr/bin/env bash
# Word Chef — dev loop: build frontend once, run the server with live code.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f frontend/out/index.html ]; then
  (cd frontend && npm install && npm run build)
fi

export PYTHONPATH="backend:game:prolepsis:prolepsis/vendor:${PYTHONPATH:-}"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
