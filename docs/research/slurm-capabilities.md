# Slurm 复用与 NPU 适配：来源核验

本文仅为历史选型调研，不作为当前实施要求。用户已确定不使用 Slurm，采用中心机 Python/Bash + MySQL，经 SSH 管理无常驻 Hive 组件的计算节点。方案以 [当前设计](../design.md) 为准；下文 Slurm 部署、插件、PAM 和依赖建议均不纳入当前方案。

研究日期：2026-09-12。本文是设计依据，不是已完成的实现或硬件验收。官方在线文档会随版本变化；源码核验固定为 SchedMD/slurm `b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5`（master，提交日期 2026-09-11）。落地必须锁定发布版本并复验配置；不能把 master 文档直接当作旧版本手册。[源码快照](https://github.com/SchedMD/slurm/tree/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5)

## 1. 可以复用的边界

**结论：复用通用 GRES、整机 allocation、队列和作业生命周期；NPU 发现、遥测与厂商运行时适配单独实现。** `npu` 可以是通用 GRES 名称，`Type` 可表达型号，`Count` 表示数量，`File` 描述设备文件。配置名字叫 `npu` 不会自动产生 Ascend 驱动、环境变量或 AI Core 计费能力。[GRES 配置](https://slurm.schedmd.com/gres.conf.html)

已核验的源码依据：`src/interfaces/gres.c` 为没有专用插件的资源维护 `np_gres_devices`，`gres_g_get_devices()` 可读取这些设备；因此“没有 gres/npu 插件”不等于“完全不能管理通用 NPU GRES”。但是这不证明某一 Ascend 型号的全部设备访问路径已正确隔离。[通用 GRES 设备处理](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/interfaces/gres.c#L8962)

可供设计使用的配置形态如下，设备文件和型号必须由现场盘点生成，不能照抄成生产配置：

```ini
# slurm.conf：概念示例
GresTypes=npu
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
NodeName=npu001 NodeAddr=192.0.2.11 Gres=npu:ascend_model_x:8

# gres.conf：单节点概念示例
AutoDetect=off
Name=npu Type=ascend_model_x File=/dev/davinci[0-7]
```

`File` 数量和 Count 必须一致；配置文件缺少实际设备时不能当成健康可分配。若一期只配置 Count，应明确只是数量调度，不能声称按卡强隔离。共享控制设备、多芯片卡、一张卡对应多个设备文件的关系，需要针对机器实测建模；`MultipleFiles` 文档主要服务 MIG，不能未经验证就承诺适用于所有 Ascend 设备。[GRES 字段定义](https://slurm.schedmd.com/gres.conf.html)

上游当前 AutoDetect 包含 NVIDIA/NVML、AMD RSMI、Intel oneAPI、AWS NRT 等路径；核验快照没有 Ascend/DCMI 后端。`nrt` 是 AWS Trainium/Inferentia，不是华为 NPU 适配。`gpu/generic` 的使用率、能耗、硬件初始化函数基本为空实现，不能拿它替代遥测适配。[GPU 后端选择源码](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/interfaces/gpu.c#L213)、[NRT 源码](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/plugins/gpu/nrt/gpu_nrt.c)、[generic 源码](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/plugins/gpu/generic/gpu_generic.c)

## 2. 调试机器占位：job allocation 优于普通 reservation

推荐由后端提交轻量 holder batch job，仅保持资源 allocation；用户业务仍通过 SSH 自行执行。这样复用 Slurm 的资源互斥、排队、节点选择、JobID 和生命周期，后续任务执行可以共用申请描述，只切换为真实 batch 脚本。不要要求 Web 请求持续维持一个 `salloc` 客户端进程。[sbatch 生命周期](https://slurm.schedmd.com/sbatch.html)

候选提交参数：`--nodes=N --exclusive --mem=0 --gres=npu:型号:每机卡数 --time=0 --no-requeue`。`--exclusive` 分配整节点 CPU/GRES，但全部内存还需 `--mem=0`；GRES 若设置了 `explicit` flag 则仍需显式申请。节点数 N 与每机 NPU 数量必须在 API 中分开表达。[sbatch 参数](https://slurm.schedmd.com/sbatch.html)、[GRES explicit](https://slurm.schedmd.com/gres.conf.html)

分区必须禁止 oversubscribe；不能只相信提交侧 `--exclusive`。debug 分区采用 `OverSubscribe=NO`、禁用会取消/挂起调试作业的抢占策略，并复核共享节点的其他分区。当前 master 文档使用 `Exclusive=node` 表达分区整机独占；旧版常见 `OverSubscribe=EXCLUSIVE`，实施时按锁定版本生成。[分区配置](https://slurm.schedmd.com/slurm.conf.html)

普通 reservation 表示允许某些用户在某段时间使用指定资源，并不是已运行的作业或 SSH 进程容器；它有独立结束时间、重叠规则和预约内作业处理语义。适合维护、预约未来时间窗口；普通机器申请用 holder job 更容易和未来任务模式共用生命周期。该取舍是基于官方机制的设计推断。[Reservation Guide](https://slurm.schedmd.com/reservations.html)

### 30 分钟保护不应成为 Slurm 的自动杀作业期限

`sbatch --time=0` 的官方语义是无时间限制；debug 分区 `MaxTime=UNLIMITED`，并检查 QOS/association 是否另有限制，包括可能因累计额度耗尽杀作业的 `GrpTRESMins`、`MaxTRESMinsPerJob`，以及影响无限作业准入的 `GrpTRESRunMins`。业务 `protected_until` 从实际交付成功时起计 30 分钟，由 MySQL 记录；到点只是允许评估空闲，不自动结束 allocation。**这一分离是设计选择，不是 Slurm 自带 AI Core 空闲回收功能。**[时间参数](https://slurm.schedmd.com/sbatch.html)、[资源限制](https://slurm.schedmd.com/resource_limits.html)

可替代方案是不断延长有限 TimeLimit，但增加正在运行作业的期限需要特权；秒会向上取整到分钟。特权主体能够突破分区 MaxTime 更新期限，不代表业务公平性、未来 reservation 或 QOS 条件一定允许这样做；必须读取更新结果。该方案还有控制面故障漏续期风险，不适合默认保护未由 Slurm 启动的进程。[scontrol TimeLimit](https://slurm.schedmd.com/scontrol.html)

无限 holder 的代价是无法提供可靠的调试结束预测，影响 backfill 对未来空闲时间的判断；也必须有独立孤儿 allocation 对账。不能把业务的 30 分钟保护期作为队列承诺的最晚空闲时间。[调度器说明](https://slurm.schedmd.com/quickstart_admin.html)

## 3. 释放、Epilog 与 SSH 的关键边界

Epilog 在 allocation 释放时对每个节点执行，默认以 root 身份运行；不同于只在启动 step 时触发的默认 Prolog。Epilog 非零返回会使节点 DRAIN，TaskEpilog/EpilogSlurmctld 的失败没有同样保证。Epilog 应短小，不应内部调用 `scontrol`、`squeue` 等 Slurm 命令；失败直接非零返回。[Prolog/Epilog 执行与失败语义](https://slurm.schedmd.com/prolog_epilog.html)

当前官方文档明确：Epilog 运行时节点处于 COMPLETING，不能接受新 allocation；Epilog 超时会 DRAIN。因此可将“关闭旧租约登录权限、终止确定属于旧租约的进程、核验 NPU/会话/设备恢复、形成清理回执”放入受控 Epilog，失败保留不可分配状态。未知 PID、采集失败、设备异常不能以成功返回掩盖。必须注入 `scancel`、holder 崩溃、slurmd 重启、节点掉电等故障验证；仅凭文档不能宣称所有异常路径都验收完成。[Epilog 与 COMPLETING](https://slurm.schedmd.com/slurm.conf.html)

必须区分两个现实部署模式：

| 模式 | 可以保证 | 不能保证 / 前提 |
| --- | --- | --- |
| 个人 Linux 账号、统一 UID/GID、受限 sudo、PAM adoption | 新 SSH 会话可加入该用户 allocation 的 extern step；作业资源限制与清理可覆盖被接管进程 | 需 `PrologFlags=contain`、task/cgroup、proctrack/cgroup；PAM 失败策略需 deny；验证 pam_systemd 冲突和容器路径 |
| 向用户发放共享 root 或可提权至 root 的密码 | Slurm 仍可防止正常调度重复分配整机，平台可记录申请和观察设备 | pam_slurm_adopt 明确无条件允许 root；旧 root 可重新登录、修改配置或创建持久进程，不能宣称强隔离、可信进程归属或彻底自动清理 |

以上 PAM 前提来自官方文档；root 放行也在源码 UID==0 分支中明确存在。[pam_slurm_adopt](https://slurm.schedmd.com/pam_slurm_adopt.html)、[root 放行源码](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/contribs/pam_slurm_adopt/pam_slurm_adopt.c#L685)

设计推断：只改密码不能结束旧 SSH 会话，也不能撤回旧用户加入的 SSH 公钥或 root 权限形成的其他访问路径。共享 root 模式应显式标注可信用户、弱隔离；若无法可靠撤销访问并核验清理，应保留 DRAIN 待管理员确认。不能将“无 AI Core 活动”推断为“已撤销访问权限”。

`ConstrainDevices=yes` 基于 GRES 设备约束作业 cgroup；默认不是 yes。它不自动接管此前已存在或从其他路径运行的进程，也不是任意 root 的安全边界。Ascend 必须验证按卡设备文件及共享控制设备的访问行为。[cgroup 配置](https://slurm.schedmd.com/cgroup.conf.html)

## 4. GPU 能力迁移矩阵

| 能力 | 可复用部分 | NPU 所需增量 / 不能自动等价的部分 |
| --- | --- | --- |
| 型号、数量、独占和队列 | GRES Type/Count、cons_tres、allocation | 现场盘点 NPU 型号、卡/芯片数量口径；一期整机最易验证 |
| 自动发现、健康与热变更 | 节点注册和 DRAIN | 独立 npu-smi/DCMI adapter；对比配置；卡数变更先下线；不是 AutoDetect=nvml 改名 |
| 单卡设备隔离 | 通用 GRES File、task/cgroup | 映射设备文件、共享管理设备、容器设备路径；需真机跨卡访问测试 |
| CPU/NUMA 与设备亲和 | GRES Cores 和部分通用 TRES binding 框架 | `Cores` 要符合 Slurm socket 拓扑；NPU 拓扑需探测；task 到 Ascend 运行时卡号映射需验证 |
| `--gpus*` / `--cpus-per-gpu` / `--mem-per-gpu` | 上层可统一 ResourceSpec | 这些名称有 GPU 专用语义；NPU API 转换为显式 CPU/内存与 `--gres=npu:...`，不要假定新增 `--npus` 开关 |
| 可见设备环境变量 | wrapper、TaskProlog 或插件扩展点 | 上游环境设置是 CUDA/ROCR/ZE/OpenCL；Ascend 变量和物理/逻辑 ID 映射由 NPU adapter 明确生成 |
| NPU 申请数量与使用时长 | 配置 `AccountingStorageTRES=gres/npu` 后用 Slurm 记录已分配资源 | “已申请 NPU 秒”不等于“AI Core 忙碌秒”，两者分别统计 |
| 显存与利用率 accounting | Slurm 通用作业记录、资源配额 | 现有 `gres/gpumem`、`gres/gpuutil` 采集绑定受支持 GPU 后端；Ascend 遥测独立入 MySQL，不能假装 Slurm 已有 `npuutil` 采集器 |
| 功率、能量 | 可借鉴 energy 插件接口 | 现有 GPU energy 经 `gpu_g_energy_read` 调厂商后端；NPU 功率/能量需适配。采样积分是估算，缺测段不当零值 |
| 调频、功率限制 | 可借鉴 step hardware init/fini 生命周期 | `--gpu-freq` 不会控制 Ascend；需厂商 API、权限、频率恢复和失败策略，列为后续能力 |
| NVIDIA MIG | 资源实例可类比 GRES | MIG 为 NVIDIA 能力，Slurm 本身也不动态切 MIG；Ascend vNPU 分割/销毁/隔离不能声称等价，另立验收项 |
| CUDA MPS | 共享资源记账思路 | MPS 依赖 CUDA 服务和变量，不是 NPU 功能；不能移植名称充当实现 |
| Sharding | 软件计数共享思路 | 上游 shard 依赖 gpu 共享关系且不提供硬隔离；NPU 超售/时间片应后续单独设计，初期不启用 |
| 错误、恢复、重置 | DRAIN、Prolog/Epilog | NPU reset/故障恢复由 adapter 处理；不能在其他用户任务运行时全卡重置 |

矩阵的调度及 GPU 专用界线来源：[GRES Guide](https://slurm.schedmd.com/gres.html)、[TRES Guide](https://slurm.schedmd.com/tres.html)。环境变量证据：[gres_common_gpu_set_env](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/plugins/gres/common/gres_common.c#L258)。共享 GRES 依赖关系：[gres.c](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/interfaces/gres.c)。能量接口：[acct_gather_energy_gpu.c](https://github.com/SchedMD/slurm/blob/b64d6ad129f0fadaaf3b9c2ea746b93fb7f20de5/src/plugins/acct_gather_energy/gpu/acct_gather_energy_gpu.c)。

## 5. 未来任务执行与提交入口

未来 PR/e2e/nightly/weekly 通过同一 ResourceSpec 生成真实 batch job，复用队列、JobID、依赖、取消、退出码和日志；构建环境、取代码、测试和产物上传属于 Python/Bash 任务 runner。Slurm 不会自动搬运所有用户文件；工作目录、代码和产物传输需明确。[sbatch](https://slurm.schedmd.com/sbatch.html)

不能只通过隐藏 `sbatch` 命令防绕过。分区 AllowGroups/AllowAccounts 解决“谁可用某分区”，但无法区分同一用户由前端提交或直接提交。controller 端 job_submit/job_modify 插件能看到真实 submit_uid/modify_uid，可验证代理身份并约束字段；Comment 中的 request_id 不是认证凭证。Lua 方案增加 Lua 库依赖，C 插件增加构建维护成本。[Job Submit Plugin API](https://slurm.schedmd.com/job_submit_plugins.html)

若全部 holder 属于平台服务 UID，个人 SSH UID 不匹配，不能同时承诺 PAM 自动收养为个人用户 allocation。若要求个人 UID 与 Slurm accounting 一致，应受控代理提交为相应用户，并由服务端提交插件验证代理身份；在共享 root 模式下，root 本身仍是受信任管理员，不能承诺上述门禁抵抗 root。

## 6. 依赖建议及验证表

| 组件 | 用途 | 建议 |
| --- | --- | --- |
| Linux、systemd、OpenSSH、统一 UID/GID、时间同步 | Slurm 节点、账号和 SSH | 基础系统依赖；UID/GID 不一致会破坏归属 |
| slurmctld、每节点 slurmd、Slurm CLI | 调度、执行、查询 | 必选；用 Python subprocess 的参数数组调用 CLI，不需要 Python Slurm binding |
| MUNGE / munged / 共享 munge.key | Slurm 组件认证 | 推荐默认；auth/slurm 可替代但引入 jwt 库，不能写成认证零依赖 |
| slurmdbd + MySQL client 开发/运行库 | Slurm 作业历史与 TRES accounting | 使用 MySQL InnoDB；数据库由 slurmdbd 管理 |
| MySQL Server | Slurm accounting 与应用数据 | 可同实例不同 schema、独立账号；应用不得直接写 Slurm 内部表 |
| hwloc、cgroup 支持；cgroup v2 所需 libbpf、D-Bus | CPU/内存/设备限制 | 随选定发行版包核验；不是仅安装 Python 即拥有隔离 |
| PAM + pam_slurm_adopt | 个人账号 SSH 接管 | 按所选访问模式启用并实测 |
| Ascend 驱动、npu-smi/DCMI、按任务所需 CANN | NPU 发现、遥测、任务运行 | 厂商本机依赖；具体版本由真实硬件兼容矩阵确定 |
| Python + MySQL driver、Bash | API、租约策略、轮询、runner | 应用最小实现；不强制 Redis、Celery、Prometheus、InfluxDB |
| slurmrestd、HTTP parser/llhttp、json-c、可选 JWT/YAML | Slurm REST | 一期可省略；直接 CLI 足够，不为前端额外部署该守护进程 |
| Lua 或 C job_submit 插件 | 个人 UID 代理且禁止直接提交 | 严格提交门禁模式才增加，必须计入依赖而非隐藏 |

依赖和认证依据：[Slurm 安装与构建依赖](https://slurm.schedmd.com/quickstart_admin.html)、[Accounting / MySQL](https://slurm.schedmd.com/accounting.html)、[REST 本地认证与 JWT](https://slurm.schedmd.com/rest.html)。同一 MySQL 实例分 schema、CLI adapter 和不引入队列中间件是本项目建议。

尚未进行真机验证，实施前必须验证以下闭环：通用 npu GRES 注册和型号筛选；整机申请并发不重复；全部卡和内存 allocation；holder 异常退出后的 Epilog 顺序及 DRAIN；个人 UID 的 SSH adoption；旧会话/旧凭据关闭；cgroup 跨卡访问；NPU 采集失败不当空闲；控制面停机后的无限 holder 保留与恢复对账；共享分区/抢占/QOS 不破坏独占；上游发布版本配置兼容性。
