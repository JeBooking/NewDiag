# 诊断流程平台 · 设计说明（v1）

本文件是重写项目的唯一设计依据。所有条目来自 2026-10-31 的需求讨论，标注「待定」的部分表示尚未拍板，实现时不得自行假设。

---

## 1. 产品边界

**做什么**

- 作者在画布上编排诊断流程：排查 → 结论 → 处置 → 问题解决
- 发布成不可变版本，供有权限的人执行
- 执行时自动调用业务 Python 函数取数与判断，人工完成处置并留痕
- 全流程记录：每次调用的输入、输出、依据、分支、时间、人工选择

**不做什么**

- 不直接操作设备。平台只调用数据服务器接口取数，动作由人对着设备完成
- 不做 BPMN 互通，不引入 BPMN 语义与序列化
- 不做实时协同编辑（v1 为共享草稿 + 编辑锁）
- 不做检查点恢复（进程重启后未完成实例标记中断，由人决定重跑）
- 不做暂停/停止（v1）

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| 流程 flow | 一份可编辑的诊断图 |
| 检出 checkout | 作者拿到编辑权后产生的未提交快照 |
| 在线版本 doc | 已保存的当前版本，作者可见；被反复覆盖，不留历史 |
| 发布版本 release | 在线版本在发布时编译出的不可变快照，执行只认它 |
| 步骤表 plan | 发布时由 doc 编译出的执行计划，参数已绑定到具体来源 |
| 快照 snapshot | 一次诊断开始时采集的数据，之后只读 |
| 实例 instance | 一次执行 |
| 令牌 token | 实例中一条正在推进的路径 |
| 运行记录 run | 某个节点的一次实际执行 |
| 待办 review | 需要人处理的任务：判断、处置 |

---

## 3. 数据模型

SQLite（WAL）。所有表带 `id TEXT PRIMARY KEY`（ULID），时间统一 ISO8601 UTC。

### flows

| 字段 | 说明 |
|---|---|
| id, name, description | |
| mode | `parallel` / `serial` |
| owner_id | 作者；v1 固定 `local` |
| current_revision_id | 指向在线版本 |
| editing_by | 检出者；空表示未检出 |
| editing_revision_id | 检出缓冲 |
| editing_since | |
| created_at, updated_at | |

### revisions

| 字段 | 说明 |
|---|---|
| id, flow_id | |
| json | 完整 doc |
| status | `working` / `committed` / `discarded` |
| author, created_at, committed_at | |

`working` 是唯一可修改的状态。`committed` 不可变。

### releases

| 字段 | 说明 |
|---|---|
| id, flow_id, revision_id | 引用快照，不复制 doc |
| version_no | 同一 flow 内递增 |
| plan_json | 编译产物 |
| signature | 函数签名指纹表，`{project}:{function} → hash` |
| published_by, published_at, note | |

### instances

| 字段 | 说明 |
|---|---|
| id, flow_id, release_id | |
| status | `running` / `waiting` / `paused` / `success` / `failed` / `escalated` |
| snapshot_id | |
| started_by, started_at, ended_at | |

### snapshots

| 字段 | 说明 |
|---|---|
| id, instance_id, seq | v1 恒为 1 |
| payload_json | 采集到的原始数据 |
| source, params_json | 数据接口与查询参数 |
| captured_at | |

### tokens

| 字段 | 说明 |
|---|---|
| id, instance_id, node_id | |
| status | `queued` / `running` / `waiting` / `done` / `failed` / `abandoned` |
| branch | 进入该 token 的分支（yes/no） |
| order | 串行队列序号 |
| generation | 回退代数，从 1 开始 |
| parent_token_id | 用于追溯路径 |
| created_at, updated_at | |

### runs

| 字段 | 说明 |
|---|---|
| id, instance_id, token_id, node_id | |
| generation, attempt | |
| status | `ok` / `failed` / `abandoned` |
| input_json | 实际传入参数（含默认值来源） |
| output_json | 返回值投影 |
| evidence_json | 依据 / 图表 / 上下文 |
| error | 异常或超时信息 |
| started_at, ended_at | |

### reviews

