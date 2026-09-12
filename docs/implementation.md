# 首版实现与验收记录

2026-09-12。设计 v6 已进入实现；本文件描述当前代码，设计文档仍保留目标规格。未向真实 NPU 节点安装组件或下发业务任务。

界面更新：任务中心暂时隐藏，保留执行模块与接口；当前入口为登录、实时信息、集群管理、机器申请。视觉改为浅色赛博朋克风格，中文微软雅黑、英文/数字 Consolas，保留桌面与移动端布局。

## 已实现的链路

| 能力 | 当前实现 |
|---|---|
| 用户名登录 | 首次登记、稳定 user_id、24 小时会话、退出、服务端确定申请归属；不需要密码 |
| 节点管理 | 纳管、维护、SSH 凭据加密/受控查看、代际/型号、卡映射、管理员确认驱动空闲显存基线 |
| 全节点实时信息 | 按卡指标、实际 PID/Docker 容器、平台登记与外部占用分离、筛选分页、缺测曲线 |
| 申请 | 同一 ResourceService 支持整机/部分卡、多节点一次性预占、幂等、FIFO、等待期限、互联和共享盘约束 |
| 交付 | 探测前后重采；事务检查占用、数量、维护、分配版本、boot_id/卡映射/配置版本，探测中发生变化须重新验证 |
| 调试回收 | 交付起保护 30 分钟，申请全卡连续 10 分钟 AI Core=0 后可清理；缺测、PID 身份不明、跨卡进程冲突保留资源 |
| 任务 | 前端提交 Bash、环境变量、工作目录、时限，全部节点准备后 SSH 启动，日志、取消、状态恢复、清理后释放 |
| 纳管检测 | A2/A3 有方向全卡互联和 TLS 检查、共享挂载发现与跨节点写读校验 |
| 数据记录 | 原始指标、PID/容器变化事件、申请区间、回收证据、任务状态和审计 |
| 统计接口 | 核心 AI Core/HBM 分钟/小时聚合、有效覆盖率、个人卡小时、按同机区间并集去重的集群机器小时 |

API 和 worker 共用 Python 包，实际运行两个进程。worker 为采集与控制使用独立 SSH 连接池；探测、交付预检查和按申请的任务/清理各有有界执行池。同一申请的任务推进与清理串行，不同申请可以并行。MySQL 会话锁阻止两个正常 worker 同时运行；失去选主连接时停止接受操作并关闭 SSH 连接。远端启动不明的任务不自动重跑。

浏览器界面不含模拟节点。浏览器验收使用的示例节点仅通过测试拦截 GET 响应展示，未写入平台库。

## 模块位置与扩展

| 模块 | 文件 | 扩展方式 |
|---|---|---|
| 会话 | backend/hive/identity.py | 后续更换认证来源仍返回 Actor；申请和任务继续使用稳定 user_id |
| 台账 | backend/hive/inventory.py | 节点字段、机型信息、凭据和采集数据之间保持明确接口 |
| 资源 | backend/hive/resources.py | 新调用方使用 create/get/devices/release；不能自己修改卡归属 |
| 硬件/传输 | backend/hive/domain.py、hardware.py、ssh.py | 实现 HardwareAdapter 并在 services.py 注册；控制和采集各注册一个共享实现的实例 |
| 指标 | backend/hive/metrics.py、telemetry.py | 注册 Metric 定义，适配器填 extensions；通用前端从指标目录生成扩展列 |
| 回收 | backend/hive/policy.py、cleanup.py | 纯决策函数与远端动作隔离；新硬件必须显式提供对应决策，不能套用 AI Core |
| 检测 | backend/hive/probes.py | 纳管与申请前复验共用；厂商命令由适配器提供 |
| 任务 | backend/hive/execution.py、scripts/task_*.sh | 前端与后续 Trigger 共用 submit；节点只运行该任务期间的 Bash 包装 |
| 统计 | backend/hive/reporting.py | 核心数值按时间积分，分钟再汇总小时，不平均平均数 |
| 前端 | frontend/src/components、pages | 公共会话/请求、资源规格表单、卡分配展示；页面不复制分配或释放规则 |

