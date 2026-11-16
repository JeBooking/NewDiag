# 诊断流程平台 · 设计说明（v1.18）

本文件是项目的唯一设计依据。v1.18 把槽位传值收敛到节点内。v1.17 把接入方式收敛为**节点槽位模板**：每种节点固定几个槽位，用户在每个槽位填一个外接工程的公开方法，平台按槽位链调用，并生成 Python 代码在后端执行。v1.16 及以前的对象／固定资源／排查操作三层配置、11 种参数来源、展示映射与判定字段映射全部移除，不再保留兼容路径。

变更记录见第 12 节。

---

## 1. 产品边界

**做什么**

- 作者在画布上编排诊断流程：开始 → 排查 → 结论 → 处置 → 问题解决／上升
- 用节点槽位把原 Python 工程接进来：每个槽位填一个公开方法，平台不描述业务逻辑
- 发布成不可变版本，供人执行（v1 无鉴权，见第 11 节）
- 执行时由生成的 Python 代码调用业务接口取数与判断，人工完成处置并留痕
- 全流程记录：每次调用的输入、输出、依据、分支、时间、人工选择
- 流程按目录树归类；启动参数在开始节点声明，发布时冻结成表单
- 实例代码可导出为独立运行的 ZIP

**不做什么**

- 不直接操作设备。平台只调用数据服务器接口取数，动作由人对着设备完成
- 不做 BPMN 互通，不引入 BPMN 语义与序列化
- 不做实时协同编辑（v1 为共享草稿 + 编辑锁）
- 不维护数据对象，也不搬运模块之间的数据：数据留在外接工程或其数据源，平台只按槽位链传返回值
- 不做检查点恢复：进程重启后未完成实例不会自动续跑，也还没有启动时批量标记中断的清理逻辑
- 支持用户终止未结束的诊断，不提供手动暂停；串行模式下到达问题解决后的 `paused` 是调度语义，不是暂停按钮
- 不提供环境镜像：解释器与第三方依赖由部署方预装，平台只固定业务源码

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| 流程 flow | 一份可编辑的诊断图 |
| 目录 folder | 流程的树形归类，空表示未分类 |
| 检出 checkout | 作者拿到编辑权后产生的未提交副本 |
| 在线版本 doc | 已保存的当前版本，作者可见；被反复覆盖，不留历史 |
| 发布版本 release | 在线版本在发布时编译成 plan，不可变 |
| 实例 instance | 一次诊断执行 |
| 到访 token | 某个节点的一次执行机会；回退以具体到访为目标 |
| 槽位 slot | 节点模板上固定的一格，填一个外接工程的公开方法 |
| 构造槽 construct | 只在开始节点，实例化外接工程的对象并命名 |
| 拉取槽 fetch | 取数槽位；结果按对象、方法、参数缓存，同实例只取一次 |
| 处理槽 process | 加工本节点拉取的结果，结果交给展示与判定 |
| 展示槽 present | 排查节点必填，产出图片／曲线／指标／表格／文字 |
| 判定槽 decide | 排查节点必填，自动判定或交人工判断 |
| 现场参数 runtimeInputs | 执行到节点时才填写的参数 |

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

同级目录不允许重名（409）。不允许把目录挂到自己或自己的子目录下。

### flows

| 字段 | 说明 |
|---|---|
| id, name, description | |
| folder_id | 所属目录；空表示未分类 |
| owner_id | 作者；v1 固定 `local` |
| current_revision_id | 指向在线版本 |
| editing_by | 检出者；空表示未检出 |
| editing_revision_id | 检出缓冲 |
| editing_since | |
| created_at, updated_at | |
| deleted_at, purged_at | 软删除与墓碑 |

运行模式不在流程上保存，见实例 `runtime_json`。

### revisions

| 字段 | 说明 |
|---|---|
| id, flow_id | |
| json | 完整 doc（分层格式） |
| status | `working` / `committed` / `discarded` |
| author, created_at, committed_at | |

`working` 是唯一可修改的状态。`committed` 不可变。

### releases

