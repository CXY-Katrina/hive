# Hive

轻量 NPU 集群管理与 SSH 任务执行平台。React + Python/Bash + MySQL；计算节点使用现有 SSH 与 Ascend 环境，无 Slurm、常驻 agent、消息队列或监控服务。

首版代码已实现：用户名登录、节点纳管、全节点实时信息、整机/部分卡申请、30 分钟保护与 10 分钟全零回收、Docker 进程识别、互联/共享盘检测、前端 Bash 任务和日志/取消。实现范围与尚未完成项见 [实现记录](docs/implementation.md)，目标规格见 [设计文档](docs/design.md)。

当前界面仅展示实时信息、集群管理和机器申请；任务中心暂时隐藏，直接访问其页面会返回实时信息。任务执行代码和后端接口保留。页面采用原有深绿侧栏与浅色主区域，辅以柔和光晕、卡片层次和轻量动效；中文使用微软雅黑、英文与数字使用 Consolas（使用浏览器所在系统的本机字体）。

## 运行

本次开发验证使用 Python 3.14、Node.js 24、MySQL 8.0.46。项目声明 Python >=3.11；计算节点为 Linux，节点不需要 Python。

1. 创建中心机环境并安装锁定依赖：

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.lock
   pip install --no-deps -e .
   cp .env.example .env
   hive keygen
   ```

   Windows 使用 `.venv\Scripts\Activate.ps1` 激活环境。将生成的密钥写入 `.env` 的 HIVE_SECRET_KEY，填写 MySQL 地址、端口、用户、密码、数据库和管理员用户名。配置文件按字面读取，不执行 shell 插值；进程环境变量优先。

2. 在已有 MySQL 中创建独立数据库和应用账号，例如：

   ```sql
   CREATE DATABASE hive CHARACTER SET utf8mb4;
   CREATE USER 'hive'@'127.0.0.1' IDENTIFIED BY '替换为应用数据库密码';
   GRANT ALL PRIVILEGES ON hive.* TO 'hive'@'127.0.0.1';
   ```

   数据库账号主机部分需与中心机的实际连接来源一致。迁移只创建/更新 Hive 表，不连接其他业务库。

   ```bash
   hive migrate
   ```

3. 构建前端：

   ```bash
   cd frontend
   npm ci
   npm run build
   cd ..
   ```

4. 分别运行 API 和 worker：

   ```bash
   hive api --host 0.0.0.0 --port 18000
   hive worker
   ```

   页面与 API 统一部署在 **18000** 端口，CLI 默认监听 `0.0.0.0:18000`。浏览器访问 `http://中心机IP:18000/login`，HIVE_ORIGIN 填同一地址的来源（不含 `/login`）。首次只需输入用户名；默认管理员登记名为 `admin`，由 HIVE_ADMIN_USERS 配置。用户名是协作登记，不验证实际身份。worker 必须运行才能采集、分配、执行任务和回收。

   HTTP 调试将 HIVE_COOKIE_SECURE=false；生产使用 HTTPS、HIVE_COOKIE_SECURE=true，HIVE_ORIGIN 填浏览器实际地址。API 可直接启用 TLS，仍使用 18000：

   ```bash
   hive api --host 0.0.0.0 --port 18000 --ssl-certfile /path/server.crt --ssl-keyfile /path/server.key
   ```

   如需前端热更新，执行 `npm run dev`；Vite 代理 API 到 18000，HIVE_ORIGIN 改为浏览器使用的开发地址。Vite 仅用于开发，部署始终由 API 在 18000 提供构建后的页面。中心机 systemd 示例见 [deploy](deploy/hive-api.service) 和 [worker 配置](deploy/hive-worker.service)，按实际安装目录、用户和 TLS 方式调整。密钥/配置文件限制为运行用户可读，并独立备份主密钥。

### 当前 Windows 中心机：Wi-Fi 访问

在项目根目录执行以下脚本启动或重启 API，自动读取 `WLAN` 的 IPv4 地址并同步 `data/preview.env` 的 HIVE_ORIGIN / HTTP Cookie 配置，固定监听 `0.0.0.0:18000`。worker 不受影响；18000 若被其他程序占用则报错，不终止其他程序。

```powershell
.\scripts\start-api.ps1
```

脚本输出本机与其他 Wi-Fi 设备应统一使用的页面地址。Wi-Fi IP 发生变化时重新执行；可用 `-EnvFile .env` 指定其他配置文件，或 `-InterfaceAlias` 指定网卡名称。它启动隐藏后台进程，不配置开机自启；日志保存在 `data/api.out.log` / `data/api.err.log`。

首次开放端口，在**管理员 PowerShell**中执行一次：

```powershell
.\scripts\enable-wifi-access.ps1
```

此脚本只维护 `Hive-WiFi-TCP-18000` 防火墙规则：Wi-Fi 接口、TCP 18000、来源 LocalSubnet，兼容 Windows 将当前 Wi-Fi 标记为公用网络的情况。不会关闭防火墙或开放 MySQL。页面能打开但不能登录时，先检查访问地址是否与 HIVE_ORIGIN 一致；端口不通时检查路由器是否启用了无线客户端隔离。

