#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
test -s "$HIVE_SERVER_DEPS/environment-report.json"
cat "$HIVE_SERVER_DEPS/environment-report.json"
