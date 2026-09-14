# 工作流脚本变量

新任务仅提供下列五类 Hive 环境变量。Shell 直接读取 `$变量名`，Python 使用 `os.environ["变量名"]`。节点和容器的数字编号从 0 开始。

| 变量 | 含义 |
|---|---|
| `HIVE_SOURCE_DIR` | 当前步骤的源码和附件根目录；bootstrap 时是宿主机临时附件目录，安装和作业步骤时是容器内目录 |
| `HIVE_NODE0_IP`、`HIVE_NODE1_IP`、… | 本次申请中各节点的实际 IP；编号与页面 node0、node1 一致 |
| `HIVE_CONTAINER0_NAME`、`HIVE_CONTAINER1_NAME`、… | 本次任务环境中的实际容器名；按环境列表顺序、每个环境内节点编号升序排列，整个任务保持相同映射 |
| `HIVE_TASK_ID` | 任务 ID |
| `HIVE_JOB_ID` | 当前作业执行实例 ID；多节点实例各自不同 |

页面显示每个环境、节点和容器变量的对应关系，不需要猜测哪个编号对应哪个容器。例如，环境 env1、env2 都部署到 node0，则它们分别对应 `HIVE_CONTAINER0_NAME`、`HIVE_CONTAINER1_NAME`；若 env1 同时部署到 node0、node1，env2 只在 node0，则依次对应 container0、container1、container2。

安装和创建容器时还没有作业实例，不应使用 `HIVE_TASK_ID` / `HIVE_JOB_ID`。任务脚本使用节点编号明确访问目标，不再注入当前 host、服务 endpoint、端口、镜像和版本等额外 Hive 环境变量。端口及业务设置写在可编辑脚本或 YAML 中。

```bash
# 作业脚本；APP_PORT 由用户或预置的公共脚本定义。
echo "节点地址：$HIVE_NODE0_IP"
echo "容器名称：$HIVE_CONTAINER0_NAME"
python3 "$HIVE_SOURCE_DIR/client.py" --url "http://$HIVE_NODE0_IP:$APP_PORT"
mkdir -p "/var/tmp/results/$HIVE_TASK_ID/$HIVE_JOB_ID"
```

```python
import os
node0 = os.environ["HIVE_NODE0_IP"]
container0 = os.environ["HIVE_CONTAINER0_NAME"]
source_dir = os.environ["HIVE_SOURCE_DIR"]
```

权重、数据集、镜像和软件包映射继续由公共 Shell 函数提供，不新增环境变量：

```bash
model_path=$(hive_resource model 组织/权重名)
dataset_path=$(hive_resource dataset 组织/数据集名)
python3 "$HIVE_SOURCE_DIR/test.py" --model "$model_path" --dataset "$dataset_path"
```

产物路径可使用 `${HIVE_TASK_ID}`、`${HIVE_JOB_ID}` 或对应裸变量写法，平台在提交时固定实际值。容器释放后，已归档产物仍可下载。

兼容说明：新提交标记 `runtime_variables: "minimal"`；已提交的历史任务保持原变量合同，防止升级打断原脚本。旧 `${source_dir}` 等模板仍接受，但新预置使用上述变量；旧草稿需要重新载入新版预置或主动调整脚本，平台不会静默覆盖用户修改。`ASCEND_RT_VISIBLE_DEVICES` 仍用于 NPU 分配，它属于 Ascend 设备配置，并非额外的 Hive 脚本变量。

实现沿用调度器向进程注入环境变量的方式，未安装或模拟 Slurm：[Slurm sbatch 环境变量说明](https://slurm.schedmd.com/sbatch.html#SECTION_OUTPUT-ENVIRONMENT-VARIABLES)。
