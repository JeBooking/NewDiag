# 诊断流程平台 · 设计说明（v1.2）

本文件是重写项目的唯一设计依据。所有条目来自 2026-10-31 的需求讨论，标注「待定」的部分表示尚未拍板，实现时不得自行假设。

变更记录见第 13 节。

---

## 1. 产品边界

**做什么**

- 作者在画布上编排诊断流程：开始 → 排查 → 结论 → 处置 → 问题解决／上升
- 发布成不可变版本，供人执行（v1 无鉴权，见第 11 节）
- 执行时自动调用业务 Python 函数取数与判断，人工完成处置并留痕
- 全流程记录：每次调用的输入、输出、依据、分支、时间、人工选择
- 流程按目录树归类；启动参数在开始节点声明，发布时冻结成表单

**不做什么**

- 不直接操作设备。平台只调用数据服务器接口取数，动作由人对着设备完成
- 不做 BPMN 互通，不引入 BPMN 语义与序列化
- 不做实时协同编辑（v1 为共享草稿 + 编辑锁）
- 不做检查点恢复：进程重启后未完成实例不会自动续跑，也还没有启动时批量标记中断的清理逻辑（见第 11 节）
- 不做用户手动暂停／停止；串行模式下到达问题解决后的 `paused` 是调度语义，不是暂停按钮

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| 流程 flow | 一份可编辑的诊断图 |
| 目录 folder | 流程的树形归类，空表示未分类 |
| 检出 checkout | 作者拿到编辑权后产生的未提交副本 |
| 在线版本 doc | 已保存的当前版本，作者可见；被反复覆盖，不留历史 |
| 发布版本 release | 在线版本在发布时编译出的不可变快照，执行只认它 |
| 步骤表 plan | 发布时由 doc 编译出的执行计划，参数已绑定到具体来源 |
| 开始节点 start | 全局唯一的入口，声明本次诊断的启动参数 |
| 启动上下文 context | 实例启动时固化的参数值，之后只读。历史上叫「快照」，表名沿用 `snapshots` |
| 实例 instance | 一次执行 |
| 令牌 token | 实例中一条正在推进的路径 |
| 运行记录 run | 某个节点的一次实际执行 |
| 待办 review | 需要人处理的任务：判断、处置、串行成功后的结束／继续 |

---

## 3. 数据模型

SQLite（WAL）。时间统一 ISO8601 UTC。除 `files` 用内容哈希作主键外，所有表带 `id TEXT PRIMARY KEY`（ULID）。

### folders

| 字段 | 说明 |
|---|---|
| id, name | |
| parent_id | 自引用；空表示根目录 |
| sort_order | 同级排序，创建时取同级最大值 + 1 |
| created_at | |

同级目录不允许重名（接口返回 409）。不允许把目录挂到自己或自己的子目录下。

### flows

| 字段 | 说明 |
|---|---|
| id, name, description | |
| mode | `parallel` / `serial` |
| folder_id | 所属目录；空表示未分类 |
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
| signature_json | 函数签名指纹表，`{工程名}:{函数名} → hash`；探测不到时为空字符串 |
| inputs_json | 发布时冻结的启动表单，`ReleaseInput[]` |
| published_by, published_at, note | |

### instances

| 字段 | 说明 |
|---|---|
| id, flow_id, release_id | |
| status | `running` / `waiting` / `paused` / `success` / `failed` / `escalated` |
| snapshot_id | 指向启动上下文 |
| started_by, started_at, ended_at | |

### snapshots（启动上下文）

| 字段 | 说明 |
|---|---|
| id, instance_id, seq | v1 恒为 1，为将来多轮上下文预留 |
| payload_json | `{"global": {...}, "scope": {函数名: {...}}}`；兼容读旧扁平载荷 |
| source | v1 固定 `start` |
| params_json | v1 恒为 `{}`，保留列 |
| captured_at | |

这张表只装启动表单的值，不再装设备数据快照（见第 6 节「启动上下文」）。

### tokens

| 字段 | 说明 |
|---|---|
| id, instance_id, node_id | |
| status | `queued` / `running` / `waiting` / `done` / `failed` / `abandoned` |
| branch | 进入该 token 的分支（yes/no），default 记空 |
| sort_order | 串行队列序号，也是并行下的创建顺序 |
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
| error | 异常或超时信息，带分类前缀（`route/*`、`execution/*`、`signature/*`、`plan/*`） |
| started_at, ended_at | |