| 字段 | 说明 |
|---|---|
| id, flow_id, revision_id | 引用快照，不复制 doc |
| version_no | 同一 flow 内递增 |
| plan_json | 编译产物 |
| signature_json | 接口签名指纹表，`{工程:模块:接口} → hash`；探测不到时为空字符串 |
| inputs_json | 发布时冻结的启动表单，`ReleaseInput[]` |
| published_by, published_at, note | |

### instances

| 字段 | 说明 |
|---|---|
| id, flow_id, release_id | |
| status | `running` / `waiting` / `paused` / `success` / `failed` / `escalated` / `terminated` |
| snapshot_id | 指向启动上下文 |
| runtime_json | `{engine:"python/v1", mode:"serial"|"parallel", maxParallel:1..128}`；缺省串行、上限 4 |
| plan_json | 启动时按当前模块副本重新编译的计划 |
| started_by, started_at, ended_at | |

### snapshots（启动上下文）

| 字段 | 说明 |
|---|---|
| id, instance_id, seq | v1 恒为 1 |
| payload_json | `{"global": {...}}` |
| source | v1 固定 `start` |
| params_json | v1 恒为 `{}`，保留列 |
| captured_at | |

### tokens

| 字段 | 说明 |
|---|---|
| id, instance_id, node_id | |
| status | `queued` / `running` / `waiting` / `done` / `failed` / `abandoned` |
| branch | 进入该 token 的分支（yes/no），default 记空 |
| sort_order | 串行队列序号，也是并行下的创建顺序 |
| generation | 回退代数，从 1 开始 |
| parent_token_id | 用于追溯诊断路径 |
| visited_at | 首次实际进入该 token 节点的 UTC 时间；回退后保留 |
| created_at, updated_at | |

### runs

| 字段 | 说明 |
|---|---|
| id, instance_id, token_id, node_id | |
| generation, attempt | |
| status | `ok` / `failed` / `abandoned` |
| input_json | 实际传入参数 |
| output_json | 返回值投影 |
| evidence_json | 依据、展示块、调用摘要 |
| error | 异常或超时信息，带分类前缀（`route/*`、`execution/*`、`signature/*`、`plan/*`） |
| started_at, ended_at | |

### reviews

| 字段 | 说明 |
|---|---|
| id, instance_id, token_id, node_id | |
| kind | `decision` / `action` |
| status | `pending` / `done` / `cancelled` |
| request_json | 展示给人的内容；`type` 为 `parameters` / `decision` / `activation` / `judgment` / `after_success` |
| decision_json | `{result:"yes"|"no"}` / `{result:"done"|"rollback", ...}` / `{result:"continue"}` / `{data:{...}}` |
| created_at, decided_at, decided_by | |

### files

| 字段 | 说明 |
|---|---|
| hash | 内容寻址，主键 |
| filename, media_type, size, path | |

### projects

| 字段 | 说明 |
|---|---|
| id, name, path, module, python, factory | 登记信息；`name` 唯一，是槽位与签名 key 的唯一口径 |
| created_at | |

接口在运行时发现，不落库。`python` 为空时用平台自身解释器。组合模式不使用 `factory`，构造器在流程里配置。

`name` 登记后不可修改：它出现在槽位与已发布版本里，改名会让已有流程找不到实现。

---

## 4. 文档格式（doc）

### 持久格式与编辑投影

revision 与流程包使用分层格式，只有当前版本可读写：

```json
{
  "format": 2,
  "flow": {"format": 1, "name": "诊断", "nodes": [], "edges": []},
  "bindings": {"format": 2, "nodes": {}},
  "presentation": {"format": 1, "nodes": {}}
}
```

- `flow`：业务节点、连接、分支顺序、说明、附件及启动参数声明
- `bindings.nodes`：按节点 id 保存的 `slots` 与 `runtimeInputs`
- `presentation`：按节点 id 保存 position；布局不可识别时重建默认坐标
- 旧格式（`format=1` 文档、`bindings.format=1`）不再读取，导入报错要求重新编排
- 编辑 API 返回 format=1 合并投影

编辑投影示例：

