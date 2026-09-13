# Qwen3-30B-A3B-W8A8 nightly 样例

本样例使用用户指定的 [nightly YAML](https://github.com/vllm-project/vllm-ascend/blob/d4d2957e5208c2f464d4625c05920bd29ea233cb/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml)，通过独立的服务启动、AISBench 和结果校验作业运行，不使用 pytest 包装入口。Hive 只负责资源申请、环境、命令、依赖、日志与产物；本仓库不复制模型测试、压测或阈值校验实现。

## 固定来源与参数

| 项目 | 值 |
|---|---|
| 执行代码来源 | [vllm-project/vllm-ascend PR #16452](https://github.com/vllm-project/vllm-ascend/pull/16452)，`revision=head`，草稿 |
| Ascend 执行提交 | `614501d2fe63f029ddea6d746a3ea4f7ef24d430` |
| 原 YAML / 代码基线 | `d4d2957e5208c2f464d4625c05920bd29ea233cb`，PR #16043 合并提交；新增入口不修改该 YAML |
| 匹配 vLLM 提交 | `a97dacb7106ee49f39f3d1fc6ae1800ff724e01d`，来自该提交的 `.github/vllm-main-verified.commit` |
| 权重 | `vllm-ascend/Qwen3-30B-A3B-W8A8`，动态 W8A8 |
| 数据集 | `vllm-ascend/GSM8K-in3500-bs400` |
| 测试 | performance，TP=1，180 个请求，并发 45，最大输出 1500 |
| 服务参数 | 最大模型长度 5600，最大批处理 tokens 16384，最大序列数 100 |
| 上游基线 / 阈值 | `812.3394` / `0.97`，保留原 YAML，由上游断言判定 |
| AISBench | 上游 CI 固定 tag `v3.1-20260609-master` |

原 [nightly 工作流](https://github.com/vllm-project/vllm-ascend/blob/d4d2957e5208c2f464d4625c05920bd29ea233cb/.github/workflows/_e2e_nightly_single_node.yaml) 将服务启动和 AISBench 包在 pytest 中。Hive 中改为显式依赖：服务作业以前台 `vllm serve` 运行；就绪检查成功后执行原生 `ais_bench`；最后执行外部 PR 的结果校验命令。配置生成与校验复用上游逻辑，独立入口本身不启动服务或压测。Hive 根据作业依赖保留服务，并在消费者结束后清理自己启动的服务。

```mermaid
flowchart LR
    S[启动 Qwen3 服务] -->|健康检查通过| B[AISBench 性能测试]
    B -->|压测命令成功| V[校验原 nightly 阈值]
```

服务端与客户端环境分别调用 PR 中的 `tools/nightly_environment.py`；后续作业加载对应 `activate.sh`。`tools/nightly_cli.py server-command` 从原 YAML 生成前台服务命令，`prepare` 生成完整 AISBench 配置。压测直接执行 `ais_bench`，保留管道退出码。`locate-results` 从本次日志确认唯一结果目录，验证其位于本任务输出目录，按原始字节归档 JSON/CSV；`verify` 使用原有阈值判断。没有调用 `RemoteOpenAIServer`、`AisbenchRunner` 或 `run_aisbench_cases`。

各作业只配置动态节点地址与容器、依赖和流程时限。样例内部端口为 18123，启动前检查是否已有监听，不停止其他服务。服务最长 30 分钟，压测最长 15 分钟，结果校验最长 10 分钟；这些是本样例配置，通用新 job 默认仍为 10 分钟。服务保留至消费者结束；运行空间按本次配置再保留 60 分钟，可正常关闭归还。

## 环境及输入

首次验收指定 `192.168.13.198`，通过正常资源接口申请整机，job 按 TP=1 使用一张逻辑卡。节点映射登记 `hive-nightly-a3-ci` 镜像别名以及上述 ModelScope 权重和数据集名称。镜像用于提供基础依赖，环境安装步骤仍检出并安装表中的精确源码版本；不会把基础镜像旧版本当成目标版本。

现有基础镜像只读调查中有 CANN 9.1.0、torch 2.10.0、torch-npu 2.10.0.post4 和 triton-ascend 3.2.2。这些是基础镜像观察值，最终安装版本以真实环境核验日志为准。

198 上找到八分片动态 W8A8 权重。本地数据文件虽然位于另一目录，其 5,666,934 字节和 SHA-256 `2beccaa48329cfaee02c28583ca17863bfe27fc80e162370d0ed5cf2475a1d9b` 与 [指定 ModelScope 数据集](https://www.modelscope.cn/datasets/vllm-ascend/GSM8K-in3500-bs400)的官方文件完全一致。原共享数据不修改；每个任务在自己的目录建立逻辑名称映射。

容器通过节点上的 `/mnt/share/c00814587/start-docker-A3.sh` 创建。服务运行、安装、校验全部在平台新建容器中；不复用他人容器。服务端与客户端可以位于同一节点的不同容器。每个任务有独立的 AISBench 配置、工作目录和结果目录；安装环境可在来源与配置一致时复用。产物路径使用 `${task_id}`，避免同时运行的任务互相覆盖。保留服务日志、AISBench 原生结果、外部校验结果、输入来源和包版本清单。

实际启动命令、上传 YAML 及机器路径存于任务数据库及管理员运行数据目录，不作为 Hive 业务代码提交。新设备部署不会自动复制节点、密码或这些环境数据；需要按 README 纳管机器并登记该设备实际映射。

## 当前验收状态（2026-09-13）

准备阶段的两次只读检查未发现 NPU 进程。平台回归完成后复查，198 的全部 16 张逻辑卡已被外部 `mlptp_dyb` 容器中的 vLLM worker 占用，约每卡 50 GiB 进程显存。平台正确将其判为外部占用，没有强制分配或清理这些进程。

真实任务 `b47b4ca2-dcbc-4744-8900-de28ceae41c4` 经排队后获得整机，创建自有容器并核验独立 PID 命名空间。但容器 GitHub fetch 发生 GnuTLS 连接中断，在 checkout 阶段退出 128。Hive 记录 FAILED，确认自有容器 STOPPED、空间 CLOSED，未启动 NPU 测试。预置 `d221944b-e2bc-49d8-90fd-95cf4c6d9f1b` 保留这次任务的配置与 13 项标签，保持禁用。

网络最小复现区分了两类现象：宿主机已有代理地址不可达，Git 命令超时；仅在本次诊断命令中移除代理后，官方 GitHub 可访问。容器没有继承这些代理，首次错误不能仅由宿主代理解释。为减少直连传输中断，Ascend 和 catlass 固定提交从其官方来源拉取并核验，匹配 vLLM 完整 bare 仓库由中心机从官方 GitHub 拉取，经 SSH 传输。中心机 vLLM 源包共 46,210,041 字节，SHA-256 为 `26d44c0bfd2c2b42651cc559e647071a00f2fe8966102e945c7329a3249dbee2`，完整 Git 对象校验通过。

源码缓存放在共享盘独立 Hive 目录。重试配置仅对本任务设置 Git URL 到本地缓存的映射，检出后继续检查固定 HEAD；不改变全局 Git 配置或 TLS 校验。三份缓存已用与任务相同的 Git 配置完成实际 fetch、checkout 和 SHA 核验。新部署需提供对应缓存或能稳定访问官方仓库。

重试任务 `4d630a87-c3df-4da0-acbe-23bfefa560f9` 后来获得 198 整机并完成源码检出，在安装阶段等待依赖解析。用户要求取消 pytest 包装后，该任务已取消；确认任务 CANCELLED、自有容器 STOPPED、空间 CLOSED，测试 job 的执行次数为 0。

零 NPU 环境诊断任务 `dc480c8a-5240-4145-84f3-2b61f0cd51fa` 已完成。原生 pip 复现固定 Ascend 的 `fastapi<0.124.0` 与匹配 vLLM 的 `fastapi[standard]>=0.133.0,<0.137.0` 无可用交集。移除额外的 audio 安装项与 PyTorch 索引后，单独 vLLM 依赖解析约 46 秒完成；正式环境仍需联合解析、安装和核验。该诊断未运行服务或 NPU 压测，不能视为 Qwen3 用例通过。

后续检查确认基础镜像的 `pip check` 原本就不是全绿：Triton Ascend 3.2.2 固定 NumPy 1.26.4，而目标 vLLM 的 OpenCV 依赖要求 NumPy 2.x；AISBench 也要求 NumPy 1.x。本次采用容器既有 NPU 依赖进行文本用例验证，完整保留安装前后依赖检查，不以修改包元数据或放宽测试阈值掩盖冲突。服务端与客户端分容器；这次验证不能证明视频、音频或其他模型环境兼容。

旧 pytest 预置保持禁用，后续用新外部 PR 的显式作业配置重新验收，不修改历史任务的冻结来源。完成实测后，应保存原生结果和执行 ID，并由管理员单项启用本样例。其余 nightly/weekly 用例仍等待用户确认后接入。普通用户提交后即可在自己的任务记录中看到排队及环境准备状态；管理员提交的调测任务不转移给其他账号。

## 显式作业接入结果（2026-09-13 16:17）

- 外部 PR 的 16 项 CLI/环境检查通过，Ruff 0.14 与格式检查通过。测试覆盖不隐式启动服务/压测、参数保持、原字节数据与结果、原阈值、CANN/ATB 加载失败和依赖审计失败。它们不替代 NPU 实测。
- 198 上的新源码缓存已从官方 PR 拉取，固定 HEAD、完整 Git 对象及与任务相同的 fetch/checkout 校验通过。
- 旧诊断空间 `7d8046d7-999a-4f3d-a931-f3da5feb7a4f` 已通过正常关闭流程结束，自有容器确认停止。
- 新任务 `7e7cc4af-27da-49f7-8f88-20b7dcb18e69` 已提交，运行空间 `f49bea22-17f2-4090-aafb-dfb882309ed6`，当前 QUEUED。复查时 198 的 16 张卡均被外部 vLLM 进程占用；三项新 job 均无执行记录。60 分钟排队窗口内，worker 在整机可用后自动推进；到期按队列规则结束等待。
- 新预置 `0873b7e4-2512-4337-8e2a-518f84bd15df` 保存三个显式作业与 13 项筛选标签，状态为待执行、禁用。尚无实测吞吐或业务通过结论，不批量接入其他用例。
- 当前页面账号 `cxy` 没有资源申请权限，提交被平台拒绝；新调测任务使用已有 `admin` 账号提交。账号权限与旧任务归属未修改。使用 `admin` 查看此次任务；普通用户只能看到自己的任务。

调测发现的 Hive 产物缺失问题已修复：脚本结束且容器身份核验完成后，明确缺失、非普通文件、符号链接或超限产物会使任务结束失败，释放对应 job 占用。SSH 或身份不确定仍保留待核验状态。相关回归 38 项通过、1 项因本机符号链接权限跳过；API、前端与 worker 已更新到 18000 部署。
