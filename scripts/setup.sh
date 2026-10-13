#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo 'Python 3.11 or newer is required.'; exit 1; }
command -v npm >/dev/null || { echo 'Node.js 20.19 or newer and npm are required.'; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m playwright install chromium
npm --prefix frontend ci
npm --prefix frontend run build
echo 'Setup complete. Run ./scripts/start.sh, then open http://127.0.0.1:8765'
