# 预置任务归档

每个 nightly YAML 对应一个独立目录和公共预置，内部按 job0、job1、job2 编排服务、压测和校验。
当前接入 [Qwen3-30B-A3B-W8A8](qwen3-30b-a3b-w8a8/README.md)。

目录维护 source.json 来源证据、workflow.json 编排、可编辑执行脚本和原 YAML 快照。
公共预置载入时读取当前上游 main 的 YAML；个人副本保留用户编辑。提交记录实际内容与哈希，不修改 vllm-ascend 上游代码。

环境直接展示自包含 bootstrap.sh、各自的 install-server.sh / install-client.sh 和通用 image-runtime.sh。环境安装与校验不读取 nightly 配置；业务分别由 server-job.sh / client-job.sh 持有，case-common.sh 共享用例参数。Python 负责 YAML 配置与结果验证；新预置不调用历史 task.sh / nightly_environment.py，也不依赖宿主机个人启动脚本。
各步骤通过 files 只声明自身需要的附件，服务端不附 AISBench 专用配置代码。helper_files 是归档可引用文件目录，不是每个 job 的全局附件清单。
脚本上传在 hive_presets/<目录名>/，YAML 使用上游原路径；同一路径在同任务内必须保持相同内容。

平台提供 HIVE_SOURCE_DIR、HIVE_NODE{n}_IP、HIVE_CONTAINER{n}_NAME、HIVE_TASK_ID、HIVE_JOB_ID、HIVE_OUTPUT_DIR，以及 hive_resource 查询函数。
预置自己的参数采用 TASK_ 变量。产物统一写入 HIVE_OUTPUT_DIR=/var/tmp/hive/outputs/<task-id>/<job-instance-id>，相关环境报告也先复制到各 job 输出目录。单节点预置从兄弟 job 目录读取同任务结果，默认 retain_minutes 为 0。

镜像和实际容器名显式传给已有机器容器启动器 /mnt/share/c00814587/start-docker-A3.sh，不由角色推测名称。安装脚本可直接编辑，保留已验证镜像运行时，客户端只在私有 checkout / venv 准备依赖。
新部署包含本目录，不迁移旧任务。预置可载入不代表性能已验证，资源提交仍遵守平台权限。
