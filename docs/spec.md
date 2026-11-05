# 诊断流程平台 · 设计说明（v1.13）

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
- 支持用户终止未结束的诊断，不提供手动暂停；串行模式下到达问题解决后的 `paused` 是调度语义，不是暂停按钮

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
| 诊断路径 | 沿 parentTokenId 从入口到某次当前到访的父链，保留原路径视图语义 |
| 执行轨迹 | 实例所有真实到访的时间记录，含失败、旧代及重复到访，不合并同名节点、不包含未执行队列 |
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
| status | `running` / `waiting` / `paused` / `success` / `failed` / `escalated` / `terminated` |
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
| parent_token_id | 用于追溯诊断路径 |
| visited_at | 首次实际进入该 token 节点的 UTC 时间；未执行为空，回退后保留 |
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

### diagnosis_states（仅诊断实例类模式）

| 字段 | 说明 |
|---|---|
| id, instance_id | 数据视图记录及所属诊断实例 |
| generation | 所属执行代，同代节点共享当前资源视图 |
| seq | 同实例递增的保存序号，避免同秒记录顺序歧义 |
| token_id | 具体到访；回退继承视图时为空 |
| reference_json | 不可变诊断清单的产物引用，不存业务大数据 |
| created_at | |

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

四阶段适配器同样登记为工程；工厂留空，节点绑定具体的适配器类，由节点配置提供构造参数。

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
- `runtimeInputs`：实例方法节点现场填写的参数，字段格式沿用 ManualField；仅支持已选实例方法的排查节点
- `binding.project` 用**工程名**，不用 id
- `snapshotFields`：旧版全局启动字段，读取时并入全局启动参数；新文档不再写

### 开始节点的启动参数

`inputs: StartParam[]`：

- `scope="global"`：所有函数可见
- `scope="function"`：只对该函数可见，覆盖同名全局参数；`function` 必填
- `type`：`text` / `number` / `boolean` / `datetime` / `file` / `array` / `object` / `json`。由函数签名推导，作者可覆盖
- `default`：设计时默认值；执行时可改，改完固化进本次启动上下文
- 参数清单可从函数签名派生；诊断类流程也支持新增全局输入。节点现场参数不要求在开始节点声明
- 数组、对象及 JSON 表单解析为原生结构传入 Python；文本与时间保持字符串，时间不自动转 datetime
- file 表单上传后保存 `{hash,name?}` 引用；实际调用前由平台校验已登记文件并解析为服务器绝对路径字符串，忽略客户端 path。已有服务器路径可用 text 参数传入
- array 须为 JSON 数组，object 须为 JSON 对象；json 支持任意 JSON 值及嵌套。`[]`、`{}`、JSON 的 `null` 和 `""` 是实际值，空白编辑框才表示未填写
- 缺开始节点时保存只提示，发布拦截

### 节点类型

| kind | 名称 | 形状 | 是否执行代码 | 后继 |
|---|---|---|---|---|
| start | 开始 | —（唯一入口） | 否 | 多条，全 default |
| dataPreparation | 数据准备 | 矩形 | 获取、解析 | 多条，全 default |
| investigation | 排查 | 菱形 | 有绑定则自动，无绑定则人工判断 | 是 / 否两组 |
| investigationAction | 排查动作 | 正梯形 | 否，人工 | 完成后接排查 |
| conclusion | 结论 | 矩形 | 否，展示设计时文本后继续 | 恰好一条，接处置或上升 |
| remediation | 处置措施 | 倒梯形 | 否，人工 | 处置 / 问题解决 / 上升 / 跳转 |
| jump | 跳转 | —（文本节点） | 否，人工 | 恰好一条，接除开始外的任意节点 |
| problemSolved | 问题解决 | 三角形 | 否 | 无 |
| escalation | 上升 | 五角星 | 否 | 无 |

`形状` 列只保留旧项目的视觉语义：v1 的画布统一把节点渲染为定宽卡片（见第 8 节），编译与调度都不依赖它。

**跳转节点**：把当前诊断转交给另一条诊断链路，本质是 import 另一个流程。当前阶段先做成人工确认的文本节点——节点说明写清转交给谁，执行到它时生成待办；人工确认转交完成后可接除开始节点外的任意节点（开始是唯一入口，只出不进），仍在设计时选定的后继上继续；转交不出去则回退（继续尝试其他思路）。跳转仍然只允许一条出线，放开的是后继类型而非后继数量。后续再接入真实的跨流程调用，届时由被调用链路的成功/失败回填到本节点。

v1 不实现：`deepDiagnosis`（深度诊断）。枚举保留，属性面板不提供入口。

### 连接规则

沿用旧项目的矩阵：