```json
{
  "format": 1,
  "name": "冷却系统诊断",
  "nodes": [
    {
      "id": "s1", "kind": "start", "title": "开始", "detail": "",
      "inputs": [{"name": "device", "type": "text"}],
      "slots": {
        "constructs": [
          {"object": "presenter", "project": "展示模块", "module": "presenter",
           "interface": "Presenter", "function": "Presenter",
           "arguments": {"device": {"source": "input", "name": "device"}}}
        ]
      }
    },
    {
      "id": "n1", "kind": "investigation", "title": "检查母线电压",
      "fields": {"observation": "母线电压", "acquisition": "复用启动数据", "assessment": "低于 210V"},
      "slots": {
        "fetch": {"object": "presenter", "project": "展示模块", "module": "presenter",
                  "interface": "Presenter.load", "function": "load", "arguments": {}},
        "present": {"object": "presenter", "project": "展示模块", "module": "presenter",
                    "interface": "Presenter.plot", "function": "plot", "view": "image",
                    "arguments": {}},
        "decide": {"object": "judge", "project": "判断模块", "module": "judge",
                   "interface": "Judge.check", "function": "check",
                   "arguments": {"data": {"source": "previous"}}}
      },
      "runtimeInputs": [{"name": "scale", "type": "number", "required": true}],
      "position": {"x": 240, "y": 80}
    }
  ],
  "edges": [
    {"id": "e0", "from": "s1", "to": "n1"},
    {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1}
  ]
}
```

### 槽位

每种节点有固定的槽位，槽位里填一个外接工程的公开方法：

| 节点 | 槽位 |
|---|---|
| start | `constructs`（可选；槽位引用了对象就必须有）+ `fetch`（可选）+ `process`（可选） |
| investigation | `fetch`（可选）+ `process`（可选）+ `present`（必填）+ `decide`（必填） |
| conclusion / investigationAction / remediation / jump / problemSolved / escalation | 无槽位 |

槽位字段：

| 字段 | 说明 |
|---|---|
| object | 构造槽里是对象名；其余槽是目标对象名，留空表示模块级函数 |
| project / module | 工程登记名与模块名 |
| interface | 接口目录中的完整名，用于探测校验 |
| function | 末级方法名 |
| view | 仅展示槽：`image` / `line` / `metrics` / `table` / `text` |
| human | 仅判定槽：`true` 表示不调用方法，由人工判断 |
| useCache | 仅拉取槽：默认使用缓存；置 `false` 表示重取一遍，取完仍写回缓存 |
| arguments | 参数名 → 来源表达式 |

**槽位链**：构造 → 拉取 → 处理 → 展示 → 判定。`previous` 只指本节点内上一步的结果：处理取拉取，展示与判定取拉取或处理。

跨节点不传值：下一个节点要数据就自己取，或从对象、数据源里拿。开始节点的拉取与处理用于把数据装进对象（由方法自己保存），返回值不向后续节点传递。

### 参数来源表达式

```json
{"lit": 210}
{"source": "input", "name": "device"}
{"source": "parameter", "name": "threshold"}
{"source": "previous"}
{"items": [...]}
{"fields": {"k": <表达式>}}
```

- `input`：启动表单的全局输入
- `parameter`：现场参数，必须在节点 `runtimeInputs` 声明
- `previous`：本节点内上一步的结果；处理槽要求有拉取槽，展示与判定要求有拉取或处理槽
- `path` 可下钻字段；`file: true` 把上传引用转成本地路径
- 不支持对象、资源、节点输出、系统路径等旧来源

### 开始节点的启动参数

`inputs: StartParam[]`，全部为全局参数：

- `type`：`text` / `number` / `boolean` / `datetime` / `file` / `array` / `object` / `json`
- `default`：设计时默认值；执行时可改，改完固化进本次启动上下文
- 数组、对象及 JSON 表单解析为原生结构传入 Python；时间保持字符串
- file 表单上传后保存 `{hash,name?}` 引用；调用前由平台校验并解析为服务器绝对路径
- 缺开始节点时保存只提示，发布拦截

