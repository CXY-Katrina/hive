# 预置任务归档

一个 nightly YAML 对应一个子目录、一个公共预置；服务端、客户端及校验是其内部的 job。
当前只接入 [Qwen3-30B-A3B-W8A8](qwen3-30b-a3b-w8a8/README.md)，其他用例等待确认后接入。

每个目录独立维护：

- `source.json`：`upstream_commit`、`nightly_yaml`、`created_by`、`helper_files`，以及从该提交 `.github/vllm-main-verified.commit` 核验的 `vllm_commit`、名称和筛选标签。
- `workflow.json`：通用资源、环境与 job 配置；使用动态节点/容器参数和资源映射，不固定机器 IP。
- Shell、Python、YAML 接入文件与独立测试、依赖及来源说明。

`helper_files` 列出的文件位于当前任务目录根部。载入时 API 将它们附在第一个环境的第一个安装步骤中；每个容器都获得完整的冻结文件集，路径为 `hive_presets/<目录名>/<文件名>`。启动命令使用 `${source_dir}` 定位上游 checkout；归档脚本放在其中独立目录，不覆盖上游文件。

更新归档通过 Hive 仓库提交，不向 vllm-ascend 提交接入改动。API 读取当前目录内容；更新后刷新并重新载入预置。已提交任务的脚本和结果保持冻结。个人副本重新载入时更新归档脚本、保留参数并提示需要重新验证；未提交的浏览器旧草稿可能因字节校验返回 409，此时重新载入。

新部署从完整 Git checkout 按 README 安装，目录会随代码提供，不迁移旧任务数据。预置可载入不代表性能通过；普通用户的资源提交仍需管理员授权。容器启动器当前使用已约定的 `/mnt/share/c00814587/start-docker-A3.sh`，目标机器需提供该路径或在环境中配置对应的引导启动命令。
