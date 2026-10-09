#!/usr/bin/env bash
set -euo pipefail

check_file() {
  label="$1"
  path="$2"
  if [ -s "$path" ]; then
    perms="$(stat -c '%a' "$path" 2>/dev/null || stat -f '%Lp' "$path")"
    bytes="$(wc -c < "$path" | tr -d ' ')"
    printf '%s: installed perms=%s bytes=%s\n' "$label" "$perms" "$bytes"
  else
    printf '%s: missing\n' "$label"
  fi
}

check_file "slack-signing-secret" "/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret"
check_file "airtable-pat" "/home/aioperator/.config/ops-dashboard-credentials/AIRTABLE_PAT"
check_file "n8n-api-key" "/home/aioperator/.config/ops-dashboard-credentials/N8N_API_KEY"
