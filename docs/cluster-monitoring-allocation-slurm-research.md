# Hive：集群监控、按需分配与 Slurm 编排调研汇总

调研日期：2026-09-12。范围：已有 Linux 服务器、GPU 计算集群、按用户条件申请整机或计算资源。依据官方文档与项目仓库；没有部署实测，适配程度为基于文档的工程判断。动态文档能力应在选定版本再次确认。

## 阅读导航

- 选型结论：适用于不同基础设施的推荐组合。
- 集群监控方案详细对比：Prometheus、Zabbix、Netdata 的指标与开源边界。
- 自动分配与用户入口：Slurm、SkyPilot、MAAS、Volcano、Kueue、HAMi、Open OnDemand。
- 空闲判定、自研范围、许可证与验证场景。
- Slurm 的部署角色、Job/Step/Task、独立作业与异构作业。
- sbatch 指令解析、命令执行位置、依赖和资源回收。
- 双 GPU 服务与单 CPU 压测：编排方案比较、完整示例及验证边界。

本文汇总本轮调研与后续技术问答，整理了讨论中澄清的术语和限制。所有适配推荐属于工程判断，脚本为待目标集群验证的示例；未进行部署或性能测试。

## 结论

- 已有服务器，用户希望申请两台机器运行任务：优先验证 Slurm + Prometheus/node_exporter/DCGM Exporter + Grafana；可用 Open OnDemand 提供 Web 入口。
- 希望有统一 AI 作业、开发环境和多集群入口：重点验证 SkyPilot。
- 已有 Kubernetes：比较 Volcano 与 Kueue；有 GPU 切分需求再评估 HAMi。
- 需要整台物理机的装机、分配、归还：评估 MAAS。
- 仅希望在已有 SSH 机器中查找空闲机器、登记借用：监控栈加轻量租约服务可能更贴合，但必须建立真正的占用和访问控制。这是设计建议，不是现成项目能力。


## 集群监控方案详细对比

核验日期：2026-09-12。仅使用项目官方仓库和官方文档。以下“适合”与自动分配边界属于工程判断。

### 结论

面向自研“机器管理与分发平台”，优先选择 **Prometheus + node_exporter + NVIDIA DCGM Exporter + Grafana OSS + Alertmanager** 作为指标底座；现成一体化运维监控可选 **Zabbix**；快速部署和逐机排障可看 **Netdata**，但其 Agent 开源不等于当前整套 UI/Cloud 开源。三个方案都能提供“候选机器筛选”的指标，不能仅凭监控读数完成安全、排他的机器分配；所有权、租约、并发争抢、到期回收应由调度器或平台数据库管理。

### 对比

| 方案 | CPU / RAM / 磁盘 | NVIDIA GPU | 多机 / 告警 | 与自研平台结合 | 许可证 |
|---|---|---|---|---|---|
| Prometheus + exporters + Grafana | node_exporter 有 cpu、meminfo、filesystem（容量）、diskstats（I/O）；按主机与设备标签查询 | DCGM Exporter 输出 GPU 利用率、显存、温度、功率等 Prometheus 指标，可与 K8s 工作负载关联 | Prometheus 多目标采集与 PromQL；Grafana 看板；Alertmanager 分组、去重、路由、静默 | 查询 API 适合资源筛选、趋势判断和自定义门户；需要自行编排组件 | Prometheus、node_exporter、DCGM Exporter、Alertmanager 为 Apache-2.0；Grafana OSS 为 AGPLv3。DCGM 库/容器所含组件另看 NVIDIA 条款 |
| Zabbix | 官方 Linux 模板包括 CPU / RAM、文件系统容量、磁盘 IOPS、等待时间、队列和繁忙率 | 当前官方 NVIDIA Agent 2 模板通过 NVML 自动发现 GPU；利用率、显存已用/空闲/总量、温度、功率、ECC；不需外部脚本 | 中央监控、模板、触发器，适合传统裸机运维 | 可作为监控系统与外部分配服务对接；本身不是租赁或作业调度器 | 7.0 起 AGPLv3 |
| Netdata | Agent 覆盖 CPU / RAM / 磁盘 / 挂载点 / 文件系统，可收集和存储、告警、导出 | 官方 nvidia_smi collector 有 GPU 利用率、显存 free/used/reserved、温度、功率；也有 DCGM collector | 支持多机架构；集成告警能力，但 nvidia_smi 集成本身没有默认告警规则 | 适合快速看到机器细节；做统一开源产品底座时须区分 Agent 和 UI/Cloud | Agent GPLv3+；当前 UI 为闭源 NCUL1；Cloud 闭源，有免费和付费层 |

### 核验依据与直接链接

