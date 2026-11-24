# 部署手册：内网服务器上线

本手册面向**长期运行在内网服务器上**的场景：开机自启、崩溃自动拉起、数据可备份、版本可回滚。

与 [README](../README.md) 的分工：README 讲怎么在开发机跑起来、怎么临时共享给邻座；本手册讲怎么把它变成一台服务器上「不用管也不会停」的服务。

---

## 0. 先做三个选择

### 0.1 部署形态：离线包，不要源码

| | 离线包（推荐） | 源码方式 |
|---|---|---|
| 目标机需要 | Python 3.11/64 位 | Python 3.11 + Node 20.19 + pnpm |
| 进程数 | 1 个（前端与接口同源） | 2 个（Vite 或静态服务器 + uvicorn） |
| 构建 | 开发机构建一次，拷 zip | 目标机现场装依赖、编译 |
| 前端更新 | 随包替换 | 需要重新 build |

选离线包的理由：服务器上不需要 Node 工具链，少一个进程就少一处故障点，前后端同源也不存在 CORS 和代理配置问题。源码方式只适合开发机。

### 0.2 常驻方式：Windows 服务（NSSM），不要双击 cmd

`start-lan.cmd` 是**前台进程**：关掉窗口就停、必须有人登录、崩溃不会重启。它适合临时试用，不适合上线。

| 方案 | 自动重启 | 开机自启 | 免登录 | 评价 |
|---|---|---|---|---|
| NSSM 注册为 Windows 服务 | 是 | 是 | 是 | **推荐**，成熟稳定，一条命令配完 |
| 任务计划程序 | 否 | 是 | 是 | 备选，崩溃后需人工拉起 |
| 启动文件夹 / 双击 cmd | 否 | 否 | 否 | 不要用于上线 |

### 0.3 访问方式：先直连 8765，需要再加反向代理

- **直连**：浏览器访问 `http://<服务器内网IP>:8765/`。够用、零额外组件。
- **反向代理**（IIS / nginx）：只有在需要统一 80 端口、HTTPS、按 IP 白名单时才上。

注意：本系统**没有登录鉴权**，放行端口等于对内网所有人开放。详见第 9 节。

---

## 1. 在联网的开发机上构建离线包

### 1.1 前置

```bash
pnpm install
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
```

### 1.2 构建

```bash
backend/.venv/Scripts/python scripts/build_bundle.py
```

脚本做三件事：`pnpm build` 出前端产物、从 PyPI 下载 cp311/win_amd64 轮子、把 `backend/app` 与 `dist` 拷进包并生成三个启动脚本。

产物：`deploy/olive-win64-<日期>.zip`（约 7 MB）。每次构建都会新建 `deploy/olive-N` 目录，不会覆盖上一次。

### 1.3 产物校验（必做）

**这一步不能省。** 历史上出现过 `wheels/` 下载失败但打包照样完成的情况，结果目标机 `setup.cmd` 报「安装失败，检查 Python 是否为 64 位」——提示语误导，真实原因是包里根本没有轮子。

解压或用压缩软件打开 zip，确认：

- `wheels/` 里有 **21 个 `.whl` 文件**，其中二进制轮子必须是 `cp311-cp311-win_amd64`（`httptools`、`pydantic_core`、`pyyaml`、`sqlalchemy`、`websockets`）
- `web/index.html` 存在
- `app/main.py` 存在
- `setup.cmd`、`start.cmd`、`start-lan.cmd` 三个都在

数量不对就重新构建，不要在目标机上试。

---

## 2. 目标服务器准备

### 2.1 系统与软件

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows Server 2016+ / Windows 10+（64 位） |
| Python | **3.11 / 64 位**，安装时勾选 `Add python.exe to PATH` |
| 磁盘 | 程序 300 MB + 数据按实际（SQLite 单文件 + 附件） |
| 其他 | 不需要 Node、不需要联网（轮子在包里） |

### 2.2 验证 Python

```cmd
py -3.11 -c "import sys,platform;print(sys.version);print(platform.architecture())"
```

