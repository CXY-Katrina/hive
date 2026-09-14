# Qwen3-30B-A3B-W8A8 nightly 预置

此目录归档一个 nightly 用例，来源 SHA、YAML 路径、AISBench SHA 和预置人员见 `source.json`。载入默认使用 main，提交时固定执行源码版本。服务器运行所选镜像已有 vLLM / Ascend 二进制，不自动把请求源码覆盖安装到镜像；实际环境版本记录在 environment-report.json。

## 环境准备与用例分离

- `bootstrap.sh`：完整、可编辑的 Docker 创建命令。Hive 先准备并固定镜像 ID，脚本检查镜像后直接 docker run，不调用服务器上的个人脚本。采用 host 网络、host IPC；按存在路径透传 Ascend 设备与只读驱动，挂载 `/mnt` 以保留资源映射路径。CANN、Python 和 vLLM 使用镜像内版本。
- `install-server.sh install` / `verify`：复用服务器镜像软件，检查导入和 vLLM CLI，记录实际模块版本。安装与校验均不读取 nightly YAML 或上游 `.github` 文件。
- `install-client.sh install` / `verify`：从 `aisbench-source` 包映射创建独立固定 SHA 的 AISBench checkout 和 venv，可使用 `python-wheelhouse`；约束已有大包版本，检查 CLI 并记录实际依赖。脚本不读取任何模型或用例配置。
- `image-runtime.sh`：两种环境共用的运行时加载函数，过滤请求 checkout 的 PYTHONPATH，加载镜像内厂商 CANN / ATB 环境。服务端要求 CANN；客户端不以缺少 CANN 脚本为错误。

安装与校验步骤各自展示本环境脚本及公共运行时附件，可直接编辑。默认环境目录分别是 `/opt/hive-env/server` 和 `/opt/hive-env/client`，可通过对应脚本中的普通 TASK_SERVER_DEPS / TASK_CLIENT_DEPS 修改。

Bootstrap 是当前整机 A3 用例的设备配置，部分卡预置应在可见脚本中收窄 devices 列表；它不会接管或删除同名容器。映射路径不在 `/mnt` 时，需在 bootstrap 挂载其目录。宿主仅需 Docker、Ascend 驱动/设备及资源文件；无额外宿主用户脚本依赖。

## 每个作业的脚本

| 作业 | 环境 | 执行内容 | 业务附件 |
| --- | --- | --- | --- |
| job0 | server_env | 生成服务参数、原生 vLLM serve、HTTP 就绪检查 | server-job.sh、case-common.sh、nightly_cli.py、YAML |
| job1 | client_env | 生成 AISBench 配置、调用官方 AISBench CLI | client-job.sh、case-common.sh、nightly_cli.py、aisbench_config.py、YAML |
| job2 | client_env | 定位 job1 输出，校验原 nightly 阈值 | client-job.sh、case-common.sh、nightly_cli.py、aisbench_config.py、YAML |

每个步骤还附带其直接调用的环境脚本和 image-runtime.sh；只在需要解析配置的步骤展示 Python / YAML。server 环境和 job0 不依赖 AISBench 文件。`case-common.sh` 仅共享用例路径、端口、参数读取和环境报告复制，环境安装不会调用它。

服务端和客户端可位于同一台机器不同容器，容器通过所申请节点 IP 通信。此预置为原单节点用例，服务地址取 `$HIVE_NODE0_IP`；修改成分布式用例需同步调整启动命令和原 YAML。

## 参数与输出

- 模型、量化、并行度、数据集、请求长度与阈值：编辑原 YAML。
- 服务端口、用例选择：编辑 case-common.sh 的 TASK_PORT / TASK_CASE / TASK_BENCHMARK。
- 服务启动设置：编辑 server-job.sh；压测与结果校验：编辑 client-job.sh。
- 每个作业输出写入 `$HIVE_OUTPUT_DIR`，实际目录为 `/var/tmp/hive/outputs/<任务ID>/<作业实例ID>`。
- job0 保存 server.json 与环境报告。job1 保存 manifest.json、AISBench 日志、完整 outputs 目录和环境报告。job2 保存结果 JSON/CSV、来源记录和判定报告。
- job2 在同一 client_env 中读取相邻 job1 输出；将作业改为多节点展开时，应同时调整这一显式输入路径。

平台只负责容器、调度、命令和归档。`nightly_cli.py` 与 `aisbench_config.py` 是 Hive 预置适配代码；执行用例来自原 YAML，AISBench 使用官方 CLI。历史 task.sh / nightly_environment.py 不在新预置附件清单中，不再参与新任务。

本次仅编辑前端与脚本，未运行测试、安装或 NPU 任务；不能据此声称自包含 Docker 配置已完成实机验证。
