#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  echo 'Run ./scripts/setup.sh first.'
  exit 1
fi
echo 'Orbit is available at http://127.0.0.1:8765. Press Ctrl+C to stop.'
exec .venv/bin/python -m uvicorn agent.main:app --host 127.0.0.1 --port 8765 --no-access-log
