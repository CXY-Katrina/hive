#!/usr/bin/env bash
set -euo pipefail
image="${1:?image}"
container="${2:?container name}"
# Adapted from start-docker-A3.sh supplied by the user; no host script dependency.
# Hive has already resolved/pulled this image. Docker rejects existing names.
docker image inspect -- "$image" >/dev/null
devices=()
for path in /dev/davinci[0-9]* /dev/davinci_manager /dev/hisi_hdc /dev/devmm_svm; do
  if test -e "$path"; then devices+=(--device "$path"); fi
done
mounts=()
for path in /usr/local/Ascend/driver /usr/local/dcmi /usr/local/bin/npu-smi \
    /etc/ascend_install.info /usr/local/sbin /home /data /tmp /mnt /root/.cache; do
  if test -e "$path"; then mounts+=(--volume "$path:$path"); fi
done
if test -d /root; then mounts+=(--volume /root:/host_root); fi
if test -f /usr/share/zoneinfo/Asia/Shanghai; then
  mounts+=(--volume /usr/share/zoneinfo/Asia/Shanghai:/etc/localtime:ro)
fi
# Whole-node A3 preset; the supplied startup requires privileged mode and 128 GiB shm.
exec docker run --detach --name "$container" --network host --shm-size 128g \
  --privileged --user 0 --workdir /home "${devices[@]}" "${mounts[@]}" \
  --entrypoint /bin/bash "$image" -lc 'exec sleep infinity'
