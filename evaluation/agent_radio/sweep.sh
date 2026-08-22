#!/usr/bin/env bash
# Parallel evaluation sweep: every fixture under tasks/, both negotiation arms.
#
# Slots make the parallelism safe: each job runs `runner.py --slot slotN`, so
# each concurrent run has its own database, its own run lock, and writes its
# artefact under data/eval/slots/slotN/runs -- outside the tracked tree, where
# a mid-sweep artefact would spoil every other run's isolation snapshot. When
# every job has finished, the artefacts are collected into the tracked runs/
# directory in one motion.
#
# Do not edit the repository while a sweep runs: every run snapshots
# `git status` before and after, and an unrelated edit lands between somebody's
# snapshots as an isolation failure no run caused.
#
# Jobs are statically partitioned round-robin across slots. Runs measure
# 150-460s each, so the imbalance this leaves is minutes, and it means a slot
# never needs to be handed between jobs mid-sweep.
#
# -j must not exceed what LMG will actually run concurrently
# (LMG_CODEX_CONCURRENT_RUNS, default 2): admission past the limit queues, and
# queue time is indistinguishable from run time in wall_ms.
#
# Lessons this script carries from the first sweep (docs/todo/…-next-steps.md):
#   - it writes its own PID to <log>/sweep.pid -- kill by PID, never by
#     matching the command line (the match finds your own shell);
#   - exit 0 means "ran", not "answered": every artefact's error field is
#     read, and a run that died with provider_protocol_error is retried once.

set -u

usage() {
  echo "usage: $0 [-j slots] [-w workers] [-t timeout_seconds] [-o log_dir]" >&2
  exit 1
}

SLOTS=2
WORKERS=2
TIMEOUT=900
LOG_DIR=""
while getopts "j:w:t:o:h" opt; do
  case "$opt" in
    j) SLOTS="$OPTARG" ;;
    w) WORKERS="$OPTARG" ;;
    t) TIMEOUT="$OPTARG" ;;
    o) LOG_DIR="$OPTARG" ;;
    *) usage ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT/evaluation:$ROOT/src"
[ -x "$PYTHON" ] || { echo "error: $PYTHON is not executable" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="${LOG_DIR:-$ROOT/data/eval/sweeps/$STAMP}"
mkdir -p "$LOG_DIR"
echo $$ > "$LOG_DIR/sweep.pid"
echo "logs: $LOG_DIR (pid $$)"

# The job list: fixture ids × arms, one "fixture arm" line each. Deprecated
# fixtures live in tasks/deprecated/ and are not globbed.
JOBS=()
for task in "$HERE"/tasks/*.json; do
  id="$(basename "$task" .json)"
  JOBS+=("$id legacy" "$id negotiation")
done
echo "${#JOBS[@]} runs across $SLOTS slots, --workers $WORKERS"

# One run, retried once if the provider dropped the connection mid-call.
# Prints one status line; writes full runner output to its own log.
run_one() {
  local fixture="$1" arm="$2" slot="$3"
  # A plain string, not an array: macOS ships bash 3.2, where expanding an
  # empty array under `set -u` is a fatal "unbound variable".
  local negotiate=""
  [ "$arm" = negotiation ] && negotiate="--negotiation"
  local log="$LOG_DIR/$fixture--$arm.log"
  local attempt
  for attempt in 1 2; do
    # shellcheck disable=SC2086 -- $negotiate is one word or nothing
    "$PYTHON" -m agent_radio.runner \
      --fixture "$fixture" --mode legacy --workers "$WORKERS" \
      --timeout-seconds "$TIMEOUT" --slot "$slot" \
      $negotiate >"$log" 2>&1
    local code=$?
    # The artefact path is the runner's last stdout line on success.
    local artefact=""
    [ $code -eq 0 ] && artefact="$(tail -n 1 "$log")"
    if [ $code -ne 0 ] || [ ! -f "$artefact" ]; then
      echo "FAIL  $fixture $arm (exit $code, attempt $attempt) -- see $log"
      return 1
    fi
    # exit 0 only says the runner ran. The artefact says whether it answered.
    local error
    error="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("error") or "")' "$artefact")"
    if [ -z "$error" ]; then
      echo "ok    $fixture $arm ($(basename "$artefact"))"
      return 0
    fi
    if [ $attempt -eq 1 ] && [[ "$error" == *provider_protocol_error* ]]; then
      echo "retry $fixture $arm (provider_protocol_error)"
      continue
    fi
    echo "ERROR $fixture $arm: $error -- see $log"
    return 1
  done
}

# Static round-robin partition: slot i takes jobs i, i+SLOTS, i+2*SLOTS, ...
run_slot() {
  local slot_index="$1"
  local i failed=0
  for ((i = slot_index; i < ${#JOBS[@]}; i += SLOTS)); do
    # shellcheck disable=SC2086 -- the line is "fixture arm" on purpose
    run_one ${JOBS[$i]} "slot$slot_index" || failed=1
  done
  return $failed
}

PIDS=()
for ((s = 0; s < SLOTS; s++)); do
  run_slot "$s" &
  PIDS+=($!)
done
STATUS=0
for pid in "${PIDS[@]}"; do
  wait "$pid" || STATUS=1
done

# Collect: slot artefacts into the tracked runs/ directory, never overwriting.
collected=0
for artefact in "$ROOT"/data/eval/slots/*/runs/*.json; do
  [ -f "$artefact" ] || continue
  dest="$HERE/runs/$(basename "$artefact")"
  if [ -e "$dest" ]; then
    echo "skip collect (exists): $(basename "$artefact")"
  else
    cp "$artefact" "$dest" && collected=$((collected + 1))
  fi
done
echo "collected $collected artefacts into evaluation/agent_radio/runs/"

rm -f "$LOG_DIR/sweep.pid"
exit $STATUS
