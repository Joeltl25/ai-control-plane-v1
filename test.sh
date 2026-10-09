#!/usr/bin/env bash
set -euo pipefail
if grep -qx 'Banana' file.txt; then
  echo 'PASS: file.txt contains Banana'
else
  echo 'FAIL: file.txt does not contain Banana'
  exit 1
fi
