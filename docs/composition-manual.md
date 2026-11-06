# 组合接入开发手册

面向把既有 Python 工程接进诊断平台的人。读完后应能：登记工程 → 在流程里声明对象、资源和操作 → 发布 → 跑通一次诊断，并且**不新增总控代码、不复制原工程算法**。

设计依据是 [spec 第 14 节](spec.md)，可运行示例在 [`examples/composed-diagnosis`](../examples/composed-diagnosis)。

---

## 1. 这套方案解决什么

旧做法要求接入方写一个 `Diagnosis` 子类，把取数、解析、绘图、判断在 `initialize` 和各个方法里拼起来。原工程的接口是现成的，但拼装代码要新写、要维护，还要跟着三个工程的发版走。

组合模式把「拼装」变成平台数据：

```
流程配置（doc.composition）
  objects    —— 谁：在哪个工程里创建哪个对象
  resources  —— 取什么：启动时/按需执行的取数与解析步骤
  operations —— 做什么：排查节点内部的有序调用 + 结果映射 + 展示

运行期
  平台调度器 → 按配置逐项 RPC → 各工程 worker 执行公开接口
             → 同工程对象保留句柄，跨工程只传 JSON / 文件 / 图片
```

接入方只提供公开接口；编排者在平台上选择接口、绑定参数。HTTP/数据库表结构不变，仍然是 doc → plan → Scheduler，人工待办、回退、终止、执行轨迹全部复用。

---

## 2. 三步接入

### 2.1 确认模块有可调用的公开接口

平台只认公开、同步、支持关键字传参的接口：

| 允许 | 说明 |
|---|---|
| 模块级函数 | `save_plot`、`parse_time` |
| 类构造器 | `DataClient(root=None, interval=None)` |
| 类方法 / 静态方法 | `Dataset.load(path)` |
| 实例方法 | `client.fetch(...)`、`judge.summary(verdicts)` |
| 带 `**kwargs` 的函数 | 只绑定显式列出的参数，例如 `save_plot(..., **savefig_kwargs)` 可以直接用 |

不允许：协程、`*args` 形式的可变位置参数、必须靠窗口按钮触发的逻辑。私有名称（`_` 开头）不会被探测，也不能调用。

本方案用到的三个工程**一行未改**：

| 工程 | 模块 | 用到的接口 |
|---|---|---|
| MockDataService | `dataclient` | `DataClient(root=...)`、`client.fetch(...)` |
| PlotEngine | `dataset` | `Dataset.load(path)`、`dataset.plot_series(device, activity, metric)` |
| PlotEngine | `plot_window` | `save_plot(data, path, title=..., xlabel=..., ylabel=...)` |
| JudgeEngine | `judgekit` | `JudgeEngine(thresholds=...)`、`judge.judge_series(item)`、`judge.summary(verdicts)` |

### 2.2 登记工程

「工程管理」页新增三行：

| 名称 | 路径 | 模块 | 解释器 |
|---|---|---|---|
| MockDataService | `D:\FILE\Projects\MockDataService` | `dataclient` | 留空（用平台自身） |
| PlotEngine | `D:\FILE\Projects\PlotEngine` | `dataset` | `...\PlotEngine\.venv\Scripts\python.exe` |
| JudgeEngine | `D:\FILE\Projects\JudgeEngine` | `judgekit` | 留空 |

规则：

- **名称**是流程配置与签名指纹的唯一键，登记后不能改。
- **模块**只是默认值；步骤里可以写同一工程下的其它模块（示例里的 `plot_window` 就是）。一个工程对应一个解释器和一个工作目录，多模块共用同一个 worker。
- **解释器**是真正执行该工程代码的 Python。PlotEngine 依赖 matplotlib/tkinter，必须用它自己的 `.venv`；`python` 留空表示用平台后端解释器。
- 工程目录会被加入该 worker 的 `sys.path` 和 `cwd`；跨目录 import 需要模块方自己处理，平台不拼接兄弟工程路径。

