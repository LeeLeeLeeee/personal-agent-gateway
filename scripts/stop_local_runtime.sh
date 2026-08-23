#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
pag_root="$(cd "$script_dir/.." && pwd)"
lmg_root="$(cd "$pag_root/.." && pwd)/local-model-gateway"
state_path="$pag_root/data/local-runtime-macos-state"
lmg_bin="$lmg_root/data/bin/lmg"

port_listener_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true
}

process_matches() {
  local pid="$1"
  local expected_command="$2"
  local command

  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$command" && "$command" == *"$expected_command"* ]]
}

process_cwd_matches() {
  local pid="$1"
  local expected_cwd="$2"
  local cwd

  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null \
    | sed -n 's/^n//p' | head -n 1)"
  [[ "$cwd" == "$expected_cwd" ]]
}

resolve_managed_pid() {
  local recorded_pid="$1"
  local port="$2"
  local expected_command="$3"
  local service="$4"
  local expected_cwd="${5:-}"
  local listener_pid

  if [[ ! "$recorded_pid" =~ ^[0-9]+$ ]]; then
    echo "runtime_state_mismatch: $service" >&2
    return 1
  fi

  if kill -0 "$recorded_pid" 2>/dev/null; then
    if ! process_matches "$recorded_pid" "$expected_command"; then
      echo "runtime_state_mismatch: $service" >&2
      return 1
    fi
    printf '%s\n' "$recorded_pid"
    return 0
  fi

  listener_pid="$(port_listener_pid "$port")"
  if [[ -z "$listener_pid" ]]; then
    printf '%s\n' "$recorded_pid"
    return 0
  fi

  if ! process_matches "$listener_pid" "$expected_command"; then
    echo "runtime_state_mismatch: $service listener pid=$listener_pid" >&2
    return 1
  fi
  if [[ -n "$expected_cwd" ]] \
    && ! process_cwd_matches "$listener_pid" "$expected_cwd"; then
    echo "runtime_state_mismatch: $service cwd pid=$listener_pid" >&2
    return 1
  fi

  echo "runtime_state_recovered: $service pid=$listener_pid" >&2
  printf '%s\n' "$listener_pid"
}

wait_for_processes() {
  local timeout_seconds="$1"
  shift
  local deadline=$((SECONDS + timeout_seconds))
  local pid

  while (( SECONDS < deadline )); do
    local running=0
    for pid in "$@"; do
      if kill -0 "$pid" 2>/dev/null; then
        running=1
        break
      fi
    done

    if (( running == 0 )); then
      return 0
    fi
    sleep 0.2
  done

  return 1
}

force_stop() {
  local pid="$1"
  local expected_command="$2"
  local service="$3"

  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi

  if ! process_matches "$pid" "$expected_command"; then
    echo "runtime_state_mismatch: $service changed during stop" >&2
    exit 1
  fi

  echo "runtime_force_kill: $service pid=$pid" >&2
  kill -KILL "$pid"
}

if [[ ! -f "$state_path" ]]; then
  printf '{"status":"not_running"}\n'
  exit 0
fi

lmg_pid="$(resolve_managed_pid "$(sed -n '1p' "$state_path")" 8788 \
  "$lmg_bin" "lmg")"
pag_pid="$(resolve_managed_pid "$(sed -n '2p' "$state_path")" 8787 \
  "personal_agent_gateway.app:create_app" "pag" "$pag_root")"

kill "$pag_pid" "$lmg_pid" 2>/dev/null || true
wait_for_processes 10 "$pag_pid" "$lmg_pid" || true

force_stop "$pag_pid" "personal_agent_gateway.app:create_app" "pag"
force_stop "$lmg_pid" "$lmg_bin" "lmg"

if ! wait_for_processes 5 "$pag_pid" "$lmg_pid"; then
  echo "runtime_stop_failed: processes still running" >&2
  exit 1
fi

rm "$state_path"
printf '{"status":"stopped","lmg_pid":%s,"pag_pid":%s}\n' "$lmg_pid" "$pag_pid"
