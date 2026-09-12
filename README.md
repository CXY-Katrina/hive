# Hive

轻量 NPU 资源管理平台。在一台中心机运行 **MySQL + Python API + Python worker**，通过 SSH 管理 Linux NPU 节点。React 页面由 API 在 **18000** 端口直接提供，无需单独部署前端 Web 服务。

当前页面：用户名登录、集群管理、所有节点实时信息、机器申请。支持整机/部分卡申请、30 分钟交付保护、按实际 AI Core 活动保留资源、连续空闲后的清理释放、真实 PID/Docker 容器、互联及共享盘检测、SoC 采集和带依据的算力规格登记。任务中心暂时隐藏，后端执行能力保留；PR、e2e、nightly/weekly 尚未实现。

**本文面向另一台 Windows x64 设备的全新部署，使用空数据库。** 仓库只提供代码、文档和测试样本，不包含节点密码、主密钥、数据库或运行数据。部署后需添加自己的真实节点，页面不会自动出现开发环境中的机器。

## 部署目录

1. [依赖与网络](#1-依赖与网络)
2. [安装 Windows 基础软件](#2-安装-windows-基础软件)
3. [安装和初始化 MySQL](#3-安装和初始化-mysql)
4. [下载代码并安装项目依赖](#4-下载代码并安装项目依赖)
5. [配置 Hive 和创建表](#5-配置-hive-和创建表)
6. [登记 SSH 公钥和准备节点](#6-登记-ssh-公钥和准备节点)
7. [启动和验收](#7-启动和验收)
8. [局域网访问](#8-局域网访问)
9. [Windows 开机运行](#9-windows-开机运行)
10. [更新与排错](#10-更新与排错)
11. [开发验证与模块](#11-开发验证与模块)

## 1. 依赖与网络

### 中心机需要什么

| 组件 | 版本与用途 | 是否常驻 |
|---|---|---|
| Windows x64 | 本文的中心机部署系统 | 是 |
| Python | 项目要求 ≥3.11；已验证 **3.14**，本文使用标准 x64 版 | API 和 worker 两个进程 |
| Python 包 | `requirements.lock` 固定版本；FastAPI、Uvicorn、PyMySQL、Paramiko、cryptography 等 | 随 Python 加载 |
| MySQL Server | 必须为 **MySQL 8.x**；已验证 **8.0.46**，下文按 8.0 安装 | 是，Windows 服务 |
| Node.js / npm | 已验证 **Node.js 24.15.0**；用于构建 React | 否 |
| Git for Windows | 克隆和更新代码 | 否 |
| OpenSSH Client | Windows 可选功能，用于公钥登记与排错；业务 SSH 由 Paramiko 执行 | 否 |
| Microsoft VC++ x64 运行库 | MySQL 安装器要求的运行依赖，缺少时按安装器提示补齐 | 无独立业务服务 |

不需要 Slurm、Redis、Celery、Docker Desktop、Kubernetes、Nginx、Prometheus 或 Grafana。MySQL Workbench、MySQL Shell 和测试依赖也不是运行平台的必需组件。MariaDB 不是已验证替代，表结构使用 MySQL 8 排序规则和 JSON 能力。MySQL 8.4、其他 Python 版本需在目标设备复验，不能把上述已验证版本理解成所有组合均已验收。

### 网络与端口

```text
浏览器 ── HTTP :18000 ──> Windows 中心机 / Python API ── TCP :3306 ──> MySQL
                                      │
                               Python worker ── SSH :22 ──> Linux NPU 节点
                                      └─────── TCP :3306 ──> MySQL
```

| 方向 | 默认端口 | 说明 |
|---|---|---|
| 浏览器 → 中心机 | TCP **18000** | 页面和 API 共用，后续部署均使用此端口 |
| API/worker → MySQL | TCP 3306 | 本文同机部署，使用 `127.0.0.1`；无需向浏览器开放数据库 |
| 中心机 → NPU 节点 | TCP 22 | SSH/SFTP；非标准端口按纳管信息配置 |
| NPU 节点之间 | 按现场 NPU 网络 | 多机互联依赖机器本身的 NPU 网络，不由 18000 端口提供 |

以下命令假设项目安装到 **`C:\hive`**，MySQL 安装到 **`C:\Program Files\MySQL\MySQL Server 8.0`**。路径不同请统一替换。除明确标注“管理员 PowerShell”外，使用日后实际运行 Hive 的普通 Windows 账号即可。

## 2. 安装 Windows 基础软件

1. 安装 [Git for Windows](https://git-scm.com/install/windows)，允许命令行使用 Git。
2. 安装 [Python Windows x64](https://www.python.org/downloads/windows/) 的标准 Python 3.14，包含 pip 和 Python launcher。不要选择嵌入式 ZIP 或 free-threaded 变体。本文使用 `py -3.14`，之后直接调用虚拟环境，不要求激活脚本。
3. 安装 [Node.js 24.15.0 的 Windows x64 MSI](https://nodejs.org/en/download/archive/v24.15.0)，保留 npm 和 PATH 选项；不需要全局安装 React/Vite。
4. 在 Windows“设置 → 可选功能”确认已安装 **OpenSSH 客户端**；中心机不需要安装 OpenSSH 服务端。

安装后重新打开 PowerShell：

```powershell
git --version
py -3.14 --version
node --version
npm.cmd --version
ssh -V
Get-Command ssh-keyscan, ssh-keygen
```

若缺少 OpenSSH Client，在管理员 PowerShell 执行：

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

本文使用 `npm.cmd`，避免部分执行策略拦截 `npm.ps1`。不需要为此关闭系统脚本策略。

## 3. 安装和初始化 MySQL

### 3.1 安装数据库服务

根据 [MySQL 官方 Windows 安装说明](https://dev.mysql.com/doc/refman/8.0/en/windows-installation.html)，下载 MySQL Installer，选择 **Server Only**，或在 Custom 中只选择 **MySQL Server 8.0 x64**。与验证环境对应的版本是 8.0.46；下载页未列出时可在官方归档选择。先完成安装器要求的 VC++ 运行库等前置项，再配置数据库。

| 向导配置项 | 本文设置 |
|---|---|
| TCP/IP | 启用，端口 `3306`；若已有服务占用，复用已有 MySQL 或改端口并同步 `.env` |
| 为远程访问开放防火墙 | 同机部署不勾选 |
| 认证方式 | 保留默认强密码认证 |
| root 密码 | 设置并保存管理员密码，仅用于初始化和运维 |
| Windows Service | 名称 `MySQL80`，勾选开机启动 |
| 服务运行账号 | 使用安装器默认账号即可 |

MySQL 官方说明：安装为 Windows 服务后可随系统启动，无需另外手工启动 `mysqld`。安装后验证：

```powershell
Get-Service MySQL80
Test-NetConnection 127.0.0.1 -Port 3306
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' --version
```

服务名不同先执行 `Get-Service *mysql*`。未启动时在管理员 PowerShell 执行 `Start-Service MySQL80`。

### 3.2 创建数据库和应用账号

进入 MySQL，`-p` 交互询问 root 密码，不把密码直接放进命令行：

```powershell
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' -u root -p
```

在 `mysql>` 执行以下 SQL，**先将示例密码替换为实际应用数据库密码**。此密码用于 Hive 连接 MySQL，不是节点 root 密码。

```sql
CREATE DATABASE hive CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'hive'@'127.0.0.1' IDENTIFIED BY 'REPLACE_WITH_A_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON hive.* TO 'hive'@'127.0.0.1';
EXIT;
```

如果库或账号已经存在，先确认归属，不要删除已有业务库来重试。应用账号只需 `hive.*` 权限，不需要全局权限。账号主机部分需匹配实际 TCP 来源；如果该 MySQL 的本机账号匹配使用 `localhost`，为相应本机账号授予同样权限，以接下来的真实连接结果为准，不使用 `%` 扩大范围。

验证应用账号：

```powershell
& 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe' --protocol=TCP -h 127.0.0.1 -P 3306 -u hive -p hive
```

进入后执行 `SELECT VERSION(), CURRENT_USER();`，确认成功后 `EXIT;`。

## 4. 下载代码并安装项目依赖

`C:\hive` 应为新的目标目录；若已有内容，选择另一个空目录并替换后续路径。

```powershell
git clone https://github.com/CXY-Katrina/hive.git C:\hive
Set-Location C:\hive
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
Set-Location frontend
npm.cmd ci
npm.cmd run build
Set-Location ..
Test-Path frontend\dist\index.html
```

最后应返回 `True`。首次安装需要访问 GitHub、Python 包源和 npm 包源；受限网络可使用团队镜像，但仍使用锁文件。Vite 7 的版本要求是 `^20.19.0 || >=22.12.0`，本文直接使用已验证的 Node.js 24。

**保留源码目录并使用上述 editable 安装。** API 根据源码位置查找 `frontend/dist`，只安装普通 wheel 不会自动带上前端。必须先构建再启动 API；若 API 启动后才生成 `dist`，重启 API 以挂载静态资源。

## 5. 配置 Hive 和创建表

### 5.1 首次创建配置

```powershell
Set-Location C:\hive
Copy-Item .env.example .env
New-Item -ItemType Directory -Path data -Force | Out-Null
if (!(Test-Path data\known_hosts)) { New-Item -ItemType File -Path data\known_hosts | Out-Null }
.\.venv\Scripts\python.exe -m hive.cli keygen
notepad .env
```

将 `keygen` 输出的整行填入 `HIVE_SECRET_KEY`。这是加密节点凭据的主密钥，生成一次并独立保存；实例投入使用后不要随意重新生成，否则已有凭据无法解密。`.env` 和 `data/` 已被 Git 忽略。

配置示例，所有中文占位值都需要替换：

```ini
HIVE_MYSQL_HOST=127.0.0.1
HIVE_MYSQL_PORT=3306
HIVE_MYSQL_USER=hive
HIVE_MYSQL_PASSWORD=替换为第3节设置的应用数据库密码
HIVE_MYSQL_DATABASE=hive
HIVE_SECRET_KEY=替换为keygen输出的整行
HIVE_ADMIN_USERS=admin
HIVE_ORIGIN=http://替换为中心机局域网IP:18000
HIVE_COOKIE_SECURE=false
HIVE_KNOWN_HOSTS=data/known_hosts
HIVE_DATA_DIR=data
```

通过 `ipconfig` 或 `Get-NetIPAddress -AddressFamily IPv4` 找中心机真实网卡 IP。`HIVE_ORIGIN` 不含 `/login`；本机与其他浏览器均使用这一统一地址，不能配置局域网 IP 后又通过 `localhost` 登录。

### 5.2 配置项

| 配置 | 默认值 / 要求 | 说明 |
|---|---|---|
| `HIVE_MYSQL_HOST` / `PORT` | `127.0.0.1` / `3306` | 使用新机真实数据库端口 |
| `HIVE_MYSQL_USER` / `PASSWORD` / `DATABASE` | 填实际账号、密码、库名 | API/worker 必须使用同一个库 |
| `HIVE_SECRET_KEY` | 必填 | `keygen` 生成的 URL-safe Base64 密钥 |
| `HIVE_ADMIN_USERS` | `admin` | 逗号分隔管理员登记名，如 `admin,cxy` |
| `HIVE_ORIGIN` | 填实际访问来源 | 协议、主机、端口必须与浏览器一致 |
| `HIVE_COOKIE_SECURE` | 示例为 `false`，代码缺省为 `true` | 内网 HTTP 为 false；HTTPS 为 true |
| `HIVE_KNOWN_HOSTS` | `data/known_hosts` | 已核验的主机公钥，运行账号可读 |
| `HIVE_DATA_DIR` | `data` | 运行账号可写 |
| `HIVE_SAMPLE_SECONDS` | `15` | 采样间隔，秒 |
| `HIVE_STALE_SECONDS` | `45` | 超过该时间视为数据过期 |
| `HIVE_SSH_TIMEOUT` | `20` | SSH 基础超时，秒 |
| `HIVE_SSH_WORKERS` | `8` | SSH 并发容量 |

配置按字面读取，不执行 shell 插值；进程环境变量优先于配置文件。相对路径按**工作目录**解析，本文固定为 `C:\hive`，也可填写绝对路径。修改配置后重启 API 和 worker。务必确认 `--env-file` 指定文件存在，否则程序可能使用默认值。

用户名登录只做团队协作登记，不验证真实身份；知道管理员登记名的人可以按该身份登录。按当前定位部署在受控团队网络，不能将它视为已经实现密码或企业身份认证的系统。

### 5.3 创建表

```powershell
.\.venv\Scripts\python.exe -m hive.cli migrate --env-file .env
```

成功时通常无额外输出且退出码为 0。命令依次处理 `backend/hive/sql` 中全部迁移，用 `schema_versions` 记录完成项，重复执行会跳过已完成项。它不创建数据库或账号；不要只手工执行 `001_initial.sql`，也不要并发运行多个迁移命令。

## 6. 登记 SSH 公钥和准备节点

### 6.1 节点需要的工具

NPU 节点为 Linux，已有 root SSH 账号、密码认证、SFTP 和匹配机型的 Ascend 驱动。平台不在节点安装常驻代理。

| 依赖 | 用途 |
|---|---|
| Bash、`/proc` | 采集与进程身份核验 |
| GNU coreutils | `cat`、`timeout`、`base64 -w0`、`readlink`、`df`、`head`、`nohup`、`mv` 等 |
| util-linux | `findmnt`、`setsid`、`flock` |
| procps | `ps`，进程查询与任务清理 |
| OpenSSH 服务端/SFTP | 连接与上传；后续任务使用 SFTP `posix-rename` 扩展 |
| `npu-smi` | 驱动提供的设备、SoC、AI Core、HBM、进程查询 |
| `hccn_tool` | 对应代际的 NPU 互联检测 |
| Docker | 可选，仅识别已有容器，不要求为纳管专门安装 |

裸机无需额外安装 Python、Node.js、MySQL、CANN 或 PyTorch；实际调试任务的业务环境由用户自行准备。驱动/固件按现场设备要求安装，本平台不自动安装或升级驱动。

非交互采集使用系统 PATH 加 `/usr/local/Ascend/driver/tools`；工具仅在交互 `.bashrc` 中可见时可能采集失败。在节点核对：

```bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/Ascend/driver/tools
command -v bash timeout base64 findmnt setsid flock ps npu-smi
npu-smi info
npu-smi info -m
```

### 6.2 登记主机公钥

在中心机执行，以下 IP 是占位示例，请替换为真实节点：

```powershell
$NodeHost = '192.0.2.20'
Test-NetConnection $NodeHost -Port 22
ssh-keyscan -T 5 -p 22 -t ed25519,rsa $NodeHost | Set-Content -Encoding ascii data\hostkey-candidate
ssh-keygen -lf data\hostkey-candidate
```

`ssh-keyscan` 只取得候选公钥。向节点管理员或通过已可信控制台核对指纹，例如节点执行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`。确认一致后登记：

```powershell
Get-Content data\hostkey-candidate | Add-Content -Encoding ascii data\known_hosts
```

每台机器重复一次。非 22 端口使用真实 `ssh-keyscan -p`，其 `[主机]:端口` 记录与平台一致。平台拒绝未知或变化的公钥，不关闭主机密钥检查。节点重装后需重新核验指纹、更新记录，并重启 worker 使缓存连接重新建立。

## 7. 启动和验收

先用两个普通 PowerShell 窗口前台运行，便于看到错误。

**窗口一：API 和前端页面**

```powershell
Set-Location C:\hive
.\.venv\Scripts\python.exe -m hive.cli api --host 0.0.0.0 --port 18000 --env-file C:\hive\.env
```

**窗口二：worker**

```powershell
Set-Location C:\hive
.\.venv\Scripts\python.exe -m hive.cli worker --env-file C:\hive\.env
```

MySQL 服务需同时运行。Node/npm/Vite 此时不需要常驻。关闭 worker 会停止采集和资源管理，而页面仍可能打开，所以不能只检查网页。

在第三个窗口检查：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 18000
curl.exe --noproxy "*" http://127.0.0.1:18000/api/health
```

预期监听 `0.0.0.0:18000`，返回 `{"status":"ok"}`。健康接口只检查数据库，继续验证：

1. 打开 `.env` 中对应的 `http://中心机IP:18000/login`，页面样式正常，输入 `admin` 登录。
2. 新数据库没有节点和申请，这是预期状态。
3. 进入“集群管理 → 纳管节点”，填写真实 IP、SSH 端口、账号、密码、A2/A3/A5 代际。
4. 等待 worker 完整采样，确认各卡数据及采样时间持续更新。
5. 对确定无业务但有驱动常驻显存的卡，在检测详情确认空闲基线；不要把业务显存登记为空闲。
6. 核验互联及共享盘。共享检测会写入并清理随机小文件，需要对应权限；同名 `/mnt` 目录不代表共享成功。
7. A3 必要时按真实设备登记 `hccn_ids`；A5 互联尚未完成实机验证。SoC 自动采集，算力须按明确资料登记，未确认时显示“待确认”。
8. 从另一台设备打开同一个地址并登录；本机通过自身 IP 成功不能代替跨设备连通验证。

资源申请交付起保护 30 分钟；之后申请全卡连续 10 分钟 AI Core 为零可清理关联进程并释放。缺测、进程身份不明或跨卡冲突时保留资源。共享 root 不强制隔离其他卡，团队须按分配卡号使用；平台外进程会显示并阻止误分配。详细规则见 [设计文档](docs/design.md)。

## 8. 局域网访问

### 8.1 放行 18000

前后端共用 **18000**，一个实例只启动一个 API。不要同时运行前台 API、后台脚本和计划任务 API。

在**管理员 PowerShell**开放实际网卡的入站访问；用 `Get-NetAdapter` 确认名称：

```powershell
Set-Location C:\hive
.\scripts\enable-wifi-access.ps1 -InterfaceAlias 'WLAN'
```

有线网络将 `WLAN` 换成实际名称，如 `以太网`。脚本仅放行选定网卡的 TCP 18000，来源限定 `LocalSubnet`；不关闭防火墙或开放 MySQL。同一 Wi-Fi 名称可能分配不同子网，对明确允许的跨子网客户端，可添加单 IP 规则：

```powershell
.\scripts\enable-wifi-access.ps1 -InterfaceAlias 'WLAN' -ClientAddress '192.0.2.30'
```

上例为占位 IP。每个客户端使用独立 `Hive-WiFi-TCP-18000-Client-*` 规则，不扩大整个网段；可在“高级安全 Windows Defender 防火墙 → 入站规则”按名称撤销。网关仍须允许双方路由，主机放行无法解除 AP、交换机或 VPN 的隔离。

### 8.2 临时后台启动

常驻部署使用第 9 节计划任务。临时使用可运行：

```powershell
.\scripts\start-api.ps1 -EnvFile .env -InterfaceAlias 'WLAN'
```

脚本自动读取网卡 IPv4，更新指定文件的 `HIVE_ORIGIN` / HTTP Cookie 配置，在 18000 后台启动或重启 API，输出访问地址，日志为 `data/api.out.log`、`data/api.err.log`。**它不启动 worker，也不配置开机启动。** 新机明确传 `-EnvFile .env`：脚本的历史默认 `data/preview.env` 是开发环境文件，不随 Git 提供。

常驻中心尽量使用固定 IP 或稳定域名；IP 变化后同步 `HIVE_ORIGIN` 并重启 API/worker，客户端改用新地址。

### 8.3 可选 HTTPS

准备与访问 IP/域名匹配、客户端信任的证书，仍使用 18000：

```powershell
.\.venv\Scripts\python.exe -m hive.cli api --host 0.0.0.0 --port 18000 --env-file C:\hive\.env --ssl-certfile C:\hive-certs\server.crt --ssl-keyfile C:\hive-certs\server.key
```

设置 `HIVE_ORIGIN=https://实际域名:18000`、`HIVE_COOKIE_SECURE=true`。临时后台脚本用于 HTTP，HTTPS 使用此明确命令或计划任务，不需要额外反向代理。

## 9. Windows 开机运行

MySQL 使用安装器建立的 `MySQL80` 服务。API 和 worker 使用 Windows 自带**任务计划程序**，不新增 NSSM 等服务包装依赖。

先停止第 7 节两个前台进程（各自 `Ctrl+C`），或确认此前后台脚本启动的进程已停止，再配置计划任务。

### 9.1 创建两个任务

运行 `taskschd.msc` →“创建任务”，分别建立两项，运行账号使用安装和配置 Hive 的固定 Windows 账号，确保可读 `.env` / `known_hosts`、可写 `data`。

| 设置 | `Hive API` | `Hive Worker` |
|---|---|---|
| 程序或脚本 | `C:\hive\.venv\Scripts\python.exe` | 同左 |
| 添加参数 | `-m hive.cli api --host 0.0.0.0 --port 18000 --env-file C:\hive\.env` | `-m hive.cli worker --env-file C:\hive\.env` |
| 起始于 | `C:\hive` | 同左 |
| 触发器 | 启动时，延迟 30 秒 | 同左 |
| 常规 | 不管用户是否登录都运行 | 同左 |
| 失败后重启 | 每 1 分钟重试，可设置多次 | 同左 |
| 已在运行时 | 不启动新实例 | 同左 |
| 超过指定时间停止 | 取消勾选 | 同左 |
| 电源条件 | 常驻中心取消仅交流电启动/转电池停止 | 同左 |

保存“不管用户是否登录都运行”时，Windows 会要求该运行账号的 Windows 凭据，这是系统任务身份，不是 Hive 用户名或节点 root 密码。普通运行无需勾选最高权限；软件安装和防火墙修改才需要管理员权限。安装目录含空格时给参数中的路径加双引号，“起始于”填写目录本身。

### 9.2 验证与日志

分别在计划任务中“运行”，确认页面和采样，再重启 Windows 验证恢复：

```powershell
Get-Service MySQL80
Get-ScheduledTask -TaskName 'Hive API','Hive Worker' | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName 'Hive API'
Get-ScheduledTaskInfo -TaskName 'Hive Worker'
```

任务“历史记录”可检查启动与退出。需要保存控制台日志时，可把任务操作改为本地 `.cmd` 包装文件，“起始于”仍为 `C:\hive`。例如 `C:\hive\data\run-api.cmd`：

```bat
@echo off
cd /d C:\hive
"C:\hive\.venv\Scripts\python.exe" -u -m hive.cli api --host 0.0.0.0 --port 18000 --env-file C:\hive\.env >> C:\hive\data\api.log 2>&1
exit /b %errorlevel%
```

worker 包装文件将子命令改为 `worker`，去掉 `--host` / `--port`，日志改为 `worker.log`。日志不自动轮转，停服务后按磁盘情况归档。

```powershell
Get-Content C:\hive\data\api.log -Tail 50
Get-Content C:\hive\data\worker.log -Tail 50
```

维护时先禁用两个任务，再“结束”，确认相关 Python 进程确已退出，完成后再启用。不要结束整台机器所有 Python 进程。临时后台脚本与计划任务选择一种运行方式，避免双启动。

**一个 MySQL 实例只能运行一个 Hive worker。** `hive.worker` 锁在整个 MySQL 实例内生效，不同 Hive 数据库也会互斥。不同 MySQL 实例没有这一保护，不要把同一批 NPU 节点同时交给两个管理中心操作。

## 10. 更新与排错

### 更新代码

先停止 API/worker，并为当前数据库和配套主密钥保存备份。备份放在仓库外的受控位置，不提交 Git。更新命令：

```powershell
Set-Location C:\hive
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Set-Location frontend
npm.cmd ci
npm.cmd run build
Set-Location ..
.\.venv\Scripts\python.exe -m hive.cli migrate --env-file .env
```

恢复 API/worker，再检查页面、登录和采样。Git 更新不会替换 `.env` 或运行数据。数据库不提供通用向下迁移，回退代码前须确认 schema 兼容。

### 常见问题

| 现象 | 检查方向 |
|---|---|
| `ERR_ADDRESS_UNREACHABLE` / 超时 | 比较双方真实网卡 IP、子网、网关；确认监听、防火墙来源和 AP/VPN 隔离；从失败设备运行 `curl.exe --noproxy "*" --connect-timeout 5 -v http://中心机IP:18000/api/health` |
| 18000 被占用 | `Get-NetTCPConnection -State Listen -LocalPort 18000`；检查前台、后台脚本和计划任务是否重复启动 |
| 页面可打开，登录“请求来源不匹配” | `HIVE_ORIGIN` 与浏览器协议、主机、端口一致；改配置后重启 API |
| HTTP 登录后返回登录页 | `HIVE_COOKIE_SECURE=false`，检查 Cookie；HTTPS 才设置 true |
| 空白页面 / assets 404 | 确认 `frontend/dist/index.html`、源码 editable 安装，构建后重启 API |
| MySQL 不可用 / Access denied | 服务、端口、库名、账号主机匹配、密码；用 mysql.exe TCP 登录单独验证 |
| 表不存在 | 先创建数据库，再运行完整 migrate；三个进程/命令使用同一配置 |
| 密钥无效 | 检查 keygen 的整行输出，实例投入使用后不要换新密钥 |
| 新平台没有节点 | 空库是预期状态，需要登记 SSH 公钥并添加真实节点 |
| 节点未知、采样不更新 | worker、SSH 公钥/密码、节点端口、非交互工具 PATH、采样质量 |
| SSH unknown host / host key changed | 核验指纹、known_hosts 路径与读取权限，不绕过校验 |
| worker 提示已有实例 | MySQL 实例级锁被其他 worker 持有，只保留目标实例 |
| AI Core 为 0 仍不可申请 | 核对 PID、HBM、健康、完整采样；确认无业务后才能登记驱动基线 |
| 前台能跑，开机任务失败 | 固定运行账号、绝对配置路径、“起始于”、目录权限、MySQL 自动启动与失败重试 |
| 字体不同 | 页面使用浏览器所在系统的微软雅黑/Consolas，字体不随仓库分发，缺少时回退 |

## 11. 开发验证与模块

运行平台不需测试依赖；开发回归可执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.lock
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认跳过真实 MySQL 集成测试。设置 `HIVE_TEST_MYSQL_PORT`、可选 `HIVE_TEST_MYSQL_USER` / `HIVE_TEST_MYSQL_PASSWORD` 后，测试创建并删除随机 `hive_test_*` 库；测试账号需要对应权限，不使用仅有 `hive.*` 权限的运行账号。Linux 任务协议测试在 Windows 明确跳过。前端 build 包含 TypeScript 检查。

本次发布前已在独立源码目录和新虚拟环境验证锁文件安装、前端构建、MySQL 空库初始化及重复迁移、真实 HTML/JS/CSS、登录、空节点/申请和退出。依赖包使用本机缓存，不能等同于无缓存新机联网下载验收；未在新 Windows 系统上执行安装器和开机任务。新设备仍需按第 7、9 节现场验证。

| 路径 | 职责 |
|---|---|
| `backend/hive/identity.py`、`inventory.py` | 身份登记、节点与凭据 |
| `resources.py`、`policy.py`、`cleanup.py` | 可复用的申请、保留和回收 |
| `ssh.py`、`hardware.py`、`hardware_profile.py` | SSH、硬件适配、静态规格 |
| `metrics.py`、`telemetry.py`、`reporting.py` | 指标定义、采集与统计 |
| `probes.py`、`execution.py`、`backend/hive/scripts` | 检测与任务执行 |
| `frontend/src/components`、`pages` | React 公共组件和页面 |
| `deploy/` | 可选 Linux systemd 示例；非 Windows 安装脚本 |

进一步阅读：[设计与申请释放规则](docs/design.md)、[实现范围](docs/implementation.md)、[SoC/算力来源](docs/hardware-profile.md)、[实机验收记录](docs/real-node-acceptance.md)、[互联与容器查询依据](docs/research/node-connectivity-and-containers.md)。历史 Slurm 研究只作背景材料，当前平台不依赖 Slurm。