```json
{
  "start": ["investigation", "generic"],
  "generic": ["remediation", "problemSolved", "escalation", "generic"],
  "investigation": ["investigation", "investigationAction", "conclusion", "escalation", "jump"],
  "investigationAction": ["investigation"],
  "conclusion": ["remediation", "escalation", "jump"],
  "remediation": ["remediation", "problemSolved", "escalation", "jump"],
  "jump": ["investigation", "investigationAction", "conclusion", "remediation", "problemSolved", "escalation", "deepDiagnosis", "jump"],
  "problemSolved": [],
  "escalation": []
}
```

`generic` 仅用于开始事件等非诊断元素的边界判定。开始节点的出边不区分是/否，一律 `default`，可以同时连多个排查节点。

### 校验规则（发布必过）

同一份规则表由前端提示与后端编译共用（`src/domain/rules.ts` 与 `backend/app/services/rules.py`），避免「前端放过、后端拒绝」。

1. 恰好一个开始节点：无入边、至少一条出线、不绑定函数、不配置 `params`；重复连到同一目标报错
2. 连接符合上表及 §6 v1.10 扩展；排查的每条出线必须有 `branch`（yes/no），同组可多条；重复连接同一目标报错
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

只有函数的**真实参数**会被绑定，候选里多出来的字段一律不传；旧函数必填参数找不到来源时发布失败，实例方法的无来源必填参数转为节点现场填写。声明了 `outputs` 但函数没有对应参数只给 warning；`params` 里出现签名之外的参数名直接报错。

发布时的校验是严格的；`commit` 与 `import` 用 `strict_binding=False`：**保存不阻断，所有问题随响应返回**，参数类问题降级为 warning，允许存半成品，把绑定错误留到发布时暴露。工程探测失败（路径不可导入、函数不存在）一律拒绝发布。

### 启动表单冻结

发布时把开始节点的启动参数与真实签名合成执行页的表单，写入 `releases.inputs_json`。执行页只读它，发布后改 doc 或改函数签名都不影响已发布版本。

每个字段：`name / scope / function / type / required / default`。

- `type`：从函数注解推控件，list/sequence 推 array，dict/mapping 推 object；作者声明的值优先，str 文件路径须显式选 file
- `required`：签名里该参数没有默认值，且作者没给设计时默认值
- `default`：作者设计时默认值优先；否则取签名默认值。探针用 `repr` 兜底的值（`datetime`、枚举、自定义对象）标记为不可信，不冻结进表单——写进去只会在执行页预填假数据
- 启动时先填默认值，再校验必填；缺参返回 400 并列出参数名
- 显式指定现场填写的同名方法参数，在该方法所有使用节点都改为现场输入时，不再冻结其旧函数局部启动字段；全局字段保留供初始化及其他用途

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
启动 file 字段对应的 snapshot 表达式另带 `"file": true`，调用前将上传引用解析成服务器路径。

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

旧函数模式返回 `True` / 字符串 `"yes"`、`"y"`、`"true"`、`"是"` → yes；`False` / `"no"`、`"n"`、`"false"`、`"否"` → no；其他值视为执行错误。**不接受**任意对象的真假性。分支没有后继也记 `route/*`。

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
- 回退：选择执行轨迹中的具体到访 + 重执行或目标分支 + 可选原因
- 动作节点没有「不 OK」；没做成就是回退

### 回退

- 目标来自完整执行轨迹，以 `targetTokenId` 精确选择某次到访，失败、旧代与重复访问均可选。禁止未执行的排队节点和无到访证据的历史记录；开始及问题解决/上升等终止节点保留在轨迹中，但不作为回退目标。
- `retry` 从所选节点重新执行，父链接所选到访的父节点，不复用失败节点自身结果；`branch` 复用该次成功到访并按指定后继分支推进（有 call 必须有对应成功结果）。失败或未完成的到访使用 retry。
- 兼容旧请求 `targetNodeId` / `target`，缺省 mode 为 branch，选择最近到访；旧接口回退到等待处理的目标会重新执行，若目标子树外仍有活跃路径则拒绝回退。新界面始终提交具体 token，避免同节点多次到访歧义。
- 回退作用于整个实例：当前未完成路径与待办取消，保留所选父链，其余路径 token 标记 abandoned，已经 failed 的 token 保留失败状态；已有 run 的成功/失败及输出、错误不再被覆盖，保留全部轨迹。
- 新 token 使用递增代数，实例回到 running；允许 running/waiting/paused/failed，禁止 success/escalated/terminated。
- 参数只从所选到访父链的有效结果继承，不能借用其他失败尝试、旁路或较新代同名节点结果。
- 分支模式下排查使用 yes/no，其余使用 default；重执行模式不要求后继或分支，但开始及终止节点不接受重执行。
- 平台不撤销设备操作；确认框明确提示取消当前所有未完成路径。
- 旧库升级回填有 run/review 证据的到访时间，done/failed/waiting/running 令牌使用历史更新时间近似；无法区分是否执行的 abandoned 记录保持空值、不列入轨迹。旧版已经覆盖的 run 状态无法完整恢复；仅有输出且无错误的旧 abandoned run 可作为成功结果兼容。