必须输出 `64bit`。若是 32 位或版本不符，`setup.cmd` 装依赖会失败——包里只有 cp311/win_amd64 轮子。

### 2.3 目录规划

```text
D:\OcapLive\              <- 程序（解压目录，纯 ASCII、无空格）
  app\  web\  wheels\
  runtime\venv\          <- setup.cmd 创建
  data\                  <- 数据库 + 附件（要备份）
  logs\                  <- 日志
```

**路径不要带中文和空格。** Windows 上子进程调用、编码、路径拼接遇到空格/中文都可能出问题，业务工程的路径同理。

建议不要放在 `C:\Program Files` 下：服务账号对该目录的写权限受限，`data\`、`logs\` 会写不进去。

### 2.4 防火墙

```cmd
netsh advfirewall firewall add rule name="OcapLive 8765" dir=in action=allow protocol=TCP localport=8765
```

只在内网开放。服务器如果同时有公网网卡，见 9.2。

---

## 3. 安装

### 3.1 解压

把 zip 拷到服务器，解压到 `D:\OcapLive`。zip 内部顶层目录名为 `olive`，解压后确认层级是 `D:\OcapLive\setup.cmd`，而不是 `D:\OcapLive\olive\setup.cmd`。

### 3.2 建虚拟环境

在 `D:\OcapLive` 下双击 `setup.cmd`，或命令行执行：

```cmd
cd /d D:\OcapLive
setup.cmd
```

正常输出两行关键信息后结束：

```text
正在创建虚拟环境（需要本机 Python 3.9+，推荐 3.11 64 位）...
使用解释器：py -3.11
完成。以后双击 start.cmd 启动。
```

若出现「安装失败」，按顺序排查：

1. `wheels\` 是否为空（第 1.3 节）——最常见
2. `py -3.11` 是否为 64 位
3. 杀毒软件是否拦截了 `runtime\venv\Lib\site-packages\*.pyd` 的写入

### 3.3 首次启动验证

先手工起一次，确认能跑再注册服务：

```cmd
cd /d D:\OcapLive
runtime\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --app-dir D:\OcapLive
```

另开一个窗口：

```cmd
curl http://127.0.0.1:8765/api/health
```

返回 `{"ok":true}` 即正常。浏览器打开 `http://127.0.0.1:8765/` 应看到页面。`Ctrl+C` 停掉这个临时进程，进入下一步。

> 注意：必须带 `--app-dir`，或先 `cd` 到解压目录。前端静态目录由环境变量 `OLIVE_WEB_DIR` 指定（服务方式务必显式设置），否则可能找不到 `web/`。

---

## 4. 注册为 Windows 服务（推荐）

### 4.1 准备 NSSM

下载 NSSM（nssm.cc）后把 `win64\nssm.exe` 放到 `D:\OcapLive\tools\nssm.exe`，或以管理员身份打开 cmd 并把它所在目录加入 PATH。

以下命令**全部在管理员 cmd** 中执行。

### 4.2 安装并配置

```cmd
nssm install OcapLive "D:\OcapLive\runtime\venv\Scripts\python.exe"
nssm set OcapLive AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir D:\OcapLive"
nssm set OcapLive AppDirectory "D:\OcapLive"
nssm set OcapLive DisplayName "OcapLive 诊断流程平台"
nssm set OcapLive Description "设备诊断流程平台（内网，无鉴权）"
nssm set OcapLive Start SERVICE_AUTO_START
```

**环境变量（关键）**：

```cmd
nssm set OcapLive AppEnvironmentExtra PYTHONUTF8=1 PYTHONIOENCODING=utf-8 OLIVE_WEB_DIR=D:\OcapLive\web OLIVE_DATA_DIR=D:\OcapLive\data OLIVE_DB=D:\OcapLive\data\olive.db OLIVE_LOG_DIR=D:\OcapLive\logs OLIVE_LOG_LEVEL=INFO
```

**控制台输出另存（uvicorn 启动期报错不写进 app.log，只能从 stdout 看）**：

