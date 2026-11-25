# AGENTS.md

本文件约束所有参与本仓库开发的 agent。`docs/spec.md` 是唯一设计依据，冲突时以 spec 为准，并在此处登记偏差。

2026-11-26：开发启动器的源码监控改为内容指纹判据。事件只当触发器，重启与否看 `backend/app` 下 `.py` 的 `mtime + size` 快照，实现照 Django `StatReloader` 与 uvicorn `StatReload`。起因：Windows 的目录通知把改名、只读位等元数据变更也报成 change，旧实现按事件重启，后端启动中途会被无关事件反复打断。同时新增 `OLIVE_DEV_NO_RELOAD=1` 关闭热重启。README 的启动说明已同步。

2026-11-26：节点类型转换（如排查 → 结论）只改 `kind`，`fields`／`slots`／`runtimeInputs`／`inputs` 等旧字段原样保留，方便转回；发布时由 `backend/app/services/rules.py` 的 `NODE_FIELDS`（每种类型允许哪些字段）与 `trim_doc_fields` 裁剪该类型用不到的字段，发布快照、在线版本与检出草稿都写裁剪后的 doc。字段表是契约的一部分，见 `docs/spec.md` 第 4 节「节点类型转换与发布期字段裁剪」。

2026-11-21：新增接入 skill `.workbuddy/skills/olive-project-integration`。把外部工程接入平台时先读它的 `SKILL.md`；探针、发布级校验、生成代码冒烟三个脚本直接调用 `backend` 实现，不另写一套规则。

2026-11-16：v1.18 把槽位传值收敛到节点内：`previous` 只指本节点上一步，跨节点不再保留；开始节点的拉取与处理用于把数据写进对象，返回值不后传。

2026-11-16：v1.17 把接入收敛为「节点槽位模板」。开始节点填构造槽（一个外接工程一个对象）与可选拉取／处理槽；排查节点固定四槽：拉取（可选）、处理（可选）、展示（必填，带展示类型）、判定（必填，可人工判断）。删除 v1.16 的对象／固定资源／排查操作三层、11 种参数来源、展示映射与判定字段映射；参数来源只剩 lit／input／parameter／previous。持久格式 2 的 `bindings.nodes` 改存 `slots`。契约见 spec 第 4、5、6 节。

2026-11-15：v1.16 收敛为「组合配置 + 生成 Python 执行」单一方案。删除旧函数绑定、四阶段函数与 investigation/v1 适配器、diagnosis/v1 业务类、数据准备节点、解释执行引擎与 `flows.mode`；`diagnosis_sdk` 只保留 import 兼容。spec 第 12 节变更记录与本节一致。
2026-11-12：新增 v1.15 通用组合接入，原工程普通对象、函数、方法通过平台配置组合；接入生成执行引擎。固定资源复用引用，普通对象回退到所选到访，保留分支已取得固定数据。契约见 spec 第 14 节。

2026-11-12：按用户确认实现生成 Python 执行、进程常驻人工等待、按启动配置控制串并行及并行上限。
流程、接入、展示、运行配置分层；数据成员首次填充后固定，按新增数据保存，回退复用到访结果。
本轮需求开发阶段按用户要求不运行测试，使用新 worktree 和 codex/generated-diagnosis 分支；用户要求推送后，按第 6 节执行推送前检查。

2026-11-05：按用户确认新增 v1.11 诊断实例类协议，契约扩展已同步到 spec、domain/types.ts 和后端模型。
新增 worker/diagnosis_sdk.py、examples/diagnosis-instance，由 lead 集成；旧接入协议保留兼容，无未登记设计偏差。
2026-11-05：按用户最新要求，v1.12 改为普通 Python 成员及对象整体保存恢复，不再强制 self.data。
恢复范围及运行资源边界记入 docs/todo.md，本轮先按全部恢复处理。
2026-11-05：按用户要求，v1.13 补齐启动文件上传、节点现场参数、数组／对象／JSON 输入。
runtimeInputs、diagnosisMethod.inputs 和 parameters 待办契约由 lead 同步更新到 spec、类型及实现。

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

- **推送到远端前必须手工执行 `backend/.venv/Scripts/python -m pytest backend/tests -q`，看到 `N passed` 且无 `failed` / `error` 才能推。**
- 不装 `pre-push` 钩子：钩子在后台运行，pytest 清理临时目录时没有确认通道，只能靠关闭批量删除保护绕过；改为显式手工执行，把确认留在人这边。
- 判断结果看 pytest 汇总行，不看退出码：退出码可能被收尾清理污染。
- 前端目前没有自动化测试，`pnpm type-check` 只保证类型；改 UI 必须在交付说明里写清手工验证步骤和实际结果。
## 7. 提交身份（2026-11-08 起）

`git blame` 默认显示的是 **author**，不是 committer。所以要能按行追到「哪个 agent 写的」，必须把 author 写成 agent。

- **author 名字**：`workbuddy-<角色>` —— `workbuddy-lead` / `workbuddy-canvas` / `workbuddy-shell` / `workbuddy-api` / `workbuddy-engine` / `workbuddy-tests`。**只写角色，不写任务**。
- **author 邮箱**：统一 `jebooking_otlk@outlook.com`（GitHub 能关联到账号，通知可达）。
- **committer 不动**：保持仓库默认身份 `JeBooking <jebooking_otlk@outlook.com>`。只覆盖 `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` 两个环境变量，**不要用 `-c user.name=...`**——那会连 committer 一起改掉，就是 2026-11-02 那次 15 个提交作者出错的成因。
- **任务信息进正文**：subject 保持 `type: 简述`，做了哪件事、改了哪些文件写进 body。
- **子 agent 仍然不提交**（见第 4 节），由 lead 统一 commit；lead 按这批代码的实际实现者设 author。
- **历史不改**：本节生效之前的提交保持原样。

```bash
GIT_AUTHOR_NAME="workbuddy-shell" GIT_AUTHOR_EMAIL="jebooking_otlk@outlook.com" \
  git commit -m "feat: 流程删除到回收站" -m "由 shell 实现：列表删除按钮 + 回收站页面"
```