1. [node_exporter 官方仓库](https://github.com/prometheus/node_exporter)：CPU、meminfo、filesystem、diskstats 均列于默认 collectors；不同操作系统支持不同，容器监控宿主机须正确挂载宿主根目录。
2. [DCGM Exporter 官方仓库](https://github.com/NVIDIA/dcgm-exporter)、[NVIDIA GPU telemetry 文档](https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html)：HTTP `/metrics` 输出 GPU 指标，有官方 Grafana dashboard；Apache-2.0。
3. [Prometheus 概览](https://prometheus.io/docs/introduction/overview/)、[HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/)、[Prometheus 仓库](https://github.com/prometheus/prometheus)：标签时间序列、查询语言、数据采集和查询能力；Apache-2.0。
4. [Alertmanager 文档](https://prometheus.io/docs/alerting/latest/alertmanager/)、[官方仓库](https://github.com/prometheus/alertmanager)：告警分组、去重、路由、静默和抑制；Apache-2.0。
5. [Grafana licensing](https://grafana.com/licensing/)：核心开源项目转为 AGPLv3。此处只列许可证事实，不对特定二开方式作法律结论。
6. [Zabbix Linux 官方集成](https://www.zabbix.com/integrations/linux)：磁盘容量和 I/O 相关指标与触发器；[NVIDIA 官方集成](https://www.zabbix.com/integrations/nvidia)：当前页面为 7.4，也提供 7.2 模板；使用 Agent 2 NVIDIA plugin，无须外部脚本；[许可页面](https://www.zabbix.com/license)。
7. [Netdata 官方仓库](https://github.com/netdata/netdata)：明确列出 Agent、UI、Cloud 的不同许可；[NVIDIA collector](https://learn.netdata.cloud/docs/collecting-metrics/collectors/hardware-and-sensors/nvidia-gpu)：基于 nvidia-smi，GPU 利用率和显存占用分开；UI 配置需付费 Cloud，可改用配置文件；[DCGM collector](https://learn.netdata.cloud/docs/collecting-metrics/collectors/hardware-and-sensors/nvidia-data-center-gpu-manager-dcgm)。

### 对“找两台空闲机器”的具体建议（设计推论）

“空闲”应至少同时满足：机器健康、指标未过期、没有有效的分配租约/作业占用、CPU/GPU 持续一段时间较低、可用内存及目标挂载点可用磁盘满足需求、GPU 型号/数量/显存满足需求。需明确整机独占还是共享 GPU。

GPU 利用率为 0% 不代表无人占用：进程可能仍保有显存，或用户租约有效但暂时等待数据。显存的“memory utilization”通常指采样时间内显存读写活跃比例，不能当作显存容量占用比例；应单独使用 framebuffer used/free/total。Zabbix 官方 NVIDIA 模板明确区分这两类。

建议流程：需求解析 → 用资产标签及监控筛选 → 在调度器/数据库原子锁定全部两台 → 返回机器和租约 → 心跳与到期释放。不能“查出两台 → 分别记录分配”，否则并发用户可能获得同一主机，或只分到一台。监控层用于选址与健康判断，调度层负责分配事实。

## 自动分配与用户入口

| 项目 | 原生能力 | 对“两台空闲机器”的适配判断 | 主要边界 |
|---|---|---|---|
| [Slurm](https://github.com/SchedMD/slurm) | 节点和 GPU 等资源申请、队列、任务执行、资源归还 | 高，适合受调度器管理的 Linux 集群 | 是作业分配；不自动把任意 SSH 服务器的低利用率解释为可分配 |
| [SkyPilot](https://github.com/skypilot-org/skypilot) | 统一 AI 计算入口，任务资源声明，Kubernetes/Slurm/云/已有机器后端 | 高，适合提交双节点 AI 任务 | 节点可能是逻辑计算节点或 Pod，不能直接等同于两台物理机独占 |
| [MAAS](https://github.com/canonical/maas) | 裸机发现、硬件检查、装机、带约束的分配和释放 | 高，适合整机交付 | 可用是生命周期状态；不是依据实时 GPU 利用率借用已部署机器 |
| [Volcano](https://github.com/volcano-sh/volcano) | Kubernetes 批任务、队列、Gang Scheduling | 高，适合多节点容器任务 | 需配置不同物理节点放置和独占策略；两个 Pod 不等于两台机器 |
| [Kueue](https://github.com/kubernetes-sigs/kueue) | 作业准入、配额、公平共享、抢占、资源类型选择 | 高，适合团队之间共享 Kubernetes 算力 | 不替代 kube-scheduler；Pod 到 Node 的调度由下层执行 |
| [HAMi](https://github.com/Project-HAMi/HAMi) | Kubernetes 上异构加速器共享、设备感知调度、显存和计算隔离 | 补充能力，适合细粒度 GPU 申请 | 不是独立的整机借用平台；隔离与硬件支持依后端而异 |
| [Open OnDemand](https://github.com/OSC/ondemand) | 浏览器 Shell、文件管理、作业提交、交互式应用、多集群入口 | 适合作为 Slurm 等后端的用户门户 | 本身不是调度器，也不替代硬件指标采集 |

以上能力来源：[Slurm 概述](https://slurm.schedmd.com/overview.html)、[SkyPilot 概述](https://docs.skypilot.ai/en/latest/overview.html)、[MAAS 分配 API](https://maas.io/docs/machine)、[Volcano 教程](https://volcano.sh/docs/gettingstarted/tutorials/)、[Kueue 概述](https://kueue.sigs.k8s.io/docs/overview/)、[HAMi 官方仓库](https://github.com/Project-HAMi/HAMi)、[Open OnDemand 官方仓库](https://github.com/OSC/ondemand)。

### Slurm 的具体落地

在已配置分区、节点和 GPU GRES 的 Slurm 集群，以下命令表达“申请两台节点、每台至少四张 A100、独占节点、持续两小时”：

```bash
salloc --nodes=2 --exclusive --mem=0 --gres=gpu:a100:4 --time=02:00:00
```

`a100` 必须与管理员配置的 GPU 类型匹配；分区策略可能影响独占行为。`--mem=0` 申请节点全部可分配内存。这里的“至少四张”不限制节点物理总卡数；整机独占会影响其他资源的分配。排队表示调度资源尚未满足，不保证立即交付。参考 [salloc](https://slurm.schedmd.com/salloc.html) 和 [GPU GRES](https://slurm.schedmd.com/gres.html)。

Web 平台可通过 [slurmrestd](https://slurm.schedmd.com/rest.html) 接入节点、任务和预约操作。用户若仍直接 SSH 到计算节点，需要配合 [cgroup 约束](https://slurm.schedmd.com/cgroup.conf.html) 与 [pam_slurm_adopt](https://slurm.schedmd.com/pam_slurm_adopt.html)，让访问权限和资源清理跟随作业生命周期。

### SkyPilot 的具体落地

官方当前文档支持提供 IP/SSH 凭据，以 `sky ssh up` 建立 SSH Node Pool。它会在远端安装依赖和运行时，文档包含 Kubernetes 组件及 Pod 配置；这不是只读取 SSH 机器状态的无侵入接入。支持在 Dashboard 查看节点池与 GPU 可用资源。[已有机器接入](https://docs.skypilot.ai/en/latest/reservations/existing-machines.html)

任务 YAML 通过 `num_nodes: 2` 和每节点 `resources` 表达双节点及 GPU/CPU/内存需求。物理机放置和独占要结合后端验证。[YAML 规范](https://docs.skypilot.ai/en/latest/reference/yaml-spec.html)

### MAAS 的具体落地

`machines allocate` 支持 CPU、内存、架构、标签、区域、存储等约束，分配对象是待部署的可用机器。分配先占用机器，再执行操作系统部署。硬盘容量约束不是已运行系统某个挂载点的动态剩余空间。[分配 API](https://maas.io/docs/machine)、[部署机制](https://maas.io/docs/deploying-machines)

官方此分配接口按单机返回，不能据此认定它支持“两台一起成功或一起失败”的事务；平台若连续申请两台，需要处理第二台失败的补偿释放。完整机器生命周期见 [当前管理文档](https://canonical.com/maas/docs/latest/how-to-guides/manage-machines/)。

### Kubernetes 方案的区别

Volcano 的 Gang Scheduling 让一个工作负载在满足所需的一组 Pod 资源后才开始；多机任务还需要通过节点放置策略确保跨机器运行。Kueue 则管理准入、配额和公平共享，并保留 Kubernetes 本身的组件分工。二者不是完全同一层的替代品；第一版应明确主要需求后选择，组合时再验证兼容性。[Volcano 教程](https://volcano.sh/docs/gettingstarted/tutorials/)、[Kueue 概述](https://kueue.sigs.k8s.io/docs/overview/)、[节点放置](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)

## “空闲”与“可分配”必须分开

Kubernetes 的标准调度依据资源请求与可分配容量；即使 CPU/内存实际使用很低，也可能因已承诺资源不足而无法放置新 Pod。[官方资源管理说明](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)

以下是建议的平台判定模型，不是项目默认值：

1. **资源归属**：未被作业、租约或预约占用，且调用者有相应配额和权限。
2. **健康状态**：机器心跳和采集数据足够新，无维护标记、设备故障或磁盘故障。
3. **资源规格**：GPU 型号/数量/显存、CPU、内存、网络和拓扑满足需求。
4. **实时余量**：例如指定 `/data` 挂载点至少剩余 1 TB，CPU/GPU 持续低负载一段时间；阈值按业务制定。
5. **共同预留**：两台一起预留；失败时释放已预留部分，防止并发重复分配。
6. **交付与回收**：返回节点地址和租约/作业 ID，提供到期、续租、归还和访问权限回收。

低利用率可能来自加载数据、等待通信或已预约任务；不能把它当作强制收回依据。若用户可绕过平台直接 SSH 运行作业，平台需要迁移存量任务、采用统一运行入口，或明确只提供推荐与协作登记。

磁盘应区分物理规格、文件系统可用空间、IO 性能和容量预留；不能把安装盘大小、Kubernetes ephemeral-storage 请求或静态节点标签当成 `/data` 当前可用容量。

## 建议自研的范围

优先复用采集器、时序存储、调度器；自研统一机器列表、资源申请、租约状态、团队权限、审批（如业务需要）、审计及自然语言到结构化申请的转换。

建议第一版请求模型：机器数量、GPU 类型和每机数量、每卡显存、内存、磁盘挂载点及余量、区域/互联、独占与否、时长、可排队与否。所有条件经结构化校验后提交给单一分配权威；已有 Slurm/Kubernetes 时应以其分配状态为准，避免再做一个独立占用账本。

可选的第一版路线：

| 当前条件 | 推荐验证组合 |
|---|---|
| 已有普通 Linux GPU 服务器，愿意统一任务入口 | Slurm + 监控栈 + 自研门户或 Open OnDemand |
| 已有 Kubernetes，主要运行训练/推理容器 | 监控栈 + Volcano 或 Kueue；细分 GPU 时加 HAMi |
| 多种计算基础设施，需要统一开发/运行体验 | SkyPilot + 监控栈 |
| 管理裸机装机、回收和重新交付 | MAAS + 监控栈 + 多机申请流程 |
| 只需要找机器、登记借用，暂不改变运行方式 | 监控栈 + 轻量租约服务；声明并实施相应访问边界 |

## 许可证记录

此处只记录一手仓库声明，不作法律适用判断。

| 项目 | 主项目许可证 | 来源 |
|---|---|---|
| Slurm | GPL，部分 contrib 工具另有许可 | [COPYING](https://github.com/SchedMD/slurm/blob/master/COPYING) |
| SkyPilot | Apache-2.0 | [仓库](https://github.com/skypilot-org/skypilot) |
| Volcano | Apache-2.0 | [仓库](https://github.com/volcano-sh/volcano) |
| Kueue | Apache-2.0 | [仓库](https://github.com/kubernetes-sigs/kueue) |
| HAMi | Apache-2.0 | [仓库](https://github.com/Project-HAMi/HAMi) |
| MAAS | AGPLv3 | [LICENSE](https://raw.githubusercontent.com/canonical/maas/master/LICENSE) |
| Open OnDemand | MIT | [仓库](https://github.com/OSC/ondemand) |

## 建议的验证场景

这些是后续 POC 验收项，尚未执行：两个用户并发申请同一对节点；只有一台可用时的排队/补偿；已占用但 GPU 利用率为零；采集过期；磁盘临界且分配前变化；作业或租约结束后的权限与进程清理；要求两台不同物理机而底层试图将两个 Pod 放在同机。

## Slurm 的开源状态与部署角色

Slurm 是开源软件，主项目采用 GNU GPL，部分 contrib 工具有独立许可证。官方代码由 SchedMD 维护；可自行部署，商业支持是另外的服务。许可证事实以 [COPYING](https://github.com/SchedMD/slurm/blob/master/COPYING) 为准；[官方仓库](https://github.com/SchedMD/slurm)、[官方文档](https://slurm.schedmd.com/) 可直接查阅。

### 提交端、控制端、执行端

| 角色 | 组件 | 职责 |
|---|---|---|
| 提交端：登录节点或 Hive 后端 | sbatch、salloc、srun，或 REST API 客户端 | 提交资源需求、发起执行、查询与取消 |
| 控制端 | slurmctld | 维护节点与作业状态、排队和资源分配 |
| 执行端：每台计算节点 | slurmd；执行步骤涉及 slurmstepd | 在指定节点启动和管理进程，报告状态 |
| 可选记账服务 | slurmdbd | 保存作业与资源使用的记账信息 |
| 可选 API 服务 | slurmrestd | 提供 REST 接口 |

提交脚本的机器不等于执行脚本的机器。Slurm 自身的客户端/服务端角色，也不同于被运行程序的 client/server 业务角色。[架构来源](https://slurm.schedmd.com/overview.html)

### 推理服务与压测客户端由谁区分

Slurm 不会识别某个 Python 程序是服务端还是压测客户端。脚本声明所需资源，并决定在哪些节点启动各个程序。Hive 可以将这些业务角色建模成模板：资源需求、启动命令、服务地址、就绪条件、超时和清理动作。

服务进程启动，不代表模型已加载、HTTP 接口可用。健康检查以及地址传递由应用层负责。Slurm 管理资源和进程生命周期，Hive 或批处理脚本管理业务流程。[作业与步骤](https://slurm.schedmd.com/overview.html)

## Slurm 的 Job、Step、Task 与异构作业

| 术语 | 含义 | 双 GPU 服务 + CPU 压测案例 |
|---|---|---|
| 实验 / Workflow | Hive 的业务对象，不是 Slurm 的通用父级对象 | 启动服务、压测、回收、整理结果 |
| Job / 作业 | 向调度器申请资源并执行工作的单位 | 申请两台 GPU 节点运行服务 |
| Job Step / 作业步骤 | 在已有分配内启动的一组任务，通常由 srun 创建 | 在两台 GPU 节点各启动一个服务进程 |
| Task | 一个执行步骤中的进程实例 | 其中一台节点上的服务进程 |
| Heterogeneous Job / 异构作业 | 由多组不同资源需求的组件组成，协同调度 | 组件 0 为 CPU，组件 1 为 GPU |

`--ntasks=2` 声明的是两个任务进程的需求，不是两个 Job；`sbatch` 本身只执行一份批处理脚本，不会因 ntasks 自动复制脚本。[概念](https://slurm.schedmd.com/overview.html)、[sbatch](https://slurm.schedmd.com/sbatch.html)

### 两个独立 Job

```text
Hive 实验 experiment-001
├─ Job 100：两台 GPU，维持推理服务
└─ Job 101：一台 CPU，运行压测
```

两次 `sbatch` 得到两个独立 Job。Slurm 维护各自资源、状态与时限，但不会自动认为它们必须一起取消。Hive 或编排脚本需要记录这些 Job ID、设置依赖、传递地址、处理失败和回收。

`--dependency=afterok:100` 是等 Job 100 成功结束；`afterany:100` 是等它以任意终态结束。两者都不是服务 readiness。独立作业可以并行提交，CPU 在获配后准备环境，再等待 GPU 健康检查。使用 `afterany` 的清理作业可以作为回收手段，但清理作业自身也可能排队，不能宣称立即释放。[依赖规则](https://slurm.schedmd.com/sbatch.html)

### 一个异构 Job

```text
异构作业 200
├─ 200+0：一台 CPU，承载批处理脚本和压测
└─ 200+1：两台 GPU，承载两个服务
```

一次 `sbatch` 提交一个异构作业。脚本中的 `#SBATCH hetjob` 分隔资源声明，第一组是 group 0，第二组是 group 1。调度器协调各组件资源；默认批处理脚本在第一组件的一台节点运行。[异构作业](https://slurm.schedmd.com/heterogeneous_jobs.html)

资源一起满足后启动，有利于避免 GPU 已运行而 CPU 还在排队；代价是必须等所有组件都能分配，CPU 不能在 GPU 资源尚未就绪时独自先运行。异构作业依赖站点调度配置，官方注明由 backfill 调度器支持，需在目标集群确认。

### 哪些命令在 CPU，哪些在 GPU

```bash
# 第一组件声明的是 CPU，因此普通命令在 CPU batch 节点执行。
hostname
source ./prepare-bench.sh

# 此命令只在第二组件的 GPU 节点运行。
srun --het-group=1 --nodes=2 --ntasks=2 --ntasks-per-node=1 \
  bash ./start-gpu-server.sh &

# 执行位置没有被前面的 srun 切换，仍是 CPU batch 节点。
bash ./wait-ready.sh
python3 ./benchmark.py

# 也可以明确创建 CPU 组上的执行步骤。
srun --het-group=0 --nodes=1 --ntasks=1 python3 ./benchmark.py
```

`srun` 只影响它启动的命令。`&` 让启动器在后台运行，使 CPU 脚本继续准备环境。`#SBATCH hetjob` 不是代码分区标记，程序是否调用 CUDA 也不会决定它自动被放到哪里。[srun](https://slurm.schedmd.com/srun.html)、[het-group](https://slurm.schedmd.com/heterogeneous_jobs.html)

### 结束进程与释放资源的区别

```bash
scancel 200      # 取消整个异构作业
scancel 200+1    # 运行后单独取消 GPU 组件
```

`200+1` 是组件标识；点号形式如 `200.1` 是 Job Step 标识，不能混淆。取消 Step 或退出服务进程不等于归还组件的资源分配。取消运行中的组件才会结束相应分配；最终释放还需 Slurm 完成进程清理和站点 Epilog。

处于 Pending 状态的异构作业不能只取消单一组件。使用明确的 `leader+offset` 形式取消组件，以避免将组件实际 Job ID 与 leader ID 混淆。[取消规则及站点 whole_hetjob 行为](https://slurm.schedmd.com/heterogeneous_jobs.html)、[scancel](https://slurm.schedmd.com/scancel.html)

## sbatch 如何读取脚本参数

`#SBATCH` 对 Bash 是注释，对 `sbatch` 是提交指令。提交时，sbatch 在执行脚本之前读取这些指令，形成资源申请并交给控制器；获配后计算节点才执行实际 Bash/Python 代码。

```bash
sbatch --wait --output="$RUN_DIR/benchmark.log" bench.sbatch
```

会同时采用 `bench.sbatch` 头部的配置：

```bash
#!/usr/bin/env bash
#SBATCH --job-name=inference-benchmark
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=8G
#SBATCH --exclusive
#SBATCH --time=00:15:00

set -euo pipefail
python3 ./benchmark.py
```

| 参数 | 含义 |
|---|---|
| job-name | 作业显示名称 |
| partition=cpu | 选择管理员命名的 cpu 分区，不是自动识别机器类别 |
| nodes=1 | 请求一台节点 |
| ntasks=1 | 按一个任务声明资源需求，不自动启动程序 |
| cpus-per-task=8 | 每任务请求 8 个 Slurm CPU，核/线程语义依站点配置 |
| mem=8G | 每节点请求 8 GiB 内存，强制限制依 cgroup 等配置 |
| exclusive | 请求不与其他作业共享节点；可能获得全节点 CPU，仍受分区策略影响 |
| time=00:15:00 | 最长运行 15 分钟，不含 Pending 排队时间 |
| output | 输出日志文件；默认错误输出也进入同一文件 |
| wait | 提交端等到作业结束，返回对应退出状态；不保证立即开始 |

规则与易错点：

1. `bash bench.sbatch` 不申请资源，会忽略这些注释并在当前机器执行。
2. 指令须位于第一条实际命令之前；`set -euo pipefail` 后的指令不会继续被解析。
3. 指令中不展开 Shell 变量。路径包含 `$RUN_DIR` 时应通过命令行传入，而不是写成 `#SBATCH --output=$RUN_DIR/test.log`。
4. 命令行同名配置可覆盖脚本配置；还需注意提交环境中的 `SBATCH_*` 变量可能影响默认参数。
5. `--exclusive --mem=8G` 不代表获得全机内存；请求全部可分配内存使用 `--mem=0`。
6. `sbatch` 传送批处理脚本本身，不自动分发模型、Python 文件或其他依赖；应使用共享路径或显式部署。

提交后用 `scontrol show job JOB_ID` 查看 Partition、ReqTRES、AllocTRES、NodeList、TimeLimit 等实际信息。[全部规则来源](https://slurm.schedmd.com/sbatch.html)

## 双 GPU 服务 + 单 CPU 并发压测案例

### 范围与两种编排的比较

本例两台 GPU 各运行一份模型副本，CPU 对两个地址发送请求。它不是跨两台 GPU 节点运行同一份分布式模型；若需要张量/流水线并行或 PD 分离，需要替换服务启动与拓扑配置。

| 方案 | 过程 | 适用性与限制 |
|---|---|---|
| 串行独立 Job | run.sh 提交 GPU → 健康检查 → sbatch --wait 提交 CPU → scancel GPU | 直观但 CPU 无法提前准备，GPU 等待 CPU 排队；原示例回收依赖登录节点脚本存活 |
| 并行独立 Job | 同时提交 CPU/GPU → CPU 准备后等待健康检查 → 压测 → 平台回收 | CPU 可在 GPU 尚未获配时准备；需要平台协调多 Job 生命周期 |
| 异构 Job | 一次协调申请 CPU/GPU → 环境准备与服务启动并行 → 压测 → 释放 GPU → CPU 整理结果 | 适合本例统一实验生命周期；必须等两类资源一起满足 |

`run.sh` 可以通过 sbatch 提交，但仅改变提交方式不会自动使子作业并行、建立依赖或关联回收；还可能额外占用一个编排节点。若在批作业内再次提交其他作业，应检查站点是否允许以及继承环境对嵌套提交的影响。

### 示例前提

- Linux Slurm 集群，配置了 cpu 与 gpu 分区和 GPU GRES；支持异构作业。
- 以下文件保存在所有节点可见的共享目录；`/shared/models/Qwen2.5-7B-Instruct` 是模型示例路径。
- GPU 节点已安装 NVIDIA 驱动及 vLLM，模型可在所选单卡运行。实际固定版本、GPU 型号和环境需在 POC 决定。
- CPU 节点安装 Python 3、curl；GPU 节点主机名可解析，CPU 到服务端口 8000 可达。
- 本例未设置 API Key，限于受控内网示范；按实际平台访问模式配置认证。
- 不提供假定实测数据；下面是可改造模板，未在用户集群执行。

### 文件一：experiment.sbatch

CPU 放在第一组件，使批处理脚本与压测留在 CPU，便于单独回收第二组件 GPU。使用无重排队示例避免重入逻辑与结果目录复用造成混淆；生产平台若启用 requeue，应另设计幂等恢复。

```bash
#!/usr/bin/env bash
#SBATCH --job-name=inference-test
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=8G
#SBATCH --exclusive
#SBATCH --no-requeue
#SBATCH --time=01:00:00
#SBATCH --output=experiment-%j.log

#SBATCH hetjob
#SBATCH --partition=gpu
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:1
#SBATCH --exclusive
#SBATCH --mem=0
#SBATCH --no-requeue
#SBATCH --time=01:00:00

set -euo pipefail

export MODEL=/shared/models/Qwen2.5-7B-Instruct
export RUN_DIR="/shared/bench-runs/$SLURM_JOB_ID"
mkdir -p "$RUN_DIR"

gpu_component="${SLURM_JOB_ID}+1"
gpu_released=0
release_gpu() {
  if (( gpu_released == 0 )); then
    if scancel "$gpu_component"; then
      gpu_released=1
    else
      echo "GPU 组件回收请求失败，请检查 $gpu_component" >&2
      return 1
    fi
  fi
}
trap 'release_gpu || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

scontrol show hostnames "$SLURM_JOB_NODELIST_HET_GROUP_1" \
  | awk '{print "http://" $0 ":8000"}' \
  > "$RUN_DIR/endpoints.txt"

# GPU：每台节点启动一个独立副本。
srun --het-group=1 \
  --nodes=2 --ntasks=2 --ntasks-per-node=1 \
  --cpus-per-task=8 --gres=gpu:1 \
  --kill-on-bad-exit=1 --wait=5 \
  --output="$RUN_DIR/server-%t.log" \
  vllm serve "$MODEL" \
    --served-model-name bench-model \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 1 --max-model-len 4096 &
server_launcher=$!

# CPU：与 GPU 启动并行，准备和激活压测环境。
source ./prepare-bench.sh

# CPU：等待两个服务可用，最多 20 分钟。
bash ./wait-ready.sh "$RUN_DIR/endpoints.txt" 1200

# CPU：非流式并发压测，原始结果落盘。
python3 ./benchmark.py

# 提交 GPU 组件回收；srun 因服务被取消而非零退出是预期情况。
release_gpu
wait "$server_launcher" || true

# CPU 可以在 GPU 回收后继续后处理。
python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path(os.environ['RUN_DIR'], 'results.json')
result = json.loads(p.read_text())
print(json.dumps(result['summary'], ensure_ascii=False, indent=2))
PY

# 正常退出批处理脚本，结束余下的 CPU 工作。
```

这里 `scancel` 成功仅代表接受了取消请求，资源回收可能经历 COMPLETING 状态；不能以 `scancel` 返回时间作为实际可再次分配时间。对失败退出的 trap 是常规保护，不承诺在 SIGKILL、节点宕机等情况下执行。Hive 仍应从 Slurm 状态对账并设置整作业时限。[sbatch](https://slurm.schedmd.com/sbatch.html)、[scancel](https://slurm.schedmd.com/scancel.html)

### 文件二：prepare-bench.sh

```bash
# 被 source，在 CPU batch shell 中执行。
# 本例只用标准库，无需联网 pip install。
# 若采用专用压测工具，在这里激活预先构建的环境，例如：
# source /shared/venvs/benchmark/bin/activate
command -v python3 >/dev/null
command -v curl >/dev/null
python3 -c 'import concurrent.futures, json, urllib.request'
echo "Benchmark 环境就绪：$(hostname)"
```

Python 示例不要求 vLLM 安装在 CPU 机器。需要大量依赖时，建议提前构建可复用环境或容器，在 CPU 节点执行必要的缓存、数据和配置准备；这属于平台方案建议。

### 文件三：wait-ready.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
endpoint_file=$1
timeout_seconds=${2:-1200}
mapfile -t urls < "$endpoint_file"
[[ ${#urls[@]} -eq 2 ]] || { echo "需要两个服务地址" >&2; exit 1; }
deadline=$((SECONDS + timeout_seconds))

while (( SECONDS < deadline )); do
  ready=1
  for url in "${urls[@]}"; do
    if ! curl --noproxy '*' -fsS --max-time 3 \
      "$url/health" >/dev/null 2>&1; then
      ready=0
    fi
  done
  if (( ready )); then
    echo "所有服务已就绪"
    exit 0
  fi
  sleep 5
done
echo "服务就绪检查超时" >&2
exit 1
```

若服务提前退出，此精简示例可能等待到 readiness 超时才失败；生产版本应结合服务 Step 状态或失败标记提前退出。健康检查成功到实际请求之间仍可能发生故障，压测客户端必须记录请求错误。

### 文件四：benchmark.py

```python
import concurrent.futures
import json
import os
import pathlib
import time
import urllib.request

run_dir = pathlib.Path(os.environ['RUN_DIR'])
urls = (run_dir / 'endpoints.txt').read_text().splitlines()
assert len(urls) == 2, urls
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

def request(i):
    url = urls[i % 2]
    body = json.dumps({
        'model': 'bench-model',
        'prompt': f'Request {i}: Explain how a GPU works.',
        'max_tokens': 128,
        'temperature': 0,
        'stream': False,
    }).encode()
    req = urllib.request.Request(
        url + '/v1/completions', data=body,
        headers={'Content-Type': 'application/json'},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
        return {
            'url': url, 'ok': True,
            'seconds': time.perf_counter() - started,
            'tokens': result['usage']['completion_tokens'],
        }
    except Exception as exc:
        return {'url': url, 'ok': False, 'error': str(exc)}

started = time.perf_counter()
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
    results = list(pool.map(request, range(400)))
elapsed = time.perf_counter() - started
success = [r for r in results if r['ok']]
summary = {
    'requests': len(results),
    'success': len(success),
    'failed': len(results) - len(success),
    'max_concurrency_total': 32,
    'elapsed_seconds': elapsed,
    'success_requests_per_second': len(success) / elapsed,
    'output_tokens_per_second': sum(r['tokens'] for r in success) / elapsed,
}
(run_dir / 'results.json').write_text(
    json.dumps({'summary': summary, 'requests': results}, indent=2)
)
print(json.dumps(summary, indent=2))
if len(success) != len(results):
    raise SystemExit(1)
```

两个服务合计最多 32 个客户端在途请求，400 个请求交替分配；不是每服务固定 16 或 32 并发。结果是总成功请求吞吐及生成 token 吞吐，不测流式 TTFT/TPOT，输入没有控制为固定 token 长度，输出也可能提前遇到 EOS。客户端、网络、模型预热及前缀缓存均可能影响结果，所以只能用于演示流程，不能直接作为严格性能结论。[vLLM 服务接口与 CLI](https://docs.vllm.ai/en/latest/cli/serve/)

### 提交、检查与回收

在共享目录准备以上四个文件后：

```bash
sbatch experiment.sbatch
squeue -u "$USER"
scontrol show job JOB_ID
sacct -j JOB_ID --format=JobID,JobName,State,ExitCode,Elapsed,AllocTRES
```

查看主日志 `experiment-JOB_ID.log`、结果目录下的 `server-*.log`、`endpoints.txt`、`results.json`。`sacct` 需站点记账配置可用；两组资源的状态应分别确认。运行中的 GPU 组件完成回收后可能记为 CANCELLED，这是主动停止常驻服务的预期状态，不应被 Hive 直接等同于整次实验失败。

### Hive 集成建议与尚未验证事项

Hive 应分别存储实验业务状态、Slurm Job/组件 ID、Step 状态和服务 readiness，而不是只用一个“运行中”字段覆盖所有含义。建议状态为：等待资源、准备环境、等待服务、压测中、回收中、完成/失败/取消。该状态模型属于平台设计建议。

需要 POC 验证的事项：

1. CPU 与 GPU 分区、GRES 和权限配置；异构调度是否启用且能协同获配。
2. 普通命令是否在 CPU 组件执行，GPU srun 是否每节点只启动一份服务。
3. GPU 模型加载与 CPU 环境准备是否重叠执行。
4. 服务失败、环境准备失败、压测失败、用户取消和节点故障时的终态及资源回收。
5. 取消 GPU 组件后 CPU 是否可以继续整理结果；回收经过的状态及耗时。
6. 进程结束、Job Step 结束与 Allocation 结束是否在平台上准确区分。
7. 活跃作业和租约不可因低利用率被重复分配；磁盘、GPU 和心跳指标的新鲜度检查。

上述脚本与流程只进行了文档整理，没有向任何 Slurm 集群提交作业，也没有部署 vLLM 或消耗 GPU 资源。