### 节点类型

| kind | 名称 | 是否执行代码 | 后继 |
|---|---|---|---|
| start | 开始 | 有槽位则执行 | 多条，全 default |
| investigation | 排查 | 有槽位则执行，否则人工判断 | 是 / 否两组 |
| investigationAction | 排查动作 | 否，人工 | 完成后接排查 |
| conclusion | 结论 | 否，展示设计时文本后继续 | 恰好一条，接处置或上升 |
| remediation | 处置措施 | 否，人工 | 处置 / 问题解决 / 上升 / 跳转 |
| jump | 跳转 | 否，人工确认转交 | 恰好一条，接除开始外的任意节点 |
| problemSolved | 问题解决 | 否 | 无 |
| escalation | 上升 | 否 | 无 |

### 连接规则

```json
{
  "start": ["investigation"],
  "investigation": ["investigation", "investigationAction", "conclusion", "escalation", "jump"],
  "investigationAction": ["investigation"],
  "conclusion": ["remediation", "escalation", "jump"],
  "remediation": ["remediation", "problemSolved", "escalation", "jump"],
  "jump": ["investigation", "investigationAction", "conclusion", "remediation", "problemSolved", "escalation", "jump"],
  "problemSolved": [],
  "escalation": []
}
```

开始节点的出边不区分是/否，一律 `default`，可以同时连多个排查节点。

### 校验规则（发布必过）

同一份规则表由前端提示与后端编译共用（`src/domain/rules.ts` 与 `backend/app/services/rules.py`）。

1. 恰好一个开始节点：无入边、至少一条出线；重复连到同一目标报错
2. 连接符合上表；排查的每条出线必须有 `branch`（yes/no），同组可多条
3. 结论：`title`、`detail` 非空；恰好一条出线
4. 处置措施、排查动作、跳转：`title`、`detail` 非空
5. 每条路径可达终止节点，不允许死路与不可达节点
6. 不允许隐式分叉、隐式结束、假汇合；开始节点按自身语义豁免
7. 环允许存在，但必须能从环内走出去
8. 构造槽的 `object` 合法且唯一；没有构造槽时，所有槽位只能是模块级函数
9. 槽位引用的接口必须探测得到，且同步、支持关键字参数
10. 参数来源只允许上表；`parameter` 必须在节点 `runtimeInputs` 声明；`previous` 只能出现在允许的槽
11. 排查节点必须有 `present` 且指定 `view`；必须有 `decide`，要么给方法，要么 `human: true`
12. 启动参数名称不重复，类型受支持
13. 旧接入字段（`composition`、`operation`、`binding`、`method`、`pipeline`、`outputs`、`dataPreparation`）不再接受：导入返回 422，发布也会拦下

---

## 5. 发布编译（doc → plan）

编译在发布时执行一次，产物不可变。

### 探测

发布前按槽位逐工程探测公开接口：模块导入、类与方法签名、docstring、实现指纹。探测失败或接口不存在直接拦发布。同一工程同模块的目录在一次发布内复用。

### 步骤表

```json
{
  "format": 1, "flowId": "f1", "release": 3, "entry": "s1",
  "steps": {
    "s1": {
      "kind": "start",
      "next": {"default": ["n1"]},
      "slots": {"constructs": [{"object": "presenter", "project": "展示模块", "...": "..."}]}
    },
    "n1": {
      "kind": "investigation",
      "slots": {
        "fetch": {"object": "presenter", "...": "..."},
        "present": {"...": "...", "view": "image"},
        "decide": {"human": true}
      },
      "runtimeInputs": [{"name": "channel", "type": "text", "required": true}],
      "next": {"yes": ["n2"], "no": ["n5"]}
    },
    "n2": {"kind": "conclusion", "text": "电压不足", "next": {"default": ["n3"]}},
    "n3": {"kind": "remediation", "human": true, "next": {"default": ["n4"]}},
    "n4": {"kind": "problemSolved", "terminal": true}
  },
  "signature": {}
}
```

