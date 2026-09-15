#!/usr/bin/env bash
# Load the image CANN runtime without hiding the installed editable source.
load_image_runtime() {
  local role="${1:-server}" cann_loaded=0 cann_env
  unset PYTHONHOME
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
