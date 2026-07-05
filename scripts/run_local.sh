#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

python -m uvicorn personal_agent_gateway.app:create_app --factory --host "${AGENT_WEB_HOST:-127.0.0.1}" --port "${AGENT_WEB_PORT:-8787}"
