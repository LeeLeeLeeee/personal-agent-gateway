#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
pag_root="$(cd "$script_dir/.." && pwd)"
workspace_root="$(cd "$pag_root/.." && pwd)"
lmg_root="$workspace_root/local-model-gateway"
state_path="$pag_root/data/local-runtime-macos-state"
env_path="$pag_root/.env"
lmg_data="$lmg_root/data"
lmg_bin="$lmg_data/bin/lmg"
pag_data="$pag_root/data"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "runtime_dependency_missing: $1" >&2
    exit 1
  }
}

port_listener_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true
}

wait_http_success() {
  local url="$1"
  local authorization="${2:-}"
  local deadline=$((SECONDS + 30))
  local args=(--silent --show-error --fail --max-time 2 "$url")

  if [[ -n "$authorization" ]]; then
    args=(--header "Authorization: Bearer $authorization" "${args[@]}")
  fi

  while (( SECONDS < deadline )); do
    if curl "${args[@]}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done

  echo "health_check_failed: $url" >&2
  return 1
}

process_matches() {
  local pid="$1"
  local expected="$2"
  local command
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$command" && "$command" == *"$expected"* ]]
}

cleanup_started() {
  local pid
  for pid in "$@"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

if [[ ! -f "$env_path" ]]; then
  echo "runtime_config_missing: $env_path" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_path"
set +a

if [[ -z "${LMG_LOCAL_TOKEN:-}" ]]; then
  export LMG_LOCAL_TOKEN="${PAG_LOCAL_TOKEN:-}"
fi

if [[ -z "${LMG_LOCAL_TOKEN:-}" ]]; then
  echo "runtime_config_missing: LMG_LOCAL_TOKEN or PAG_LOCAL_TOKEN" >&2
  exit 1
fi

if [[ ! -d "$lmg_root" ]]; then
  echo "lmg_root_missing: $lmg_root" >&2
  exit 1
fi

require_command curl
require_command go
require_command lsof

if [[ -f "$state_path" ]]; then
  lmg_pid="$(sed -n '1p' "$state_path")"
  pag_pid="$(sed -n '2p' "$state_path")"
  if [[ "$lmg_pid" =~ ^[0-9]+$ && "$pag_pid" =~ ^[0-9]+$ ]] \
    && process_matches "$lmg_pid" "$lmg_bin" \
    && process_matches "$pag_pid" "personal_agent_gateway.app:create_app" \
    && [[ "$(port_listener_pid 8788)" == "$lmg_pid" ]] \
    && [[ "$(port_listener_pid 8787)" == "$pag_pid" ]] \
    && wait_http_success "http://127.0.0.1:8788/livez" \
    && wait_http_success "http://127.0.0.1:8788/readyz" "$LMG_LOCAL_TOKEN" \
    && wait_http_success "http://127.0.0.1:8787/health/live" \
    && wait_http_success "http://127.0.0.1:8787/health/ready"; then
    printf '{"status":"already_running","lmg_pid":%s,"pag_pid":%s}\n' "$lmg_pid" "$pag_pid"
    exit 0
  fi

  echo "runtime_state_mismatch: remove no processes automatically" >&2
  exit 1
fi

for port in 8787 8788; do
  listener_pid="$(port_listener_pid "$port")"
  if [[ -n "$listener_pid" ]]; then
    echo "port_conflict: port=$port pid=$listener_pid" >&2
    exit 1
  fi
done

python_bin="$pag_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "pag_python_missing: $python_bin" >&2
  exit 1
fi

mkdir -p "$(dirname "$lmg_bin")" "$pag_data"
(
  cd "$lmg_root"
  go build -o "$lmg_bin" ./cmd/lmg
)

nohup env \
  LMG_HOST=127.0.0.1 \
  LMG_PORT=8788 \
  LMG_DATA_DIR="$lmg_data" \
  LMG_ALLOWED_ROOTS="${LMG_ALLOWED_ROOTS:-$workspace_root}" \
  "$lmg_bin" >"$lmg_data/lmg-runtime.out.log" 2>"$lmg_data/lmg-runtime.err.log" < /dev/null &
lmg_pid=$!

if ! wait_http_success "http://127.0.0.1:8788/livez" \
  || ! wait_http_success "http://127.0.0.1:8788/readyz" "$LMG_LOCAL_TOKEN"; then
  cleanup_started "$lmg_pid"
  exit 1
fi

nohup env \
  PYTHONPATH="$pag_root/src${PYTHONPATH:+:$PYTHONPATH}" \
  LMG_BASE_URL=http://127.0.0.1:8788 \
  AGENT_WEB_HOST=127.0.0.1 \
  AGENT_WEB_PORT=8787 \
  "$python_bin" -m uvicorn personal_agent_gateway.app:create_app --factory \
  --host 127.0.0.1 --port 8787 >"$pag_data/pag-runtime.out.log" \
  2>"$pag_data/pag-runtime.err.log" < /dev/null &
pag_pid=$!

if ! wait_http_success "http://127.0.0.1:8787/health/live" \
  || ! wait_http_success "http://127.0.0.1:8787/health/ready"; then
  cleanup_started "$pag_pid" "$lmg_pid"
  exit 1
fi

printf '%s\n%s\n' "$lmg_pid" "$pag_pid" > "$state_path"
printf '{"status":"started","lmg_pid":%s,"pag_pid":%s}\n' "$lmg_pid" "$pag_pid"
