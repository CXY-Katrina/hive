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
HIVE_ADMIN_PASSWORD_HASH=替换为admin-password命令生成的密码哈希
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
| `HIVE_ADMIN_PASSWORD_HASH` | 必填 | 运行 `python -m hive.cli admin-password`，交互输入管理员密码，复制生成的哈希配置；不保存明文密码 |
| `HIVE_ORIGIN` | 填实际访问来源 | 协议、主机、端口必须与浏览器一致 |
| `HIVE_COOKIE_SECURE` | 示例为 `false`，代码缺省为 `true` | 内网 HTTP 为 false；HTTPS 为 true |
| `HIVE_KNOWN_HOSTS` | `data/known_hosts` | 已核验的主机公钥，运行账号可读 |
| `HIVE_DATA_DIR` | `data` | 运行账号可写 |
| `HIVE_SAMPLE_SECONDS` | `15` | 采样间隔，秒 |
| `HIVE_STALE_SECONDS` | `45` | 超过该时间视为数据过期 |
| `HIVE_SSH_TIMEOUT` | `20` | SSH 基础超时，秒 |
| `HIVE_SSH_WORKERS` | `8` | SSH 并发容量 |

配置按字面读取，不执行 shell 插值；进程环境变量优先于配置文件。相对路径按**工作目录**解析，本文固定为 `C:\hive`，也可填写绝对路径。修改配置后重启 API 和 worker。务必确认 `--env-file` 指定文件存在，否则程序可能使用默认值。

管理员必须使用密码登录，未配置密码哈希时拒绝管理员登录。升级后原有的免密管理员会话失效，需重新登录。普通成员仍使用固定用户名登记；首次登录后默认只能查看信息，由管理员在“人员管理”中分别开启服务器申请和查看服务器密码权限。后端实时检查权限，已有申请不会自动授予密码查看权限；撤销申请权限后仍可归还已有资源。

人员管理可删除普通成员，删除会使其全部会话失效并禁止该用户名重新登录，同时保留历史申请和审计记录。有未结束的申请或任务时须先结束；不能删除管理员。普通成员用户名登记不验证真实身份，适用于受控团队网络。

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
| Ascend-DMI（MindCluster ToolBox） | 可选；管理员手动执行 FP16 实测算力，普通纳管与采样不需要 |

普通纳管无需额外安装 Python、Node.js、MySQL、CANN 或 PyTorch；实际调试任务的业务环境由用户自行准备。若使用实测算力功能，节点还需兼容的 ToolBox 与 CANN 运行库（`libascendcl.so` 等），仅安装驱动不能保证测试可用。驱动/固件按现场设备要求安装，本平台不自动安装或升级驱动。

非交互采集使用系统 PATH 加 `/usr/local/Ascend/driver/tools`；工具仅在交互 `.bashrc` 中可见时可能采集失败。在节点核对：

```bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/Ascend/driver/tools
command -v bash timeout base64 findmnt setsid flock ps npu-smi
npu-smi info
npu-smi info -m
uname -a
uname -m
```

#### 可选：实测算力

在需要测试的 Linux 节点安装与其 CPU 架构、驱动兼容的官方 MindCluster ToolBox 和 CANN 运行库。ARM64 使用 aarch64 安装包，x86_64 使用 x86_64 安装包；不要把 Windows 中心机的架构当作 NPU 节点架构。包的版本和安装权限要求以对应版本的厂商说明为准。平台不分发安装包，也不自动修改节点业务环境。

平台优先在系统路径和 `/usr/local/Ascend/toolbox/latest/Ascend-DMI/bin`、`/usr/local/Ascend/toolbox/latest/bin` 查找 `ascend-dmi`，并兼容 `/usr/local/Ascend/toolbox/*/Ascend-DMI/bin` 下已安装的版本。只读检查可在节点执行：

```bash
source /usr/local/Ascend/toolbox/set_env.sh
# 按已安装的 CANN 布局选择一个存在的环境脚本
source /usr/local/Ascend/cann/set_env.sh
# 较早版本的对应路径为 /usr/local/Ascend/ascend-toolkit/set_env.sh
ascend-dmi --version
ascend-dmi -f -h
```

安装完成后，在“集群管理 → 重新检测”刷新工具状态。管理员在“查看检测结果 → 实测算力”手动触发测试；整机有申请、实际 NPU 进程、非零 AI Core、缺测或未确认的空闲显存基线时，后端拒绝执行。测试期间暂停该节点的新申请，后台执行不阻塞普通指标采集。页面显示逐逻辑设备的 FP16 实测 TFLOPS，摘要为最小值–最大值；不会将结果换算或标为 560T / 752T 理论规格。