vendor/device_kind/generation 用于资源匹配，不把所有硬件固定为 A2/A3/A5 枚举；首版 UI 仅开放已提供的 Ascend 选项。新适配器须返回完整设备与进程观测；未支持或缺测不会被当作空闲。

扩展指标目前支持注册校验、最新值、原始 JSON 记录和通用展示；MetricCatalog 提供聚合算法。持久分钟/小时表目前仅保存 AI Core/HBM 核心指标；其他指标的长期聚合须接入 Reporting，不能把尚未保存的扩展历史显示为存在。

## 验证方式与边界

已执行真实 MySQL 集成、纯规则和假传输接口测试，覆盖用户名并发登记、幂等、卡锁争用、整机/部分卡冲突、多机生命周期、日志权限、取消和失联恢复。MySQL 测试自行创建随机 hive_test_* 数据库并销毁，与预览数据库分离。

本轮共验证 82 个通过的测试场景，另有 4 个 Linux 协议测试在 Windows 跳过；前端生产构建、桌面/移动端浏览器流程和 Python 依赖一致性检查通过。最后新增的调度轮询回归确认：前四个长任务不会使后续任务和归还操作一直得不到处理。

前端已做 TypeScript 检查、生产构建和真实 API 浏览器流程，覆盖桌面/移动端、空集群、申请/取消、任务/日志/取消、刷新和退出。硬件解析测试是合成的厂商格式样本，不代表真实 A2/A3/A5 驱动已经验收。

Linux 本地任务协议测试在 tests/test_task_scripts.py，验证重复启动、关闭标记、后台进程超时与日志上限；当前 Windows 环境不具备 Linux /proc、setsid、flock，测试明确跳过。Bash 脚本已做语法及静态检查。

实机纳管时还需核验：

- 实际 npu-smi 的完整设备、显存单位、PID 和 Docker cgroup 输出。
- A3 的 hccn 工具编号：在节点详情登记 hccn_ids；不从有歧义的物理卡号猜测。A5 互联返回未支持，普通单机能力不因此假装失败。
- 真实 SSH 断开后任务继续运行，超时及取消能够清理完整业务进程，MPI/容器启动模板遵守前台执行约定。
- 节点上的 hostname/key、现有工具和共享盘读写条件。平台不会自动停防火墙、改 TLS 或安装驱动。

## 尚未实现的设计项

- PR webhook、环境模板构建、e2e/JUnit、nightly/weekly：按设计保留为后续。
- 任意任务产物文件收集/下载、独立构建阶段时限及专用多机 HCCL 模板：当前提供脚本执行、分配清单和日志；脚本自行使用已有环境。
- 第 9.4 节的完整统计报表，包括各阶段分位数、分配率与故障率等；当前接口提供上表所列基础统计。
- 动态编辑机型模板、完整主机 CPU/内存约束、OS SSH 账号创建/删除：当前管理用于连接的账号和密码，不批量修改系统账号。
- 审计/申请/远端任务目录的长期归档清理：当前保守保留。核心原始指标保留 24 小时、分钟 30 天、小时 365 天，进程变化 7 天；删除原始数据前须已完成聚合。日志每节点任务最多 20 MiB。

## 实现依据

锁定读取采用 [MySQL InnoDB 官方文档](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html) 中的事务行锁机制；SSH 使用 [Paramiko Client](https://docs.paramiko.org/en/stable/api/client.html) 的主机密钥校验与命令接口。具体分配/恢复协议由本项目实现。

测试入口参考 [FastAPI Testing](https://fastapi.tiangolo.com/tutorial/testing/)，前端构建使用 [Vite 官方指南](https://vite.dev/guide/)。Ascend/toolkit 的具体探测依据见 [已有调研](research/node-connectivity-and-containers.md)。
