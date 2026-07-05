#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python -m uvicorn personal_agent_gateway.app:create_app --factory --host "${AGENT_WEB_HOST:-127.0.0.1}" --port "${AGENT_WEB_PORT:-8787}"