- `slots`：发布时冻结的槽位，指纹写进每个调用
- `runtimeInputs`：执行到该节点时要现场填写的参数
- 没有槽位的排查节点编译成 `{"human": true}`

### 启动表单冻结

发布时把开始节点的启动参数写入 `releases.inputs_json`，每个字段 `name / type / required / default`。执行页只读它，发布后改 doc 或改接口签名都不影响已发布版本。

- `required`：启动参数未被任何槽位引用时按作者声明；被引用时取接口签名该参数是否必填，且作者给了默认值就不算必填
- `default`：作者设计时默认值优先；否则取签名默认值。探针用 `repr` 兜底的值标记为不可信，不冻结进表单

### 签名指纹

对每个被引用接口的参数表做规范化：`接口名(参数名:类型[=默认值], ...)`，按名称排序后整体 sha256。哈希不含返回注解。

发布时写入 `releases.signature_json`。执行前逐个比对当前代码；实例还没有任何 run 记录时比对，一旦跑过节点，人工恢复不再拦：

- 不一致：实例直接 `failed`，错误码 `signature/changed`
- 指纹为空字符串视为未知，不阻断

---

## 6. 执行语义

### 生成 Python 执行

发布固定流程编排、槽位和输入表单，不固定模块实现代码。实例启动时：

1. 读取槽位引用的工程，复制源码到该实例目录（排除 `.git`、`.venv`、`node_modules`、`__pycache__`）
2. 按副本重新探测接口并编译 plan
3. 生成 `flow.py`、`runtime/`、`configuration.json`、`modules/`
4. 起常驻子进程执行

已经运行的实例始终使用启动时复制的源码；后续修改原工程只影响新实例。依赖包和解释器环境由部署方管理。

`flow.py` 的每个节点是一个函数，函数体只调用运行库并返回后继分支；运行库 `runtime/generated_runtime.py` 负责与平台交换 enter/complete/ask/error 事件。命令行入口为 `python flow.py --mode serial` 或 `--mode parallel --max-parallel 4`，终端代替平台接收人工输入。

### 槽位执行

- 构造槽在开始节点执行一次，对象常驻所属工程进程；后续按对象名引用，不重复构造
- 拉取与处理的返回值只在本节点内流转；跨节点不保留，节点要数据就自己取（命中缓存）
- 拉取结果按 `{工程, 模块, 接口, 对象, 归一化参数}` 缓存，同一实例只调用一次；处理不缓存
- 拉取槽取消「使用缓存」时跳过缓存读取，取完写回缓存，后续节点跟着用新值；其余槽位不允许设置
- 展示按 `view` 校验返回值并生成展示块；判定接受布尔、`yes`/`no` 或 `{result, reason}`
- 判定 `human: true` 时不调用方法，直接生成判断待办
- 对象图按 pickle 快照；回退时恢复到目标到访，已取得的上游值与缓存保留

### 人工等待

生成代码同步等待 stdin 消息，平台与业务模块进程持续存活。人工回复后从等待点继续；等待时间不计入自动步骤超时（默认 120 秒）。终止、回退及服务关闭结束对应分支和业务子进程。平台重启后自动恢复常驻进程不在本轮范围内。

### 串行与并行

仅由实例 runtime 决定。

- 串行沿深度优先顺序执行，一条思路无后继或出错后继续下一个同级分支；同一进程保留已取得的数据。成功但还有待排查思路时询问结束／继续
- 并行使用真实独立分支进程。遇到分叉，原进程继续第一个子分支，其余子分支排队
- `maxParallel` 限制活跃排查分支数，人工等待仍占名额；分支进程退出后释放名额
- 分叉继承分叉时已有的对象状态与缓存；之后各分支内存、对象、缓存独立

### 回退

当前入口限定 waiting/paused/failed；只选择已完成且有保存结果的具体到访 B。B 的原输出和祖先输出可供新路径读取，直接从新选后继执行，不再运行 B，不重新提出人工输入或判断。后续节点输出不进入新路径有效历史。旧进程、排队项和未处理待办取消；历史 run 保留，新代数阻止旧进程继续写入。开始与终止节点不能作为目标。

### 实例状态收敛

