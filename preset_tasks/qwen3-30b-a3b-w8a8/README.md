# Qwen3-30B-A3B-W8A8 可编辑预置任务

每个阶段的命令都放在本目录的具名脚本中，并通过 `step.files` 展示为可编辑附件。
`launch` 只负责调用附件，例如：

```bash
bash "$HIVE_SOURCE_DIR/hive_presets/qwen3-30b-a3b-w8a8/serve_prepare.sh"
```

所有附件（Shell、Python、YAML）均可在载入后的任务或个人副本中修改。每次提交由平台捕获实际
上传内容和哈希，保留本次执行依据；不需要修改上游仓库或创建外部 PR。

## 默认来源和 YAML 附件

载入公共预置时，先解析上游 `vllm-project/vllm-ascend` 的 **main** 为固定提交，再取得该提交的：

`tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml`

前端将本次取得的原始 YAML 填入同名附件。获取失败时不应提交，也不能静默使用历史快照。
个人副本保留用户编辑过的 YAML。执行脚本始终读取本次 `HIVE_SOURCE_DIR` 下这个原路径，支持
上传文件覆盖；不读取本目录的 `case.yaml` 作为执行回退。

[source.json](source.json) 和 [case.yaml](case.yaml) 保存最初上游
`d4d2957e5208c2f464d4625c05920bd29ea233cb` 的来源证据与原始字节。`input_files` 将原上游
路径映射到该历史快照，`default_ref: main` 声明公共预置默认来源；历史 SHA 不代表新执行的 SHA。
环境准备使用平台提供的 `HIVE_ASCEND_SHA` / `HIVE_VLLM_SHA` 校验本次 checkout。

## 修改测试参数

在服务准备、AISBench 准备或校验准备步骤中打开原 YAML 附件。同名附件必须使用一致内容。
编辑单个 `test_cases` 条目即可；以下字段直接参与执行，不是展示标签：

| YAML 字段 | 实际作用 |
|---|---|
| `test_cases[].name` | case 名称；只有一个 case 时自动选择 |
| `test_cases[].model` | 查找节点上的 `model` 资源映射，并作为服务模型名称 |
| `test_cases[].benchmarks.perf.dataset_path` | 查找节点上的 `dataset` 资源映射 |
| `test_cases[].server_cmd` | 原生 `vllm serve` 参数列表，可改 TP、最大模型长度、批处理 tokens、最大序列数等 |
| `test_cases[].envs` | 服务环境变量，保留 YAML 中的原值 |
| `test_cases[].benchmarks.perf.batch_size` | AISBench 并发数 |
| `test_cases[].benchmarks.perf.num_prompts` | 实际请求数 |
| `test_cases[].benchmarks.perf.max_out_len` | 每个请求最大输出长度 |
| `test_cases[].benchmarks.perf.request_rate` | 请求发送速率 |
| `test_cases[].benchmarks.perf.baseline` / `threshold` | 结果校验使用的基线与阈值 |

更改 TP 时，也应调整服务 job 的 `npu_count`，保证分配卡数足够。模型和数据集名称需要存在于
所选节点的资源映射。多个 case 时设置 `HIVE_CASE` 明确选择；默认单 case 无需硬编码名称。
`HIVE_BENCHMARK` 默认为 `perf`。可通过纯读取入口查看 YAML 生效参数：

```bash
python3 "$HIVE_SOURCE_DIR/hive_presets/qwen3-30b-a3b-w8a8/nightly_cli.py" parameters \
  --config "$HIVE_SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml"
```

该入口还支持 `--format shell`，只输出四个固定名称、经安全引用的变量赋值。
`common.sh` 先检查读取命令成功，再加载这些赋值。没有历史默认参数回退。

## 可见脚本与调用关系

| 阶段 | 主要附件 |
|---|---|
| 创建两个环境 | `bootstrap.sh` |
| 服务端安装 / 核验 | `install_server.sh`、`verify_server_environment.sh` |
| 客户端安装 / 核验 | `install_client.sh`、`verify_client_environment.sh` |
| 服务准备 / 执行 / 就绪 | `serve_prepare.sh`、`serve.sh`、`serve_ready.sh` |
| AISBench 准备 / 执行 | `aisbench_prepare.sh`、`aisbench.sh` |
| 结果定位 / 校验 | `verify_prepare.sh`、`verify.sh` |
| 共用路径与 YAML 读取 | `common.sh` |
| 环境准备、配置生成、阈值逻辑 | `nightly_environment.py`、`nightly_cli.py`、`aisbench_config.py` |