### 启动上下文

- 实例启动时把表单值固化为上下文，结构固定两层：`{"global": {...}, "scope": {函数名: {...}}}`，写进 `snapshots.payload_json`，`source` 固定 `start`
- 之后所有节点只读它；不再有「启动时全量采集设备数据」的语义，详细数据由下游函数按上下文实时拉取，返回值落在运行记录里
- 取值顺序：该函数的作用域 > 全局 > 旧扁平载荷（兼容老实例）
- 可选参数留空时，编译期带 `optional` 标记，运行时省略该参数，交给函数自身默认值

### 会话子进程

- 每个实例一个常驻子进程，`cwd` 为登记工程目录，解释器为登记解释器（缺省用平台自身）
- 协议：stdin/stdout 上的 JSON 行，请求 `{id, function, args}`，响应 `{id, ok, value|error}`
- 超时默认 120s，kill 后重启会话重试一次；会话崩溃同样重试一次；业务异常不重试
- 与子进程的 stdin/stdout/stderr 一律 UTF-8：父进程以 `PYTHONIOENCODING=utf-8` 启动，子进程启动时重设三路流编码；解码失败按错误上报（会话侧计 `execution/crash`，探针侧作发布校验失败），不得用 `errors="replace"` 把坏字节替换成 U+FFFD 后落库
- 只约束 stdio，不设置 `PYTHONUTF8`：UTF-8 模式会连业务脚本 `open()` 的默认编码一起改掉，读 GBK 文件的老工程会被打挂
- 采集与判断函数必须只读、可重复调用

---

### 诊断实例类（首选，v1.12）

整张图绑定一个业务类，一次诊断是该类的一个逻辑实例。doc 顶层可选 diagnosis：
`{project, function: "类名", protocol: "diagnosis/v1", version, config: {}}`。
业务类继承标准库 SDK 的 Diagnosis。initialize 的真实参数按开始节点启动上下文绑定，
只在实例首次启动时执行获取、解析主要数据，按普通 Python 写法保存为 self.report 等成员。
排查节点可配置 `method: "实例方法名"`，额外参数沿用 params 和发布期来源解析；不选方法仍是人工排查。
节点参数可在页面选择「执行到此节点时填写」，写入 runtimeInputs；优先于同名启动或上游来源，不能与节点显式 params 同时绑定。
未配置来源的必填方法参数也自动生成现场字段，可选参数缺来源时使用 Python 默认值。
现场字段支持全部八种输入类型，必填性按真实方法签名，默认值优先用字段配置，其次可信的签名默认值。
节点方法返回 `{presentation: {blocks: [...]}, result?, reason?, data?}`；result 支持 bool、yes/no，
自动结果必须有非空 reason；result 空或省略时进入人工判断。人工提交不重跑业务方法。
现有调度器继续管理 token、分支、人工等待和回退，不生成整图顺序执行函数。

按用户当前要求，普通业务对象全部成员默认保存、恢复，不要求 self.data 固定容器或成员声明清单。
构造器成员、私有成员、后续节点新增或修改的成员按 Python 对象图整体保存，保留共享引用和循环引用。
首次执行 __init__ 和 initialize；人工等待后的恢复不重跑二者，直接还原对象状态并继续调用方法。
缓存管理上下文放在对象外，不占用业务自定的 self.data、self.cache、self.inputs 名称；
旧容器接口通过兼容入口保留。离开平台独立调用资源方法时按普通方法执行。
连接、句柄、锁、窗口等运行资源的恢复范围和排除／重建规则本轮暂不设计，记入 docs/todo.md；
先假定全部需要恢复，不能序列化时明确失败，不静默遗漏成员或重新采集伪装恢复。

按需资源方法用 `@resource(version="1")` 包住获取、解析，普通方法调用即触发缓存。
键由诊断类身份、版本、构造配置、启动输入、资源名、资源版本、全部绑定后参数及默认值规范化生成。
设备、文件哈希、查询时刻／时间范围、解析参数等可变来源必须作为显式参数；不能仅按函数名或字段名缓存。
同键并发调用合并；成功 None、空列表等可缓存；失败不写成功缓存。资源缓存读取提供副本，
普通成员按 Python 共享读写语义使用，更新随到访保存。处置不隐式刷新，重新采样由新的明确业务请求表达。

