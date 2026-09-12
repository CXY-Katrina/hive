# 节点互联与实际 Docker 容器识别核验

核验日期：2026-09-12。仅阅读源码和文档，未安装 toolkit、连接实际节点或执行远程脚本。

指定仓库固定版本：`xuchi-0808/blue-server-toolkit@67b5b56594f418a33210de5c47f1fb7f6adb4563`。它是用户指定的实践指导；厂商通用兼容性仍需实际节点验证。

## 1. NPU 互联检查

仓库给出的命令如下；`id` 是对应工具实际使用的设备编号，不能直接假设等于前端卡号。[toolkit 互联指导](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/docs/npu-roce-connectivity.md)

| 代际 | 获取本端 NPU 地址 | 从本端探测对端地址 |
|---|---|---|
| A2 | `hccn_tool -i <id> -ip -g` | `hccn_tool -i <id> -ping -g address <peer_npu_ip>` |
| A3 | `hccn_tool -i <id> -vnic -g` | `hccn_tool -i <id> -hccs_ping -g address <peer_npu_ip>` |
| A5 | 该指导未给出 | 不能根据 A3 示例认定兼容 |

指导以返回 `This pkt ping success` 为成功依据，并要求 `hccn_tool -i <id> -tls -g` 的 switch 设置一致。主机网络、NPU 数据面、任务 TCP 端口必须分别检查；主机 ping 成功不证明 NPU 通，NPU 探测成功也不证明业务端口通。它记录了部分环境 link/net_health 与实测不一致的案例，因此应保留诊断结果，不仅凭 link DOWN 判定不通。[同一指导](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/docs/npu-roce-connectivity.md)

上游 vLLM-Ascend 的 A2/A3 教程提供逐设备地址、探测和 TLS 查询，要求参与通信的 NPU 互通，并说明容器需要对应通信配置文件。示例设备范围属于其示例机型，不作为 Hive 的固定设备数量。[上游通信环境检查](https://github.com/vllm-project/vllm-ascend/blob/3b7cf0fad5f37fb9e55d2622143819081a91e91a/docs/source/tutorials/features/pd_disaggregation_mooncake_multi_node.md#verify-multi-node-communication-environment)

### Hive 的设计推导

以下为本项目建议，不是源仓库已有调度功能：

- 纳管时通过中心机 SSH，在节点两端发起探测；不要求节点之间配置 SSH 密钥。记录源节点/设备、目标节点/设备、方向、结果、检查时间及简短错误。
- NPU0 探测可以初筛；需要多机互联的申请，必须覆盖最终选定的设备集合，逐节点对、逐设备对、双向通过，并确认 TLS 一致。三台机器不能用 A–B、B–C 成功推断 A–C 成功。
- 申请时复查最终候选组合；未检查、超时、解析失败或 A5 未有适配命令，均不能作为“已确认互联”资源。可继续用于不要求互联的申请。
- 检测有命令超时和并发限制；不改 IP、TLS 或防火墙。原指导的停止 firewalld 是修复操作，不能自动并入纳管检查。
- 主机 TCP 只对配置明确且对端正在监听的探测/业务端口作双向检查；未监听导致的拒绝不等于网络故障。纳管探测端口通过不保证未来所有业务端口通过，任务启动前需按任务实际端口复检。
- 基础 NPU 探测不是 HCCL/PD 端到端运行测试，不承诺业务带宽、性能或所有容器配置正确。

## 2. 卡号与设备编号

A3 编号说明区分 NPU、单卡内部 chip 与全局 chip ID。但该页的 `/dev/davinci` 映射表与同仓 A3 启动脚本存在矛盾：前者按 8 个设备文件描述双芯卡，后者明确映射 `davinci0` 到 `davinci15`。因此不能照抄成统一映射公式。[编号说明](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/docs/a3-chip-numbering.md)、[A3 容器脚本](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/scripts/start-docker-A3.sh)

Hive 应使用纳管时实际设备枚举建立前端卡号、采集对象、进程设备、可见设备 ID 和互联工具 ID 的映射；界面删除“物理卡数/可调度设备数”两套口径，不代表内部可以丢掉映射。A5 容器脚本的存在也不证明其互联命令兼容 A3。[A5 容器脚本](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/scripts/start-docker-A5.sh)

## 3. 从实际 NPU 进程反查 Docker

`who.sh` 的方法是读取宿主机 `/proc/<pid>/cgroup`，识别 cgroup v1 的 `/docker/<64位ID>` 或 v2 的 `docker-<64位ID>.scope`，再用 `docker ps -a --filter id=<ID>` 返回容器名、镜像和状态。输入应来自宿主机 NPU 进程 PID，不能使用容器内部 PID。[who.sh 源码](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/scripts/who.sh)

由上述实现可以确定的限制：它只识别两种 Docker cgroup 格式；无法匹配不能直接证明进程运行于裸机。PID 退出、权限不足、Docker 不可访问、容器删除都可能使查询失败。其脚本没有处理 PID 重用核验，也没有可靠识别平台申请者的能力。[who.sh 源码](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/scripts/who.sh)

Hive 建议按宿主机 PID 和进程启动标识采集实际容器，批量查 Docker 并缓存；明确区分“宿主机进程”“Docker 容器”“容器未知/查询失败”。容器名不能用于猜测申请人。页面并列展示平台申请状态与实际进程/容器；平台外使用占卡但不自动归到某个申请。清理应重新核验目标进程身份，不能直接按容器名停止整个容器。

## 4. 共享盘证据边界

以上互联指导、PID 脚本和容器启动脚本未提供共享盘判定算法。A3/A5 脚本把宿主机 `/mnt` 映射进容器，仅能说明目录映射，不能证明不同节点访问同一后端存储。[A3 脚本](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/scripts/start-docker-A3.sh)、[A5 脚本](https://github.com/xuchi-0808/blue-server-toolkit/blob/67b5b56594f418a33210de5c47f1fb7f6adb4563/scripts/start-docker-A5.sh)

Hive 的挂载发现和跨节点共享验证应单独设计，不能用“存在 `/mnt`”或“路径同名”代替共享盘检查。
