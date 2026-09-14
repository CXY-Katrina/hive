# 任务编辑器验收（2026-09-14）

本轮覆盖配置、调度和归档能力。未提交真实 NPU 任务、未拉取真实镜像，也没有将之前性能未达标的记录改为通过。

| 需求 | 实现与验证 |
|---|---|
| 公共预置默认 main | 先解析 main，再取冻结提交的 nightly YAML；任一步失败禁止提交。HTTP 测试验证 main 移动后拒绝过期 SHA。 |
| 复用实际容器、按天保留 | 从已就绪空间选择服务器/容器；默认 3 天。复用更新保留策略，运行中的任务不会被提前释放。 |
| 选择 Quay 镜像 | 公开标签 API 分页、短期缓存；本机镜像优先，缺失只对明确 Quay 标签执行一次有界拉取。SSH 边界测试覆盖失败、重启与取消。 |
| 统一脚本变量 | Shell/Python 使用 HIVE_ 环境变量；多节点 HTTP/SSH 测试验证节点列表、当前编号和服务地址。旧模板仍兼容。 |
| 脚本可见、可编辑 | 每个阶段保留具名附件；同名文件编辑同步，个人副本和浏览器草稿保存修改。创建容器步骤可展开编辑。 |
| 默认产物目标 | 默认覆盖 job 环境全部节点；下拉框按服务器/容器选择，增加节点时默认目标随之扩展。 |
| 完整 AISBench outputs | 保留实际 results 输出树为 tar.gz，并继续保存 JSON/CSV 摘要。测试验证包内容、下载名称、MIME、冲突不覆盖与失败恢复。 |

验证使用真实 HTTP 路由、隔离 MySQL 测试数据库，以及 GitHub/SSH 外部边界模拟；未使用生产数据做写入测试。浏览器测试在独立随机端口拦截 API，另用真实预置内容验证双节点提交配置通过服务端 schema。

主回归组：sources/image_catalog/presets/preset_archive/workflows 共 50 项通过；multinode/variables/directory_artifacts/artifacts/artifact_failures 共 38 项通过，其中 3 项因 Windows 不支持对应 Linux 文件描述符或符号链接测试条件而跳过。镜像和保留期另有专门回归；前端构建、编辑器交互及作业图/节点映射回归通过。Linux 目录快照与 Docker 的真实执行仍需在目标环境验收。

只读联网核验成功：main 解析为 `0979baf25c0ee501ab0e4ef9b88ea7d9a6d29839`，关联 vLLM 为 `a97dacb7106ee49f39f3d1fc6ae1800ff724e01d`，原 nightly YAML 可读取；Quay 首页返回 50 个真实标签且存在后续页。这是验收时观察值，不是代码中的固定版本或假数据。

变量及来源说明见 [工作流变量](workflow-variables.md)，Windows 部署和依赖见 [README](../README.md)。

本机 API 与 worker 已用原配置重启，监听 `0.0.0.0:18000`。线上只读 HTTP 核验确认管理员登录成功、普通用户可载入唯一公共预置、17 个编辑附件、默认3天、main/YAML和Quay标签接口正常。未执行生产资源申请，历史任务和空间记录保留。
