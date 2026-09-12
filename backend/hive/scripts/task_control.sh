#!/usr/bin/env bash
# Immutable per-attempt control plane. Called only with platform-generated paths.
set -euo pipefail
dir=$1
action=$2
mkdir -p -- "$dir"
chmod 700 "$dir"
exec 9>"$dir/lock"
flock -x 9
identity() {
  local raw rest
  [[ -r /proc/$1/stat ]] || return 1
  raw=$(<"/proc/$1/stat") || return 1
  rest=${raw##*) }
  set -- $rest
  printf '%s' "${20}"
}
session_members() {
  local path raw rest member candidates status
  local -a fields
  # Bound Bash work to this attempt's session; procps scans busy hosts in C.
  # Empty selection exits 1; every other failure stays unconfirmed.
  if candidates=$(ps -s "$1" -o pid= 2>&1); then :
  else
    status=$?
    [[ $status = 1 && -z $candidates ]] || return 1
  fi
  for member in $candidates; do
    [[ $member =~ ^[0-9]+$ ]] || return 1
    path=/proc/$member/stat
    if ! { IFS= read -r raw <"$path"; } 2>/dev/null; then
      [[ ! -e $path ]] && continue
      return 1
    fi
    rest=${raw##*) }
    read -ra fields <<<"$rest"
    [[ ${#fields[@]} -ge 20 ]] || return 1
    if [[ ${fields[3]} = "$1" && ${fields[0]} != Z ]]; then
      printf '%s %s\n' "$member" "${fields[19]}"
    fi
  done
}
boot=$(cat /proc/sys/kernel/random/boot_id)
case "$action" in
  launch)
    if [[ -f $dir/closed ]]; then printf 'CLOSED\n'; exit; fi
    if [[ -f $dir/intent ]]; then printf 'EXISTING\n'; exit; fi
    # Persist before spawning; an incomplete handoff must never launch twice.
    date -u +%s >"$dir/intent"
    nohup setsid bash "$dir/runner.sh" "$dir" </dev/null >"$dir/supervisor.log" 2>&1 9>&- &
    printf 'STARTING\n'
    ;;
  status)
    if [[ -f $dir/result ]]; then cat "$dir/result"; exit; fi
    if [[ -f $dir/identity ]]; then
      read -r pid start recorded_boot <"$dir/identity"
      if [[ $recorded_boot = "$boot" ]] && [[ $(identity "$pid" || true) = "$start" ]]; then
        printf 'RUNNING\n'
      else printf 'UNKNOWN\n'; fi
    elif [[ -f $dir/closed ]]; then printf 'CLOSED\n'
    elif [[ -f $dir/intent ]]; then printf 'UNKNOWN\n'
    else printf 'PREPARED\n'; fi
    ;;
  close)
    # Permanent tombstone; a delayed runner must take this same lock first.
    : >"$dir/closed"
    if [[ -f $dir/identity ]]; then
      read -r pid start recorded_boot <"$dir/identity"
      if [[ $recorded_boot = "$boot" ]] && [[ $(identity "$pid" || true) = "$start" ]]; then
        # The runner is a setsid session leader; enumerate only that session.
        declare -A targets=()
        members=$(session_members "$pid") || { printf 'UNKNOWN\n'; exit; }
        while read -r member member_start; do
          [[ -z $member ]] || targets[$member]=$member_start
        done <<<"$members"
        for member in "${!targets[@]}"; do
          [[ $(identity "$member" || true) != "${targets[$member]}" ]] || kill -TERM "$member" 2>/dev/null || true
        done
        sleep 2
        for member in "${!targets[@]}"; do
          [[ $(identity "$member" || true) != "${targets[$member]}" ]] || kill -KILL "$member" 2>/dev/null || true
        done
      fi
      # Also catches children surviving a disappeared session leader. Never
      # signal them without recorded identity; retain allocation for inspection.
      if [[ $recorded_boot = "$boot" ]]; then
        members=$(session_members "$pid") || { printf 'UNKNOWN\n'; exit; }
        [[ -z $members ]] || { printf 'UNKNOWN\n'; exit; }
      fi
    fi
    printf 'CLOSED\n'
    ;;
  *) exit 64 ;;
esac
