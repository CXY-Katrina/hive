# Qwen3-30B-A3B-W8A8 可编辑预置任务

唯一 Shell 入口是 [task.sh](task.sh)，按 action 执行各阶段，不为每个 action 创建包装文件。
静态执行代码为 `task.sh`、`nightly_environment.py`、`nightly_cli.py`、`aisbench_config.py`；加上原路径 YAML，共五个可编辑附件。每个步骤的 `files` 声明实际依赖，提交时平台记录真实上传内容及哈希，不需要修改上游代码。

```bash
bash "$HIVE_SOURCE_DIR/hive_presets/qwen3-30b-a3b-w8a8/task.sh" serve-prepare
```

| 环节 | action | 行为 |
|---|---|---|
| 创建环境 | `bootstrap IMAGE CONTAINER` | 镜像与当前实际容器名显式传入，不猜测容器编号 |
| 安装依赖 | `install-server` / `install-client` | 核验镜像运行时、补客户端私有依赖 |
| 环境核验 | `verify-env server` / `verify-env client` | 展示真实环境报告 |
| job1 | `serve-prepare` / `serve` / `ready` | 生成参数、执行原生 vLLM、检查就绪 |
| job2 | `bench-prepare` / `bench` | 生成完整配置、执行原生 AISBench |
| job3 | `verify-prepare` / `verify` | 定位本次结果、校验原 YAML 阈值 |

job2 等待 job1 ready；job3 等待 job2 succeeded。编号与名称均为 `job1`、`job2`、`job3`。
`retain_minutes` 默认 **0**，所有任务结束后由平台正常关闭容器、归还资源。

## 来源与测试参数

载入公共预置时解析上游 `vllm-project/vllm-ascend` 的 **main** 为固定提交，获取该提交的原文件：

`tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml`

执行始终读取本次 `HIVE_SOURCE_DIR` 下的原路径，可由用户编辑附件覆盖。同名附件内容必须一致。
获取 main 文件失败时不能静默回退；个人副本保留用户修改。[source.json](source.json) 与 [case.yaml](case.yaml) 只保存最初 `d4d2957e5208c2f464d4625c05920bd29ea233cb` 的历史证据。
安装从当前 `git rev-parse HEAD` 和 `.github/vllm-main-verified.commit` 读取 Ascend / vLLM 版本，不依赖平台 SHA 变量。

| YAML 字段 | 实际作用 |
|---|---|
| `test_cases[].name` | case 名称；只有一个 case 时自动选择 |
| `test_cases[].model` | 节点 model 映射名称、服务模型名称 |
| `test_cases[].benchmarks.perf.dataset_path` | 节点 dataset 映射名称 |
| `test_cases[].server_cmd` | 原生服务 TP、模型长度等参数 |
| `test_cases[].envs` | 服务环境变量 |
| `benchmarks.perf.batch_size` / `num_prompts` | 并发数 / 请求数 |
| `benchmarks.perf.max_out_len` / `request_rate` | 最大输出长度 / 请求速率 |
| `benchmarks.perf.baseline` / `threshold` | 校验基线与阈值 |

修改 TP 时同步调整 job1 的 `npu_count`。多个 case 时指定 `TASK_CASE`；`TASK_BENCHMARK` 默认 `perf`。
纯读取入口可展示当前参数，`--format shell` 只输出四个安全引用的 `TASK_` 赋值：

```bash
python3 "$HIVE_SOURCE_DIR/hive_presets/qwen3-30b-a3b-w8a8/nightly_cli.py" parameters \
  --config "$HIVE_SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml"
```

## 平台输入与预置配置

平台公开变量仅为 `HIVE_SOURCE_DIR`、`HIVE_NODE{n}_IP`、`HIVE_CONTAINER{n}_NAME`、`HIVE_TASK_ID`、`HIVE_JOB_ID`。
此 single_node 预置服务地址使用 `HIVE_NODE0_IP` 加脚本内的 `TASK_PORT`，不依赖跨 job 地址变量。
bootstrap 的两个位置参数来自镜像字段与当前容器字段，内部模板为 `${image}` / `${container_name}`，并非额外公开环境变量；多节点展开时也传入当前真实名称，不按 server/client 猜测编号。

平台保留 `hive_resource` Shell 函数。模型、数据集以及 `aisbench-source`、可选 `python-wheelhouse` 包路径均通过该函数查询，未硬编码某台机器的实际资源路径。
其他设置都属于此预置，可在 `task.sh` 配置区或环境配置中编辑：

