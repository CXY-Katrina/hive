#!/usr/bin/env bash
set -euo pipefail
: "${1:?image}" "${2:?container name}"
image="$1"
container="$2"
# Hive resolves/pulls and pins this image ID before this creation step.
# No host-side user script is required.
docker image inspect -- "$image" >/dev/null
# Keep model/dataset/package mappings under /mnt available at the same paths.
# Mount only host driver files; CANN/Python/vLLM come from the selected image.
mounts=(--volume /mnt:/mnt)
for path in /usr/local/Ascend/driver /usr/local/dcmi /usr/local/bin/npu-smi /etc/ascend_install.info /etc/hccn.conf; do
  if test -e "$path"; then mounts+=(--volume "$path:$path:ro"); fi
done
devices=()
for path in /dev/davinci[0-9]* /dev/davinci_manager /dev/devmm_svm /dev/hisi_hdc; do
  if test -e "$path"; then devices+=(--device "$path"); fi
done
# Host networking supports server/client communication by the allocated node IP.
# This whole-node preset exposes the node's NPU devices; partial-card presets
# must narrow this device list. Never delete or take over an existing container.
exec docker run --detach --name "$container" --network host --ipc host \
  --user 0 "${devices[@]}" "${mounts[@]}" \
  --entrypoint /bin/bash "$image" -lc 'exec sleep infinity'