| 字段 | 说明 |
|---|---|
| id, instance_id, token_id, node_id | |
| kind | `decision` / `action` |
| status | `pending` / `done` / `cancelled` |
| request_json | 展示给人看的内容：说明、依据、附件 |
| decision_json | `{result:"yes"\|"no"}` 或 `{result:"done"\|"rollback", target?, reason?}` |
| created_at, decided_at, decided_by | |

### files

| 字段 | 说明 |
|---|---|
| hash | 内容寻址，主键 |
| filename, media_type, size, path | |

### projects

| 字段 | 说明 |
|---|---|
| id, name, path, module, python, factory | 登记信息；接口在运行时发现，不落库 |

---

## 4. 文档格式（doc）

```json
{
  "format": 1,
  "name": "冷却系统诊断",
  "mode": "parallel",
  "snapshotFields": ["voltage"],
  "nodes": [
    {
      "id": "n1",
      "kind": "investigation",
      "title": "检查母线电压",
      "detail": "观察母线电压是否低于 210V",
      "fields": {
        "observation": "母线电压",
        "acquisition": "调用数据服务器读取 voltage",
        "assessment": "低于 210V 判为异常"
      },
      "attachments": [{ "hash": "sha256:…", "name": "接线图.pdf" }],
      "binding": { "project": "冷却系统", "function": "judge_voltage" },
      "outputs": [],
      "params": { "voltage": { "snapshot": "voltage" } },
      "position": { "x": 120, "y": 80 }
    }
  ],
  "edges": [
    { "id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1 }
  ]
}
```

- `snapshotFields`：启动输入里可供节点引用的字段名，发布时参与参数绑定
- `outputs`：该节点返回结构里的字段名，供下游绑定；布尔判据留空
- `params`：参数名 → 来源，可省略；省略时按函数签名自动匹配
- `binding.project` 用**工程名**，不用 id

### 节点类型

| kind | 形状 | 是否执行代码 | 后继 |
|---|---|---|---|
| investigation 排查 | 菱形 | 有绑定则自动，无绑定则人工判断 | 是 / 否两组 |
| investigationAction 排查动作 | 正梯形 | 否，人工 | 完成后接排查 |
| conclusion 结论 | 矩形 | 否，展示设计时文本后继续 | 恰好一条，接处置或上升 |
| remediation 处置措施 | 倒梯形 | 否，人工 | 处置 / 跳转 / 问题解决 / 深度诊断 / 上升 |
| problemSolved 问题解决 | 三角形 | 否 | 无 |
| escalation 上升 | 五角星 | 否 | 无 |

v1 不实现：`deepDiagnosis`（深度诊断）、`jump`（跳转）。枚举保留，属性面板不提供入口。理由：旧项目中 `jump` 仅返回转交状态、并未真正跳转，属于半成品。

### 连接规则

沿用旧项目的矩阵，去掉未实现的类型：

```json
{
  "start": ["investigation", "generic"],
  "generic": ["remediation", "problemSolved", "escalation", "generic"],
  "investigation": ["investigation", "investigationAction", "conclusion", "escalation"],
  "investigationAction": ["investigation"],
  "conclusion": ["remediation", "escalation"],
  "remediation": ["remediation", "problemSolved", "escalation"],
  "problemSolved": [],
  "escalation": []
}
```

`generic` 仅用于开始事件等非诊断元素的边界判定。

### 校验规则（发布必过）

同一份规则表由前端提示与后端编译共用，避免「前端放过、后端拒绝」。

1. 恰好一个入口节点（没有入边的节点），且必须是排查节点
2. 连接符合上表；排查的每条出线必须有 `branch`（yes/no），同组可多条
3. 排查节点：四个描述字段非空；无绑定视为人工节点，合法；有绑定则工程与函数必须存在
4. 结论：文本非空；恰好一条出线
5. 处置措施：说明非空
6. 每条路径可达终止节点（问题解决 / 上升），不允许死路
7. 不允许隐式分叉、隐式结束、假汇合（诊断节点按自身语义豁免）
8. 参数绑定必须能解析：来源节点在当前版本中存在且在其上游

---

