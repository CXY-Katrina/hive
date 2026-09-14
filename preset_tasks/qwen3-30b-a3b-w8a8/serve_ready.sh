#!/usr/bin/env bash
set -euo pipefail
exec curl --fail --silent --show-error --retry 300 --retry-all-errors --retry-delay 2 \
  --retry-max-time 900 --connect-timeout 2 --max-time 5 "http://${HIVE_HOST_IP:?}:${HIVE_PORT:?}/health"
