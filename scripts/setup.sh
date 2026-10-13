#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo 'Python 3.11 or newer is required.'; exit 1; }
command -v npm >/dev/null || { echo 'Node.js 20.19 or newer and npm are required.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 20 || major === 20 && minor < 19 || major === 22 && minor < 12) { console.error("Node.js 20.19+, 22.12+, or newer is required."); process.exit(1); }'
python3 -m venv .venv
.venv/bin/python -m pip install --requirement requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m playwright install chromium
npm --prefix frontend ci
npm --prefix frontend run build
echo 'Setup complete. Run ./scripts/start.sh, then open http://127.0.0.1:8765'
