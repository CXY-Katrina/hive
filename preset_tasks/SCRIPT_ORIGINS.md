# 预置脚本来源与参数

本清单区分 Hive 编写的适配层、用户提供的环境指导、上游原始文件。公共脚本在仓库的 `preset_tasks/common/` 维护，下发到 `$HIVE_SOURCE_DIR/hive_presets/common/`。它们不是 vLLM-Ascend 已有脚本；执行的 vLLM 服务与 AISBench CLI 仍来自各自安装的源码。模型参数和判定阈值来自附带的 nightly YAML。

## 当前生效文件

| 文件 | 来源 | 用途与位置参数（依次） |
| --- | --- | --- |
| `common/bootstrap-a3.sh` | 根据用户提供的 `start-docker-A3.sh` 配置改写并归档 | 整机 A3 Docker 创建；镜像、容器名。不调用服务器上的个人脚本。 |
| `common/runtime.sh` | Hive 新增 | 加载镜像已有 CANN/ATB 环境；供其他脚本 source，无模型配置。 |
| `common/install-server.sh` | Hive 新增，安装命令参考用户提供的 `script.md` | 服务端依赖目录；读取所选源码内 `.github/vllm-main-verified.commit`，安装匹配 vLLM 和所选 vLLM-Ascend。 |
| `common/verify-server.sh` | Hive 新增 | 服务端依赖目录；检查导入、CLI、环境报告。 |
| `common/install-client.sh` | Hive 新增 | 客户端依赖目录、AISBench commit；安装私有 AISBench checkout/venv，沿用镜像底层依赖。 |
| `common/verify-client.sh` | Hive 新增 | 客户端依赖目录；检查 AISBench CLI 和环境报告。 |
| `common/serve.sh` | Hive 新增 | YAML、用例名、测试类型、IP、PORT、服务端依赖目录、输出目录；读取 YAML 后直接启动原生 vllm serve。 |
| `common/ready.sh` | Hive 新增 | IP、PORT、健康路径（默认 `/health`）、等待秒数（默认 900）；HTTP 检查。 |
| `common/bench.sh` | Hive 新增 | YAML、用例名、测试类型、IP、PORT、客户端依赖目录、输出目录；生成配置后直接运行官方 AISBench CLI。 |
| `common/verify.sh` | Hive 新增 | YAML、用例名、测试类型、客户端依赖目录、输出目录、压测 manifest、压测日志；定位本轮结果并按 YAML 阈值判定。 |
| `common/nightly_cli.py` | Hive 新增的 Python 适配层，非上游原文件 | 解析 YAML、生成命令/配置、定位结果、组织判定，参数由上述 Shell 入口传递。 |
| `qwen3-30b-a3b-w8a8/case.yaml` | vLLM-Ascend 上游原始 YAML 归档 | 上游路径、commit 和 SHA256 见同目录 `source.json`；运行时使用附带 YAML，前端可编辑。 |

实际来源为 `vllm-project/vllm-ascend` 的原始文件是上述 YAML，以及安装时读取的 `.github/vllm-main-verified.commit`；vLLM 的 `build_rust.sh` 来自所选 vLLM checkout。AISBench 请求/数据集模板及 CLI 来自固定 AISBench checkout，既不是 Hive 自制测试引擎，也不是 vLLM-Ascend 的文件。

需要改服务 IP、端口、健康接口、用例、输出目录或上游结果路径时，直接编辑对应步骤的启动命令参数。公共脚本没有内置 Qwen 用例或 job1 依赖。示例任务的 job2 输入指向同一 client_env 中 job1 的目录，这一关系写在任务配置中；跨节点时应先由任务脚本传输结果或使用共享路径。


## 为什么保留一个 Python 适配文件

`nightly_cli.py` 不是上游必须安装的组件，而是当前选择“保留 nightly YAML、移除 pytest 入口、直接调用 vLLM/AISBench”后的公共格式适配。YAML 不是 vllm serve 或 AISBench CLI 可直接读取的配置格式，所以这里读取 server_cmd、生成 AISBench 所需 Python 配置，并按 YAML 中原 baseline/threshold 判定结果。实际推理和压测仍由官方程序完成。原 aisbench_config.py 的纯函数已合并到这个文件，不再需要第二个模块。若以后上游提供独立 CLI，可整体替换这层。

已删除模型目录的全部历史 Shell/Python 封装及仅测试废弃环境/分发器的测试。仍有效的 YAML 适配测试保留并指向 common；本轮未运行测试。