### reviews

| 字段 | 说明 |
|---|---|
| id, instance_id, token_id, node_id | |
| kind | `decision` / `action` |
| status | `pending` / `done` / `cancelled` |
| request_json | 展示给人看的内容：说明、依据、附件、操作提示；串行成功后的待办带 `type: "after_success"` |
| decision_json | `{result:"yes"|"no"}` / `{result:"done"|"rollback", branch?, target?, reason?}` / `{result:"continue"}` |
| created_at, decided_at, decided_by | |

### files

| 字段 | 说明 |
|---|---|
| hash | 内容寻址，主键 |
| filename, media_type, size, path | |

### projects

| 字段 | 说明 |
|---|---|
| id, name, path, module, python, factory | 登记信息；`name` 唯一，是节点绑定与签名 key 的唯一口径 |
| created_at | |

接口在运行时发现，不落库。`python` 为空时用平台自身解释器。

`name` 登记后不可修改：它出现在 doc 的 `binding.project` 与已发布版本里，改名会让已有流程找不到实现。

---

## 4. 文档格式（doc）

```json
{
  "format": 1,
  "name": "冷却系统诊断",
  "mode": "parallel",
  "nodes": [
    {
      "id": "s1",
      "kind": "start",
      "title": "开始",
      "detail": "",
      "inputs": [
        { "name": "temperature", "scope": "global", "type": "number" },
        { "name": "voltage", "scope": "function", "function": "judge_voltage", "type": "number" }
      ],
      "position": { "x": 0, "y": 120 }
    },
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
      "position": { "x": 240, "y": 80 }
    }
  ],
  "edges": [
    { "id": "e0", "from": "s1", "to": "n1" },
    { "id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1 }
  ]
}
```

示例省略 `n2` 之后的节点与边。

- `inputs`：开始节点声明的启动参数（见下）
- `outputs`：该节点返回结构里的字段名，供下游绑定；布尔判据留空
- `params`：参数名 → 来源，可省略；省略时按函数签名与启动参数自动匹配
- `binding.project` 用**工程名**，不用 id
- `snapshotFields`：旧版全局启动字段，读取时并入全局启动参数；新文档不再写

### 开始节点的启动参数

`inputs: StartParam[]`：

- `scope="global"`：所有函数可见
- `scope="function"`：只对该函数可见，覆盖同名全局参数；`function` 必填
- `type`：`text` / `number` / `boolean` / `datetime`。由函数签名推导，作者可覆盖
- `default`：设计时默认值；执行时可改，改完固化进本次启动上下文
- 参数清单由下游绑定函数的签名派生，不预置、不允许手工新增；没有绑定函数时为空
- 平台不做类型转换：表单值原样传字符串，类型只决定控件
- 缺开始节点时保存只提示，发布拦截

### 节点类型

| kind | 名称 | 形状 | 是否执行代码 | 后继 |
|---|---|---|---|---|
| start | 开始 | —（唯一入口） | 否 | 多条，全 default |
| investigation | 排查 | 菱形 | 有绑定则自动，无绑定则人工判断 | 是 / 否两组 |
| investigationAction | 排查动作 | 正梯形 | 否，人工 | 完成后接排查 |
| conclusion | 结论 | 矩形 | 否，展示设计时文本后继续 | 恰好一条，接处置或上升 |
| remediation | 处置措施 | 倒梯形 | 否，人工 | 处置 / 问题解决 / 上升 |
| problemSolved | 问题解决 | 三角形 | 否 | 无 |
| escalation | 上升 | 五角星 | 否 | 无 |

`形状` 列只保留旧项目的视觉语义：v1 的画布统一把节点渲染为定宽卡片（见第 8 节），编译与调度都不依赖它。

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

`generic` 仅用于开始事件等非诊断元素的边界判定。开始节点的出边不区分是/否，一律 `default`，可以同时连多个排查节点。

### 校验规则（发布必过）

同一份规则表由前端提示与后端编译共用（`src/domain/rules.ts` 与 `backend/app/services/rules.py`），避免「前端放过、后端拒绝」。