plan 顶层 diagnosis 保存上述绑定、args、definition（完整接口及资源版本描述）；
step.diagnosisMethod 保存 name、args、inputs（冻结的现场字段数组）。发布期探测 initialize、构造配置、公开方法和资源定义，
并冻结签名指纹；每次业务执行比对完整 definition 与 version。不归档业务源码，算法改变应更新版本重新发布。
诊断类流程不混用旧 binding、pipeline、dataPreparation；未配置 diagnosis 的历史流程保持原有行为。

开始节点执行 initialize 后记录初始化 run。对象、缓存结果和清单写成实例隔离的不可变产物，
业务对象及新资源结果采用 python-pickle/v1 快照，清单和对外展示仍为 JSON；主数据库只存引用。
diagnosis_states 表：id、instance_id、generation、seq、token_id（可空）、
reference_json、created_at；按实例和执行代查当前资源视图，按 token 固定具体到访的数据版本。
resource 清单通过参数键指向结果产物；object 指向整个业务对象；data 保留旧初始化容器产物兼容。
manifest 包含类定义身份和启动输入。Python 快照只恢复本实例业务进程产出的内容，校验完整性，不能使用上传附件代替。
既有 JSON 资源和容器产物继续兼容；节点返回 presentation、data 仍按 JSON／图片协议传给平台。
运行依据 pipeline.diagnosisState 和人工待办检查点保留清单引用，节点额外 data 也以产物引用保存。
人工动作、判断和串行暂停时关闭会话；继续执行从快照恢复 Python 对象和资源视图，
已有资源不重复获取、不执行业务解析。内存对象并非始终存活，但逻辑实例、执行记录保持同一次。

有现场字段的方法节点先创建 `reviews.kind=decision, request.type="parameters"` 待办并关闭会话，业务方法尚未执行。
request.fields 存表单，request.pipeline 存冻结自动参数及诊断检查点；提交仍用原 review API，`{result:"done",data:{参数名:值}}`。
平台校验后合并参数，file 解析成路径，恢复当前执行代最新对象与缓存再调用方法；同级分支在等待期间的新数据不被旧检查点覆盖。
字段错误或方法失败返回 409，保留原待办可修正后重试；成功后完成现场待办，按方法返回值继续或新建 judgment 待办。
人工判断只提交结果，不重新调用方法；现场填参及判断等待都不重跑 initialize。

同一执行代内，同级分支共享资源视图。回退新建执行代并继承所选到访当时的 manifest，
retry 虽然重跑方法但沿用该次已获得的资源；branch 沿用该次结果及数据视图。
未加载过的资源仍在首次访问时加载，较晚到访和其他代的数据不能污染历史。
节点判断失败仍记录此前成功加载的资源；超时／崩溃仍可能重复调用，不承诺跨故障 exactly-once。
产物缺失、损坏或类版本不符时报错，不偷偷重新采集。备份包含 backend/data，不新增资源自动回收，
不扩大平台重启自动续跑能力。不增加 API 路径；工程探测响应增加 methods、resources 描述。
使用步骤见 [模块接入手册](module-integration.md) 和 [实例示例](../examples/diagnosis-instance/README.md)。

### 节点内配置四阶段函数（兼容）

新建排查节点默认使用四阶段函数配置，不要求业务模块继承 SDK、编写适配器类或使用固定函数名。
节点属性面板分别选择获取、解析、展示、判断的工程和函数。获取、判断可缺省转人工；解析、展示必选。
旧 binding 模式保留；同一节点不能同时提供 binding 和 pipeline。数据准备节点使用获取／解析子集，其他节点不支持 pipeline。

doc 节点新增可选 pipeline：

```json
{
  "pipeline": {
    "acquire": {"project": "电压函数示例", "function": "lookup_measurement"},
    "parse": {"project": "电压函数示例", "function": "decode_voltage"},
    "present": {"project": "电压函数示例", "function": "describe_measurement"},
    "judge": {
      "project": "电压函数示例", "function": "is_under_voltage",
      "arguments": {
        "value": {"source": "parsed", "path": "voltage"},
        "limit": {"lit": 210}
      }
    },
    "manualFields": []
  }
}
```

manualFields 使用既有人工字段协议；未绑定获取函数时不能为空。
获取函数参数沿用节点 params 与开始上下文绑定，不把中间数据作为启动输入。
使用四阶段函数的流程允许在开始节点手工添加全局诊断输入，不再完全依赖获取函数签名推导。
人工获取时保存全局启动输入，供人工待办定位本次诊断。
其他阶段 arguments 参数映射支持 `{source:"raw"|"parsed"|"decision"|"inputs", path?:"字段路径"}`、
`{lit:值}`、`{omit:true}`。inputs 为全局启动输入与获取函数局部启动输入／实际入参合成的上下文。
raw 只对解析阶段有效：人工判断恢复只重跑展示、不重新解析，引用 raw 的展示在恢复时拿不到值，发布校验直接拒绝。
缺省映射：解析第一参数接 raw，展示第一参数接 parsed；判断第一参数不能只靠缺省——声明类型为标量
（text/number/boolean/datetime，以及 float/int/str/bool/date/time/decimal 等 Python 注解，`Optional`／
`Union` 剥壳后判定）时必须显式写出 `source` 与 `path`，缺 `path` 的发布校验直接报错，避免把整段上游
数据传给判断函数；非标量参数仍可用缺省的 parsed 接收整段数据。展示第二参数仅在名为
decision、judgment、result 时接 decision。其余必填参数必须配置，可选参数可省略使用函数默认值。
解析不能引用 parsed 或 decision，判断不能引用 decision；展示接 decision 时须接受首次调用的 None。