## 5. 发布编译（doc → plan）

编译在发布时执行一次，产物不可变。

### 参数绑定

发布前先用 probe 拿到每个被绑定函数的真实签名，再编译：

1. 节点上已保存的显式 `params` 优先
2. 快照字段同名
3. 上游运行结果中唯一的同名字段
4. 函数默认值（可选参数无来源时直接省略，交给 Python 默认值）

只有函数的**真实参数**会被绑定，候选里多出来的字段一律不传；必填参数找不到来源时发布失败，错误消息带参数名和候选来源。声明了 `outputs` 但函数没有对应参数只给 warning；`params` 里出现签名之外的参数名直接报错。

发布时的校验是严格的；`commit` 用 `strict_binding=False`，把参数类问题降级为 warning，允许保存半成品，把绑定错误留到发布时暴露。工程探测失败（路径不可导入、函数不存在）一律拒绝发布。

### 步骤表

```json
{
  "format": 1,
  "flow_id": "f1",
  "release": 3,
  "mode": "parallel",
  "entry": "n1",
  "steps": {
    "n1": {
      "kind": "investigation",
      "call": {
        "project": "cooling",
        "function": "judge_voltage",
        "args": {
          "voltage": { "snapshot": "voltage" },
          "threshold": { "lit": 210.0 }
        }
      },
      "next": { "yes": ["n2"], "no": ["n5"] }
    },
    "n2": { "kind": "conclusion", "text": "电压不足", "next": { "default": ["n3"] } },
    "n3": { "kind": "remediation", "human": true, "next": { "default": ["n4"] } },
    "n4": { "kind": "problemSolved", "terminal": true }
  },
  "signature": { "cooling:judge_voltage": "sha256:…" }
}
```

参数表达式三种：`{"lit": 常量}`、`{"snapshot": 路径}`、`{"run": 节点ID, "path": "output.路径"}`。

### 签名指纹

对每个被绑定函数的 `inspect.signature` 做规范化哈希：参数名、顺序、类型、默认值、返回注解。发布时写入 `releases.signature`。

执行前逐个比对当前代码；不一致则拒绝启动，返回差异（新增/删除/改名/默认值变化）。作者确认后重新发布。

---

## 6. 执行语义

### 调度循环

```
while 存在可推进的 token:
    t = 取 token            # 并行：任取；串行：队列头
    step = plan.steps[t.node]
    if step.human:
        t.status = waiting; 创建 review; continue
    args = 逐项求值(step.call.args, snapshot, runs)
    out = 会话子进程调用(step.call, args)     # 超时 kill，重试一次
    写 run
    for 后继 in 选择分支(step.next, out):
        创建 token（串行则入队）
```

实例状态收敛顺序：有 `running` token → running；有 `waiting` token → waiting；串行已到达 problemSolved 且队列仍有剩余 → paused；无活跃 token 时，有 problemSolved → success，否则有 escalation → escalated，否则 failed。优先级 `success > escalated > failed`。

### 排查的分支判定

业务函数返回 `True`/`"yes"`/`"是"` → yes；`False`/`"no"`/`"否"` → no；其他值视为执行错误。**不接受**任意对象的真假性。

人工排查由待办选择 yes/no，结果与自动结果走同一条路径。

### 并行

- 同组多条出线同时启动，互不设优先级
- 一条路径失败只结束自己，其他继续
- 失败分两种：`route/*`（没有匹配分支，属正常排除）与 `execution/*`（异常、超时）。前者记路径失败，后者记执行错误
- 多条处置措施可连到同一个问题解决节点，到达即继续，不等待聚合

### 串行

- 一个排查节点的同组后继按 `edge.order` 升序入队，未填按创建顺序
- 一次只跑队首；失败则试下一条；队列耗尽仍无成功 → failed
- 任一路径到达 problemSolved → paused，生成一个决策待办：结束 / 继续
- 选择结束后实例 success；选择继续则恢复队列

### 人工待办

两种：

| kind | 出现时机 | 选项 |
|---|---|---|
| decision | 排查无绑定，或人工判断 | 是 / 否 |
| action | 排查动作、处置措施 | 完成 / 回退 |

