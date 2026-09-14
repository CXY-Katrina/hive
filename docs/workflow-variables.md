# 工作流脚本变量

Hive 在运行脚本时注入环境变量，Shell 可以直接读取，Python 可以通过 `os.environ` 读取。
这种方式与调度系统向作业传递运行上下文的思路一致：Slurm 的 `sbatch` 会设置作业环境变量，
`srun` 也提供节点和任务上下文。Hive 使用自己的 `HIVE_` 命名和执行模型，不模拟 `SLURM_*`
变量，也不要求安装 Slurm。[Slurm sbatch 环境变量说明](https://slurm.schedmd.com/sbatch.html#SECTION_OUTPUT-ENVIRONMENT-VARIABLES)、
[Slurm srun 说明](https://slurm.schedmd.com/srun.html)

## 节点、容器与来源

| 变量 | 含义 |
|---|---|
| `HIVE_NODE0_IP`、`HIVE_NODE1_IP`、… | 当前运行空间中 `node0`、`node1` 等逻辑节点实际绑定的 IP；只有已分配的节点才有对应值 |
| `HIVE_NODEID` | 当前逻辑节点的数字编号，例如 `node0` 为 `0`、`node1` 为 `1` |
| `HIVE_JOB_NODELIST` | 运行空间已绑定节点的 IP，按节点编号排序、逗号分隔，例如 `192.0.2.10,192.0.2.11` |
| `HIVE_JOB_NUM_NODES` | 上述资源分配中的节点数量；不是进程数或 NPU 卡数 |
| `HIVE_HOST_IP` | 当前脚本实际执行节点的 IP |
| `HIVE_CONTAINER_NAME` | 当前环境实例的容器名称 |
| `HIVE_SOURCE_DIR` | 当前阶段的源码或上传文件根目录，使用绝对路径 |
| `HIVE_IMAGE` | 当前环境解析后的镜像身份；创建容器时使用该值，不另写死某台机器的镜像名称 |
| `HIVE_ASCEND_SHA` | 本次解析并冻结的上游 vLLM Ascend 提交 |
| `HIVE_VLLM_SHA` | 本次来源声明的 vLLM 提交锚点 |

节点列表描述整个运行空间的资源分配。某个 job 可以只选择其中一部分节点；变量不会因此把
剩余节点重新编号。`HIVE_NODEID` 也不是分布式进程的 rank，需要多个进程时由用户的启动脚本
配置进程数量及 rank。

环境安装和 job 阶段的 `HIVE_SOURCE_DIR` 指向各自固定的 checkout。创建容器的 bootstrap
阶段，该路径是平台暂存的上传文件根目录，此时不要假定已经存在 Git checkout。
复用镜像运行时，`HIVE_ASCEND_SHA` / `HIVE_VLLM_SHA` 仍表示请求的来源版本；镜像内真正导入
的版本由环境审计报告记录，不能仅凭这两个变量宣称镜像运行时已升级。

## 任务、实例与端口

| 变量 | 含义 |
|---|---|
| `HIVE_TASK_ID` | 本次提交的工作流任务 ID |
| `HIVE_JOB_ID` | 当前 job 的实际执行实例 ID；多节点展开后每个实例不同，不一定等于表单中的逻辑 job 名称 |
| `HIVE_PORT` | 当前服务 job 已配置端口列表中的首个端口 |
| `HIVE_ENDPOINT` | 当前服务 job 对应的 `http://<实际节点IP>:<端口>` 地址 |

任务和 job 变量用于 job 执行阶段；不要在创建、安装环境时依赖某个尚未执行的 job ID。
需要给同一任务的多个实例隔离输出时，同时使用任务 ID 和实际实例 ID：

```bash
set -euo pipefail
output_dir="/var/tmp/my-task/$HIVE_TASK_ID/$HIVE_JOB_ID"
mkdir -p -- "$output_dir"
printf '%s\n' "$HIVE_HOST_IP" > "$output_dir/node.txt"
```

只有服务 job 已填写 `ports` 配置时，平台才据此提供端口及 endpoint。该地址不代表服务已经
就绪，消费者仍应配置对服务 job 的 `ready` 依赖。多个端口时，这组变量使用首个端口。

如果端口由用户脚本自行决定，就由用户配置显式传给服务和客户端；Hive 不解析脚本来猜测端口，
也不会根据某个任意变量自动生成 endpoint。例如，在两个环境中明确设置同一个 `APP_PORT`：

```bash
# 服务脚本
exec python3 server.py --host "$HIVE_HOST_IP" --port "$APP_PORT"

# 客户端脚本：主机来自资源分配，端口来自用户配置。
python3 client.py --url "http://$HIVE_NODE0_IP:$APP_PORT"
```

## 跨 job 的服务地址

已配置端口的服务实例可通过如下变量访问：

| 示例变量 | 指向 |
|---|---|
| `HIVE_JOB_SERVE_NODE0_HOST` | 逻辑 job `serve` 在 `node0` 上的实际主机 IP |
| `HIVE_JOB_SERVE_NODE0_PORT` | 该服务实例的首个配置端口 |
| `HIVE_JOB_SERVE_NODE0_ENDPOINT` | 该服务实例的完整 HTTP 地址 |

通用形式为 `HIVE_JOB_<规范化逻辑job名>_<NODE编号>_<HOST/PORT/ENDPOINT>`。job 名称转成大写，
非字母数字字符转换成下划线，例如 `serve-api` 对应 `SERVE_API`。多节点服务要显式选择实例，
不要把逻辑 job 名称当成唯一服务地址。

消费者先声明 `serve` 的 `ready` 依赖，然后直接读取变量：

```bash
curl --fail --silent --show-error "$HIVE_JOB_SERVE_NODE0_ENDPOINT/health"
python3 client.py --host "$HIVE_JOB_SERVE_NODE0_HOST" --port "$HIVE_JOB_SERVE_NODE0_PORT"
```

未配置服务端口或尚未有可用服务上下文时，不应依赖这些地址变量。需要它们的脚本可以用
`${HIVE_JOB_SERVE_NODE0_ENDPOINT:?服务地址未就绪}` 明确报错。

## JSON 上下文与资源映射

`HIVE_NODES_JSON` 提供与节点列表对应的结构化信息：

```json
[
  {"node_alias": "node0", "host": "192.0.2.10", "ip": "192.0.2.10"},
  {"node_alias": "node1", "host": "192.0.2.11", "ip": "192.0.2.11"}
]
```

`HIVE_RESOURCE_MAP_JSON` 是当前节点在运行空间绑定时冻结的资源映射。模型、数据集、镜像、
依赖包各用一个字典，名称映射到该节点的实际路径或镜像：

```json
{
  "model": {"team/model-name": "/mnt/models/model-name"},
  "dataset": {"team/data-name": "/mnt/datasets/data-name"},
  "image": {"runtime-image": "local-image:tag"},
  "package": {"aisbench-source": "/mnt/tools/benchmark"}
}
```

Shell 使用平台注入的 `hive_resource` 函数查找，不需要自行解析 JSON：

```bash
model_path="$(hive_resource model team/model-name)"
dataset_path="$(hive_resource dataset team/data-name)"
python3 run.py --model "$model_path" --dataset "$dataset_path"
```

映射不存在时该函数返回非零并给出错误。映射按节点区分；同一个逻辑名称可以在不同节点对应
不同路径。正在运行或复用中的空间保留绑定时的快照，修改节点映射不会静默改变其已绑定路径。

Python 使用标准库读取同一上下文：

```python
import json
import os

nodes = json.loads(os.environ["HIVE_NODES_JSON"])
resources = json.loads(os.environ["HIVE_RESOURCE_MAP_JSON"])
node_id = int(os.environ["HIVE_NODEID"])
model_path = resources["model"]["team/model-name"]
print(os.environ["HIVE_TASK_ID"], os.environ["HIVE_JOB_ID"], nodes[node_id]["ip"], model_path)
```

## 附件、产物路径与兼容性

Shell 附件使用 `$HIVE_HOST_IP` 或 `${HIVE_HOST_IP}`；Python 附件使用 `os.environ`。
这些是运行时环境变量，脚本不需要依赖 Hive 对代码文本做替换。调用脚本时对路径加引号：

```bash
bash "$HIVE_SOURCE_DIR/hive_presets/example/run.sh"
```

产物配置不是 Shell 脚本，由平台支持的占位符展开任务和实际 job 实例 ID，例如：

```json
{"path": "/var/tmp/my-task/${HIVE_TASK_ID}/${HIVE_JOB_ID}/results", "kind": "auto"}
```

路径必须与脚本实际写入的位置一致；修改变量写法不应改变目录布局。历史模板中的
`${task_id}` / `${job_id}` 及已有 `${host}`、`${node0.ip}`、`${serve.host}` 等命令参数写法
继续兼容，新模板和文档统一推荐 `HIVE_` 变量。