1. 恰好一个开始节点：无入边、至少一条出线、不绑定函数、不配置 `params`；重复连到同一目标报错
2. 连接符合上表；排查的每条出线必须有 `branch`（yes/no），同组可多条；重复连接同一目标报错
3. 排查节点：`title`、`detail` 非空，`fields` 的 observation / acquisition / assessment 三项非空；无绑定视为人工节点，合法；有绑定则工程与函数必须存在
4. 结论：`title`、`detail` 非空（执行时展示 detail）；恰好一条出线
5. 处置措施、排查动作：`title`、`detail` 非空
6. 每条路径可达终止节点（问题解决 / 上升），不允许死路；不允许不可达节点
7. 不允许隐式分叉、隐式结束、假汇合；开始节点按自身语义豁免（多条出线是常态）
8. 参数绑定必须能解析：来源节点在当前版本中存在且在其上游
9. 启动参数：同一 `(scope, function, name)` 不重复；`scope=function` 必须指定函数
10. 环允许存在，但必须能从环内走出去；无出口的环报错

老文档（没有开始节点）用 `require_start=False` 时退回旧入口规则：入度为 0 的节点恰好一个，且必须是排查节点。这条路径只保留作兼容（测试与历史文档），生产发布一律要求开始节点。

---

## 5. 发布编译（doc → plan）

编译在发布时执行一次，产物不可变。

### 参数绑定

发布前先用 probe 拿到每个被绑定函数的真实签名，再编译。同名参数按层竞争，只有最高那一层参与绑定：

1. 节点上已保存的显式 `params`
2. 函数局部启动参数（`scope=function`，函数名一致）
3. 全局启动参数（`scope=global`，含旧 `snapshotFields`）
4. 上游运行结果中唯一的同名字段（有同名启动参数时不参与竞争）
5. 函数默认值（可选参数无来源时直接省略，交给 Python 默认值）

同一层出现多个来源视为歧义，报错并要求在节点上显式指定 `params`。

只有函数的**真实参数**会被绑定，候选里多出来的字段一律不传；必填参数找不到来源时发布失败，错误消息带参数名和候选来源。声明了 `outputs` 但函数没有对应参数只给 warning；`params` 里出现签名之外的参数名直接报错。

发布时的校验是严格的；`commit` 与 `import` 用 `strict_binding=False`：**保存不阻断，所有问题随响应返回**，参数类问题降级为 warning，允许存半成品，把绑定错误留到发布时暴露。工程探测失败（路径不可导入、函数不存在）一律拒绝发布。

### 启动表单冻结

发布时把开始节点的启动参数与真实签名合成执行页的表单，写入 `releases.inputs_json`。执行页只读它，发布后改 doc 或改函数签名都不影响已发布版本。

每个字段：`name / scope / function / type / required / default`。

- `type`：从函数注解推 `datetime` / `number` / `boolean` / `text`，作者声明的值优先
- `required`：签名里该参数没有默认值，且作者没给设计时默认值
- `default`：作者设计时默认值优先；否则取签名默认值。探针用 `repr` 兜底的值（`datetime`、枚举、自定义对象）标记为不可信，不冻结进表单——写进去只会在执行页预填假数据
- 启动时先填默认值，再校验必填；缺参返回 400 并列出参数名

### 步骤表

```json
{
  "format": 1,
  "flowId": "f1",
  "release": 3,
  "mode": "parallel",
  "entry": "s1",
  "steps": {
    "s1": { "kind": "start", "next": { "default": ["n1"] } },
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
  "signature": {}
}
```

参数表达式三种：`{"lit": 常量}`、`{"snapshot": 路径}`、`{"run": 节点ID, "path": "output.路径"}`；后两种可带 `"optional": true`，取不到值时省略该参数。

`signature` 在 plan 里通常为空对象，真实指纹存 `releases.signature_json`（见下）。

### 签名指纹

对每个被绑定函数的参数表做规范化：`函数名(参数名:类型[=默认值], ...)`，按函数名排序后整体 sha256。哈希**不含返回注解**——探针只保留参数表，发布与启动两条计算路径必须算出同一个值。

发布时写入 `releases.signature_json`。执行前逐个比对当前代码；实例还没有任何 run 记录时比对，一旦跑过节点，人工恢复不再拦：

- 不一致：实例直接 `failed`，错误码 `signature/changed`，差异列出 `added` / `removed` / `changed`
- 指纹为空字符串视为未知，不阻断
- 作者确认后重新发布

---

## 6. 执行语义