- 完成：推进到后继
- 回退：选择已执行的祖先节点 + 目标分支 + 可选原因
- 动作节点没有「不 OK」；没做成就是回退

### 回退

- 目标节点必须在当前 generation 已执行
- 将该节点之后的所有 token 与 run 标记 `abandoned`，不删除
- 直接按用户选择的分支生成新 token，`generation + 1`
- 实例状态回到 running；若原本 paused，则剩余队列作废并按新分支重建
- 有其他活跃 token 时禁止回退（v1 保守策略），提示先处理完
- 平台不撤销任何设备操作，确认框需写明

### 快照

- 启动时调用一次采集，结果整体写入 snapshot，之后所有节点只读
- 动作改变的是现场，不是快照；历史里记录动作发生在快照之后
- 表带 `seq`，为将来的「重新采集」预留，v1 恒为 1

### 会话子进程

- 每个实例一个子进程，`cwd` 为登记工程目录，解释器为登记解释器
- 协议：stdin/stdout 上的 JSON 行，请求 `{id, function, args}`，响应 `{id, ok, value|error}`
- 超时默认 120s，可配置；超时 kill 后重试一次
- 采集函数必须只读、可重复调用

---

## 7. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/flows` | 列表 |
| POST | `/api/flows` | 新建，自动创建首条 working revision |
| GET | `/api/flows/{id}` | 含当前在线版本与检出状态 |
| PATCH | `/api/flows/{id}` | 名称、描述、模式 |
| POST | `/api/flows/{id}/checkout` | 取编辑权，复制 current 为 working |
| PUT | `/api/flows/{id}/working` | 暂存 doc（自动，仅持有者） |
| POST | `/api/flows/{id}/commit` | 校验 → 写入 committed → 释放锁 |
| POST | `/api/flows/{id}/discard` | 放弃检出 |
| POST | `/api/flows/{id}/admin/commit` | 管理员代存 |
| POST | `/api/flows/{id}/admin/discard` | 管理员丢弃并释放 |
| POST | `/api/flows/{id}/releases` | 编译并发布 |
| GET | `/api/flows/{id}/releases` | 版本列表 |
| GET | `/api/releases/{id}` | 含 plan |
| POST | `/api/flows/{id}/instances` | 启动，body 含 release_id 与启动输入 |
| GET | `/api/instances` | 按流程/状态筛选 |
| GET | `/api/instances/{id}` | 详情：tokens、runs、reviews |
| GET | `/api/instances/{id}/events` | SSE：状态、新 run、新待办 |
| POST | `/api/instances/{id}/reviews/{reviewId}` | 提交判断或完成/回退 |
| POST | `/api/instances/{id}/rollback` | `{targetNodeId, branch, reason?}`，branch 为任意非空字符串（排查 yes/no，其余 default） |
| GET | `/api/projects` `POST` | 工程登记 |
| POST | `/api/projects/{id}/refresh` | 重新发现接口 |
| GET | `/api/projects/{id}/functions` | 接口与签名 |
| POST | `/api/files` | 上传，返回内容哈希 |
| GET | `/api/files/{hash}` | 下载 |

---

## 8. 前端

### 页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 流程列表 | 名称、状态、最近发布、最近执行 |
| `/flows/:id/edit` | 设计页 | 画布 + 属性面板 + 工具栏 |
| `/flows/:id/releases` | 版本列表 | 版本、发布时间、发布人、差异入口 |
| `/flows/:id/run` | 执行页 | 画布只读 + 待办面板 + 进度 |
| `/instances` | 执行记录 | 全部实例 |
| `/instances/:id` | 实例详情 | 路径、记录、人工操作、结论 |
| `/projects` | 工程管理 | 登记、发现接口、调试会话 |

### 结构

```
src/
├── router/
├── stores/            editor / flows / instances
├── domain/            doc.ts kinds.ts rules.ts plan.ts validate.ts
├── components/
│   ├── canvas/        VueFlow 封装、自动布局、缩放/迷你地图
│   ├── nodes/         6 种节点渲染器（卡片）
│   ├── edges/         是/否分支边
│   ├── panels/        节点属性、流程属性、绑定选择
│   ├── tasks/         判断待办、处置待办、回退选择
│   └── history/       运行记录、路径时间线
├── services/          API 客户端、SSE
└── styles/
```