```cmd
nssm set OcapLive AppStdout "D:\OcapLive\logs\service-out.log"
nssm set OcapLive AppStderr "D:\OcapLive\logs\service-err.log"
nssm set OcapLive AppRotateFiles 1
nssm set OcapLive AppRotateOnline 1
nssm set OcapLive AppRotateBytes 10485760
```

**崩溃自动重启**：

```cmd
nssm set OcapLive AppExit Default Restart
nssm set OcapLive AppRestartDelay 5000
nssm set OcapLive AppStopMethodConsole 15000
```

### 4.3 启动与验证

```cmd
nssm start OcapLive
sc query OcapLive
curl http://127.0.0.1:8765/api/health
```

- `sc query` 应为 `STATE : 4 RUNNING`
- 他机浏览器访问 `http://<服务器IP>:8765/`

**重启服务器验证一遍**：服务应自动起来，页面可用。这一步不做，等于没验证自启。

### 4.4 服务账号（业务工程在内网共享路径时才需要）

服务默认以 `LocalSystem` 运行，访问 `\\NAS\...` 这类 UNC 路径会失败。改成域账号：

```cmd
nssm set OcapLive ObjectName "DOMAIN\svc-olive" "密码"
nssm restart OcapLive
```

该账号需要对解压目录有读写权限（`data\`、`logs\`）。

### 4.5 备选：任务计划程序（不想装 NSSM）

在 `D:\OcapLive` 下新建 `start-service.cmd`：

```cmd
@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "OLIVE_WEB_DIR=%~dp0web"
set "OLIVE_DATA_DIR=%~dp0data"
set "OLIVE_DB=%~dp0data\olive.db"
set "OLIVE_LOG_DIR=%~dp0logs"
if "%OLIVE_LOG_LEVEL%"=="" set "OLIVE_LOG_LEVEL=INFO"
"%~dp0runtime\venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir "%~dp0"
```

然后：

```cmd
schtasks /create /tn "OcapLive" /tr "D:\OcapLive\start-service.cmd" /sc onstart /ru SYSTEM /rl HIGHEST /f
schtasks /run /tn "OcapLive"
```

代价：**进程崩溃后不会自动拉起**，只能靠人发现页面打不开再去启动。所以能用 NSSM 就用 NSSM。

---

## 5. 首次业务配置

服务起来后，按这个顺序配：

1. **登记业务工程**：顶部「工程」→「登记工程」
   - 路径填**服务器上**的绝对路径，不是开发机的路径
   - 模块名如 `motor`（对应工程根目录下的 `motor.py`）
   - 解释器：业务工程自己的 Python 绝对路径。**平台不会往这个解释器里装依赖**，业务依赖（numpy、串口 SDK）必须提前在服务器上装好
   - 解释器留空会回落到平台 venv 的 Python，此时业务依赖就要装进 `runtime\venv`——不推荐，升级重建 venv 会丢
2. **点「接口」验证探测**：能看到类、方法与签名才算通。探测失败会直接显示报错
3. **建流程 → 保存 → 发布**
4. **启动一次，走完待办**，确认业务函数真能取到数

业务工程改了函数签名，必须重新发布；执行前签名指纹会对不上并拦下。

---

## 6. 数据、日志与备份

### 6.1 都在哪

| 内容 | 路径 | 说明 |
|---|---|---|
| 数据库 | `D:\OcapLive\data\olive.db` | SQLite，WAL 模式，另有两个 `-wal` / `-shm` 文件 |
| 附件 | `D:\OcapLive\data\files\<xx>\<hash>` | 按内容哈希分层存放 |
| 应用日志 | `D:\OcapLive\logs\app.log` | 5 MB 轮转，保留 5 份，UTF-8 |
| 服务控制台 | `D:\OcapLive\logs\service-out.log` / `service-err.log` | 启动期崩溃看这里 |

### 6.2 备份

WAL 模式下**不能只复制 `.db` 一个文件**，会丢最近的写入。两种可靠做法：

- **停服复制整个 `data` 目录（最稳）**
  ```cmd
  nssm stop OcapLive
  robocopy D:\OcapLive\data D:\Backup\OcapLive\%date:~0,4%%date:~5,2%%date:~8,2% /E /COPYALL
  nssm start OcapLive
  ```
- **不停服**：用 SQLite 官方 `.backup` 命令导出单文件快照，再复制附件目录

建议用任务计划程序建一个每日定时任务跑上面的 robocopy，并把备份目录放到另一块盘或网络存储。保留份数按现场要求，至少 7 天。

### 6.3 恢复

1. 停服务
2. 把备份的 `data` 目录整体覆盖回去（先移走现有 `data`，不要混着放）
3. 启动服务，`/api/health` 确认存活，进页面抽查一条历史执行记录

### 6.4 日志

每个 HTTP 响应都带 `X-Request-ID`，日志行前缀 `[xxxxxxxx]` 与它一致。排查某次操作时，用这个 id 串起一次请求的全部日志。

临时要提高日志级别：

```cmd
nssm set OcapLive AppEnvironmentExtra ... OLIVE_LOG_LEVEL=DEBUG
nssm restart OcapLive
```

`OLIVE_LOG_PAYLOAD=1` 会把调用实参与返回值也写日志，**只在排障期间开**，否则业务数据会落到磁盘上。

---

## 7. 升级与回滚

原则：**程序目录可替换，数据目录不可覆盖。**

### 7.1 升级

```cmd
nssm stop OcapLive
```

1. 新包解压到**新目录**，如 `D:\OcapLive-20261110`，不要覆盖旧目录
2. 在新目录跑一次 `setup.cmd`（幂等，已有 venv 会复用并补装依赖）
3. 把旧目录的 `data\` **整体复制**到新目录（升级期间产生的增量写入会丢，所以选停机窗口做）
4. 改服务指向新目录：
   ```cmd
   nssm set OcapLive Application "D:\OcapLive-20261110\runtime\venv\Scripts\python.exe"
   nssm set OcapLive AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir D:\OcapLive-20261110"
   nssm set OcapLive AppDirectory "D:\OcapLive-20261110"
   nssm set OcapLive AppEnvironmentExtra ... OLIVE_WEB_DIR=D:\OcapLive-20261110\web OLIVE_DATA_DIR=D:\OcapLive-20261110\data OLIVE_DB=D:\OcapLive-20261110\data\olive.db OLIVE_LOG_DIR=D:\OcapLive-20261110\logs
   nssm start OcapLive
   ```
5. 验证 `/api/health` 与页面，抽查一条历史记录
6. 观察一天，再删旧目录

### 7.2 回滚

把第 4 步的三条路径改回旧目录，启动即可。**旧目录和旧 `data` 都不动**，所以回滚是分钟级的。

### 7.3 前后端必须同版本

`app/`（后端）和 `web/`（前端）来自同一次构建。只换其中一个会导致接口字段对不上。升级一律整包换。

### 7.4 数据库结构变更

启动时 `init_db()` 会自动建缺失的表、补齐老库缺的列（见 `backend/app/db.py` 的 `_migrate`）。**不做降级迁移**：新版本改过的库，回滚到旧版本可能因多出的列而报错。所以回滚前先把升级前的 `data` 备份准备好。

---

## 8. 监控与故障排查

### 8.1 探活

```cmd
curl http://127.0.0.1:8765/api/health
```

返回 `{"ok":true}`。这个端点不写 INFO 日志，可以高频轮询。要接监控系统就监控它。

### 8.2 常见故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| 服务起不来，`sc query` 不是 RUNNING | Python 路径或参数错 | 看 `logs\service-err.log` |
| 页面打不开但 `/api/health` 正常 | `OLIVE_WEB_DIR` 没设或指错 | 指向解压目录下的 `web` |
| 他机打不开，本机正常 | 防火墙未放行 8765，或绑了 127.0.0.1 | 放行端口；`--host 0.0.0.0` |
| `setup.cmd` 报「安装失败，检查 Python 是否为 64 位」 | 多数是 `wheels\` 为空或轮子 ABI 不符 | 回到 1.3 节校验产物 |
| 执行报「工程未登记」「函数不存在」 | 服务器上工程路径/模块/解释器不对 | 工程页点「接口」看探测报错 |
| 执行前被拦「函数签名与发布时不一致」 | 业务函数改过但没重新发布 | 确认改动后发布新版本 |
| 端口被占用 | 8765 已被别的进程监听 | `netstat -ano | findstr :8765` 查 PID |
| 服务运行但业务调用超时 | 业务解释器缺依赖，或子进程无权限访问设备/网络 | 用登记的 Python 手工跑一次业务函数 |

### 8.3 现场取信息的三件东西

1. `logs\app.log` 里对应时间段的 `[request-id]` 行
2. `logs\service-err.log` 尾部（启动期崩溃）
3. `sc query OcapLive` 的 STATE

---

## 9. 安全边界

### 9.1 没有鉴权

系统没有登录、没有角色、没有操作审计到人（只有操作记录）。**任何能访问 8765 的人都能改流程、发布版本、启动执行。**

因此：

- 只在内网开放，不要做端口映射到公网
- 服务器上不要同时跑别的对外服务
- 需要区分人的时候，先上反向代理做 IP 白名单，不要指望应用层

### 9.2 只绑内网网卡

服务器若有多块网卡（含公网），`--host 0.0.0.0` 会把服务暴露到所有网卡。改成绑定内网 IP：

```cmd
nssm set OcapLive AppParameters "-m uvicorn app.main:app --host 192.168.1.10 --port 8765 --app-dir D:\OcapLive"
```

### 9.3 需要 HTTPS / 统一入口时

在前面挂 IIS 或 nginx 反向代理到 `127.0.0.1:8765`，并：

- 反向代理所在机器与服务同机时，服务可以只绑 `127.0.0.1`
- 在代理层做 IP 白名单
- 前端是 SPA，代理要配置「非静态资源回落到 `index.html`」，否则刷新子路由会 404

### 9.4 最小权限

- 服务账号不用 Administrator，用专用账号
- 只给解压目录 + 业务工程目录的读写权限
- 业务工程目录给**只读**即可（平台只调用，不写入）

---

## 附录 A：环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OLIVE_WEB_DIR` | 同包 `web/` | 前端静态目录。服务方式务必显式设置 |
| `OLIVE_DATA_DIR` | 同包 `data/` | 数据根目录，附件在 `files/` 下 |
| `OLIVE_DB` | `data/olive.db` | 数据库文件路径，也可给完整连接串 |
| `OLIVE_LOG_DIR` | 同包 `logs/` | 日志目录 |
| `OLIVE_LOG_LEVEL` | `INFO` | 改为 `DEBUG` 输出调度细节 |
| `OLIVE_LOG_PAYLOAD` | 空 | 设 `1` 时记录实参与返回值，仅排障期开 |
| `PYTHONUTF8` / `PYTHONIOENCODING` | 空 | 服务方式设为 `1` / `utf-8`，避免中文乱码 |