1. 有 `running` token → `running`
2. 有 `waiting` token → `waiting`
3. 有 `queued` token：串行且已到达 problemSolved → `paused`，否则 `running`
4. 无活跃 token 时，有 problemSolved → `success`，否则有 escalation → `escalated`，否则 `failed`

优先级 `success > escalated > failed`；进入终止态时写 `ended_at`。

### 排查的分支判定

判定的 `result` 输出 `yes` / `no`（布尔自动转换），或人工选择 yes/no。分支没有后继记 `route/*`。

### 人工待办

| 场景 | 类型 | 选项 |
|---|---|---|
| 节点没有槽位，或判定槽为 `human` | decision | 是 / 否 |
| 槽位参数需要现场填写 | parameters | 填字段后提交 |
| 排查动作、处置措施 | action | 完成 / 回退 |
| 串行成功但还有同级思路 | after_success | 结束 / 继续 |

- 现场参数的业务校验失败保留同一待办，可修正后重试
- 动作节点没有「不 OK」；没做成就是回退

### 启动上下文

- 启动时把表单值固化为 `{"global": {...}}`，写进 `snapshots.payload_json`
- 之后所有槽位只读它；数据由业务接口按上下文或外部数据源实时获取
- 可选参数留空时，运行时省略该参数，交给 Python 默认值

### 业务子进程协议

- 每个实例的每个工程一个常驻子进程，`cwd` 为副本工程目录，解释器为登记解释器（缺省用平台自身）
- 协议：stdin/stdout 上的 JSON 行；请求 `{id, function, args}` 或 `{id, function:"__newdiag_slot__", args:{op,...}}`；响应 `{id, ok, value|error}`，可选 `files` 列本次新落盘的图片
- `op`：`construct` 构造对象、`call` 调用方法、`restore` 取状态引用
- stdio 一律 UTF-8：父进程以 `PYTHONIOENCODING=utf-8` 启动，子进程重设三路流编码；不设置 `PYTHONUTF8`，避免打挂读 GBK 文件的业务脚本
- 业务异常不重试，直接记 `execution/exception`

### 手动终止

- `running` / `waiting` / `paused` 时提供「终止诊断」
- 实例进入 `terminated`，活跃与等待 token 标 `abandoned`，待办标 `cancelled`，历史保留
- 终止后的待办提交和回退返回 409；重复终止幂等

---

## 7. API

