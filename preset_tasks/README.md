# 预置任务归档

一个 nightly YAML 对应一个子目录、一个公共预置；服务端、客户端及校验是其内部的 job。
当前只接入 [Qwen3-30B-A3B-W8A8](qwen3-30b-a3b-w8a8/README.md)，其他用例等待确认后接入。

每个目录独立维护：

- `source.json`：`upstream_commit`、`nightly_yaml`、`created_by`、`helper_files`，以及从该提交 `.github/vllm-main-verified.commit` 核验的 `vllm_commit`、名称和筛选标签。
- `workflow.json`：通用资源、环境与 job 配置；使用动态节点/容器参数和资源映射，不固定机器 IP。
- Shell、Python、YAML 接入文件与独立测试、依赖及来源说明。

`helper_files` 列出的文件位于当前任务目录根部，步骤通过 `files` 声明所需附件，API 填充可编辑内容。当前预置只有一个 `task.sh` 按 action 调度，以及三个 Python helper；加上原路径 YAML，共五个静态附件。所有环境和 job 共用同一文件内容，不为每个 action 创建包装脚本。每个容器获得完整冻结文件集，接入脚本位于 `hive_presets/<目录名>/`，启动命令使用 `HIVE_SOURCE_DIR` 定位当前上游 checkout；原 YAML 使用上游原路径，其他脚本不覆盖上游文件。

更新归档通过 Hive 仓库提交，不向 vllm-ascend 提交接入改动。公共预置载入时取得当前 main 的原 YAML；用户可修改 Shell、Python、YAML 并保存个人副本，提交记录实际内容和来源哈希。已提交任务的脚本和结果保持冻结，个人副本保留用户编辑；相同路径在不同步骤中必须使用一致内容。

公开平台变量只有 `HIVE_SOURCE_DIR`、`HIVE_NODE{n}_IP`、`HIVE_CONTAINER{n}_NAME`、`HIVE_TASK_ID`、`HIVE_JOB_ID`，资源映射通过 `hive_resource` 函数查询。预置自己的参数使用普通 `TASK_` 变量；SHA 从 checkout 和 `.github/vllm-main-verified.commit` 读取。job 按 `job1`、`job2`、`job3` 编号，默认保留期为 0。运行时环境恢复逻辑也在可见附件中，不依赖生成的 activate.sh。

新部署从完整 Git checkout 按 README 安装，目录会随代码提供，不迁移旧任务数据。预置可载入不代表性能通过；普通用户的资源提交仍需管理员授权。容器启动器当前使用已约定的 `/mnt/share/c00814587/start-docker-A3.sh`，目标机器需提供该路径或在环境中配置对应的引导启动命令。