## 附录 B：目录、端口、进程

```text
D:\OcapLive\
  app\                 后端代码（随包替换）
  web\                 前端产物（随包替换）
  wheels\              离线轮子（仅 setup 用）
  runtime\venv\        平台虚拟环境（可重建）
  data\                数据库与附件（必须备份）
  logs\                日志
  setup.cmd            建环境
  start.cmd            前台启动，仅本机
  start-lan.cmd        前台启动，监听所有网卡
  tools\nssm.exe       服务注册工具（手工放置）
```

| 项 | 值 |
|---|---|
| 监听端口 | 8765（离线包）/ 8000 + 5173（源码方式） |
| 探活端点 | `GET /api/health` |
| 接口文档 | `GET /docs`（需后端监听 `0.0.0.0` 才能从他机访问） |
| 进程 | 1 个 uvicorn 主进程 + 按需派生的业务会话子进程 |

## 附录 C：上线检查清单

- [ ] `py -3.11` 验证输出 `64bit`
- [ ] zip 里 `wheels/` 有 21 个 `.whl`
- [ ] 解压路径纯 ASCII、无空格，不在 `Program Files` 下
- [ ] `setup.cmd` 输出「完成」
- [ ] `curl http://127.0.0.1:8765/api/health` 返回 `{"ok":true}`
- [ ] 服务已注册，`Start` 为 `SERVICE_AUTO_START`
- [ ] 防火墙放行 8765，他机浏览器能打开
- [ ] **重启服务器后服务自动启动、页面可用**
- [ ] 业务工程已登记，「接口」探测成功
- [ ] 流程已发布，试跑一次走完全程
- [ ] 备份任务已建，手工跑一次并确认备份目录有内容
- [ ] 升级/回滚步骤已在纸面走通一遍（知道改哪三行配置）

