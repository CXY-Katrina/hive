# Qwen3-30B-A3B-W8A8 预置任务接入脚本

本目录归档这一预置任务自己的接入脚本，由 `admin` 创建。执行来源是
[上游原始提交 d4d2957](https://github.com/vllm-project/vllm-ascend/tree/d4d2957e5208c2f464d4625c05920bd29ea233cb)
及其中的 `tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml`。
不依赖外部 PR 新增的 `tools` 文件，也不修改上游源码。固定来源、YAML 哈希和文件清单见
[source.json](source.json)。其他预置任务应使用自己的归档目录。

## 文件与执行边界

| 文件 | 用途 |
|---|---|
| `nightly_environment.py` | 检查上游提交、准备服务端或客户端环境、记录真实运行时和依赖审计 |
| `nightly_cli.py` | 从原 YAML 生成服务命令和 AISBench 配置；定位、归档及校验已存在的结果 |
| `aisbench_config.py` | 本任务独立保存的配置渲染和性能阈值校验函数 |
| `source.json` | 创建者、上游提交、YAML、AISBench 提交与上传前缀 |
| `tests/fixtures/upstream/` | 从固定 Git 对象读取的原始测试夹具；生产执行读取实际上游 checkout |
| `LICENSE` | 渲染与校验代码来源的 Apache-2.0 许可 |

运行时将三个 Python 文件按原字节上传到上游 checkout 内的隔离目录
`hive_presets/qwen3-30b-a3b-w8a8/`，不覆盖上游文件。脚本支持直接通过绝对路径执行和独立导入。
生成的 `verify.sh` 也调用同一归档脚本的绝对路径。

Hive 负责节点、容器、作业依赖、超时、日志和产物。服务作业前台执行 `vllm serve`，客户端作业
前台执行原生 `ais_bench`，最后单独校验结果。配置生成与结果校验不启动服务、请求或
`AisbenchRunner`，不调用 pytest 包装入口。

## 依赖

- 配置生成和结果校验：Python 3.11+、PyYAML；其余为 Python 标准库。
- 环境准备：Python、Bash、Git；采用私有环境安装时还需要 Python `venv`、pip。
- 服务端镜像：已有可导入并运行 CLI 的 vLLM、vLLM Ascend、Torch、Torch NPU，以及已安装的
  CANN/ATB 环境。镜像复用模式不安装、克隆或编译服务端运行时。
- 客户端：固定 AISBench `0da56eadb2ac85c31c2540f4f5b69af3ec5717a5` 的 API 依赖。共享
  AISBench 仓库只作为 Git 对象来源，脚本将其克隆到私有目录，忽略并记录共享工作区修改。
- 可选的 `--install-client-dependencies` 在私有 `venv --system-site-packages` 中补齐依赖，
  复用镜像的 Torch/NumPy/NPU 栈，约束已有受保护包和 Transformers；只在私有环境使用
  OpenCV 4.11.0.86、Pillow 11.2.1。没有该开关时，缺少依赖直接失败。

镜像复用报告明确区分请求的上游提交与实际导入的镜像版本、路径、Git 提交、修改状态和
已跟踪修改的哈希。它不代表上游请求版本已安装，也不宣称完整依赖可解。原镜像包必须保持不变。
客户端只允许一个有证据的新增依赖例外：`vllm 0.28.0+empty` 要求 OpenCV >=4.13.0，而本 HTTP
客户端使用私有 OpenCV 4.11.0.86；客户端不运行 vLLM。报告保留完整例外及原因，其他新增或恶化
的冲突仍会失败。

## 命令约定

下面的 `SOURCE_DIR` 是固定上游 checkout，`RUN_DIR`、`DEP_DIR` 是本次任务自己的绝对目录。
节点 IP、权重、数据集和现有 AISBench 仓库路径由任务资源映射提供，不写死在脚本中。

```bash
SCRIPTS_DIR="$SOURCE_DIR/hive_presets/qwen3-30b-a3b-w8a8"
YAML="$SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml"
CASE=Qwen3-30B-A3B-W8A8-TP1

python "$SCRIPTS_DIR/nightly_environment.py" \
  --source-root "$SOURCE_DIR" \
  --source-commit d4d2957e5208c2f464d4625c05920bd29ea233cb \
  --role server --runtime-mode image-reuse \
  --vllm-sha "$(cat "$SOURCE_DIR/.github/vllm-main-verified.commit")" \
  --dep-dir "$DEP_DIR"
source "$DEP_DIR/activate.sh"
```

客户端在自己的容器中使用 `--role client`，添加 `--benchmark-source "$AISBENCH_REPOSITORY"`；
需要补齐依赖时显式添加 `--install-client-dependencies`。默认 `source-install` 流程仍保留，
本预置的镜像复用流程必须显式指定 `image-reuse`。准备目录必须尚不存在。

服务作业：

```bash
python "$SCRIPTS_DIR/nightly_cli.py" server-command \
  --config "$YAML" --case "$CASE" --benchmark perf \
  --model-path "$MODEL_PATH" --host 0.0.0.0 --port "$SERVER_PORT" \
  --output-dir "$RUN_DIR/server"
cd /var/tmp
exec bash "$RUN_DIR/server/server.sh"
```

服务就绪后，客户端作业：

```bash
python "$SCRIPTS_DIR/nightly_cli.py" prepare \
  --config "$YAML" --case "$CASE" --benchmark perf \
  --benchmark-home "$DEP_DIR/benchmark" \
  --model-path "$MODEL_PATH" --dataset-path "$DATASET_PATH" \
  --host "$SERVER_IP" --port "$SERVER_PORT" --output-dir "$RUN_DIR/client"
cd /var/tmp
set -euo pipefail
bash "$RUN_DIR/client/benchmark.sh" 2>&1 | tee "$RUN_DIR/client/benchmark.log"
```

`benchmark.py` 包含完整的模型、数据集和固定 AISBench 官方性能 summarizer 配置。原 YAML 中
TP=1、180 个请求、并发 45、最大输出 1500、基线 812.3394 和阈值 0.97 保持原样。
GSM8K 单文件映射会按原字节复制到私有 `train.jsonl` / `test.jsonl`，不修改共享数据。

独立结果归档与校验作业：

```bash
python "$SCRIPTS_DIR/nightly_cli.py" locate-results \
  --config "$YAML" --case "$CASE" --benchmark perf \
  --manifest "$RUN_DIR/client/manifest.json" --log "$RUN_DIR/client/benchmark.log" \
  --output-dir "$RUN_DIR/collected"
exec bash "$RUN_DIR/collected/verify.sh"
```

定位只接受本次日志中的唯一结果目录，并限定在本任务私有 `work_dir` 内。原始 JSON/CSV
按字节复制，记录原路径和 SHA256。定位成功不代表 AISBench 进程成功；Hive 必须保留实际退出码。
校验退出码为 0（通过）、1（性能阈值失败）、2（输入或结果无效），`python -O` 同样执行阈值检查。

## 测试与来源

在具备 PyYAML 的独立 Python 环境中，从 Hive 根目录运行：

```bash
python -m unittest discover -s preset_tasks/qwen3-30b-a3b-w8a8/tests -p 'test_*.py'
```

保留原先 25 项 CLI/环境行为测试，并补充归档独立导入、生成校验入口及错误上游提交拒绝检查。
测试不依赖 `data/direct-nightly-pr`，不启动模型服务或 NPU 压测。
`aisbench_config.py` 的渲染与阈值计算来自固定上游 `tools/aisbench.py` 的对应逻辑；本目录保存
本预置自己的接入实现，不要求修改上游仓库。
