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

## 结构

```
src/          前端（Vue 3 + Vue Flow + Element Plus）
backend/      FastAPI + SQLite
docs/         设计说明
```