同一 Wi-Fi 名称不保证客户端处于同一 IP 子网。如果需要允许已确认的跨子网客户端，在管理员 PowerShell 中执行 `.\scripts\enable-wifi-access.ps1 -ClientAddress 客户端IPv4地址`。它为该 IP 单独添加 `Hive-WiFi-TCP-18000-Client-*` 规则，只放行 Wi-Fi 的 TCP 18000；不扩大整个网段的访问范围，重复运行默认命令也不会覆盖已添加的客户端规则。网关仍须允许双方路由，主机放行不能解除网络设备的隔离策略。客户端地址变更后，需要重新确认并更新对应规则。

## 纳管节点

- 在中心机配置 HIVE_KNOWN_HOSTS 指向已核验的 SSH 主机公钥文件。可用现有 SSH 工具取得公钥并核对指纹后登记；平台拒绝未知/变化的主机密钥，不自动信任。
- 管理员在“集群管理”输入节点 IP、端口、SSH 账号/密码、A2/A3/A5 和机型。worker 通过 SSH 发现真实卡列表；没有完整有效采样时不会分配。
- 节点需已有 SSH/SFTP、Bash、procfs、npu-smi、驱动工具 hccn_tool；任务还使用 timeout、nohup、setsid、flock、procps（ps）、head、cat、mv 等系统工具。Docker 信息仅在节点已有 Docker 时查询，不为平台强制安装 Docker。
- 对无业务但驱动常驻显存非零的卡，可在纳管详情确认空闲显存基线；平台要求该卡无申请、无 PID、AI Core=0 且采集完整，不能自动把任意显存占用学习为空闲。已确认的 Ascend 基线允许 2 MiB 读数波动；未确认和其他硬件不默认获得容差。
- A3 互联如缺少 hccn 编号，在纳管详情录入 slot → hccn_id 映射，例如 `{"0:0":0,"0:1":1}`，必须根据实机确定。A5 的互联命令尚未验证，保持未支持；不会将主机 SSH 可达当作 NPU 互联通过。
- 纳管互联每机选一个 NPU 做有方向抽检；申请要求互联时，对最终选中的全部卡组合复验。抽检不代表所有卡组合或业务端口已经验证。
- 共享盘优先检查 /mnt 挂载，再用专用随机小文件跨节点校验，支持 hostname/IP 源别名，检查只清理本次文件。仅存在同名目录不会被判断为共享。

任务使用分配清单和 ASCEND_RT_VISIBLE_DEVICES，不对共享 root 用户强制隔离。自动任务脚本应前台运行并等待业务子进程，不通过额外 setsid 或 docker -d 逃离受管任务。手工 SSH 调试按平台分配的卡使用；平台外占用会显示并阻止再次分配。

## 验证

```bash
pip install -r requirements-test.lock
python -m unittest discover -s tests -v
cd frontend
npm ci
npm run build
```

默认跳过真实 MySQL 集成测试。设置 HIVE_TEST_MYSQL_PORT、可选 HIVE_TEST_MYSQL_USER/HIVE_TEST_MYSQL_PASSWORD 后，测试账号需能创建/删除数据库；测试仅操作自己创建的随机 hive_test_* 库。不要给测试配置业务数据库凭据。Linux 任务协议测试仅在具有实际 Linux /proc 与所需系统命令时运行，Windows 明确跳过。

已接入 5 台真实节点、80 个逻辑 NPU 设备，完成真实采集、容器识别、互联抽检、共享盘、资源申请/归还与 SSH 自检任务；详见 [实机验收记录](docs/real-node-acceptance.md) 和 [实现边界](docs/implementation.md)。

## 代码与依赖

- `backend/hive/`：Identity、Inventory、ResourceService、Telemetry、HardwareAdapter、AdmissionProbe、Execution、Cleanup、Reporting。
- `backend/hive/sql/`：MySQL 迁移；`backend/hive/scripts/`：任务期间执行的 Bash 包装脚本。
- `frontend/`：React 页面与公共模块；`tests/`：规则、SSH/硬件边界、MySQL/API 与任务协议测试。
- 生产 Python 直接依赖：FastAPI、Uvicorn、PyMySQL、Paramiko、cryptography；完整传递依赖版本在 [requirements.lock](requirements.lock)。
- 前端运行时只依赖 React/react-dom；TypeScript/Vite 用于构建，完整版本在 [package-lock.json](frontend/package-lock.json)。
- httpx 仅用于测试；没有 Redis、Celery、Kubernetes、Prometheus 或 LDAP。

后续 PR/环境模板、e2e/nightly/weekly 将复用已有资源和任务接口。任意任务产物收集、完整历史统计报表和扩展指标的长期聚合尚未实现，不将设计目标描述为现有能力。

参考：[节点互联与 Docker 核验](docs/research/node-connectivity-and-containers.md)；[历史 Slurm 研究](docs/research/slurm-capabilities.md) 仅作历史记录，当前不依赖 Slurm。