## 附录 D：从 NewDiag 改名到 OcapLive

平台 2026-11-24 由 NewDiag 更名为 OcapLive，运行期的名字一并换了，**新代码不认旧名**。已经在跑的服务器按下面五步切换，缺一步都起不来。

改名前先备份，出事能退回去：

```bat
nssm stop NewDiag
robocopy D:\NewDiag\data D:\Backup\NewDiag-pre-rename /E /COPYALL
```

### D.1 数据库文件改名

SQLite 开 WAL，三个文件必须一起改，漏掉 `-wal` 会丢掉最近的事务：

```bat
cd /d D:\NewDiag\data
ren newdiag.db olive.db
ren newdiag.db-wal olive.db-wal
ren newdiag.db-shm olive.db-shm
```

`-wal` / `-shm` 不存在是正常的（上次停服干净就没有），没有就跳过那一行。

### D.2 换环境变量和服务名

服务名、显示名一起换，旧服务先删掉，避免两个服务抢 8765 端口：

```bat
nssm stop NewDiag
nssm remove NewDiag confirm
nssm install OcapLive "D:\OcapLive\runtime\venv\Scripts\python.exe"
nssm set OcapLive AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir D:\OcapLive"
nssm set OcapLive AppDirectory "D:\OcapLive"
nssm set OcapLive DisplayName "OcapLive 诊断流程平台"
nssm set OcapLive Start SERVICE_AUTO_START
nssm set OcapLive AppEnvironmentExtra PYTHONUTF8=1 PYTHONIOENCODING=utf-8 OLIVE_WEB_DIR=D:\OcapLive\web OLIVE_DATA_DIR=D:\OcapLive\data OLIVE_DB=D:\OcapLive\data\olive.db OLIVE_LOG_DIR=D:\OcapLive\logs OLIVE_LOG_LEVEL=INFO
nssm start OcapLive
```

