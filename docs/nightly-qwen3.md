# Qwen3-30B-A3B-W8A8 nightly 样例

本样例使用用户指定的 [nightly YAML](https://github.com/vllm-project/vllm-ascend/blob/d4d2957e5208c2f464d4625c05920bd29ea233cb/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml)，执行上游原生 pytest。Hive 只负责资源申请、环境、命令、依赖、日志与产物；本仓库不复制模型测试、压测或阈值校验实现。

## 固定来源与参数

| 项目 | 值 |
|---|---|
| 来源 | vllm-project/vllm-ascend PR #16043，`revision=merged` |
| Ascend 提交 | `d4d2957e5208c2f464d4625c05920bd29ea233cb` |
| 匹配 vLLM 提交 | `a97dacb7106ee49f39f3d1fc6ae1800ff724e01d`，来自该提交的 `.github/vllm-main-verified.commit` |
| 权重 | `vllm-ascend/Qwen3-30B-A3B-W8A8`，动态 W8A8 |
| 数据集 | `vllm-ascend/GSM8K-in3500-bs400` |
| 测试 | performance，TP=1，180 个请求，并发 45，最大输出 1500 |
| 服务参数 | 最大模型长度 5600，最大批处理 tokens 16384，最大序列数 100 |
| 上游基线 / 阈值 | `812.3394` / `0.97`，保留原 YAML，由上游断言判定 |
| AISBench | 上游 CI 固定 tag `v3.1-20260609-master` |

入口来自 [nightly 工作流](https://github.com/vllm-project/vllm-ascend/blob/d4d2957e5208c2f464d4625c05920bd29ea233cb/.github/workflows/_e2e_nightly_single_node.yaml)：设置 `CONFIG_YAML_PATH=Qwen3-30B-A3B-W8A8.yaml`，调用 `tests/e2e/nightly/single_node/models/scripts/test_single_node.py`。上游负责启动服务、选择可用端口、就绪检查、AISBench、结果断言和服务关闭。

## 环境及输入

首次验收指定 `192.168.13.198`，通过正常资源接口申请整机，job 按 TP=1 使用一张逻辑卡。节点映射登记 `hive-nightly-a3-ci` 镜像别名以及上述 ModelScope 权重和数据集名称。镜像用于提供基础依赖，环境安装步骤仍检出并安装表中的精确源码版本；不会把基础镜像旧版本当成目标版本。

现有基础镜像只读调查中有 CANN 9.1.0、torch 2.10.0、torch-npu 2.10.0.post4 和 triton-ascend 3.2.2。这些是基础镜像观察值，最终安装版本以真实环境核验日志为准。

198 上找到八分片动态 W8A8 权重。本地数据文件虽然位于另一目录，其 5,666,934 字节和 SHA-256 `2beccaa48329cfaee02c28583ca17863bfe27fc80e162370d0ed5cf2475a1d9b` 与 [指定 ModelScope 数据集](https://www.modelscope.cn/datasets/vllm-ascend/GSM8K-in3500-bs400)的官方文件完全一致。原共享数据不修改；每个任务在自己的目录建立逻辑名称映射。

容器通过节点上的 `/mnt/share/c00814587/start-docker-A3.sh` 创建。服务运行、安装、校验全部在平台新建容器中；不复用他人容器。每个任务有独立的 AISBench 配置副本、工作目录和结果目录；安装环境可复用。产物路径使用 `${task_id}`，避免同时运行的任务互相覆盖。保留原生 pytest 日志、JUnit、AISBench 结果、输入来源和包版本清单。

实际启动命令、上传 YAML 及机器路径存于任务数据库及管理员运行数据目录，不作为 Hive 业务代码提交。新设备部署不会自动复制节点、密码或这些环境数据；需要按 README 纳管机器并登记该设备实际映射。

## 当前验收状态（2026-09-13）

准备阶段的两次只读检查未发现 NPU 进程。平台回归完成后复查，198 的全部 16 张逻辑卡已被外部 `mlptp_dyb` 容器中的 vLLM worker 占用，约每卡 50 GiB 进程显存。平台正确将其判为外部占用，没有强制分配或清理这些进程。

真实任务 `b47b4ca2-dcbc-4744-8900-de28ceae41c4` 经排队后获得整机，创建自有容器并核验独立 PID 命名空间。但容器 GitHub fetch 发生 GnuTLS 连接中断，在 checkout 阶段退出 128。Hive 记录 FAILED，确认自有容器 STOPPED、空间 CLOSED，未启动 NPU 测试。预置 `d221944b-e2bc-49d8-90fd-95cf4c6d9f1b` 保留这次任务的配置与 13 项标签，保持禁用。

网络最小复现区分了两类现象：宿主机已有代理地址不可达，Git 命令超时；仅在本次诊断命令中移除代理后，官方 GitHub 可访问。容器没有继承这些代理，首次错误不能仅由宿主代理解释。为减少直连传输中断，Ascend 和 catlass 固定提交从其官方来源拉取并核验，匹配 vLLM 完整 bare 仓库由中心机从官方 GitHub 拉取，经 SSH 传输。中心机 vLLM 源包共 46,210,041 字节，SHA-256 为 `26d44c0bfd2c2b42651cc559e647071a00f2fe8966102e945c7329a3249dbee2`，完整 Git 对象校验通过。

源码缓存放在共享盘独立 Hive 目录。重试配置仅对本任务设置 Git URL 到本地缓存的映射，检出后继续检查固定 HEAD；不改变全局 Git 配置或 TLS 校验。三份缓存已用与任务相同的 Git 配置完成实际 fetch、checkout 和 SHA 核验。新部署需提供对应缓存或能稳定访问官方仓库。

重试任务 `4d630a87-c3df-4da0-acbe-23bfefa560f9` 已通过 Hive 提交，原 YAML、SHA、TP 和阈值保持不变。提交后 198 再次出现全部 16 张卡被外部进程占用，任务处于 QUEUED，尚未创建重试容器。排队窗口仍为 60 分钟，worker 会在资源可用时自动推进。测试尚未完成，没有实测吞吐或成功结论；平台测试通过不代表本模型业务用例通过。

队列到期会按现有规则结束等待，不会强占资源。节点空闲后可提交同一配置的新任务；保留旧任务记录作为排队证据。完成实测后，应保存原生结果和执行 ID，并由管理员单项启用本样例。其余 nightly/weekly 用例仍等待用户确认后接入。