全部挂在 `/api` 下，JSON 字段用 camelCase（后端 snake_case 由 schemas 转换）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/flows` | 列表；`folderId`、`search` |
| POST | `/api/flows` | 新建，自动创建首条 committed revision |
| POST | `/api/flows/import` | 导入 doc |
| GET | `/api/flows/{id}/export` | 导出流程包（zip） |
| POST | `/api/flows/import-package` | 导入流程包；附件按内容哈希入库 |
| GET | `/api/flows/{id}` | 含当前在线版本与检出状态 |
| PATCH | `/api/flows/{id}` | 名称、描述、所属目录 |
| POST | `/api/flows/{id}/checkout` | 取编辑权 |
| PUT | `/api/flows/{id}/working` | 暂存 doc |
| POST | `/api/flows/{id}/commit` | 校验 → committed → 释放锁；问题随响应返回 |
| POST | `/api/flows/{id}/discard` | 放弃检出 |
| POST | `/api/flows/{id}/admin/commit` | 管理员代存 |
| POST | `/api/flows/{id}/admin/discard` | 管理员丢弃并释放 |
| POST | `/api/flows/{id}/probe` | 设计期探测草稿槽位引用的接口 |
| POST | `/api/flows/{id}/releases` | 编译并发布 |
| GET | `/api/flows/{id}/releases` | 版本列表 |
| GET | `/api/releases/{id}` | 含 plan、签名、启动表单、doc |
| POST | `/api/flows/{id}/instances` | 启动，`{releaseId?, input, runtime?:{mode,maxParallel}}` |
| GET | `/api/instances` | 按 `flowId` / `status` 筛选 |
| GET | `/api/instances/{id}` | 详情：tokens、runs、reviews、启动上下文、runtime |
| GET | `/api/instances/{id}/events` | SSE：实例状态摘要 |
| POST | `/api/instances/{id}/reviews/{reviewId}` | 提交判断、参数、完成、回退或继续 |
| POST | `/api/instances/{id}/terminate` | 终止 |
| POST | `/api/instances/{id}/rollback` | `{targetTokenId?, targetNodeId?, branch?, reason?}` |
| GET | `/api/instances/{id}/code` | 导出实例生成的代码 ZIP |
| GET/POST/PATCH/DELETE | `/api/folders...` | 目录 |
| GET/POST/PATCH/DELETE | `/api/projects...` | 工程登记与接口发现 |
| POST/GET | `/api/files...` | 上传与下载 |

说明：

- `GET /api/files/{hash}` 的 `disposition` 只有 `inline` 生效；白名单判断用存储文件名扩展名，不用上传时的 `media_type`；内联响应带 `X-Content-Type-Options: nosniff`
- 启动实例时缺必填参数返回 400
- 回退的 `branch` 缺省 default；按目标节点类型校验（排查 yes/no，其余 default）
- `/events` 推的是摘要，客户端按整体替换处理
- 删除工程不检查引用：已引用它的流程不会立即报错，但重新探测或发布会失败

---

## 8. 前端

### 页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 流程列表 | 目录树 + 搜索；导出、导入、回收站 |
| `/flows/:id/edit` | 设计页 | 画布 + 属性面板 + 工具栏 |
| `/flows/:id/releases` | 版本列表 | 版本、发布时间、发布人、备注 |
| `/flows/:id/run` | 启动页 | 选版本、填启动表单、选串并行 |
| `/instances` | 执行记录 | 全部实例 |
| `/instances/:id` | 实例详情 | 只读画布 + 待办面板 + 时间线 + 运行记录 + 代码导出 |
| `/projects` | 工程管理 | 登记、接口目录、调试 |

### 结构

```
src/
├── router/
├── stores/            editor / flows / instances
├── domain/            types.ts kinds.ts rules.ts parameters.ts
├── components/
│   ├── canvas/        VueFlow 封装、校验、迷你地图
│   ├── nodes/         定宽节点卡片
│   ├── edges/         是/否分支边
│   ├── panels/        节点属性（槽位）、流程属性、启动表单
│   ├── tasks/         判断待办、参数待办、处置待办、回退
│   └── history/       运行记录、路径时间线
├── services/          API 客户端、SSE
└── styles/
```

### 画布交互

- 滚轮平移、Shift+滚轮横向、Ctrl+滚轮缩放
- 端口悬停 150ms 弹出可连接类型，点选即建节点并连线
- 节点右上角显示校验问题数量，悬停列出全部问题
- 开始节点不可删除；新建流程与老文档在设计页打开时自动补齐
- 工具栏「自动布局」整图重排并自动取景
- 选中节点时，与它直连的边播虚线流动动画

### 附件展示

只有两种方式：白名单类型在线预览（图片 / PDF / 文本 / 音视频），其余提示不支持并给下载。`svg` / `html` / `xml` 不内联，防止脚本注入。单附件上限 50 MB。附件内容寻址、不可变，替换即换哈希。

### 编辑锁

- 进设计页只读文档，不取锁；被他人持有时只读
- 首次改动才 `checkout`
- 持锁期间每 2s 防抖暂存 working
- 「保存」提交并释放锁，不自动重新检出
- 离开页面不释放锁
- 保存不阻断：issues 在页面提示，是否拦住交给发布

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
│   │   ├── compiler.py    doc → plan：结构校验与槽位编译
│   │   ├── slots.py       槽位校验、接口探测、回退状态合并
│   │   ├── rules.py       与前端共用的连接与必填规则
│   │   ├── scheduler.py   实例持久化基类：token、待办、运行记录、状态收敛
│   │   ├── generated_runner.py  生成执行的分支进程、事件与回退
│   │   ├── codegen.py     生成 flow.py 与实例目录
│   │   ├── code_export.py 导出实例代码 ZIP
│   │   ├── signatures.py  接口探测与签名指纹
│   │   ├── flow_format.py 分层持久格式
│   │   ├── parameters.py  启动/现场参数校验
│   │   ├── projects.py    工程登记与接口发现
│   │   └── storage.py     内容寻址文件
│   └── worker/
│       ├── generated_runtime.py  生成代码的运行库
│       ├── slot_runtime.py  槽位链、拉取缓存、展示与判定
│       ├── objects.py     工程内对象：构造、调用、快照
│       ├── session.py     业务子进程入口，JSON 行协议
│       ├── probe.py       接口探针
│       ├── artifacts.py   不可变产物与对象快照
│       └── node_sdk.py    值投影、图片落盘、字段与展示校验
└── tests/
```

