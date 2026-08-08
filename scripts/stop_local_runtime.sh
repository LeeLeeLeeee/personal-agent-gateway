#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
pag_root="$(cd "$script_dir/.." && pwd)"
lmg_root="$(cd "$pag_root/.." && pwd)/local-model-gateway"
state_path="$pag_root/data/local-runtime-macos-state"
lmg_bin="$lmg_root/data/bin/lmg"

if [[ ! -f "$state_path" ]]; then
  printf '{"status":"not_running"}\n'
  exit 0
fi

lmg_pid="$(sed -n '1p' "$state_path")"
pag_pid="$(sed -n '2p' "$state_path")"

if [[ ! "$lmg_pid" =~ ^[0-9]+$ ]] || ! ps -p "$lmg_pid" -o command= 2>/dev/null | grep -Fq "$lmg_bin"; then
  echo "runtime_state_mismatch: lmg" >&2
  exit 1
fi

if [[ ! "$pag_pid" =~ ^[0-9]+$ ]] || ! ps -p "$pag_pid" -o command= 2>/dev/null | grep -Fq "personal_agent_gateway.app:create_app"; then
  echo "runtime_state_mismatch: pag" >&2
  exit 1
fi

kill "$pag_pid" "$lmg_pid"
rm "$state_path"
printf '{"status":"stopped","lmg_pid":%s,"pag_pid":%s}\n' "$lmg_pid" "$pag_pid"
