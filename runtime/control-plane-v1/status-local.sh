#!/usr/bin/env bash
set -euo pipefail

STATE="${CONTROL_PLANE_V1_STATE:-/home/aioperator/control-plane/state/control-plane-v1}"
PORT="${CONTROL_PLANE_V1_PORT:-8798}"
PID_FILE="$STATE/gateway.pid"

pid=""
running=false
if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    running=true
  fi
fi

health="not_running"
if curl -sS --max-time 2 "http://127.0.0.1:$PORT/health" >/tmp/control-plane-v1-health.json 2>/tmp/control-plane-v1-health.err; then
  health="$(cat /tmp/control-plane-v1-health.json)"
fi

cat <<JSON
{"gateway_running":$running,"gateway_pid":"$pid","health":$health}
JSON