登记后点「刷新接口」应能看到构造器、方法和函数列表。探测是**静态**的：只读签名和 docstring，不实例化、不调用业务方法。

### 2.3 在流程里配置

路径：设计页 → 空白处点击（不选中节点）→ 右侧「流程属性」→「组合配置」。

三段配置：

1. **工程对象**：给对象起实例内名字，选工程、模块、构造器。构造参数常用 `平台路径 → 输出目录`。
2. **启动资源**：一组有序步骤。示例里是 `fetch`（取数落盘）→ `dataset`（解析成 Dataset 对象，`saveAs: dataset`）。
3. **节点操作**：排查节点内部的有序步骤，外加「自动判断」和「展示」。

然后在画布上选中排查节点 → 右侧「组合操作」→ 选择操作 → 填操作参数（示例里是 `metric = vibration`）。

---

## 3. 配置速查

### 3.1 对象

```json
{
  "id": "client",
  "project": "MockDataService",
  "module": "dataclient",
  "entry": "DataClient",
  "args": { "root": { "system": "directory" } }
}
```

对象在**首次被引用**时创建，之后整个实例复用。对象只活在所属工程的 worker 里，句柄不跨进程。

### 3.2 步骤

```json
{
  "id": "fetch",
  "project": "MockDataService",
  "module": "dataclient",
  "kind": "method",
  "target": "client",
  "function": "fetch",
  "args": {
    "device_id": { "input": "device" },
    "activity": { "input": "activity" },
    "start_time": { "input": "start_time" },
    "end_time": { "input": "end_time" }
  },
  "output": "file"
}
```

- `kind=function` 时 `function` 可以是 `Class.method`（类方法），例如 `Dataset.load`。
- `kind=constructor` 时 `function` 是类名，构造参数写在 `args`。
- `kind=method` 时 `target` 必须是已声明的对象，或前面步骤 `saveAs` 的结果。
- `output` 决定返回值怎么处理：

| output | 行为 |
|---|---|
| `auto`（默认） | 路径对象→文件；`bytes`→图片；可 JSON 化→值；其余→对象 |
| `value` | 强制按 JSON 值传，不能序列化就报错 |
| `object` | 强制保留为对象，需要 `handle`（平台自动分配） |
| `file` / `files` | 返回值必须是已存在的文件路径（或路径列表） |
| `image` | 返回值是图片路径或 `bytes`，落盘为内容寻址图片 |

- `saveAs` 把对象结果登记成实例内对象名，后续步骤可以 `target: 这个名字` 调它的方法。示例里 `Dataset.load` 的结果命名为 `dataset`，两个排查操作共用它。
- `owner` 可选：对象所属类名。返回类型没有注解时，平台无法静态推断方法属于哪个类，写上它才能做签名校验。

### 3.3 参数来源

| 写法 | 取值 |
|---|---|
| `{"lit": 12}` | 常量（数字、布尔、字符串、数组、对象都行） |
| `{"input": "device"}` | 启动表单的全局参数；`"a.b"` 取嵌套字段 |
| `{"param": "metric"}` | 节点提供的操作参数 |
| `{"step": "series"}` | 同一列表里更早步骤的结果；可加 `"path": "y"` |
| `{"resource": "main", "step": "dataset"}` | 资源结果；首次引用按需资源会触发它执行 |
| `{"object": "client"}` | 对象句柄（只能传给同工程步骤） |
| `{"system": "directory"}` | 平台为本次调用分配的新目录 |
| `{"system": "file", "name": "chart.png"}` | 分配目录下的文件路径 |
| `{"items": [表达式…]}` | 列表，元素可以继续写来源 |
| `{"fields": {"peak": 表达式}}` | 字典，值可以继续写来源 |

限制：

- `{"step": ...}` 只能引用**前面**的步骤，不能前向引用，也不能引用其它操作的结果。
- `{"param": ...}` 只能出现在操作步骤里；启动资源不能引用节点参数。
- `{"object": ...}` 只保证同工程可用。跨工程使用会走「导出为 JSON/文件」，导出不了就明确报错。