发布编译为 plan.step.pipeline：保留各阶段工程和函数，增加 signature 参数表和展开后的 arguments；
顶层增加 args 保存获取参数表达式。所有阶段参与发布／启动签名指纹检查，每次调用还逐项比较发布参数表。
业务函数在独立工程子进程中执行，平台内置编排器依次完成获取、解析、展示、判断和最终展示。
阶段边界要求 JSON 数据，禁止 repr 兜底或任意对象隐式转换；不同工程不共享对象状态。
函数须同步且参数显式命名、支持关键字传参；不支持可变参数和位置专用参数。
每个阶段使用独立的调用超时预算，默认 120 秒，超时／进程崩溃重试一次；业务异常不自动重试。

展示返回 blocks 描述时使用通用展示组件，返回普通文本／JSON 时展示文本；None 视为错误，
不能仅弹桌面窗口。判断支持 bool、yes/no，或带 result 与非空 reason 的对象；
普通返回值由平台生成包含函数名和返回值的依据。
人工阶段、检查点、文件校验、run 输出、回退及终止沿用下节机制；运行依据同时保存每阶段实际参数与耗时。
运行依据不归档原始数据：raw 只留字节数与 sha256，任一阶段参数值序列化后超过 4 KB 同样压成摘要；
解析结果在 `run.output` 里只存一份，待办为恢复人工判断另存一份，证据里不再重复。
人工判断恢复只执行最终展示，不重复取数或解析。函数实现代码不归档，签名一致不代表算法未改变。

平台不把外部类的全部构造参数暴露为流程配置。模块普通函数或已有工程工厂负责业务资源创建；
工厂沿用无参调用约定，其真实依赖由部署方提供。无关窗口、控制器的强制依赖仍需模块方解除，
平台不绕过构造函数或补入假对象。相关接入约束见手册第 0 节。

### 共享数据准备（v1.10）

数据准备节点 `kind=dataPreparation` 放在作者指定的位置，使用 pipeline 的 acquire／parse 完成一次
获取和解析；缺 acquire 时沿用 manualFields 人工取数，parse 必选，不支持 present／judge／binding。
成功后记录 run 并沿全部 default 后继推进，失败只结束本路径。每次实际到访生成新的不可变数据版本；
同一到访的多个后继引用同一结果，数据引用不会创建 token 或触发另一遍执行。

排查节点 pipeline 新增可选 `dataSource: {node: "准备节点ID", path?: "结果内字段路径"}`。
配置后不可再配置 acquire／parse／manualFields，仅执行 present／judge；path 留空使用整个解析结果，
阶段参数仍用 source=parsed 按字段选择。发布时要求来源为其他数据准备节点，且支配当前节点：
从开始节点到当前节点的所有可达路径都必须先经过来源。拒绝旁路、未来、自身及普通排查来源。
数据依赖只通过 dataSource 表达；控制边仍只负责调度和诊断分支，不增加数据连线。

连接矩阵同步扩展：start、investigation、investigationAction、jump 可接 dataPreparation；
dataPreparation 可接 investigation 或 dataPreparation，可有多个默认后继；重复目标及带 branch 的
准备节点出线报错。其他连接规则不变。

共享模式的自动获取、解析在业务进程内将 JSON 结果写入 `data/artifacts/<实例散列>/<产物ID>/`，
data.json 保存一次数据，index.sqlite 保存字段路径对应的字节范围。节点间只传
`{"$artifact":"不可变产物ID","path":[]}` 引用；读取具体字段只解码该字段的字节范围，不加载整份结果。
run.output.data、检查点及待办保留引用，不复制全部数据。字段路径沿用点分字典键／数组下标语义；
业务函数接收正常 JSON 数据，产物引用由平台解析。请求整个结果时仍需加载整个结果。
未启用共享模式的旧四阶段节点输出保持兼容。

