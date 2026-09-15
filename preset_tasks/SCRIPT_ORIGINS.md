# 预置脚本来源与参数

本清单区分 Hive 编写的适配层、用户提供的环境指导、上游原始文件。公共脚本在仓库的 `preset_tasks/common/` 维护，下发到 `$HIVE_SOURCE_DIR/hive_presets/common/`。它们不是 vLLM-Ascend 已有脚本；执行的 vLLM 服务与 AISBench CLI 仍来自各自安装的源码。模型参数和判定阈值来自附带的 nightly YAML。

## 当前生效文件

| 文件 | 来源 | 用途与位置参数（依次） |
| --- | --- | --- |
| `qwen3-30b-a3b-w8a8/bootstrap.sh` | 根据用户提供的 `start-docker-A3.sh` 配置改写并归档 | 整机 A3 Docker 创建；镜像、容器名。不调用服务器上的个人脚本。 |
| `common/runtime.sh` | Hive 新增 | 加载镜像已有 CANN/ATB 环境；供其他脚本 source，无模型配置。 |
| `common/install-server.sh` | Hive 新增，安装命令参考用户提供的 `script.md` | 服务端依赖目录；读取所选源码内 `.github/vllm-main-verified.commit`，安装匹配 vLLM 和所选 vLLM-Ascend。 |
| `common/verify-server.sh` | Hive 新增 | 服务端依赖目录；检查导入、CLI、环境报告。 |
| `common/install-client.sh` | Hive 新增 | 客户端依赖目录、AISBench commit；安装私有 AISBench checkout/venv，沿用镜像底层依赖。 |
| `common/verify-client.sh` | Hive 新增 | 客户端依赖目录；检查 AISBench CLI 和环境报告。 |
| `common/serve-prepare.sh` | Hive 新增 | YAML、用例名、测试类型、IP、PORT、服务端依赖目录、输出目录；检查端口并生成服务参数。 |
| `common/serve.sh` | Hive 新增 | 服务命令 JSON 路径；执行原生 `vllm serve`。 |
| `common/ready.sh` | Hive 新增 | IP、PORT、健康路径（默认 `/health`）、等待秒数（默认 900）；HTTP 检查。 |
| `common/bench-prepare.sh` | Hive 新增 | YAML、用例名、测试类型、IP、PORT、客户端依赖目录、输出目录；生成 AISBench 配置。 |
| `common/bench.sh` | Hive 新增 | 客户端依赖目录、manifest 路径、日志路径；调用官方 AISBench CLI。 |
| `common/verify-prepare.sh` | Hive 新增 | YAML、用例名、测试类型、客户端依赖目录、输出目录、压测 manifest 路径、压测日志路径；查找结果。 |
| `common/verify.sh` | Hive 新增 | YAML、用例名、测试类型、客户端依赖目录、输出目录；按 YAML 阈值生成判定报告。 |
| `common/nightly_cli.py` | Hive 新增的 Python 适配层，非上游原文件 | 解析 YAML、生成命令/配置、定位结果、组织判定，参数由上述 Shell 入口传递。 |
| `common/aisbench_config.py` | Hive 新增的配置/结果适配层，非上游原文件 | 渲染 AISBench 配置并按 nightly 阈值判断结果。 |
| `qwen3-30b-a3b-w8a8/case.yaml` | vLLM-Ascend 上游原始 YAML 归档 | 上游路径、commit 和 SHA256 见同目录 `source.json`；运行时使用附带 YAML，前端可编辑。 |

实际来源为 `vllm-project/vllm-ascend` 的原始文件是上述 YAML，以及安装时读取的 `.github/vllm-main-verified.commit`；vLLM 的 `build_rust.sh` 来自所选 vLLM checkout。AISBench 请求/数据集模板及 CLI 来自固定 AISBench checkout，既不是 Hive 自制测试引擎，也不是 vLLM-Ascend 的文件。

需要改服务 IP、端口、健康接口、用例、输出目录或上游结果路径时，直接编辑对应步骤的启动命令参数。公共脚本没有内置 Qwen 用例或 job1 依赖。示例任务的 job2 输入指向同一 client_env 中 job1 的目录，这一关系写在任务配置中；跨节点时应先由任务脚本传输结果或使用共享路径。

## 历史兼容文件

`qwen3-30b-a3b-w8a8/` 下仍保留历史版本的 `runtime.sh`、`install-server.sh`、`verify-server.sh`、`install-client.sh`、`verify-client.sh`、`serve-prepare.sh`、`serve.sh`、`ready.sh`、`bench-prepare.sh`、`bench.sh`、`verify-prepare.sh`、`verify.sh`、`nightly_cli.py`、`aisbench_config.py`。来源与对应公共版本一致；新预置的 manifest 不再引用这些旧路径。

此外，`task.sh`、`server-job.sh`、`client-job.sh`、`case-common.sh`、`image-runtime.sh`、`nightly_environment.py` 均是 Hive 新增的旧版封装，保留用于历史引用，当前预置不下发也不执行。`nightly_environment.py` 不是 vLLM-Ascend 已有脚本。

`tests/` 下 Python 文件是 Hive 的适配层测试代码，不下发、不属于任务脚本。本轮没有运行测试或实机任务。
