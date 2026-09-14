#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
test -s "$HIVE_CLIENT_DEPS/environment-report.json"
cat "$HIVE_CLIENT_DEPS/environment-report.json"
