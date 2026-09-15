# Qwen3-30B-A3B-W8A8 nightly 预置

一个目录对应一个 nightly YAML。来源 SHA、YAML 路径、AISBench SHA 和预置人员见 source.json；载入默认 main，提交时固定执行源码。

公共脚本现统一在 `../common/`，下发路径为 `hive_presets/common/`；本目录只保留硬件相关 bootstrap、用例 YAML、工作流与来源元数据。所有脚本来源和参数见 [脚本清单](../SCRIPT_ORIGINS.md)。同目录旧脚本仅为历史兼容保留。

## 环境准备

环境安装不读取模型或 nightly 参数，服务端和客户端分别准备。

- bootstrap.sh 将用户提供的 start-docker-A3.sh 配置归档为自包含 Docker 命令：host 网络、128 GiB 共享内存、privileged、Ascend 设备及驱动、/home、/data、/tmp、/mnt 等现有路径挂载。Hive 先准备镜像，脚本不调用宿主个人脚本，不删除已有容器。这是整机 A3 预置，部分卡或其他硬件需调整设备配置。
- install-server.sh 参考用户提供的 script.md：读取所选 Ascend checkout 的 .github/vllm-main-verified.commit，将对应 vLLM 安装到容器私有 /opt/hive-env/server/vllm；安装 setuptools-rust、运行 build_rust.sh，然后使用 VLLM_TARGET_DEVICE=empty pip install -e . --no-build-isolation --no-index --no-deps。随后从 $HIVE_SOURCE_DIR 安装所选 vLLM-Ascend。PyTorch、CANN、Triton 等底层依赖沿用镜像。脚本在 checkout 根目录运行，不自动改写上游 setup.py，不复制参考文档中的代理或禁用 TLS 配置。
- verify-server.sh 单独检查导入与 CLI。安装报告记录实际模块位置、版本、两仓库 SHA 和源码差异摘要，不能把镜像旧版本冒充提交的源码。
- install-client.sh 使用 aisbench-source 包映射创建私有固定 SHA checkout 与 venv，可使用 python-wheelhouse；约束已有大包以避免替换 Torch/NPU。verify-client.sh 单独检查 CLI。
- runtime.sh 是唯一共用 Shell helper，加载镜像 CANN/ATB 环境，不屏蔽已安装的源码路径，不处理模型和用例。

安装与校验均展示自己的脚本和 runtime.sh，作业不再附安装脚本。编译工具链及 CANN/PyTorch 兼容性需由镜像满足；本轮没有实际安装或验证。

## 每个阶段一个入口文件

| 作业 | 环境 | 前检查 | 主执行 | 就绪检查 |
| --- | --- | --- | --- | --- |
| job0 | server_env | serve-prepare.sh | serve.sh | ready.sh |
| job1 | client_env | bench-prepare.sh | bench.sh | — |
| job2 | client_env | verify-prepare.sh | verify.sh | — |

每个文件直接执行该阶段，不再通过长脚本的命令参数分发。服务准备读取 YAML 并生成原生 vLLM 命令；压测准备生成 AISBench 配置，压测调用官方 CLI。涉及 YAML 的阶段带 nightly_cli.py 与 YAML，仅 AISBench 配置生成和阈值判定需要 aisbench_config.py。服务启动仅附自身与 runtime.sh，就绪检查只有 ready.sh。

nightly_cli.py 和 aisbench_config.py 是 Hive 预置适配代码。模型、并行度、数据集、请求长度与阈值编辑原 YAML；端口、IP、用例选择、依赖目录及产物/输入目录在每一步启动命令中显式传参，公共脚本不内置 Qwen 名称或 job1 路径。

## 产物归属

HIVE_OUTPUT_DIR=/var/tmp/hive/outputs/<任务ID>/<作业实例ID>，job1 与 job2 的同名路径不会覆盖。

- job0：server.json、服务端环境报告。
- job1：AISBench 配置、manifest、客户端环境报告和完整 results（AISBench outputs）目录。
- job2：只归档 verification.json 和 result-location.json。同一 client_env 中只读 ../job1/manifest.json、日志及原始结果，在自己的目录复制 JSON/CSV 作为校验输入，不回写 job1，也不再次归档原始结果和环境报告。

此预置对应单节点用例。改为多节点执行时需同步调整服务地址及 job2 显式输入位置。各步骤附件均可前端编辑。历史 task.sh、server-job.sh、client-job.sh、case-common.sh、image-runtime.sh、nightly_environment.py 不在新预置附件清单中。

本轮仅修改脚本与配置，未运行测试、环境安装或 NPU 任务。
