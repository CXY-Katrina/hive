#!/usr/bin/env bash
set -euo pipefail
TASK_PORT="${TASK_PORT:-18123}"
exec curl --fail --silent --show-error --retry 300 --retry-all-errors --retry-delay 2 \
  --retry-max-time 900 --connect-timeout 2 --max-time 5 "http://${HIVE_NODE0_IP:?}:$TASK_PORT/health"