服务作业最终前台执行原生 `vllm serve`；客户端前台执行原生 `ais_bench`。流水线通过
`set -euo pipefail` 保留 AISBench 失败退出码，即使 `tee` 写日志成功也不会掩盖失败。
配置生成不启动服务、压测或 `AisbenchRunner`，不调用 pytest 包装入口。

## 平台变量与本任务设置

脚本统一使用平台提供的变量，不混用 `${host}`、`${node0.ip}` 或 `${serve.host}` 模板：

- `HIVE_SOURCE_DIR`、`HIVE_ASCEND_SHA`、`HIVE_VLLM_SHA`：本次固定源码和版本。
- `HIVE_TASK_ID`、`HIVE_JOB_ID`：实际任务和 job。
- `HIVE_HOST_IP`、`HIVE_CONTAINER_NAME`、`HIVE_IMAGE`：当前节点、容器和解析后的镜像。
- `HIVE_PORT`：服务 job 的已分配端口。
- `HIVE_JOB_SERVE_NODE0_HOST`、`HIVE_JOB_SERVE_NODE0_PORT`、`HIVE_JOB_SERVE_NODE0_ENDPOINT`：
  node0 上 `serve` job 的实际服务地址；客户端不写死节点 IP。

本任务还支持可编辑设置：`HIVE_CASE`、`HIVE_BENCHMARK`、`HIVE_CASE_YAML`、
`HIVE_BOOTSTRAP_SCRIPT`。默认启动脚本路径为 `/mnt/share/c00814587/start-docker-A3.sh`，可在
环境变量或 `bootstrap.sh` 附件中修改。服务端/客户端依赖目录默认分别为
`/opt/hive-nightly-deps/server`、`/opt/hive-nightly-deps/client`，输出默认
`/var/tmp/hive-nightly/$HIVE_TASK_ID`；若更改这些路径，也应同步修改产物路径。

## 环境依赖和实际版本

- 配置生成、参数读取和校验需要 Python 3.11+、PyYAML，其余是标准库。
- 环境准备需要 Python、Bash、Git；补客户端依赖时需要 `venv`、pip。
- 服务端复用镜像中已安装的 vLLM、vLLM Ascend、Torch/NPU 和 CANN/ATB，不安装、克隆或编译
  服务端运行时。报告区分请求的源码版本与实际镜像版本、路径、Git 修改状态和哈希。
- 客户端从节点 `aisbench-source` 包映射读取 Git 对象，私有克隆固定 AISBench
  `0da56eadb2ac85c31c2540f4f5b69af3ec5717a5`；不修改共享工作区。
- 客户端显式在私有 `venv --system-site-packages` 补齐 AISBench API 依赖，复用镜像已有的
  Torch/NumPy/NPU 栈，约束现有受保护包和 Transformers。仅私有环境采用 OpenCV 4.11.0.86、
  Pillow 11.2.1，原镜像包保持不变。
- 若存在 `python-wheelhouse` 包映射，使用该目录并启用 `PIP_NO_INDEX`；否则按现有 pip 源安装。

原镜像已有冲突保留在报告中。客户端只允许已确认的一项新增例外：`vllm 0.28.0+empty` 要求
OpenCV >=4.13.0，而该 HTTP 客户端使用私有 OpenCV 4.11.0.86；客户端不执行 vLLM。
其他新增或恶化冲突仍会失败，不宣称完整依赖可解或上游请求版本已实际安装。

## 产物与复用

AISBench job 归档完整目录 `/var/tmp/hive-nightly/${HIVE_TASK_ID}/client/results`，标签为
**AISBench outputs（完整结果目录）**。平台自动识别目录并打包，包含原生 `--work-dir` 下的
时间戳结果树。原来的 benchmark 配置、输入 manifest、环境报告以及 JSON/CSV、结果定位记录、
阈值判定 JSON 继续保留。

`locate-results` 只接受本次日志中的唯一路径，并限定在本任务私有结果目录；保留原字节和哈希。
定位成功不代表压测进程成功。校验退出码为 0（通过）、1（阈值失败）、2（输入或结果无效），
`python -O` 也执行阈值检查。预置环境保留时间默认为 4320 分钟（3 天），资源和容器回收由平台管理。

## 测试与代码来源

在具备 PyYAML 的独立环境中，从 Hive 根目录运行：

```bash
python -m unittest discover -s preset_tasks/qwen3-30b-a3b-w8a8/tests -p 'test_*.py'
```

测试覆盖原有配置/校验/镜像复用行为、YAML 编辑确实改变执行参数、具名脚本与产物引用，以及
原生失败码经过前台日志管道仍保留。测试不依赖 `data/direct-nightly-pr`，不启动 NPU 压测。
`aisbench_config.py` 的渲染和阈值逻辑源自历史固定上游 `tools/aisbench.py`，许可见 [LICENSE](LICENSE)。
