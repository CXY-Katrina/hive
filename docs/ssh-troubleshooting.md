# 节点 SSH 排查

在部署 Hive 的机器上执行，工作目录切到项目目录。以下命令使用同一个虚拟环境和配置文件，不需要把密码写入命令行。

## 读取平台记录的具体错误

```powershell
Set-Location C:\hive
git pull --ff-only
.\.venv\Scripts\python.exe scripts\diagnose-node.py --env-file .env
```

仅查看某台节点（替换实际 IP）：

```powershell
.\.venv\Scripts\python.exe scripts\diagnose-node.py --env-file .env --host 192.168.13.159
```

输出包括节点状态、数据库中保存的 reason、最后采样时间、实际配置与 known_hosts 路径。不输出密码、密钥或连接字符串。若 API/worker 使用其他配置文件，传入相同的 --env-file；进程环境变量仍优先于文件。

## 使用 Hive 实际连接方式验证

```powershell
.\.venv\Scripts\python.exe scripts\diagnose-node.py --env-file .env --host 192.168.13.159 --probe
```

使用数据库中的账号与解密后的密码、Hive 的主机公钥校验和 SSHTransport 执行只读标记命令。远端需要 Bash 和 timeout，与采集通道一致。不会修改节点、数据库、主机公钥文件或提交任务。`ssh_and_bash_ok: true` 表示 SSH 与基础命令通道成功，不代表 npu-smi 和完整采集也成功；失败时输出经过 Hive 处理的错误与退出码。不会输出远端原始 stdout/stderr。

## 对比手工 SSH

```powershell
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password root@192.168.13.159
```

非 22 端口添加 `-p 实际端口`，用户也应与纳管配置一致。Hive 不自动加载个人私钥、SSH Agent 或 ~/.ssh/config 中的跳板机设置。

“查看凭据”能显示正确密码，说明该 API 的密码存储与解密基本正常，但不证明服务器接受密码认证；还需确认 worker 使用与 API 相同的配置、主密钥和数据库。

- 主机公钥未登记或变化：按 README 的 SSH 指纹核验流程处理，不关闭校验。
- 认证失败：检查实际账号、密码认证策略；手工私钥登录成功不能代替此验证。
- 超时或无法连接：在中心机检查 IP、端口和路由。
- SSH/Bash 成功但采集失败：查看保存的 reason，继续定位 npu-smi、Ascend 驱动或采集命令。
- 采样时间持续不更新：确认 worker 已启动且连接同一个数据库。

提供诊断输出即可，不要发送 .env、密码、密钥或完整数据库。不要为排查重新生成 HIVE_SECRET_KEY。


## 已复现：Ascend950DT / npu-smi 26.2.0.b020 表头不兼容

只读检查命令（将 NODE_IP 替换为节点地址，SSH 交互输入密码）：

```powershell
ssh root@NODE_IP "uname -m; npu-smi info -m; npu-smi info"
```

已连接实机并将 `npu-smi info -m` 原始输出传给当前 `hive.hardware.parse_mapping`，复现 `Unsupported device mapping header`。这是设备输出解析失败，不是 SSH 登录失败。

实际映射表头：

```text
NPU ID  Slot ID  Chip ID  Chip Phy-ID  Chip Name
```

当前解析器要求 `NPU ID / Chip ID / Chip Logic ID / 可选 Chip Phy-ID / Chip Name`。该实机没有 `Chip Logic ID`，因此拒绝解析。实机还有以下格式差异：

- 指标表使用 `NPU Util(%)`，而当前解析器要求 `AICore(%)`。
- 指标和进程表以 NPU ID 为设备索引，布局与当前双芯表不同。
- 进程表新增 `Process id in container` 列。

现已适配并在该机器完整采集验证通过：8 个设备、显存、AI Core 和进程清单均正常解析。AI Core 单独通过 `npu-smi info -t usages -i NPU_ID` 的 `Aicore Usage Rate(%)` 获取，`NPU Util(%)` 另存为扩展指标，不替代 AI Core。当前支持实测单芯片、NPU ID 与 Chip Phy-ID 一致的 Ascend950 格式；重映射或不明确的编号仍拒绝解析。未知数据继续保持不可分配，不能当成零利用率。检查仅运行只读命令，没有修改驱动、容器或业务。

### 更新和重启

在 API 和 worker 各自窗口按 Ctrl+C 停止，再执行：

```powershell
Set-Location C:\hive
git pull --ff-only
```

窗口一重启 API（刷新内存中的采集适配器和指标目录）：

```powershell
.\.venv\Scripts\python.exe -m hive.cli api --host 0.0.0.0 --port 18000 --env-file C:\hive\.env
```

窗口二重启 worker（加载新的周期采集逻辑）：

```powershell
.\.venv\Scripts\python.exe -m hive.cli worker --env-file C:\hive\.env
```

本次不需要重启 MySQL、NPU 机器或容器，不需要数据库迁移、重新安装 Python 依赖或重建前端。浏览器刷新，等待约 30–60 秒，再使用本文的诊断脚本查看 reason 和 sampled_at。实际项目路径不同请替换路径，API 和 worker 必须使用同一配置。