### D.3 换目录名

`D:\NewDiag` 可以直接重命名成 `D:\OcapLive`，也可以在旧目录里就地升级、只改服务配置里的路径。前者更省事，但重命名前必须先 `nssm stop`，Windows 会锁住正在执行的文件。

### D.4 环境变量对照表

| 旧名 | 新名 |
|---|---|
| `NEWDIAG_WEB_DIR` | `OLIVE_WEB_DIR` |
| `NEWDIAG_DATA_DIR` | `OLIVE_DATA_DIR` |
| `NEWDIAG_DB` | `OLIVE_DB` |
| `NEWDIAG_LOG_DIR` | `OLIVE_LOG_DIR` |
| `NEWDIAG_LOG_LEVEL` | `OLIVE_LOG_LEVEL` |
| `NEWDIAG_LOG_PAYLOAD` | `OLIVE_LOG_PAYLOAD` |

自建的 `start-service.cmd`、计划任务、反向代理、监控脚本里如果写死了旧名，一并改掉。

### D.5 业务工程侧的两处

- 工程根目录下用来排除大目录的 `.newdiagignore` 改名成 `.oliveignore`，不改就不生效，发布时会连数据集一起拷进源码副本。
- 旧版导出的流程包 `manifest.json` 里 `kind` 是 `newdiag-flow-package`，新版是 `olive-flow-package`。导入时不校验该字段，旧包照常能导；只有脚本按 `kind` 做判断的地方需要跟着改。

### D.6 已经生成的实例不受影响

每个实例目录（`data/executions/<id>/`）自带一份 `runtime/generated_runtime.py` 和 `session.py`，协议名跟着各自副本走。改名只影响之后新生成的实例，历史实例照旧跑完。