### 执行模型（当前实现）

当前是**计划解释执行**，不是代码生成执行：

- 发布时把 doc 编译成 `plan`（步骤表 + 每个参数的来源表达式），存进发布版本；
- 运行期由调度器逐 token 解释：求值参数 → 调业务函数 → 按返回值选分支 → 状态写回 SQLite；
- 不存在「把整张图生成一段 Python 源码、再启动服务跑它」这条路径，也没有生成代码的运行时。

2026-10-31 讨论过「图 → 一整段 Python 代码 → 后端执行」的方案，当时没有采纳，原因是人工等待跨天无法用同步代码表达，回退、并行/串行又都需要可恢复的持久状态；生成的代码最终仍要绕回状态机，等于把调度器重写一遍。选择的是「编译期绑定参数 + 计划解释执行」，参数传递问题在编译期解决，不在运行期按名字猜。

生成 Python 源码目前只作为**导出**候选（见 §11），未实现。如果将来要让生成代码承担执行，必须先回答人工等待、回退、进程隔离三件事，不能只做渲染。

### 调度循环

```
while 存在可推进的 token:
    t = 取 token            # 并行：全部 queued；串行：队首，且有 waiting/running 时不动
    step = plan.steps[t.node]
    if step.terminal:
        t.status = done; continue
    if step.human:
        t.status = waiting; 创建 review; continue
    if step.call:
        args = 逐项求值(step.call.args, 启动上下文, runs)
        out = 会话子进程调用(step.call, args)     # 超时 kill，重试一次
        写 run；按 out 选分支
    else:
        直接按 default 投递后继                    # 开始、结论
```

实例状态收敛顺序：

1. 有 `running` token → `running`
2. 有 `waiting` token → `waiting`
3. 有 `queued` token：串行且已到达 problemSolved → `paused`，否则 `running`
4. 无活跃 token 时，有 problemSolved → `success`，否则有 escalation → `escalated`，否则 `failed`

优先级 `success > escalated > failed`；进入终止态时写 `ended_at` 并关闭会话子进程。

### 排查的分支判定

业务函数返回 `True` / 字符串 `"yes"`、`"y"`、`"true"`、`"是"` → yes；`False` / `"no"`、`"n"`、`"false"`、`"否"` → no；其他值视为执行错误。**不接受**任意对象的真假性。分支没有后继也记 `route/*`。

人工排查由待办选择 yes/no，结果与自动结果走同一条路径。

### 并行

- 同组多条出线同时启动，互不设优先级
- 一条路径失败只结束自己，其他继续
- 失败分两种：`route/*`（没有匹配分支，属正常排除）与 `execution/*`（异常、超时、参数求值失败）。前者记路径失败，后者记执行错误
- 多条处置措施可连到同一个问题解决节点，到达即继续，不等待聚合

### 串行

- 一个排查节点的同组后继按 `edge.order` 升序入队，未填按数组顺序
- 后继插到父令牌之后（深度优先），不是简单追加队尾：先把一条思路走到底，再换下一条
- 一次只跑队首；队首失败则试下一条；队列耗尽仍无成功 → failed
- 任一路径到达 problemSolved 且队列仍有剩余 → `paused`，生成一个决策待办：结束 / 继续
- 选择结束后实例 `success`，剩余队列与待办作废；选择继续则恢复队列，同一个成功令牌不再重复拦

### 人工待办

| kind | 出现时机 | 选项 |
|---|---|---|
| decision | 排查无绑定，或人工判断 | 是 / 否 |
| action | 排查动作、处置措施 | 完成 / 回退 |

另有第三种形态：串行成功后的「结束 / 继续」，存为 `kind=decision` 且 `request.type="after_success"`，结果 `continue` 或结束。

- 完成：推进到后继
- 回退：选择已执行的祖先节点 + 目标分支 + 可选原因
- 动作节点没有「不 OK」；没做成就是回退

### 回退

- 目标节点必须执行过：有 `call` 的节点要留下一条 `ok` 的 run，其余节点令牌走过即算；按最新一代取最近的令牌
- 目标正在等人工时按「重做该节点」处理，不要求它有后继
- 将该节点之后的所有 token 与 run 标记 `abandoned`，不删除；待办标记 `cancelled`
- 直接按用户选择的分支生成新 token，`generation + 1`
- 实例状态回到 running；若原本 paused，则剩余队列作废并按新分支重建
- 有其他活跃路径时禁止回退（v1 保守策略），提示先处理完
- 分支键由目标节点类型决定：排查是 yes/no，其余是 default，接口层归一
- 平台不撤销任何设备操作，确认框需写明

