#!/usr/bin/env bash
# Word Chef — full test battery.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH="backend:game:prolepsis:prolepsis/vendor:${PYTHONPATH:-}"

echo "══ 1/3 · unit / game-rule / prolepsis / api / multiplayer / anti-cheat / persistence ══"
python3 -m pytest tests/ -q

echo ""
echo "══ 2/3 · Prolepsis production acceptance ══"
python3 scripts/acceptance_prolepsis.py

echo ""
echo "══ 3/3 · frontend build verification ══"
test -f frontend/out/index.html || (cd frontend && npm run build)

echo ""
echo "ALL TEST BATTERIES GREEN"
