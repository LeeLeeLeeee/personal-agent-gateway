#!/usr/bin/env bash
set -euo pipefail

: "${AGENT_WEB_PORT:=8787}"

cloudflared tunnel --url "http://127.0.0.1:${AGENT_WEB_PORT}"