产物按实例隔离，不依赖会话、工程工厂或类成员的存活；切换工程、人工等待后仍可读取。
人工判断恢复只重跑最终展示，沿用同一产物。人工取数检查点仍保存字段定义和启动输入。
回退只从所选到访父链查找最近一次成功准备结果，不借用旁路或其他代结果。
重跑下游排查沿用祖先准备结果；重跑准备节点生成新产物，历史运行继续引用旧产物。
超时／崩溃重试仍可能重复执行业务函数，本功能不承诺跨失败的严格 exactly-once；
准备函数继续要求只读、可重复调用。终止后保留已完成历史和产物，不接受迟到结果推进。

不新增主数据库表或 API 路径。产物须随 backend/data 一起备份，暂不自动回收；
缺失或损坏时报执行错误，禁止偷偷重新获取导致历史数据漂移。重启不自动续跑的边界保持不变。

### 四阶段排查适配器（investigation/v1，兼容模式）

此模式与旧函数绑定并存。平台编译器以节点类注册表处理各类节点，运行期仍解释 plan；
外部排查适配器继承 SDK 的 `InvestigationNode`，不取代调度器。

doc 的 `binding` 与 plan 的 `call` 新增可选字段：

```json
{
  "project": "电压诊断",
  "function": "VoltageCheck",
  "protocol": "investigation/v1",
  "version": "1.0.0",
  "config": {"threshold": 210}
}
```

plan.call 仍包含原有 args 表达式。config 为构造参数，args 为本次诊断输入，不混用。
未提供 acquire 时，诊断输入由适配器 inputs 声明；提供 acquire 时按该方法的真实签名绑定。
探测不构造适配器；发布前验证必需方法、初始化参数和版本，运行及人工恢复时再次校验版本。
缺省 protocol 保持旧行为；不识别的协议拒绝发布。

四个方法：acquire（可选）、parse（必需）、present（必需）、judge（可选）。
按采集 → 解析 → 展示 → 判断 → 展示结果执行。acquire／judge 为 None 时，分别创建人工采集／判断待办；
已实现的方法抛异常不自动转人工。每次调用构造 `Adapter(**config)`，结束后调用 close。
进入四阶段人工待办后关闭业务会话，恢复时重新导入模块并检查版本。
不同工程切换时切换会话子进程；业务状态不得依赖节点间对象或人工等待期间对象的存活。

人工阶段继续使用 reviews.kind=decision，通过 request.type 区分：

- `acquisition`：request.fields 为 manual_fields，request.pipeline 保存输入与阶段检查点；提交 `{result:"done", data:{...}}`。
- `judgment`：request.presentation 为展示描述，request.pipeline 保存原始／解析快照；提交 `{result:"yes"|"no", reason:"非空依据"}`。
- 人工恢复只执行尚未完成阶段；人工判断不会重复采集、解析。非法数据或调用失败保留原待办，允许修正后提交。
- 旧待办类型和提交格式继续有效。人工采集的 file 字段由平台验证哈希并解析存储路径，不接受客户端指定本地路径。

request.presentation 使用 `{blocks:[...]}`，块类型支持 text、metrics、table、line、image；详见接入手册。
执行待办和历史运行记录中的 line／image 块支持点击，在独立浏览器窗口打开图形查看页 `/figure`。
查看页支持缩小、放大（25%～400%）和适应窗口；图片另支持原始尺寸。默认适配窗口宽高，放大后可滚动查看。
图片按已有文件哈希读取，曲线复制当前展示数据，不重复调用业务方法。弹窗被拦截时提示允许弹窗后重试，关闭查看窗口不影响诊断。
image 块的 `src` 接受 64 位小写十六进制摘要（平台附件的内容哈希），也接受业务函数返回的图片 `bytes`
（平台落盘后换成摘要，仅 PNG／JPEG／WebP／GIF，单张 ≤ 10 MB，SVG 拒绝）；不接受文件路径或 http(s) 地址；
`alt`／`caption` 出现时必须是字符串。摘要经既有附件接口取图，运行记录里只保存摘要本身。
人工判断前和最终运行记录均渲染展示描述，不执行外部 HTML／JavaScript，不嵌入桌面窗口。

自动阶段可在适配器内部传递原有对象；持久化原始数据与解析结果必须可 JSON 表达，复杂对象通过
dump_raw／dump_parsed／load_parsed 显式保存和恢复。不使用 repr 代替可恢复数据，不接受非有限数值。
最终 run.output 为 `{data:解析快照, result:"yes"|"no", reason:依据}`，run.evidence 包含分支、展示、
检查点、阶段耗时、适配器名和版本；未完成阶段保存于待办，不生成冒充节点成功的 run。

不新增表或接口路径。POST `/api/instances/{id}/reviews/{reviewId}` 请求新增可选 data 对象。
工程接口探测响应新增 protocol、version、configParams、manualFields、automaticAcquisition、automaticJudgment。
版本与配置随计划发布固定，但平台不归档外部代码、依赖或数据源；旧版源码由接入方保留。
重启自动恢复边界保持原规格；本模式不增加后台自动续跑能力。

