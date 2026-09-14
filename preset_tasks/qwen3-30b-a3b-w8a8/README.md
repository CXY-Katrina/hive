# Qwen3-30B-A3B-W8A8 预置任务

环境准备直接展示三个可编辑 Shell 文件，不再调用复杂安装封装：

- `bootstrap.sh`：将所选镜像和当前实例容器名传给节点已有的 `start-docker-A3.sh`。
- `install-server.sh`：复用镜像的 vLLM / Ascend / Torch / CANN，检查导入和 vLLM CLI，记录实际版本和源码请求。没有源码编译或运行时重装。
- `install-client.sh`：读取节点 AISBench 源码映射，私有 clone 固定提交，创建 venv，直接运行可见的 pip 安装命令。

`nightly_environment.py` 仅作为历史兼容文件保留，新预置不引用它。
`task.sh` 提供必要公共环境加载以及业务执行 action，安装脚本只 source 其中的公共函数。
每个步骤仅列出自己实际使用的附件，不把所有文件汇总到每个 job。

## Job 与文件

| Job | 作用 | 主要附件 |
|---|---|---|
| job0 | 服务准备、原生 vLLM serve、就绪检查 | task.sh、nightly_cli.py、原 YAML |
| job1 | 准备配置、原生 AISBench 压测 | task.sh、nightly_cli.py、aisbench_config.py、原 YAML |
| job2 | 定位 job1 结果、校验阈值 | task.sh、nightly_cli.py、aisbench_config.py、原 YAML |

job1 等待 job0 ready，job2 等待 job1 succeeded。服务端准备只解析 YAML，不加载 AISBench helper。
`nightly_cli.py` 只有在生成 AISBench 配置或验证性能时才导入 `aisbench_config.py`。

## 来源与可编辑参数

公共预置载入时解析上游 vllm-project/vllm-ascend 的 main 为固定提交，并取该提交的原文件：

`tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml`

任务始终读取本次 HIVE_SOURCE_DIR 中该原路径，可由附件编辑覆盖。取得 main 文件失败时不静默回退，个人副本保留用户修改。
[source.json](source.json) 与 [case.yaml](case.yaml) 保存历史提交 d4d2957e5208c2f464d4625c05920bd29ea233cb 的原始证据。
服务端安装报告从当前 checkout 和 .github/vllm-main-verified.commit 读取请求版本，明确标记实际运行时来自镜像。

| YAML 字段 | 作用 |
|---|---|
| test_cases[].name | case 名称，唯一 case 自动选择 |
| test_cases[].model | model 映射名称、服务模型名称 |
| benchmarks.perf.dataset_path | dataset 映射名称 |
| test_cases[].server_cmd / envs | vLLM 参数 / 环境变量 |
| benchmarks.perf.batch_size / num_prompts | 并发 / 请求数 |
| benchmarks.perf.max_out_len / request_rate | 最大输出长度 / 请求速率 |
| benchmarks.perf.baseline / threshold | 校验基线与阈值 |

修改 TP 时同步调整 job0 的 npu_count。同名附件内容必须一致；多个 case 时设置 TASK_CASE，TASK_BENCHMARK 默认为 perf。
`nightly_cli.py parameters --config ...` 是纯参数读取入口，支持 --format shell 输出安全引用的 TASK_ 赋值。

## 平台输入与输出目录

平台变量为 HIVE_SOURCE_DIR、HIVE_NODE{n}_IP、HIVE_CONTAINER{n}_NAME、HIVE_TASK_ID、HIVE_JOB_ID 和 HIVE_OUTPUT_DIR。
资源路径通过平台保留的 hive_resource 函数查询。服务地址由 HIVE_NODE0_IP 和脚本中的 TASK_PORT（默认 18123）组成；修改端口时同步 job0 的 ports。
bootstrap 的两个位置参数来自镜像和当前容器字段（内部模板 ${image} / ${container_name}），不猜测容器编号。

所有 job 产物都在平台提供的 HIVE_OUTPUT_DIR：

`/var/tmp/hive/outputs/<task-id>/<job-instance-id>`

预置为单节点，实例 id 就是 job0 / job1 / job2。job0 保存 server.json，job1 保存 benchmark.py、manifest.json、aisbench.log 和完整 results 目录，job2 保存定位记录、原始 JSON/CSV 和验证报告。
job2 从 `$(dirname "$HIVE_OUTPUT_DIR")/job1` 读取本次压测数据；各 job 先将所属容器的环境报告复制到自身 HIVE_OUTPUT_DIR/environment-report.json，再归档。
产物配置统一使用 `${HIVE_OUTPUT_DIR}/文件名`，完整 AISBench outputs 对应 `${HIVE_OUTPUT_DIR}/results` 并由平台自动打包。
默认 retain_minutes 为 0，任务结束后由平台正常关闭容器和释放资源。

其他预置设置为普通 TASK_ 变量：TASK_SERVER_DEPS / TASK_CLIENT_DEPS 默认 /opt/hive-nightly-deps/server / client；TASK_CASE_YAML 可指定原 YAML；TASK_CANN_ENV 可指定厂商环境入口；TASK_BOOTSTRAP_SCRIPT 默认 /mnt/share/c00814587/start-docker-A3.sh。
AISBench 提交在 install-client.sh 中显式列出，也可编辑 TASK_AISBENCH_SHA。

## 安装依赖与执行方式

服务端需要镜像已有 Python 3.11+、PyYAML、vLLM、vLLM Ascend、Torch/NPU、CANN/ATB。安装脚本检查这些组件而不推测新版本。
客户端从 aisbench-source 包映射私有 clone 固定 AISBench 0da56eadb2ac85c31c2540f4f5b69af3ec5717a5，不修改共享目录；使用 venv --system-site-packages 复用大包，并将镜像已有 Torch/NPU/NumPy 等版本写入 pip constraints。
实际依赖命令直接可见：安装固定 checkout 的 [api]、PyYAML、OpenCV 4.11.0.86 和 Pillow 11.2.1。后两项是该已接入镜像与固定 AISBench 的已知客户端兼容组合，只覆盖私有 venv，不修改原镜像。
有 python-wheelhouse 包映射时启用本地 wheel / PIP_NO_INDEX，否则沿用现有 pip 源。环境准备还需要 Bash、Git、venv、pip；任务使用 curl、tee。

pip check 输出保存在环境报告中，只作为诊断信息，不宣称完整依赖一致，也不再进行复杂的逐条冲突审计。
唯一外部任务基础设施入口是节点已有的 start-docker-A3.sh；CANN / ATB 环境脚本属于厂商依赖。

所有实际业务命令都在可见附件中。task.sh 从非请求源码目录恢复运行环境，保留镜像合法路径并过滤请求 checkout，不 source 生成的 activate.sh。
服务将生成的 argv 交给原生 vllm serve；客户端调用官方 ais_bench.benchmark.cli.main，前台 pipefail + tee 保留失败退出码。
生成的 server.sh / benchmark.sh / verify.sh 只用于命令说明，不被预置执行；没有 pytest / AisbenchRunner 包装。
配置和校验逻辑保留原 YAML 参数、完整官方 AISBench 配置以及原阈值算法，代码许可见 [LICENSE](LICENSE)。