节点渲染为定宽卡片：左侧类型色条 + 图标 + 标题 + 两行摘要 + 状态角标；排查节点显示「是/否」两个出口。渲染器注册表化，将来换几何形状只换组件。

### 编辑锁交互

- 进设计页先 `checkout`；被占用则只读，显示「XX 正在编辑」
- 编辑中每 2s 防抖暂存 working（不提示、不算保存）
- 「保存」提交并释放锁；「放弃」需二次确认
- 管理员可预览 working，选择代存或丢弃

---

## 9. 后端结构

```
backend/
├── app/
│   ├── main.py            FastAPI 入口、静态文件托管
│   ├── models.py          SQLAlchemy 模型
│   ├── schemas.py         请求/响应
│   ├── api/               flows / releases / instances / projects / files
│   ├── services/
│   │   ├── compiler.py    doc → plan，含校验与参数绑定
│   │   ├── rules.py       与前端共用的连接与必填规则
│   │   ├── scheduler.py   双策略调度循环
│   │   ├── sessions.py    会话子进程管理
│   │   ├── signatures.py  签名指纹
│   │   └── storage.py     内容寻址文件
│   └── worker/
│       └── session.py     子进程入口，JSON 行协议
└── tests/
```

---

## 10. 核心时序

**保存**

1. `PUT working` 暂存（可多次）
2. `POST commit`：校验 doc → 新 revision 标 committed → `flows.current_revision_id` 更新 → 释放锁

**发布**

1. 取 current revision
2. 校验规则 → 解析参数绑定 → 生成 plan → 记录签名指纹
3. 写 `releases`，版本号递增

**执行到人工**

1. `POST instances` → 采集快照 → 建 entry token
2. 调度器推进自动节点，写 run
3. 遇到人工节点 → 建 review、token 置 waiting、实例 waiting
4. 前端 SSE 收到待办 → 面板展示 → 用户选
5. 提交后生成后继 token，实例回 running

**回退**

1. 用户选目标节点与分支、填原因
2. 校验目标已执行、无其他活跃 token
3. 其后 token/run 标 abandoned，建新 token（generation+1）
4. 实例回 running，历史保留全部记录

---

## 11. 尚未决定

| 项 | 影响 | 建议 |
|---|---|---|
| 结论文本是否允许运行时生成 | 需要额外返回值约定 | v1 按设计时文本处理 |
| `jump` / `deepDiagnosis` 是否要 | 节点类型与连接规则 | v1 不做，枚举保留 |
| 权限模型细节：谁能发布、管理员范围 | 内网版落地 | v1 用本地开关模拟 |
| 诊断文档的格式与导出 | 依赖 runs/reviews 字段 | 数据已够，格式后定 |
| 导出的 Python 代码形态 | 单文件 / 多文件 / 含 runner | 排在 demo 之后 |
| 自动布局算法 | 导入与一键排版 | dagre 足够，待验证 |
| 附件清理策略 | 存储增长 | 按引用计数定期清理 |

---

## 12. 与旧项目的差异

| 旧 | 新 |
|---|---|
| BPMN 模型 + moddle | 自有 JSON |
| bpmn-js + 40 个属性面板 | Vue Flow + 6 种节点 |
| 编译成 BPMN 再部署 Flowable | 编译成步骤表，自研调度 |
| Java 运行时 + H2 | 纯 Python + SQLite |
| external worker + topic 协调器 | 调度器直接管理会话子进程 |
| 运行时按参数名在变量池里找值 | 发布时绑定到具体来源 |
| 单页 + 抽屉 | 多页路由 |
| 浮窗待办 + 曲线连接 | 右侧待办面板 + 定位 |
| 无版本概念，每次执行拍快照 | 草稿 / 在线版本 / 发布版本三层 |
| 只有并行 | 并行 + 串行双策略 |
| 动作三模式（自动/人工/核验） | 只有人工，完成 / 回退 |
