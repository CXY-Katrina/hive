#!/usr/bin/env bash
set -euo pipefail
dir=$1
if [[ ${2:-} = payload ]]; then
  # This whole payload stays under timeout, including log draining and any
  # background child processes. timeout owns our process group by default.
  raw=$(</proc/$$/stat)
  rest=${raw##*) }
  read -ra own_fields <<<"$rest"
  group=${own_fields[2]}
  # Keep the monitored wrapper alive during TERM grace so timeout -k can kill
  # stubborn descendants; a wrapper exiting first would disarm that deadline.
  trap ':' TERM
  set +e
  bash "$dir/job.sh" 2>&1 | {
    head -c 20971520 || { cat >/dev/null; exit 74; }
    cat >/dev/null
  } >"$dir/output.log"
  pipeline=("${PIPESTATUS[@]}")
  set -e
  code=${pipeline[0]}
  [[ ${pipeline[1]} = 0 ]] || code=74
  # Scan procfs with Bash builtins; do not count transient ps/awk children.
  while :; do
    busy=false
    for path in /proc/[0-9]*/stat; do
      member=${path#/proc/}; member=${member%/stat}
      [[ $member != "$$" && $member != "$group" ]] || continue
      if ! raw=$(<"$path"); then
        [[ ! -e $path ]] && continue
        exit 75
      fi
      rest=${raw##*) }
      read -ra fields <<<"$rest"
      [[ ${#fields[@]} -ge 20 ]] || exit 75
      if [[ ${fields[2]} = "$group" && ${fields[0]} != Z ]]; then busy=true; break; fi
    done
    [[ $busy = true ]] || break
    sleep 1
  done
  exit "$code"
fi
exec 9>"$dir/lock"
flock -x 9
[[ ! -f $dir/closed ]] || exit 0
raw=$(</proc/$$/stat)
rest=${raw##*) }
read -ra fields <<<"$rest"
printf '%s %s %s\n' "$$" "${fields[19]}" "$(cat /proc/sys/kernel/random/boot_id)" >"$dir/identity.tmp"
mv -- "$dir/identity.tmp" "$dir/identity"
flock -u 9
exec 9>&-
seconds=$(cat "$dir/timeout")
set +e
timeout --kill-after=10 "$seconds" bash "$dir/runner.sh" "$dir" payload
code=$?
set -e
printf 'EXITED %s\n' "$code" >"$dir/result.tmp"
mv -- "$dir/result.tmp" "$dir/result"
