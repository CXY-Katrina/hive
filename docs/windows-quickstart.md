# Windows 全新部署

本指南使用全新数据库，不迁移旧数据。当前前端隐藏任务功能，保留集群管理、实时信息、机器申请与人员管理。

## 1. 安装基础依赖

安装 Git、标准 Python 3.14 x64、Node.js 24（含 npm）、MySQL Server 8.0、Windows OpenSSH Client。MySQL 安装为自动启动的 Windows 服务，监听本机 3306；记下安装时设置的 root 密码。详细安装步骤见 [README 第 2–3 节](../README.md#2-安装-windows-基础软件)。不需要 Docker Desktop、Slurm 或 Nginx。

## 2. 创建空数据库

PowerShell 中打开 MySQL（安装路径不同请替换）：

```powershell
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' -u root -p
```

输入 root 密码后执行，先替换应用密码占位值：

```sql
CREATE DATABASE hive CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'hive'@'127.0.0.1' IDENTIFIED BY 'REPLACE_WITH_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON hive.* TO 'hive'@'127.0.0.1';
EXIT;
```

## 3. 下载并构建前后端

普通 PowerShell：

```powershell
git clone https://github.com/CXY-Katrina/hive.git C:\hive
Set-Location C:\hive
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
Copy-Item .env.example .env
New-Item -ItemType Directory -Path data -Force | Out-Null
New-Item -ItemType File -Path data\known_hosts -Force | Out-Null
.\.venv\Scripts\python.exe -m hive.cli keygen
.\.venv\Scripts\python.exe -m hive.cli admin-password
notepad .env
```

`keygen` 输出密钥值，将其填入 `HIVE_SECRET_KEY`。`admin-password` 交互输入你希望设置的管理员密码，将输出的整行 `HIVE_ADMIN_PASSWORD_HASH=...` 写入 `.env`。

同时在 `.env` 中填写：

```ini
HIVE_MYSQL_HOST=127.0.0.1
HIVE_MYSQL_PORT=3306
HIVE_MYSQL_USER=hive
HIVE_MYSQL_PASSWORD=第2步设置的应用密码
HIVE_MYSQL_DATABASE=hive
HIVE_ADMIN_USERS=admin
HIVE_ORIGIN=http://127.0.0.1:18000
HIVE_COOKIE_SECURE=false
HIVE_KNOWN_HOSTS=data/known_hosts
HIVE_DATA_DIR=data
```

保留刚填入的密钥和密码哈希。新部署没有默认 admin 密码，开发机的 `password` 不会随 Git 分发。不要提交 `.env` 或运行数据。

创建表：

```powershell
.\.venv\Scripts\python.exe -m hive.cli migrate --env-file .env
```

## 4. 启动服务

窗口一（前端页面 + 后端 API）：

```powershell
Set-Location C:\hive
.\.venv\Scripts\python.exe -m hive.cli api --host 0.0.0.0 --port 18000 --env-file C:\hive\.env
```

窗口二（采集、机器分配和资源释放）：

```powershell
Set-Location C:\hive
.\.venv\Scripts\python.exe -m hive.cli worker --env-file C:\hive\.env
```

两个窗口保持运行；Ctrl+C 停止相应进程。MySQL 服务也需运行。任务页面隐藏不影响 worker 对机器采集、申请分配和回收的职责。

**前端构建后由 Python API 提供，不需要额外启动 Vite 或 npm dev。**

打开 <http://127.0.0.1:18000/login>，使用 `admin` 和第 3 步自设密码。健康检查：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:18000/api/health
```

返回 `{"status":"ok"}` 表示 API 能连接数据库。空环境没有节点和历史记录；按 [README 第 6 节](../README.md#6-登记-ssh-公钥和准备节点) 核验并登记节点 SSH 公钥，再在页面纳管节点。采样时间持续更新才表示 worker 与节点连通。

## 5. 局域网访问与日后更新

局域网使用时，将 `.env` 的 `HIVE_ORIGIN` 改为 `http://中心机实际IP:18000`，重启 API，浏览器统一使用该地址。防火墙和开机自启动见 [README 第 8–9 节](../README.md#8-局域网访问)。

日后更新：停止 API 和 worker，执行 `git pull --ff-only`、锁文件依赖安装、前端 `npm.cmd ci` / `npm.cmd run build`、`hive.cli migrate`，再启动两个进程。保留 `.env`、主密钥和 `data`，不要重复覆盖初始化文件。
