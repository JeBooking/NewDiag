# 诊断流程平台

在画布上编排设备诊断流程，发布成不可变版本，由平台调用业务 Python 接口取数与判断，人工完成处置，全过程留痕。

- 设计依据：[docs/spec.md](docs/spec.md)
- 协作规则：[AGENTS.md](AGENTS.md)

## 运行

前端：

```bash
pnpm install
pnpm dev          # http://127.0.0.1:5173
```

后端：

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

开发态 Vite 把 `/api` 代理到 `127.0.0.1:8000`。

## 内网单人测试

服务跑在内网某台机器、你用另一台电脑的浏览器访问时：

```bash
# 服务端：后端保持回环即可，前端监听所有网卡
backend/.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pnpm dev:lan          # 访问 http://<那台机器的内网IP>:5173
```

浏览器只访问 5173，`/api` 由 Vite 在服务端代理到 8000，所以不涉及 CORS。需要在服务端防火墙放行 5173。

想直接看接口文档（`/docs`）时，后端才需要 `--host 0.0.0.0` 并放行 8000——注意这套没有鉴权，放行即对内网所有人开放。

依赖安装需要外网或内网镜像：npm 用 `pnpm config set registry <镜像>`，pip 用 `-i <镜像>`；纯离线环境要提前把 node_modules 和 wheel 包带过去。

## 调试

后端日志写到 `backend/logs/app.log`，5 MB 轮转、保留 5 份。

| 环境变量 | 作用 |
|---|---|
| `NEWDIAG_LOG_LEVEL=DEBUG` | 打开调试日志：每步调度、参数求值、会话调用与耗时、SSE 不涉及 |
| `NEWDIAG_LOG_DIR` | 日志目录，默认 `backend/logs` |
| `NEWDIAG_LOG_PAYLOAD=1` | 把实参与返回值也写进日志；默认只记字段名，避免敏感数据落盘 |

每个 HTTP 响应都带 `X-Request-ID`，日志行前缀 `[abcd1234]` 与它对应，出问题时按这个 id 串起一次请求。

前端：开发态默认在浏览器控制台输出 `[newdiag]` 前缀的请求、SSE 重连与组件错误；生产构建里在控制台执行 `localStorage.setItem('newdiag:debug','1')` 再刷新即可打开。

## 离线部署包

在能上网的机器上构建：

```bash
backend/.venv/Scripts/python scripts/build_bundle.py
```

产物：`deploy/newdiag-win64-<日期>.zip`（约 25 MB）。

目标机器解压后双击 `start.cmd`，浏览器打开 http://127.0.0.1:8765/ 即可，**不需要装 Node、Python 或任何依赖**。内网他机访问用 `start-lan.cmd`。

包里含：Python 3.11 嵌入版、FastAPI / Uvicorn / SQLAlchemy 等运行时依赖、前端静态产物、示例工程 `examples/cooling`。业务工程自身的依赖（numpy、串口 SDK 等）仍需在目标机器上、业务工程自己的解释器里装好，登记工程时把 `python` 填成那个解释器。

## 验证

```bash
pnpm type-check                      # 前端类型
pnpm build                           # 类型 + 打包
backend/.venv/Scripts/python -m pytest backend/tests -q       # 后端 55 个用例
```

三套端到端脚本（需要后端已启动，且先跑过 `demo_seed.py` 登记工程）：

```bash
backend/.venv/Scripts/python scripts/demo_seed.py       # 直线：排查 → 结论 → 处置 → 问题解决
backend/.venv/Scripts/python scripts/demo_branching.py  # 分支 + 回退 + 上升
backend/.venv/Scripts/python scripts/demo_serial.py     # 串行队列 + 成功后暂停 + 继续
```

示例业务工程在 `examples/cooling`，登记时填路径 `examples/cooling`、模块 `cooling.diagnosis`。

## 结构

```
src/          前端（Vue 3 + Vue Flow + Element Plus）
  domain/     契约：doc / plan / instance 类型、连接规则
  components/ 画布、节点、边、属性面板、待办、历史
  views/      列表、设计、版本、执行、工程
backend/      FastAPI + SQLite
  app/api/            HTTP 接口
  app/services/       编译、调度、会话、签名、存储
  app/worker/         会话子进程与接口探针
examples/     示例业务工程
scripts/      端到端演示
docs/         设计说明
```

