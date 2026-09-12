# Hive

轻量 NPU 集群管理与 SSH 任务执行平台。React + Python/Bash + MySQL；计算节点使用现有 SSH 与 Ascend 环境，无 Slurm、常驻 agent、消息队列或监控服务。

首版代码已实现：用户名登录、节点纳管、全节点实时信息、整机/部分卡申请、30 分钟保护与 10 分钟全零回收、Docker 进程识别、互联/共享盘检测、前端 Bash 任务和日志/取消。实现范围与尚未完成项见 [实现记录](docs/implementation.md)，目标规格见 [设计文档](docs/design.md)。

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
   hive api --host 127.0.0.1 --port 8000
   hive worker
   ```

   浏览器访问 http://127.0.0.1:8000/login。首次只需输入用户名；默认管理员登记名为 `admin`，由 HIVE_ADMIN_USERS 配置。用户名是协作登记，不验证实际身份。worker 必须运行才能采集、分配、执行任务和回收。

   本机 HTTP 开发将 HIVE_COOKIE_SECURE=false、HIVE_ORIGIN=http://127.0.0.1:8000；生产使用 HTTPS、HIVE_COOKIE_SECURE=true，HIVE_ORIGIN 填浏览器实际地址。API 可直接启用 TLS：

   ```bash
   hive api --host 0.0.0.0 --port 8443 --ssl-certfile /path/server.crt --ssl-keyfile /path/server.key
   ```

   如需前端热更新，执行 `npm run dev`；Vite 代理 API 到 8000，HIVE_ORIGIN 改为浏览器使用的开发地址。中心机 systemd 示例见 [deploy](deploy/hive-api.service) 和 [worker 配置](deploy/hive-worker.service)，按实际安装目录、用户和 TLS 方式调整。密钥/配置文件限制为运行用户可读，并独立备份主密钥。

## 纳管节点

- 在中心机配置 HIVE_KNOWN_HOSTS 指向已核验的 SSH 主机公钥文件。可用现有 SSH 工具取得公钥并核对指纹后登记；平台拒绝未知/变化的主机密钥，不自动信任。
- 管理员在“集群管理”输入节点 IP、端口、SSH 账号/密码、A2/A3/A5 和机型。worker 通过 SSH 发现真实卡列表；没有完整有效采样时不会分配。
- 节点需已有 SSH/SFTP、Bash、procfs、npu-smi、驱动工具 hccn_tool；任务还使用 timeout、nohup、setsid、flock、head、cat、mv 等系统工具。Docker 信息仅在节点已有 Docker 时查询，不为平台强制安装 Docker。
- 对无业务但驱动常驻显存非零的卡，可在纳管详情确认空闲显存基线；平台要求该卡无申请、无 PID、AI Core=0 且采集完整，不能自动把任意显存占用学习为空闲。
- A3 互联如缺少 hccn 编号，在纳管详情录入 slot → hccn_id 映射，例如 `{"0:0":0,"0:1":1}`，必须根据实机确定。A5 的互联命令尚未验证，保持未支持；不会将主机 SSH 可达当作 NPU 互联通过。
- 共享盘优先检查 /mnt 挂载，再用专用随机小文件跨节点校验，检查只清理本次文件。仅存在同名目录不会被判断为共享。

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

本轮完成 MySQL 集成与浏览器验收，尚未接入真实 NPU 节点。真实驱动输出、PID/容器映射和 Linux SSH 任务协议仍需现场验收，详见 [实现与验收记录](docs/implementation.md)。

## 代码与依赖

- `backend/hive/`：Identity、Inventory、ResourceService、Telemetry、HardwareAdapter、AdmissionProbe、Execution、Cleanup、Reporting。
- `backend/hive/sql/`：MySQL 迁移；`backend/hive/scripts/`：任务期间执行的 Bash 包装脚本。
- `frontend/`：React 页面与公共模块；`tests/`：规则、SSH/硬件边界、MySQL/API 与任务协议测试。
- 生产 Python 直接依赖：FastAPI、Uvicorn、PyMySQL、Paramiko、cryptography；完整传递依赖版本在 [requirements.lock](requirements.lock)。
- 前端运行时只依赖 React/react-dom；TypeScript/Vite 用于构建，完整版本在 [package-lock.json](frontend/package-lock.json)。
- httpx 仅用于测试；没有 Redis、Celery、Kubernetes、Prometheus 或 LDAP。

后续 PR/环境模板、e2e/nightly/weekly 将复用已有资源和任务接口。任意任务产物收集、完整历史统计报表和扩展指标的长期聚合尚未实现，不将设计目标描述为现有能力。

参考：[节点互联与 Docker 核验](docs/research/node-connectivity-and-containers.md)；[历史 Slurm 研究](docs/research/slurm-capabilities.md) 仅作历史记录，当前不依赖 Slurm。
