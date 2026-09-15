# 预置任务归档

每个 nightly YAML 对应一个独立目录和公共预置，内部按 job0、job1、job2 编排服务、压测和校验。
当前接入 [Qwen3-30B-A3B-W8A8](qwen3-30b-a3b-w8a8/README.md)。

目录维护 source.json 来源证据、workflow.json 编排、可编辑执行脚本和原 YAML 快照。
公共预置载入时读取当前上游 main 的 YAML；个人副本保留用户编辑。提交记录实际内容与哈希，不修改 vllm-ascend 上游代码。

环境直接展示自包含 bootstrap.sh、各自 install-server.sh / install-client.sh、独立 verify-server.sh / verify-client.sh 和通用 runtime.sh。服务端安装选定 Ascend 源码及 .github 指定的 vLLM，底层 NPU 依赖沿用镜像；环境步骤不读取 nightly 配置。每个作业阶段一个 Shell 文件，Python 负责 YAML 配置与结果验证；新预置不调用历史分发脚本，也不依赖宿主个人启动脚本。
各步骤通过 files 只声明自身需要的附件，服务端不附 AISBench 专用配置代码。helper_files 是归档可引用文件目录，不是每个 job 的全局附件清单。
脚本上传在 hive_presets/<目录名>/，YAML 使用上游原路径；同一路径在同任务内必须保持相同内容。

平台提供 HIVE_SOURCE_DIR、HIVE_NODE{n}_IP、HIVE_CONTAINER{n}_NAME、HIVE_TASK_ID、HIVE_JOB_ID、HIVE_OUTPUT_DIR，以及 hive_resource 查询函数。
预置自己的参数采用 TASK_ 变量。产物统一写入 HIVE_OUTPUT_DIR=/var/tmp/hive/outputs/<task-id>/<job-instance-id>，相关环境报告也先复制到各 job 输出目录。单节点预置从兄弟 job 目录读取同任务结果，默认 retain_minutes 为 0。

镜像和实际容器名显式传给附件 bootstrap.sh，不由角色推测名称。安装脚本可直接编辑；服务端源码和客户端 checkout / venv 均位于容器私有目录。job1 保留原始 outputs，job2 只读 job1 输入并在自己的目录生成校验报告，避免重复归档。
新部署包含本目录，不迁移旧任务。预置可载入不代表性能已验证，资源提交仍遵守平台权限。

## 公共脚本与来源

可复用 Shell/Python 位于 `common/`，该目录不是一个预置任务。每个任务通过 `source.json` 的 `common_files` 列表声明所需脚本，步骤附件保留可编辑源码。来源与逐脚本参数见 [SCRIPT_ORIGINS.md](SCRIPT_ORIGINS.md)。
