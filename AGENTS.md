# AGENTS.md

本文件约束所有参与本仓库开发的 agent。`docs/spec.md` 是唯一设计依据，冲突时以 spec 为准，并在此处登记偏差。

## 0. 沟通风格（重要）
- **高信息密度（2026-10-30 两次强调）**：像真人对话一样，先想清楚再说，把话浓缩到最少字数。追求**每句话的信息熵**，不是砍掉结论只留干条——是删废话、删铺垫、删重复、删总结式收尾。宁短勿长。

## 1. 技术栈

- 前端：Vue 3.5 + TypeScript 5.9 + Vite 7 + Vue Router 4 + Pinia 3 + Element Plus 2 + `@vue-flow/core` 1.x
- 后端：Python 3.13 + FastAPI + SQLAlchemy 2 + SQLite（WAL）
- 包管理：前端只用 `pnpm`；后端用项目内 `backend/.venv`

## 2. 文件所有权（禁止越界）

| 归属 | 路径 |
|---|---|
| lead | `package.json`、`tsconfig*.json`、`vite.config.ts`、`index.html`、`src/main.ts`、`src/App.vue`、`src/styles/**`、`src/domain/**`、`AGENTS.md`、`README.md`、`docs/**` |
| canvas | `src/components/canvas/**`、`src/components/nodes/**`、`src/components/edges/**` |
| shell | `src/router/**`、`src/views/**`、`src/stores/**`、`src/services/**`、`src/components/panels/**`、`src/components/tasks/**`、`src/components/history/**` |
| api | `backend/app/main.py`、`backend/app/db.py`、`backend/app/models.py`、`backend/app/schemas.py`、`backend/app/api/**`、`backend/app/services/storage.py`、`backend/app/services/projects.py` |
| engine | `backend/app/services/compiler.py`、`rules.py`、`scheduler.py`、`sessions.py`、`signatures.py`、`backend/app/worker/**` |
| tests | `backend/tests/**`、`scripts/**` |

只写自己名下的文件。需要别人文件里的改动，写在交付说明里，由 lead 处理。

## 3. 契约（不可自行修改）

- `src/domain/types.ts`：文档、计划、实例、运行记录、待办的 TypeScript 类型。字段名改动必须经 lead。
- `docs/spec.md` 第 3、4、5、7 节：表结构、doc 格式、plan 格式、API 路径。
- 后端 `models.py` / `schemas.py` 的字段名必须与 `types.ts` 一致（snake_case 用于内部，API JSON 用 camelCase，由 schemas 转换）。

缺字段时不要自行添加，先在交付说明里提出。

## 4. 代码规范

- TypeScript `strict`，禁止 `any` 扩散；跨模块只用 `@/` 别名
- Vue 单文件组件用 `<script setup lang="ts">`
- 格式：单引号、不加分号、2 空格缩进、行宽 100
- 标识符用英文；注释用中文，只写「为什么」，不写「做了什么」
- 不加没被要求的依赖；不写 emoji
- 不提交 git（`git commit` 由 lead 执行）

## 5. 验收

- 前端：`pnpm type-check` 通过；自己的组件能在 `pnpm dev` 下渲染
- 后端：`backend/.venv/Scripts/python -m pytest` 通过；`uvicorn` 能起
- 改动后只跑与自己相关的检查，不要全仓库格式化

## 6. 推送前必须全绿

- **推送到远端前必须跑 `backend/.venv/Scripts/python -m pytest backend/tests -q`，全绿才能推。**
- 本地已装 `pre-push` 钩子自动执行；新克隆或换机器后先跑一次 `sh scripts/install-hooks.sh`。
- 钩子被绕过（`git push --no-verify`）或环境缺 `.venv` 时，仍要手工跑一遍再推。
- 前端目前没有自动化测试，`pnpm type-check` 只保证类型；改 UI 必须在交付说明里写清手工验证步骤和实际结果。
