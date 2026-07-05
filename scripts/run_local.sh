#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
requested_host="${AGENT_WEB_HOST:-}"
requested_port="${AGENT_WEB_PORT:-}"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
if [ -n "$requested_host" ]; then
  AGENT_WEB_HOST="$requested_host"
fi
if [ -n "$requested_port" ]; then
  AGENT_WEB_PORT="$requested_port"
fi

python_bin="python"
if [ -x .venv/bin/python ]; then
  python_bin=".venv/bin/python"
fi

PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src" "$python_bin" -m uvicorn personal_agent_gateway.app:create_app --factory --host "${AGENT_WEB_HOST:-127.0.0.1}" --port "${AGENT_WEB_PORT:-8787}"