### 3.4 自动判断

```json
"result": {
  "step": "summary",
  "outcome": "overall",
  "reason": "reasons",
  "normalReason": "三条规则均未触发：峰值、均值、趋势都在阈值内"
}
```

- `step`：取哪个步骤的返回值做映射。
- `outcome`：判断字段路径；支持 `overall`、`verdicts.0.result` 这类写法。
- `reason`：依据字段，字符串或字符串数组；数组会用「；」连接。
- 判断值只接受 `true/false` 或 `yes/no`（大小写、`y/n`、`是/否` 也认）；其他值按执行错误处理，**不会当成「否」**。
- 不写 `result`：操作照常执行，取数和绘图完成后生成人工判断待办，待办中带展示块。

### 3.5 展示

```json
"presentation": [
  { "type": "image", "step": "chart", "caption": "振动曲线" },
  { "type": "table", "step": "summary", "path": "verdicts",
    "columns": ["label", "result", "reason"], "headers": ["规则", "结论", "依据"] }
]
```

- `image` 必须引用 `output=image` 的步骤。
- `table` 的数据是对象数组，`columns` 是字段名，`headers` 是表头（可省）。
- `metrics`：`{"type":"metrics","step":"summary","path":"features","items":[{"label":"峰值","key":"peak","unit":"mm/s"}]}`。
- `line`：`{"type":"line","step":"series","path":"y"}`。
- `text`：`{"type":"text","text":"固定说明"}`，或用 `step` + `path` 取业务文本。
- 不写 `presentation`：图片全部展示，其余步骤给一行摘要。

---

## 4. 运行期到底发生了什么

以示例的一次振动排查为例：

```
开始节点
  ├─ 创建/复用对象 client（MockDataService worker）
  ├─ fetch        → 文件落盘 → 归档为内容寻址文件
  └─ Dataset.load → PlotEngine worker 里得到 Dataset，saveAs=dataset

排查节点 n1（operation=vibrationCheck, metric=vibration）
  ├─ plot_series(dataset, device, activity, metric) → JSON（x/y/label）
  ├─ save_plot(data=series, path=<平台分配>/vibration.png) → 图片摘要
  ├─ JudgeEngine(thresholds={peak, mean, trend}) → 对象（saveAs=vibrationJudge）
  ├─ judge_series(item=series) → Verdict 列表（对象，留在 JudgeEngine worker）
  ├─ summary(verdicts=<Verdict 列表>) → {overall, reasons, verdicts}
  └─ 映射 overall → yes/no，reasons → 依据，生成展示块
```

关键点：

1. **同一份解析数据**既画图又判断。`series` 是 JSON，传给 JudgeEngine；`Dataset` 和 `Verdict` 列表从不跨进程。
2. **不重复取数**。启动资源只跑一次；多个排查节点共享 `dataset`。
3. **人工等待不丢对象**。组合流程在 waiting 时保留 worker，回来继续调用同一个 `dataset`。
4. **回退可重算**。回退到某个排查节点重新执行操作，资源仍是原来的结果；业务对象句柄仍有效。
5. **失败不猜**。参数求值失败、接口异常、句柄失效、判断值无法识别，都记成执行错误并结束当前路径，不会把异常当「否」。

### 资源缓存

- 键是资源 id。启动输入在一次诊断内冻结（写进 `snapshots`），同一个实例不会出现第二组输入。
- `lazy` 资源在第一次被 `{"resource": ...}` 引用时执行；失败不缓存，下次重新尝试。
- 资源产出的对象可以给操作使用，但**只有 `trigger: start` 的资源产出会进入操作的可用对象表**；`lazy` 资源产出的对象只能在它自己的步骤链里用。

### 错误分类

