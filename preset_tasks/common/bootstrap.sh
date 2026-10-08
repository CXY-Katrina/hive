#!/bin/bash
# bootstrap.sh — Ascend A2 / A3 / A5 容器启动脚本
# 用法: bash bootstrap.sh <image_id> <container_name>
set -euo pipefail

IMAGES_ID=${1:?Usage: bash bootstrap.sh <image_id> <container_name>}
NAME=${2:?Usage: bash bootstrap.sh <image_id> <container_name>}

# 若容器已存在则报错退出，避免误覆盖。
existing_names=$(docker ps -a --format '{{.Names}}')
if grep -Fxq -- "$NAME" <<< "$existing_names"; then
    echo "error: container $NAME already exists. 若要重建请先 docker rm -f $NAME" >&2
    exit 1
fi

# 按实际设备发现芯片，不固定 A2 / A3 / A5 的设备数量。
shopt -s nullglob
devices=()
for device in /dev/davinci[0-9]*; do
    [[ "$device" =~ ^/dev/davinci[0-9]+$ && -c "$device" ]] || continue
    devices+=(--device="$device")
done
if (( ${#devices[@]} == 0 )); then
    echo "error: no NPU devices found under /dev/davinci[0-9]*" >&2
    exit 1
fi
for device in /dev/davinci_manager /dev/hisi_hdc /dev/devmm_svm; do
    if [[ -c "$device" ]]; then devices+=(--device="$device"); fi
done

mounts=()
for path in /usr/local/Ascend/driver /usr/local/Ascend/driver/lib64 \
    /usr/local/Ascend/driver/tools/hccn_tool /usr/local/Ascend/driver/version.info \
    /usr/local/dcmi /usr/local/bin/npu-smi /etc/ascend_install.info \
    /usr/local/sbin /home /data /tmp /mnt /root/.cache; do
    if [[ -e "$path" ]]; then mounts+=(-v "$path:$path"); fi
done
if [[ -d /root ]]; then mounts+=(-v /root:/host_root); fi
if [[ -f /usr/share/zoneinfo/Asia/Shanghai ]]; then
    mounts+=(-v /usr/share/zoneinfo/Asia/Shanghai:/etc/localtime)
fi

exec docker run --name "$NAME" -it -d --net=host --shm-size=128g \
    --privileged=true -w /home \
    "${devices[@]}" --entrypoint=bash "${mounts[@]}" "$IMAGES_ID"
