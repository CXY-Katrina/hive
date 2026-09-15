# Qwen3-30B-A3B-W8A8 nightly 预置

一个目录对应一个 nightly YAML。来源 SHA、YAML 路径、AISBench SHA 和预置人员见 source.json；载入默认 main，提交时固定执行源码。

公共脚本现统一在 `../common/`，下发路径为 `hive_presets/common/`；本目录保留用例 YAML、工作流与来源元数据；硬件相关创建脚本是 common/bootstrap-a3.sh。所有脚本来源和参数见 [脚本清单](../SCRIPT_ORIGINS.md)。旧兼容脚本已删除。

## 环境准备

环境安装不读取模型或 nightly 参数，服务端和客户端分别准备。

- common/bootstrap-a3.sh 将用户提供的 start-docker-A3.sh 配置归档为自包含 Docker 命令：host 网络、128 GiB 共享内存、privileged、Ascend 设备及驱动、/home、/data、/tmp、/mnt 等现有路径挂载。Hive 先准备镜像，脚本不调用宿主个人脚本，不删除已有容器。这是整机 A3 预置，部分卡或其他硬件需调整设备配置。
- install-server.sh 参考用户提供的 script.md：读取所选 Ascend checkout 的 .github/vllm-main-verified.commit，将对应 vLLM 安装到容器私有 /opt/hive-env/server/vllm；安装 setuptools-rust、运行 build_rust.sh，然后使用 VLLM_TARGET_DEVICE=empty pip install -e . --no-build-isolation --no-index --no-deps。随后从 $HIVE_SOURCE_DIR 安装所选 vLLM-Ascend。PyTorch、CANN、Triton 等底层依赖沿用镜像。脚本在 checkout 根目录运行，不自动改写上游 setup.py，不复制参考文档中的代理或禁用 TLS 配置。
- verify-server.sh 单独检查导入与 CLI。安装报告记录实际模块位置、版本、两仓库 SHA 和源码差异摘要，不能把镜像旧版本冒充提交的源码。
- install-client.sh 使用 aisbench-source 包映射创建私有固定 SHA checkout 与 venv，可使用 python-wheelhouse；约束已有大包以避免替换 Torch/NPU。verify-client.sh 单独检查 CLI。
- runtime.sh 是唯一共用 Shell helper，加载镜像 CANN/ATB 环境，不屏蔽已安装的源码路径，不处理模型和用例。

安装与校验均展示自己的脚本和 runtime.sh，作业不再附安装脚本。编译工具链及 CANN/PyTorch 兼容性需由镜像满足；本轮没有实际安装或验证。

## 每个作业一个执行入口

| 作业 | 环境 | 主执行 | 就绪检查 |
| --- | --- | --- | --- |
| job0 | server_env | common/serve.sh | common/ready.sh |
| job1 | client_env | common/bench.sh | — |
| job2 | client_env | common/verify.sh | — |

准备已并入执行入口，三个作业的预置前置校验为空。serve.sh 从 YAML 读取参数后直接启动服务；bench.sh 从 YAML 生成配置后调用官方 AISBench；verify.sh 定位压测结果后直接判定。ready.sh 仅等待服务 HTTP 可用，确保压测在服务启动完成后运行。

nightly_cli.py 是唯一 Hive Python 适配层，负责 nightly YAML 与原生 CLI 配置格式之间的转换及阈值判定；实际测试仍由 AISBench 执行，不经过 pytest。原 aisbench_config.py 已合并删除。模型参数编辑 YAML；IP、端口、用例、目录由启动命令参数传入。

## 产物归属

HIVE_OUTPUT_DIR=/var/tmp/hive/outputs/<任务ID>/<作业实例ID>，job1 与 job2 的同名路径不会覆盖。

- job0：server.json、服务端环境报告。
- job1：AISBench 配置、manifest、客户端环境报告和完整 results（AISBench outputs）目录。
- job2：只归档 verification.json 和 result-location.json。同一 client_env 中只读 ../job1/manifest.json、日志及原始结果，在自己的目录复制 JSON/CSV 作为校验输入，不回写 job1，也不再次归档原始结果和环境报告。

此预置对应单节点用例。改为多节点执行时需同步调整服务地址及 job2 显式输入位置。各步骤附件均可前端编辑。全部历史兼容脚本已删除。

本轮仅修改脚本与配置，未运行测试、环境安装或 NPU 任务。