接入及桌面模块改造说明见 [module-integration.md](module-integration.md)。

### 手动终止

- 实例详情在 `running` / `waiting` / `paused` 时提供「终止诊断」，确认后执行。
- 终止提交后实例进入 `terminated`，记录 `ended_at`；活跃和等待 token 标记 `abandoned`，待办标记 `cancelled`，已完成历史保留。
- 停止后续调度并关闭该实例的独立会话进程；正在调用的结果不得覆盖终止状态，已发生的外部副作用不回滚。已启动的签名探测可运行至结束或超时，终止接口不等待探测，其结果不再推进诊断。
- 终止后的待办提交和回退返回 409。终止与自然完成串行判定，已自然结束则返回 409，重复终止返回当前详情。

## 7. API

全部挂在 `/api` 下，JSON 字段用 camelCase（后端 snake_case 由 schemas 转换）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/flows` | 列表；`folderId`（`root` 表示未分类）、`search` 按名称/描述过滤 |
| POST | `/api/flows` | 新建，自动创建首条 committed revision |
| POST | `/api/flows/import` | 导入 doc，一次建流程并写入在线版本，不占检出锁 |
| GET | `/api/flows/{id}/export` | 导出流程包（zip：workflow.json + manifest.json + attachments/）；`releaseId` 可选，缺省导当前在线版本 |
| POST | `/api/flows/import-package` | 导入流程包；附件按内容哈希入库，哈希不一致时改写 doc 引用 |
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
| POST | `/api/instances/{id}/terminate` | 无请求体，返回实例详情；允许 running/waiting/paused 终止，重复终止幂等，其他终态返回 409 |
| POST | `/api/instances/{id}/rollback` | `{targetTokenId?, targetNodeId?, mode?:"branch"|"retry", branch?, reason?}`；兼容 `target`，至少指定一种目标 |
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
| POST | `/api/files` | 上传，返回内容哈希、文件名、媒体类型、大小 |
| GET | `/api/files/{hash}` | 取附件内容；`disposition=inline` 且类型在白名单内时内联，缺省为下载 |

说明：

- `GET /api/files/{hash}` 的 `disposition` 只有 `inline` 生效，其余值一律按下载处理
- 白名单判断用**存储的文件名扩展名**，不用上传时报的 `media_type`：Windows 客户端给 Office 文件的类型常常是空或 `application/octet-stream`，不可信
- 不在白名单却带了 `inline`，不报错、直接按下载返回，避免前端猜错类型时页面拿不到文件
- 内联响应由服务端指定 `Content-Type` 并带 `X-Content-Type-Options: nosniff`，防止把改名后的 HTML 当图片执行
- 启动实例时缺必填参数返回 400，`detail` 列出参数名
- 回退的 `branch` 缺省 default；branch 模式按目标节点类型校验（排查 yes/no，其余 default），retry 模式忽略分支
- `/events` 推的是摘要（状态、token 计数、run 数、待办数、最后活动时间），不是增量事件；客户端按整体替换处理
- 删除工程不检查引用：已经引用它的流程不会立即报错，但重新探测或发布会失败

---

## 8. 前端

### 页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 流程列表 | 目录树 + 名称/描述搜索；名称、状态、最近发布、最近执行；导出当前版本、导入 JSON 或包 |
| `/flows/:id/edit` | 设计页 | 画布 + 属性面板 + 工具栏 |
| `/flows/:id/releases` | 版本列表 | 版本、发布时间、发布人、备注；可导出指定发布版本 |
| `/flows/:id/run` | 启动页 | 选版本、填启动表单、点开始；不是执行观察页 |
| `/instances` | 执行记录 | 全部实例 |
| `/instances/:id` | 实例详情 | 画布只读 + 待办面板 + 路径时间线 + 运行记录 |
| `/projects` | 工程管理 | 登记、发现接口、调试会话 |

流程包导出的是 doc 快照，不含业务工程代码：`manifest.json` 只记录流程信息、绑定的工程/函数与附件清单。导入包时附件按内容重新入库，内容被改过导致哈希不同的，doc 里的引用会同步改写。

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

### 参考数据的展示

附件出现在四处：节点属性面板、判断待办、处置待办、运行记录。四处共用 `AttachmentList`，类型判断只在 `services/files.ts` 实现一次。

只有两种展示方式，没有第三种：

| 方式 | 类型 | 交互 |
|---|---|---|
| 在线预览 | 图片 `png jpg jpeg webp gif bmp avif`、`pdf`、文本 `txt log csv md json yaml yml ini cfg`、音频 `mp3 wav ogg m4a aac flac`、视频 `mp4 webm mov` | 点文件名弹预览框：文本拉全文放 `pre`，PDF 用 `iframe`，音视频用原生控件，图片 `img` 撑满 |
| 下载 | 其余全部，含 `docx xlsx pptx dwg zip exe` 与 `svg html xml` | 文件名旁标「不支持在线预览」，点下载落到本机，由系统关联程序打开 |

- 判定依据是文件名扩展名，前后端各一份同一张表，改表必须两边一起改
- `svg` / `html` / `xml` 不内联：它们能在浏览器里执行脚本，内联等于让上传者往本平台页面注入代码
- 单个附件上限 50 MB，与上传一致
- 没有「用本机软件打开」这个动作。服务端代为打开只会在服务器桌面上弹窗，局域网用户在别的机器前看不到；浏览器也不允许网页启动本地程序。要真正一键调起本机软件，必须给每台电脑装协议处理程序，v1 不做

附件的不可变性：

- 存储是内容寻址，`data/files/` 的内容与任何可写路径无关，页面只读不写
- 用户拿到的永远是副本：点下载落到本机下载目录。源文件不可能被改，这是机制保证，不是权限约束
- 因此不提供在线编辑。要更新参考文件就上传新文件替换引用，哈希随之改变
- 运行中的实例读发布版本冻结的那份引用（`_node_meta` 走 `release.revision_id`），换文件不影响已开始的实例；要让在跑的流程用上新文件，必须重新发布
- 被替换掉的旧内容仍被历史运行记录引用，回收只能按引用计数，不能按上传时间

### 画布交互

- 编辑页右侧属性栏可收起以扩大画布，也可通过键盘或鼠标展开，恢复当前选中项的属性。
- 编辑画布左上角的节点菜单仅显示图例和名称；悬停或键盘聚焦时显示节点用途，仍可点击或拖入画布。
- 小地图按统一比例显示节点、简化内容和连线，标出主画布视口并支持定位导航。

- 滚轮平移、Shift+滚轮横向、Ctrl+滚轮缩放
- 端口悬停 150ms 弹出可连接类型，点选即建节点并连线
- 节点右上角显示校验问题数量，悬停列出全部问题
- 开始节点不可删除；新建流程与老文档在设计页打开时自动补齐，保存时落库
- 工具栏「自动布局」整图重排并自动取景；导入或打开时只要有节点缺合法坐标，先整图重排（导入的坐标随版本落库，打开的标记未提交）
- 选中节点时，与它直连的边按 `from → to` 播虚线流动动画；直接前驱蓝色高亮、直接后继绿色高亮，其余节点不变

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

1. 用户从执行轨迹选具体到访、重执行或分支、填原因
2. 校验真实到访及模式可用性
3. 废弃当前未完成路径、取消待办，保留运行结果和轨迹，建新 token（generation+1）
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
| v1.3（2026-11-02） | 流程包导出与回导：zip 内含 workflow.json、manifest.json、attachments/；导出可选发布版本，导入时附件重新入库并修正哈希 |
| v1.4（2026-11-02） | 参考数据展示定为两条路：白名单类型在线预览，其余提示不支持并给下载；明确参考文件不可在线编辑，替换即换哈希；不提供服务端代开本机软件 |

| v1.5（2026-11-03） | 新增手动终止诊断：终止按钮、terminated 终态及终止接口，保留历史并取消未完成工作 |

| v1.6（2026-11-03） | 区分诊断路径与执行轨迹；回退可选失败及旧代具体到访，新增重执行模式和持久到访时间，保留原始运行结果 |
| v1.7（2026-11-03） | 四阶段排查适配器、带参数初始化、人工取数及判断、声明式展示与模块接入手册；保留旧函数协议 |
| v1.8（2026-11-04） | 排查节点内直接配置四阶段函数、参数来源和人工字段，由平台自动编排；兼容适配器类模式 |
| v1.9（2026-11-04） | 四阶段运行记录不再归档原始数据：raw 只留字节数与哈希，超 4 KB 的阶段参数压摘要，解析结果只存一份；present/judge 拒绝引用 raw |
| v1.10（2026-11-04） | 数据准备节点、按实际到访共享不可变解析产物、字段索引读取及回退版本隔离 |
| v1.11（2026-11-05） | 流程诊断类、实例方法、启动共享数据、按需资源缓存、人工等待对象恢复及按到访回退资源视图；保留旧协议 |
| v1.12（2026-11-05） | 普通 Python 成员与业务对象整体快照，默认全部恢复，不再强制 self.data；恢复范围与运行资源边界移入 TODO |
| v1.13（2026-11-05） | 启动上传文件并解析服务器路径、实例方法到节点现场填参／上传、数组与对象及通用 JSON 表单；旧启动参数绑定保持兼容 |
