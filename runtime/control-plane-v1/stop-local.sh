#!/usr/bin/env bash
set -euo pipefail

STATE="${CONTROL_PLANE_V1_STATE:-/home/aioperator/control-plane/state/control-plane-v1}"
PID_FILE="$STATE/gateway.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "not_running"
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "not_running"
  exit 0
fi

kill "$pid"
for _ in 1 2 3 4 5; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "stopped pid=$pid"
    exit 0
  fi
  sleep 1
done

echo "still_running pid=$pid"
exit 1
