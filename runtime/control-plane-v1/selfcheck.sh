#!/usr/bin/env bash
set -euo pipefail
ROOT="${CONTROL_PLANE_V1_ROOT:-/home/aioperator/control-plane/runtime/control-plane-v1}"
SLACK_FILE="${SLACK_SIGNING_SECRET_FILE:-/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret}"
AIRTABLE_FILE="${AIRTABLE_PAT_FILE:-/home/aioperator/.config/ops-dashboard-credentials/AIRTABLE_PAT}"
PORT="${CONTROL_PLANE_V1_PORT:-8798}"

echo "check_time=$(date -Is)"
echo "root=$ROOT"
[ -f "$ROOT/server.mjs" ] || [ -f "$ROOT/gateway.mjs" ] && echo "gateway_file=present" || echo "gateway_file=missing"
[ -s "$AIRTABLE_FILE" ] && echo "airtable_pat=present" || echo "airtable_pat=missing"
[ -s "$SLACK_FILE" ] && echo "slack_signing_secret=present" || echo "slack_signing_secret=missing"
if command -v node >/dev/null 2>&1; then echo "node=present"; else echo "node=missing"; fi
if curl -sS --max-time 2 "http://127.0.0.1:$PORT/health" >/tmp/control-plane-v1-health.json 2>/tmp/control-plane-v1-health.err; then
  echo "health=$(cat /tmp/control-plane-v1-health.json)"
else
  echo "health=not_running"
fi