### 启动上下文

- 实例启动时把表单值固化为上下文，结构固定两层：`{"global": {...}, "scope": {函数名: {...}}}`，写进 `snapshots.payload_json`，`source` 固定 `start`
- 之后所有节点只读它；不再有「启动时全量采集设备数据」的语义，详细数据由下游函数按上下文实时拉取，返回值落在运行记录里
- 取值顺序：该函数的作用域 > 全局 > 旧扁平载荷（兼容老实例）
- 可选参数留空时，编译期带 `optional` 标记，运行时省略该参数，交给函数自身默认值

### 会话子进程

- 每个实例一个常驻子进程，`cwd` 为登记工程目录，解释器为登记解释器（缺省用平台自身）
- 协议：stdin/stdout 上的 JSON 行，请求 `{id, function, args}`，响应 `{id, ok, value|error}`
- 超时默认 120s，kill 后重启会话重试一次；会话崩溃同样重试一次；业务异常不重试
- 采集与判断函数必须只读、可重复调用

---

## 7. API

全部挂在 `/api` 下，JSON 字段用 camelCase（后端 snake_case 由 schemas 转换）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/flows` | 列表；`folderId`（`root` 表示未分类）、`search` 按名称/描述过滤 |
| POST | `/api/flows` | 新建，自动创建首条 committed revision |
| POST | `/api/flows/import` | 导入 doc，一次建流程并写入在线版本，不占检出锁 |
| GET | `/api/flows/{id}` | 含当前在线版本与检出状态 |
| PATCH | `/api/flows/{id}` | 名称、描述、模式、所属目录（显式传 null 表示移到未分类） |
| POST | `/api/flows/{id}/checkout` | 取编辑权，复制 current 为 working |
| PUT | `/api/flows/{id}/working` | 暂存 doc（自动，仅持有者） |
| POST | `/api/flows/{id}/commit` | 校验 → 写入 committed → 释放锁；问题随响应返回，不阻断 |
| POST | `/api/flows/{id}/discard` | 放弃检出 |
| POST | `/api/flows/{id}/admin/commit` | 管理员代存 |
| POST | `/api/flows/{id}/admin/discard` | 管理员丢弃并释放 |
| POST | `/api/flows/{id}/probe` | 设计期探测草稿 doc，返回 `{工程:函数: 参数表}`，不落盘 |
| POST | `/api/flows/{id}/releases` | 编译并发布，body 可带 `note` |
| GET | `/api/flows/{id}/releases` | 版本列表 |
| GET | `/api/releases/{id}` | 含 plan、签名、启动表单、doc |
| POST | `/api/flows/{id}/instances` | 启动，body `{releaseId?, input}`；省略 releaseId 用最新版本 |
| GET | `/api/instances` | 按 `flowId` / `status` 筛选 |
| GET | `/api/instances/{id}` | 详情：tokens、runs、reviews、启动上下文 |
| GET | `/api/instances/{id}/events` | SSE：实例状态摘要，内容变化才推，另有心跳 |
| POST | `/api/instances/{id}/reviews/{reviewId}` | 提交判断或完成/回退/继续 |
| POST | `/api/instances/{id}/rollback` | `{targetNodeId, branch, reason?}`；兼容 `target` |
| GET | `/api/folders` | 目录列表（含流程数） |
| POST | `/api/folders` | 新建目录，body `{name, parentId?}` |
| PATCH | `/api/folders/{id}` | 改名或移动；同级重名、挂到自身子树下返回 409 |
| DELETE | `/api/folders/{id}` | 仅允许空目录（无子目录、无流程），否则 409 |
| GET | `/api/projects` | 工程列表 |
| POST | `/api/projects` | 工程登记 |
| PATCH | `/api/projects/{id}` | 改登记信息（路径/模块/解释器/工厂）；不含名称，空串清空后两项 |
| DELETE | `/api/projects/{id}` | 删除登记，204；不检查流程引用 |
| POST | `/api/projects/{id}/refresh` | 重新发现接口 |
| GET | `/api/projects/{id}/functions` | 接口与签名 |
| POST | `/api/files` | 上传，返回内容哈希 |
| GET | `/api/files/{hash}` | 下载 |

说明：

- 启动实例时缺必填参数返回 400，`detail` 列出参数名
- 回退的 `branch` 必须是任意非空字符串，引擎按目标节点类型归一（排查 yes/no，其余 default）
- `/events` 推的是摘要（状态、token 计数、run 数、待办数、最后活动时间），不是增量事件；客户端按整体替换处理
- 删除工程不检查引用：已经引用它的流程不会立即报错，但重新探测或发布会失败

---

## 8. 前端

### 页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 流程列表 | 目录树 + 名称/描述搜索；名称、状态、最近发布、最近执行 |
| `/flows/:id/edit` | 设计页 | 画布 + 属性面板 + 工具栏 |
| `/flows/:id/releases` | 版本列表 | 版本、发布时间、发布人、备注 |
| `/flows/:id/run` | 启动页 | 选版本、填启动表单、点开始；不是执行观察页 |
| `/instances` | 执行记录 | 全部实例 |
| `/instances/:id` | 实例详情 | 画布只读 + 待办面板 + 路径时间线 + 运行记录 |
| `/projects` | 工程管理 | 登记、发现接口、调试会话 |

### 结构

```
src/
├── router/
├── stores/            editor / flows / instances
├── domain/            types.ts kinds.ts rules.ts
├── components/
│   ├── canvas/        VueFlow 封装、缩放/迷你地图
│   ├── nodes/         单一卡片渲染器，覆盖 7 种节点类型
│   ├── edges/         是/否分支边
│   ├── panels/        节点属性、流程属性、绑定选择、启动表单
│   ├── tasks/         判断待办、处置待办、回退选择
│   └── history/       运行记录、路径时间线
├── services/          API 客户端、SSE
└── styles/
```

节点渲染为定宽卡片：左侧类型色条 + 图标 + 标题 + 两行摘要 + 状态角标；排查节点显示「是/否」两个出口；开始节点副标题显示启动参数数量。渲染器注册表化，将来换几何形状只换组件。

自动布局在 `src/components/canvas/autolayout.ts`：按最长路径分列，列内按文档顺序，环与孤立节点放最后一列。坐标仍随 doc 落库；`position` 缺失或不是有限数字时，视为整篇需要重排。

### 画布交互

- 滚轮平移、Shift+滚轮横向、Ctrl+滚轮缩放
- 端口悬停 150ms 弹出可连接类型，点选即建节点并连线
- 节点右上角显示校验问题数量，悬停列出全部问题
- 开始节点不可删除；新建流程与老文档在设计页打开时自动补齐，保存时落库
- 工具栏「自动布局」整图重排并自动取景；导入或打开时只要有节点缺合法坐标，先整图重排（导入的坐标随版本落库，打开的标记未提交）

### 编辑锁交互

- 进设计页只读文档，不取锁；被他人持有时只读，显示「XX 正在编辑」
- 首次改动才 `checkout`；拿不到锁（他人抢先）转只读，横幅显示实际占用者
- 持锁期间每 2s 防抖暂存 working（不提示、不算保存）
- 「保存」提交并释放锁，不自动重新检出；页面仍可编辑，下次改动重新取锁
- 离开页面不释放锁：未保存的草稿与锁一并保留，重新进入继续编辑
- 「放弃」释放锁并丢弃草稿，需二次确认；未持锁时只清本地
- 保存不阻断：返回的 issues 在页面上提示，是否拦住交给发布
- 管理员代存／丢弃接口已就绪，前端尚未提供入口（见第 11 节）

---

## 9. 后端结构

```
backend/
├── app/
│   ├── main.py            FastAPI 入口、静态文件托管
│   ├── db.py              引擎、会话、ULID、老库补列
│   ├── models.py          SQLAlchemy 模型
│   ├── schemas.py         请求/响应
│   ├── logging_setup.py   日志与请求 id
│   ├── api/               flows / folders / releases / instances / projects / files
│   ├── services/
│   │   ├── compiler.py    doc → plan，含校验与参数绑定
│   │   ├── rules.py       与前端共用的连接与必填规则
│   │   ├── scheduler.py   双策略调度循环
│   │   ├── sessions.py    会话子进程管理
│   │   ├── signatures.py  接口探测与签名指纹
│   │   ├── projects.py    工程登记
│   │   └── storage.py     内容寻址文件
│   └── worker/
│       ├── session.py     子进程入口，JSON 行协议
│       └── probe.py       接口探针，输出函数签名
└── tests/
```

---

## 10. 核心时序

**保存**

1. 首次改动 → `checkout`：拿锁并建立 working 草稿
2. `PUT working` 暂存（可多次，2s 防抖）
3. `POST commit`：校验 doc → 新 revision 标 committed → `flows.current_revision_id` 更新 → 释放锁；问题只在响应里提示

**发布**

1. 取 current revision
2. 探测绑定函数的真实签名 → 校验规则 → 解析参数绑定 → 生成 plan
3. 采集签名指纹 → 冻结启动表单
4. 写 `releases`（plan / signature / inputs），版本号递增

**执行到人工**

1. `POST instances`：按版本表单校验启动参数 → 固化启动上下文 → 建 entry token
2. 调度器推进开始节点、自动节点，写 run
3. 遇到人工节点 → 建 review、token 置 waiting、实例 waiting
4. 前端 SSE 收到摘要 → 待办面板展示 → 用户选
5. 提交后生成后继 token，实例回 running

**回退**

1. 用户选目标节点与分支、填原因
2. 校验目标已执行、无其他活跃路径
3. 其后 token/run 标 abandoned，待办标 cancelled，建新 token（generation+1）
4. 实例回 running，历史保留全部记录

---

## 11. 尚未决定

| 项 | 影响 | 建议 |
|---|---|---|
| 结论文本是否允许运行时生成 | 需要额外返回值约定 | v1 按设计时文本处理 |
| 导出的 Python 代码形态 | 单文件 / 多文件 / 含 runner | 排在 demo 之后 |
| 生成 Python 代码直接执行（替代 plan 解释） | 需重写人工等待、回退、并行/串行与进程隔离 | 先做「渲染导出」验证，不替换现有 plan |
| 诊断文档的格式与导出 | 依赖 runs/reviews 字段 | 数据已够，格式后定 |
| 附件清理策略 | 存储增长 | 按引用计数定期清理 |
| 布局质量（减少连线交叉） | 平行分支多时可能穿卡片 | 先用 BFS 分层观察，必要时再引 dagre |
| 权限模型细节：谁能发布、管理员范围 | 内网版落地 | v1 无鉴权，管理员接口直接开放，前端未接入口 |
| 启动参数的类型转换 | 非字符串参数（`datetime`、枚举）需约定 | v1 原样传字符串，函数自己解析 |
| 重启后未完成实例的清理 | 会一直停在 running/waiting，看着像还在跑 | 启动时批量标 failed 或新增 interrupted 状态 |

---

## 12. 与旧项目的差异

| 旧 | 新 |
|---|---|
| BPMN 模型 + moddle | 自有 JSON |
| bpmn-js + 40 个属性面板 | Vue Flow + 7 种节点 |
| 编译成 BPMN 再部署 Flowable | 编译成步骤表，自研调度 |
| Java 运行时 + H2 | 纯 Python + SQLite |
| external worker + topic 协调器 | 调度器直接管理会话子进程 |
| 运行时按参数名在变量池里找值 | 发布时绑定到具体来源 |
| 单页 + 抽屉 | 多页路由 |
| 浮窗待办 + 曲线连接 | 右侧待办面板 + 定位 |
| 无版本概念，每次执行拍快照 | 草稿 / 在线版本 / 发布版本三层 |
| 只有并行 | 并行 + 串行双策略 |
| 动作三模式（自动/人工/核验） | 只有人工，完成 / 回退 |
| 启动输入散落在全局变量池 | 开始节点声明 + 发布时冻结表单 |

---

## 13. 变更记录

| 版本 | 内容 |
|---|---|
| v1（2026-10-31） | 初版：四层版本、doc/plan 格式、双策略调度、回退、并行下的待办 |
| v1.1 | 开始节点与启动参数（取代启动时的设备快照）、流程目录树、端口候选面板、执行页改为启动入口、可选参数留空即省略 |
| v1.2（2026-11-01） | 编辑锁改为懒检出（首次改动取锁、保存释放不重检、离开保留草稿）；工程支持编辑与删除，名称不可改；文档改为只保留当前态，v1.1 增量并入正文，历史见 git |