---

## 10. 核心时序

**保存**

1. 首次改动 → `checkout`
2. `PUT working` 暂存（2s 防抖）
3. `POST commit`：校验 doc → 新 revision → 释放锁

**发布**

1. 取 current revision
2. 探测槽位引用的接口 → 校验 → 冻结槽位与启动表单 → 生成 plan
3. 写 `releases`

**执行到人工**

1. `POST instances`：校验启动参数 → 固化启动上下文 → 复制源码并生成代码 → 建 entry token
2. 生成进程推进，`enter/complete` 事件写 run
3. 遇到人工节点 → 建 review、token 置 waiting、实例 waiting
4. SSE 推摘要 → 待办面板展示 → 用户提交
5. 事件线程把答案写回生成进程，继续执行

**回退**

1. 用户从轨迹选具体到访、选后继分支、填原因
2. 校验该到访有可复用结果
3. 废弃未完成路径与待办，保留历史，建新 token（generation+1）
4. 从选中到访的输出继续

---

## 11. 尚未决定

| 项 | 影响 | 建议 |
|---|---|---|
| 结论文本是否允许运行时生成 | 需要额外返回值约定 | v1 按设计时文本处理 |
| 平台重启后恢复生成实例 | 需重建进程及未完成等待 | 本轮不做 |
| 工程托管方式 | 当前登记只是服务器本机路径 | 见 `docs/todo.md` |
| 附件清理策略 | 存储增长 | 按引用计数定期清理 |
| 失败原因区分 | `failed` 一个桶装了四类原因 | 见 `docs/todo.md` |
| 权限模型细节 | 内网版落地 | v1 无鉴权 |

---

## 12. 变更记录

| 版本 | 内容 |
|---|---|
| v1（2026-10-31） | 初版：四层版本、doc/plan、双策略调度、回退、并行待办 |
| v1.1–v1.6 | 开始节点与启动参数、目录树、编辑锁、流程包、手动终止、执行轨迹 |
| v1.7–v1.10 | 四阶段适配器、节点内四阶段函数、共享数据准备（v1.16 已移除） |
| v1.11–v1.13 | 诊断业务类、普通成员快照、上传与现场参数（业务类协议 v1.16 已移除，文件与现场参数保留） |
| v1.14（2026-11-12） | 新实例生成 Python 执行、常驻人工等待、限额并行、回退复用结果、分层持久化、启动固定源码 |
| v1.15（2026-11-12） | 普通 Python 工程组合接入：对象、固定资源、操作配置 |
| v1.16（2026-11-15） | 收敛为组合 + 生成执行单一方案：删除旧函数绑定、四阶段函数与适配器、诊断业务类、数据准备节点、解释执行引擎与 `flows.mode`；`diagnosis_sdk` 仅留 import 兼容 |
| v1.17（2026-11-16） | 接入改为节点槽位模板：开始节点构造对象并可选取数，排查节点固定「拉取／处理／展示／判定」四槽；删除 composition 三层、11 种参数来源、展示与判定字段映射；参数来源收敛为 lit／input／parameter／previous |
| v1.18（2026-11-16） | 槽位传值收敛到节点内：`previous` 只指本节点上一步，跨节点不再保留；开始节点的拉取与处理用于把数据写进对象，返回值不后传 |