| 设置 | 默认值 / 说明 |
|---|---|
| `TASK_PORT` | `18123`；修改时同步 job1 的 ports，所有步骤使用相同值 |
| `TASK_CASE` / `TASK_BENCHMARK` | 自动选择唯一 case / perf |
| `TASK_CASE_YAML` | 当前 checkout 中上述原 YAML 路径 |
| `TASK_SERVER_DEPS` / `TASK_CLIENT_DEPS` | `/opt/hive-nightly-deps/server` / client |
| `TASK_OUTPUT_DIR` | `/var/tmp/hive-nightly/$HIVE_TASK_ID`；修改时同步产物路径 |
| `TASK_BOOTSTRAP_SCRIPT` | `/mnt/share/c00814587/start-docker-A3.sh` |
| `TASK_CANN_ENV` | 可选 CANN 环境脚本，默认查找标准安装路径 |

`load_runtime` 完整代码在 task.sh 附件中：从非源码目录加载 CANN / ATB，再设置固定客户端 venv 和 AISBench 路径。
它不 source 生成的 `activate.sh`，也不使用生成的 CLI 包装文件。
唯一外部任务基础设施入口是节点上已有的 `start-docker-A3.sh`，其调用和参数在 bootstrap 附件中可见；标准 CANN / ATB 环境脚本属于已有厂商运行时依赖。

服务 JSON 的 argv 经静态可见的执行逻辑交给原生 `vllm serve`；客户端 manifest 的 argv 直接交给官方 `ais_bench.benchmark.cli.main:main`。
两者前台运行，执行 cwd 不在待验证源码目录。`set -euo pipefail` 保留 AISBench 退出码，tee 成功不会掩盖失败。
配置工具生成的 server.sh / benchmark.sh / verify.sh 只作为命令说明，本预置不执行它们；验证直接调用可见 helper。
没有 AisbenchRunner、pytest 或其他隐式启动入口。

## 依赖与实际版本

- 配置生成、参数读取和验证需要 Python 3.11+、PyYAML，其余使用标准库。Shell 使用 Bash、Git、curl、tee。
- 环境准备需要 Git；补客户端依赖需要 venv、pip。
- 服务端复用镜像的 vLLM、vLLM Ascend、Torch/NPU、CANN/ATB，不安装、克隆或编译服务端运行时。报告区分请求源码与实际镜像版本、导入路径、Git 修改状态及哈希。
- 客户端从 aisbench-source 映射私有克隆固定 AISBench `0da56eadb2ac85c31c2540f4f5b69af3ec5717a5`，不修改共享源码。
- 客户端用 `venv --system-site-packages` 补 API 依赖，复用原镜像 Torch/NumPy/NPU，约束受保护包和 Transformers。仅私有环境采用 OpenCV 4.11.0.86、Pillow 11.2.1；原镜像不变。
- 存在 python-wheelhouse 映射时使用该目录并启用 PIP_NO_INDEX，否则使用现有 pip 源。

原镜像已有冲突保存在报告中。客户端只允许已确认的一项新增例外：vllm 0.28.0+empty 要求 OpenCV >=4.13.0，而 HTTP 客户端使用私有 OpenCV 4.11.0.86；客户端不执行 vLLM。
其他新增或恶化冲突仍失败，不宣称完整依赖可解或请求的上游版本已实际安装。

## 产物与测试

job2 归档完整 `/var/tmp/hive-nightly/${HIVE_TASK_ID}/client/results`，标签为 **AISBench outputs（完整结果目录）**，由平台打包原生时间戳结果树。
配置、manifest、环境报告、原始 JSON/CSV、结果定位记录和阈值报告继续保留。
locate-results 只接受本次日志中的唯一路径，限定任务私有目录，并记录原字节与哈希。
定位成功不代表压测成功；验证退出码为 0（通过）、1（阈值失败）、2（输入无效），python -O 同样执行阈值判断。

在具备 PyYAML 的独立环境中，从 Hive 根目录运行：

```bash
python -m unittest discover -s preset_tasks/qwen3-30b-a3b-w8a8/tests -p 'test_*.py'
```

测试覆盖配置与阈值、镜像复用、YAML 编辑、统一附件、bootstrap 当前名称原样传递、原生失败码经过日志管道仍保留。不依赖 data/direct-nightly-pr，不启动 NPU 压测。
aisbench_config.py 的渲染与阈值逻辑源自历史上游 tools/aisbench.py，许可见 [LICENSE](LICENSE)。
