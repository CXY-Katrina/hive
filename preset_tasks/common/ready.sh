#!/usr/bin/env bash
set -euo pipefail
host="${1:?server IP}"
port="${2:?server port}"
path="${3:-/health}"
wait_seconds="${4:-900}"
exec curl --fail --silent --show-error --retry 300 --retry-all-errors --retry-delay 2 \
  --retry-max-time "$wait_seconds" --connect-timeout 2 --max-time 5 "http://$host:$port$path"
