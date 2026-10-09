#!/usr/bin/env bash
set -euo pipefail

ROOT="${CONTROL_PLANE_V1_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
STATE="${CONTROL_PLANE_V1_STATE:-/home/aioperator/control-plane/state/control-plane-v1}"
NODE_BIN="${NODE_BIN:-node}"
PID_FILE="$STATE/gateway.pid"
LOG_DIR="$STATE/logs"

mkdir -p "$STATE/commands" "$LOG_DIR"
chmod 700 "$STATE" "$STATE/commands" "$LOG_DIR" 2>/dev/null || true

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "already_running pid=$old_pid"
    exit 0
  fi
fi

nohup "$NODE_BIN" "$ROOT/gateway.mjs" >> "$LOG_DIR/gateway.log" 2>> "$LOG_DIR/gateway.err" &
pid=$!
echo "$pid" > "$PID_FILE"
chmod 600 "$PID_FILE" 2>/dev/null || true

echo "started pid=$pid"
