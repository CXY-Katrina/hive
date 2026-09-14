#!/usr/bin/env bash
# Restore the selected image runtime; no model or test-case configuration.
load_image_runtime() {
  local role="${1:-server}" cann_loaded=0 cann_env path source_root filtered=""
  local paths=()
  # Never import a requested checkout over the image's installed NPU binaries.
  source_root="$(cd -- "$HIVE_SOURCE_DIR" && pwd -P)"
  IFS=: read -r -a paths <<< "${PYTHONPATH:-}"
  for path in "${paths[@]}"; do
    test -n "$path" || continue
    path="$(cd -- "$path" 2>/dev/null && pwd -P)" || continue
    case "$path" in "$source_root"|"$source_root"/*) continue ;; esac
    filtered="${filtered:+$filtered:}$path"
  done
  unset PYTHONHOME
  export PYTHONPATH="$filtered"
  export PYTHONNOUSERSITE=1
  cd /var/tmp
  set +u
  for cann_env in "${TASK_CANN_ENV:-}" /usr/local/Ascend/cann/set_env.sh \
      /usr/local/Ascend/ascend-toolkit/set_env.sh /usr/local/Ascend/ascend-toolkit/latest/set_env.sh; do
    if test -n "$cann_env" && test -f "$cann_env"; then
      source "$cann_env" >/dev/null
      cann_loaded=1
      break
    fi
  done
  if test "$cann_loaded" != 1 && test "$role" = server; then echo "CANN environment script missing" >&2; exit 1; fi
  if test -f /usr/local/Ascend/nnal/atb/set_env.sh; then
    source /usr/local/Ascend/nnal/atb/set_env.sh >/dev/null
  fi
  set -u
  export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
}