`ascend-dmi --version` 成功只代表工具可运行，不代表算力所需运行库齐全。如果出现 `libascendcl.so` 加载失败，按 [官方运行库排查说明](https://www.hiascend.com/document/detail/zh/mindcluster/730/toolbox/toolboxug/toolboxug_0097.html) 检查 CANN 安装与动态库路径。详细命令、占用判断和异常恢复见 [硬件与算力说明](docs/hardware-profile.md)。

### 6.2 登记主机公钥

推荐在“集群管理 → 纳管节点”填写地址、SSH 账号和密码后点击“检测连接”。首次连接会显示服务器的 SHA256 主机指纹；通过可信控制台或节点管理员核对后，勾选确认再继续。已登记的公钥发生变化时仍会拒绝连接，不会自动覆盖旧公钥。

检测通过后自动读取系统机型、CPU 架构和 HDK 信息，无需手填机型；系统没有提供机型时明确显示未识别。点击“添加节点”会再次验证连接，然后保存节点及核验过的公钥。连接或认证失败不会创建节点。运行账号需要能写入 `data/known_hosts` 所在目录。NPU 代际仍由管理员按实际设备选择。

页面每 3 秒刷新，节点行显示等待首次采样、正在采集、采集失败、最近采样时间，以及后续互联与共享盘检测状态。后台默认每 15 秒发起一次采集，单次查询时间随节点和进程数量变化；失败时显示具体原因并自动重试。

管理员可通过“移除节点”停止纳管。有效申请、运行中任务或算力测试未结束时禁止移除。移除会清除保存的节点密码，保留历史申请和采样记录，不修改远端进程与文件；之后可以重新添加相同地址。

也可沿用下面的命令行方式预先登记公钥：

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

1. 打开 `.env` 中对应的 `http://中心机IP:18000/login`，页面样式正常，输入 `admin` 和配置的管理员密码登录。
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

## 12. PR 任务编排与容器执行

“机器申请”从上到下展示机器资源、任务环境、任务 Jobs；多个环境和多个 job 分别使用 Tab。任务名默认为创建时间，可以修改。使用 vllm-project/vllm-ascend PR；提交时读取固定提交中的 `.github/vllm-main-verified.commit`，冻结对应的 vLLM SHA、执行文件和输入摘要。普通 PR 使用 head；nightly 基线可以使用已合并 PR 的 merge 提交，记录 `revision=merged`。PR 更新后需要重新解析再提交，已提交任务不随 PR 自动变化。

当前浏览器按用户名自动保存完整申请草稿到 IndexedDB，包括上传的文本文件；切换页面、刷新后可继续编辑。草稿仅存在当前浏览器，不跨设备同步。成功提交后清除草稿；空间引用在提交时仍由服务端校验。无需增加数据库或前端依赖。

每个任务包含多个 job，依赖只通过作业图连线设置，连线旁提供删除按钮。图中显示已添加的前处理和后处理；点击 + 才展开相应文件与启动命令。每个 job 默认超时 **10 分钟**，可修改。文件直接多选上传，支持 Shell、Python、YAML；另有“启动命令”用于调用脚本。上传 Shell/Python 需与固定 PR 中的文件一致；相同文件名在 PR 中重名时，可以将上传名称补成 PR 相对路径。YAML 可覆盖配置内容，由启动命令指定外部 runner；Hive 不解释业务 YAML。未填写启动命令时按上传顺序运行 Shell/Python 文件。

每个环境配置容器代称、镜像和一个或多个节点代称，不需要选择服务端/客户端角色。同一环境选 node0、node1 时分别创建独立容器；两个环境都选 node0 时在同一机器创建两个容器。安装/核验通过上传文件与启动命令完成。job 默认在其环境的所有节点分别执行，也可选择其中部分节点；纯请求客户端可以选 0 张 NPU。服务 job 需提供就绪检查，依赖等待所有上游实例 ready，普通 job 等待所有上游实例 succeeded。无依赖且卡不冲突的 job 可以并行，同一宿主机最多 4 个运行 job。端口和普通环境变量由脚本自行配置。现有启动脚本映射所有卡，实际使用范围仍依赖团队遵守分配规则。

安装环境可由多个任务复用。同一运行空间继续持有原资源申请，后续任务可选择该空间。当前复用要求 PR/vLLM SHA 和安装配置相同，支持替换测试 YAML；改变安装代码或依赖时新建空间。默认所有任务结束后关闭环境并归还资源；勾选保留后按页面时长保留，并可主动关闭。取消单个任务不会关闭其他任务使用的环境，关闭空间会取消其中未结束任务。关闭结果不明确时继续保留资源并显示原因。

### 12.1 部署与依赖

中心机继续使用现有 Python/MySQL/API/worker，页面仍部署在 **18000**。升级代码后停止 API 和 worker，使用同一环境配置运行迁移，再构建前端并启动两个进程：

```powershell
.\.venv\Scripts\python.exe -m hive.cli migrate --env-file .env
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

按上文已有 Windows 启动方式重新启动。新增的工作流和预置表随 `migrate` 自动建立；不会导入机器密码、历史数据或示例任务。没有 Redis、Celery、Slurm、节点 agent 或中心机 YAML 解析依赖。Playwright 仅用于开发验证，不是运行服务所需依赖。

节点需要已有 SSH、Docker、Bash、`flock`、`timeout`、`setsid`、`ps`、`sha256sum`、`base64` 等基础工具。容器也需这些进程控制工具及 Git；Python 入口还需对应解释器。业务依赖全部由所选镜像和 PR 安装入口负责，Hive 不会在裸机安装 CANN、PyTorch 或测试工具。

当前登记的外部引导入口是 `/mnt/share/c00814587/start-docker-A3.sh`，参数为镜像和容器名。它需要节点已有镜像；Hive 固定镜像 ID 后调用，不自动拉取猜测的镜像。入口也可引用 PR 中的文件。容器创建是宿主机引导步骤；其余业务步骤均在绑定的容器中运行。启动器需在时限内同步返回，退出 0 表示指定容器已创建；不要让启动器在返回后另起延迟创建容器的后台工作。

### 12.2 外部脚本参数与结果

参数数组支持 `${source_dir}`、`${image}`、`${container_name}`、`${ascend_sha}`、`${vllm_sha}`、`${task_id}`、`${job_id}`、`${node0.ip}`、`${port}` 等引用；`${服务jobID.endpoint}` 引用依赖服务的地址。环境准备阶段尚无 job ID，部署脚本应使用环境/节点上下文。

容器内提供 `HIVE_CONTEXT_JSON`、`HIVE_SOURCE_DIR`、`HIVE_PACKAGES_JSON` 和平台分配的 `ASCEND_RT_VISIBLE_DEVICES`。安装入口读取包名、版本和来源等输入，自行完成安装及核验；客户端的包列表独立于服务端。未提供安装脚本或软件包链接时，平台不会凭版本号推测安装命令。

每项产物配置环境/容器与节点目标（可多选）、名称和容器内绝对路径，一行一项。不同节点或容器的相同路径分别归档；一个明确目标不会因多个 job 实例重复归档。不要求选择文件类型。普通文件原样保存；包含 metrics 字段的 JSON 按下述通用协议校验展示，Hive 不计算业务指标或阈值：

```json
{
  "metrics": [
    {"name": "metric_name", "value": 1.0, "unit": "unit", "tags": {}, "verdict": "unknown"}
  ],
  "verdict": "unknown"
}
```

`verdict` 支持 `passed`、`failed`、`unknown`。缺失时显示“业务判定未提供”，不会把步骤退出 0 当作性能/精度达标。外部失败判定不会被后置脚本成功覆盖。上例只说明数据格式，不是示例测试结果。

每个执行阶段最多保留 20 MiB 原始日志，网页展示尾部并支持下载完整归档；达到上限会标记截断。每项产物最多 4 MiB、每个 job 最多 16 项/总计 16 MiB。归档存放在 `HIVE_DATA_DIR` 下的 `workflow-logs` 和 `workflow-artifacts`，不写入 Git。当前不自动删除归档，需要部署者为该目录预留容量和备份。

### 12.3 nightly / weekly 预置接入

预置有两种来源：从 PR 中的 JSON 清单导入，或将平台上实际执行成功的任务发布为预置。清单格式为 `{"items":[{"id":"...","name":"...","tags":{},"workflow":{...}}]}`，workflow 使用[任务接口契约](docs/workflow-api.md)中的通用字段。上游已有的 nightly pytest 入口可直接调用，不要求为已有用例补写业务适配。Hive 不解析业务 YAML、不重写测试或阈值判定。

管理员调用 `POST /api/presets/from-workflow`，提交 `{workflow_id,item_id,name,tags}`，保存成功任务的可重建配置和来源执行 ID；发布时清除本次验收的指定机器约束。发布不自动启用。用户应用已启用预置后，可修改 YAML、命令、资源参数，通过“另存新用例”保存个人变体；保留原代码提交与父预置，标记为未验证，不覆盖基线。个人变体仅本人及管理员可见。

管理员可在机器申请的预置区导入清单并单项启用，也可通过 `POST /api/presets/import` 提交 `{source:{pr,head_sha,vllm_sha},path:"PR内清单.json"}`。所有导入项初始禁用，可筛选标签并查看来源。首批只开放一个已确认样例：将 `HIVE_WORKFLOW_SAMPLE_PRESET_ID` 配置为该清单条目的 `id`（不是数据库 UUID），重启 API 后由管理员单项调用 `POST /api/presets/{数据库UUID}/enable` 批准。默认配置为空时全部不可启用，不提供批量启用操作。

指定 Qwen3 样例的上游调查记录见[任务设计](docs/task-workflows.md)。真实业务验收需要实际 PR、对应的部署/测试/校验入口、本地镜像、权重及数据集；尚未提供这些输入时，空预置列表是正常状态。其他 nightly/weekly 用例等待样例验收后再接入执行。

### 12.4 节点资源映射与动态参数

纳管节点时可选填写四类映射，管理员之后可在节点详情中增删，其他用户可查询：

| 类型 | 逻辑名称 | 节点上的目标 |
|---|---|---|
| model | ModelScope 组织/权重名 | 权重绝对路径 |
| dataset | ModelScope 组织/数据集名 | 数据集绝对路径 |
| image | 镜像别名 | 本机镜像名/标签/摘要 |
| package | 依赖包别名 | 软件包绝对路径 |

环境镜像可以填写已登记的镜像别名；平台按实际分配节点解析。映射登记只保存数据，不下载权重、安装包或拉取镜像。容器启动器需把这些目录挂载到容器内的相同路径，脚本负责检查所需文件是否存在。

首次分配时冻结每台节点的映射版本，环境复用继续使用该快照。管理员后续修改用于新空间，避免运行中的任务突然指向其他文件。一个节点最多 128 条、合计 16 KiB；并发编辑使用版本号检查，冲突时重新读取。

平台预置 `HIVE_NODE0_IP`、`HIVE_NODE1_IP` 等所有分配节点的实际 IP；`HIVE_HOST_IP` / `${host}` 始终是当前实例所在服务器，`HIVE_CONTAINER_NAME` / `${container_name}` 是当前真实容器名。启动命令可用 `${node0.ip}`、`${node1.ip}` 访问任意已申请节点。Python 可读取 `HIVE_NODES_JSON` 获取全部节点上下文。多节点服务使用 `${服务jobID.node0.endpoint}` 指定目标实例；同节点的简写 `${服务jobID.endpoint}` 指向本节点实例，无同节点且存在多个实例时必须写明节点。普通 Bash 变量及端口由用户脚本管理。

Bash 入口可用 `hive_resource model 组织/权重名`、`hive_resource dataset 组织/数据集名`、`hive_resource package 包别名` 查询本环境冻结的实际路径；不存在的映射返回非零退出码。Python 入口读取 `os.environ["HIVE_RESOURCE_MAP_JSON"]`，JSON 结构为 `{"model":{},"dataset":{},"image":{},"package":{}}`，每类按逻辑名称索引。平台只提供通用路径解析，模型及测试逻辑仍维护在外部 PR。

查询接口为 `GET /api/nodes/{id}/mappings`；管理员替换为 `PUT` 同路径，参数 `{version,entries:[{kind,name,target}]}`，传空 entries 可清空。

### 12.5 开发验证

```powershell
$env:HIVE_TEST_MYSQL_PORT = "测试MySQL端口"
$env:HIVE_TEST_MYSQL_USER = "测试账号"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd --prefix frontend run test:browser
```

MySQL 测试使用独立随机测试库，GitHub 与调度测试的 SSH 都在外部边界模拟。浏览器测试在独立随机端口运行并拦截 API，不提交真实任务。Linux 进程协议还需隔离容器实测；仅 Windows mock 通过不能作为 NPU 业务验收证据。

本次平台回归与 Linux 协议实测结果见[任务编排验收记录](docs/workflow-acceptance.md)，其中列明尚待业务 PR 和资源完成的样例验收。