| 错误 | 含义 |
|---|---|
| `execution/argument` | 参数来源不存在、字段路径取不到、类型不能传递 |
| `execution/compose` | 接口调用失败、判断值无法识别、图片未落盘 |
| `execution/timeout` / `execution/crash` | worker 超时或崩溃；组合调用不自动重试 |
| `execution/compose`（句柄已失效） | worker 重建或平台重启后句柄失效，需要回退重跑 |

---

## 5. 接入一个新接口的检查清单

1. 它是公开、同步、能关键字调用的吗？带 `**kwargs` 可以，`*args` 不行。
2. 它返回什么？
   - 普通 dict/list → `output: value` 或 `auto`。
   - `pathlib.Path` 或文件路径字符串 → `output: file`。
   - 业务对象（Dataset、Model、Engine）→ `output: object`，并用 `saveAs` 命名。
   - 图片路径 / `bytes` → `output: image`。
3. 它的参数从哪来？启动表单、常量、前面步骤的字段、资源结果，选一种写清楚，**不要靠猜**。
4. 如果要跨工程使用它的结果，结果能 JSON 化吗？不能的话，把消费它的步骤放在同一个工程里，或在模块方补一个投影接口。
5. 它有副作用吗（取数、写文件、建设备连接）？有副作用的步骤不要放进会被频繁重跑的操作里；平台不会自动重试，但回退会重跑。
6. 改完接口签名后要重新发布：签名指纹会拦下运行中的实例。

---

## 6. 常见问题

**「工程 X 里找不到接口」**
模块名写错，或接口不在 `__all__` / 不是公开名。用工程管理页刷新接口确认实际名字；类方法要写 `Class.method`。

**「对象 X 尚未创建」**
`target` 引用了未被声明、也不是前面步骤 `saveAs` 产出的名字。把产出它的步骤放到前面，或检查 `saveAs` 拼写。

**「需要普通值，但来源是 handle」**
把对象直接传给了需要 JSON 的参数。在该工程内先用方法产出可 JSON 化的结果（例如 `plot_series`），或把消费步骤放进同一个工程。

**「判断值无法判定为是/否」**
`outcome` 指到了错误字段，或业务返回的不是 yes/no。用运行记录里的 `composition.steps` 预览确认实际结构。

**「句柄已失效」**
worker 超时/崩溃后被重建，或平台重启过。句柄不持久化是明确的边界：回退重跑该路径，不会偷偷重新取数。

**图片不显示**
图片必须是 `output=image` 的步骤；`presentation` 的 `image.src` 由平台从摘要生成，不要手填路径。

**改了原工程代码，跑旧实例报签名不一致**
预期行为。重新发布后再启动；已经在跑的实例不会自动换代码。

---

## 7. 已知边界

- 只支持同一台后端机器上的本地 worker；跨机器需要额外的文件传输层。
- 不把业务对象跨解释器传递，也不给不同工程安装对方依赖。
- 不支持任意表达式、循环、条件分支脚本；分支仍由流程图的 yes/no 表达。
- 平台重启不自动续跑未完成实例；组合对象随 worker 消失。
- 资源缓存键只有资源 id：一次诊断内换设备/时间范围要重新启动实例，不支持同一实例内参数化重取。
- 签名指纹覆盖参数表，不覆盖实现；改了算法但签名没变时，需要人工重新发布。
- 桌面窗口不会迁移成浏览器交互；需要展示的图必须走 `save_plot` 这类无窗口导出接口。

---

## 8. 从旧方案迁移

| 旧 | 新 |
|---|---|
| 写 `Diagnosis` 子类，`initialize` 里取数解析 | 启动资源 + `saveAs` 对象 |
| 每个排查方法内部拼装调用 | 节点操作的有序步骤 |
| 方法返回 `{presentation, result, reason}` | `result` 映射 + `presentation` 块 |
| 四阶段函数逐个选 | 一条操作里可以多次调用、可传对象 |
| 数据准备节点共享解析结果 | 启动资源 + `{"resource": ...}` |

旧流程不需要改：`binding`、`pipeline`、`diagnosis` 仍然按原语义执行。新流程不要再混用四阶段或数据准备节点，发布时会直接拒绝。
