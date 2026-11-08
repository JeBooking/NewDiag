# 组合接入手册

第 2 章是界面操作，照着点就行；第 3–5 章是配置字段、运行语义和接口约定，给要改流程或接新工程的人看。

读完后应能：登记工程 → 声明对象、资源和操作 → 发布 → 跑通一次诊断，并且**不新增总控代码、不复制原工程算法**。

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

### 先跑通示例

两条路任选：

- 命令行一条命令跑完（最快，见 [`examples/composed-diagnosis/README.md`](../examples/composed-diagnosis/README.md)）：
  ```bash
  backend/.venv/Scripts/python.exe examples/composed-diagnosis/run.py
  ```
- 在界面里跟着第 2 章点一遍，约 5 分钟。

看到 `实例状态：success` 就说明后端、三个解释器、接口探测、发布、调度全链路可用。
之后再按第 3 节改配置、按第 5 节接自己的工程。

---

## 2. 示例：照着点一遍

这一章只写点哪里、填什么，不讲原理。整套约 5 分钟；原理和字段含义在后面的「配置速查」。

### 2.1 启动平台

在仓库根目录执行：

```bash
pnpm install    # 第一次才需要
pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`。

### 2.2 登记三个工程

左侧点「工程管理」→ 右上「登记工程」，按下面三行各登记一次（工厂函数都留空）：

| 名称 | 工程路径 | 模块 | 解释器 |
|---|---|---|---|
| MockDataService | `D:\FILE\Projects\MockDataService` | `dataclient` | 不填 |
| PlotEngine | `D:\FILE\Projects\PlotEngine` | `dataset` | `D:\FILE\Projects\PlotEngine\.venv\Scripts\python.exe` |
| JudgeEngine | `D:\FILE\Projects\JudgeEngine` | `judgekit` | 不填 |

- 名称照抄：后面的流程按名字找工程，登记后不能改。
- 解释器不填 = 用平台自带的 Python；PlotEngine 必须填它自己的，否则画图会报找不到 matplotlib。
- 三行都登记完，在列表里点每行的「接口」；能看到接口列表就说明这一行走通了。
  抽屉里会列出普通函数、构造器（如 `Dataset`）和方法（如 `Dataset.load`，名字带点的是方法）。
- **登记只是告诉平台代码在哪，还没接进流程。** 示例的 `workflow.json` 里已经按这三个名字写好了引用，
  所以名字必须一字不差；自己新建流程时，在「工程对象」和每个步骤的「工程」下拉里选这些名字，就是把它接进来（见 3.0）。

### 2.3 导入示例流程

左侧「流程」→ 右上「导入」→ 把 `examples/composed-diagnosis/workflow.json` 拖进弹窗（或点击选择文件）→ 点「导入」。
列表里出现「三工程组合诊断（振动与温度）」。

### 2.4 发布

点流程名字进入设计页 → **点一下画布空白处**（取消选中节点，右侧才显示流程属性）→ 属性栏拉到最下面 →
点「发布新版本」→ 备注可空 → 确定。出现版本 v1 就算发布成功。

### 2.5 跑一次（走异常分支）

回到「流程」列表 → 这一行右侧点「执行」。表单里显示的是英文参数名，按下表填，其余不动：

| 表单字段 | 含义 | 填什么 |
|---|---|---|
| `device` | 设备 | `D001` |
| `activity` | 工况 | `满载` |
| `start_time` | 起始时间 | `2026-11-05 08:00` |
| `end_time` | 结束时间 | `2026-11-05 10:00` |
| `peak` / `mean` / `trend` | 三个阈值 | 保持默认（12 / 40 / 0.05） |

点「开始执行」，跳到实例详情，等 5～10 秒。看到下面这些就算跑通：

1. 画布上「振动是否异常」变绿。
2. 页面下方「运行记录」标签页里：结论「是」，依据「实测 13.05 > 阈值 12」。
3. 同一条运行记录里有一张「振动曲线」，点开能看图。
4. 走到「停机检查轴承」会停下，**右侧**待办出现「完成 / 回退」→ 点「完成」。
5. 最后走到「异常已处理」，实例状态显示「成功」。

### 2.6 再跑一次（走正常分支）

重复 2.5，只改两格：`device` 填 `D002`，`activity` 填 `空载`。
预期：「振动是否异常」为「否」→「温度是否偏高」也为「否」→ 最后走到「设备正常」。

### 2.7 三段配置各管什么（认路）

设计页点空白处，右侧「流程属性」有三段。示例已经配好，不用改，只要知道谁管什么：

| 段落 | 管什么 | 示例里是什么 |
|---|---|---|
| 工程对象 | 提前创建、后面反复用的对象 | 取数用的 `client` |
| 启动资源 | 开始节点执行一次的取数和解析 | `fetch` 取数 → `dataset` 解析 |
| 节点操作 | 排查节点内部的一组调用 | 振动、温度两个排查 |

### 2.8 卡住了看这里

| 现象 | 怎么办 |
|---|---|
| 发布报「接口探测失败」 | 工程路径或模块写错。回 2.2 核对，并点该行的「接口」看能不能列出来 |
| 发布报「找不到接口」 | PlotEngine 的解释器没填它的 `.venv`；补上再点「接口」确认 |
| 提示「引用了组合配置里不存在的操作」 | 流程属性里的组合配置丢了。重新导入一次 `workflow.json`；旧版设计页打开后保存会丢字段，已修复 |
| 提示「工程未登记」 | 流程里的工程名和工程管理里的名称不一致；改名或重新选 |
| 执行后一直「运行中」 | 等 10 秒刷新；仍不动就看实例详情里运行记录的报错 |
| 停在「停机检查轴承」不动 | 这是人工处置，右侧待办点「完成」 |
| 列表里没有「执行」按钮 | 这个流程还没发布，回 2.4 |

---

## 3. 配置速查

### 3.0 工程怎么接进流程

登记和流程是两件事：登记只回答「代码在哪、用哪个解释器」；流程里要靠**工程名**去引用它。

```
工程管理：名称 MockDataService → D:\FILE\Projects\MockDataService，解释器 xxx
                        │ 按名称引用
                        ▼
流程配置：对象.project = "MockDataService"；步骤.project = "MockDataService"
                        │ 发布
                        ▼
plan：冻结 工程名 + 模块名 + 接口名 + 参数绑定
                        │ 执行
                        ▼
平台按名字找到登记项，用登记的目录和解释器起 worker，调用接口
```

操作（operation）是流程层的「无代码函数」：一个名字 + 一组参数 + 有序步骤 + 判断映射。
它不属于任何工程，也不写 Python；`vibrationCheck` 就是这样一个操作，所以三个工程里都找不到它。

因此：

- 流程里选的是**工程名**，不是路径。路径改了不用动流程；名字改了所有引用都断。
- 一个工程可以只被引用一次，也可以被多个对象/步骤引用；不引用就不会出现在这次诊断里。
- 步骤里的 `module` 可以和登记时的模块不同（PlotEngine 的 `plot_window` 就是），但必须在同一个工程目录下。
- 发布时会按「工程名:模块:接口」去探测；工程没登记、名字拼错、接口不存在，都会在这一步报错。

### 3.1 对象

先分清界面上的四个词：

| 词 | 是什么 | 示例 |
|---|---|---|
| 工程 | 登记的一行：一个目录 + 一个解释器 | `MockDataService` |
| 模块 | 工程目录下的 `.py` 文件或包名 | `dataclient` |
| 构造器 | 要创建的**类名**，不是函数 | `DataClient` |
| 构造参数 | 类名括号里的参数，看 `__init__` 签名 | `root`、`interval` |

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

字段怎么理解：

- `project` 是工程登记名，决定去哪个目录、用哪个解释器；
- `module` 是那个目录下的模块名，决定 `import` 哪个文件；
- `entry` 是**类名**，等价于 Python 里的 `DataClient(...)`；不要填函数名；
- `args` 是构造参数，来自这个类的 `__init__` 签名。必填参数（没有默认值的）必须绑定，否则发布失败；
  可选参数不写就交给 Python 默认值。

同一个类可以建多个对象，用不同 `id` 和不同构造参数区分，例如两个阈值不同的 `JudgeEngine`。
构造器下拉里会列出模块的全部公开类（含 `QueryError` 这类异常类），按需选，不是每个都要用。

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

### 3.6 示例配置对照

想知道 `workflow.json` 里每一步对应界面哪一格，看这两张表。

「启动资源」两步：

| 步骤 id | 工程 | 模块 | 调用类型 | 对象/接口 | 输出 | 参数来源 |
|---|---|---|---|---|---|---|
| `fetch` | MockDataService | dataclient | 实例方法 | `client.fetch` | 数据文件 | 设备/工况/起止时间 ← 启动参数 |
| `dataset` | PlotEngine | dataset | 普通函数 | `Dataset.load` | 对象 + `saveAs: dataset` | `path` ← `fetch` 的结果 |

「节点操作」`vibrationCheck` 五步：

| 步骤 id | 工程 | 模块 | 调用类型 | 对象/接口 | 输出 |
|---|---|---|---|---|---|
| `series` | PlotEngine | dataset | 实例方法 | `dataset.plot_series` | 普通值 |
| `chart` | PlotEngine | plot_window | 普通函数 | `save_plot` | 图片 |
| `judge` | JudgeEngine | judgekit | 构造器 | `JudgeEngine` | 对象 + `saveAs: vibrationJudge` |
| `verdicts` | JudgeEngine | judgekit | 实例方法 | `vibrationJudge.judge_series` | 对象 |
| `summary` | JudgeEngine | judgekit | 实例方法 | `vibrationJudge.summary` | 普通值 |

---

### 3.7 从零配一个新操作（以电流为例）

`vibrationCheck` 不是代码，是流程配置里的一个操作。它的定义只在
`workflow.json` 的 `composition.operations.vibrationCheck` 下，不在任何工程里。

操作 = 名字 + 参数 + 有序步骤 + 判断映射，相当于一个「无代码函数」：

```
currentCheck(metric)            ← 名字和参数
  series   = dataset.plot_series(..., metric)
  chart    = save_plot(series, ...)
  judge    = JudgeEngine(...)
  verdicts = judge.judge_series(series)
  summary  = judge.summary(verdicts)
  return summary.overall ? 是 : 否，依据 summary.reasons
```

照着做，给示例加一个电流检查：

1. 设计页点画布空白处 → 右侧「流程属性」→「组合配置」→「节点操作」→ 点「+ 操作」。
2. 第一行 id 改成 `currentCheck`；右边参数框填 `metric`。
3. 点「+ 添加步骤」，按下面五行填（接口都从下拉里选）：

| 步骤 id | 工程 | 模块 | 调用类型 | 调用对象 | 接口 | 输出 | 参数 |
|---|---|---|---|---|---|---|---|
| `series` | PlotEngine | `dataset` | 实例方法 | `dataset` | `plot_series` | 自动 | `device`、`activity` ← 启动参数；`metric` ← 操作参数 |
| `chart` | PlotEngine | `plot_window` | 普通函数 | — | `save_plot` | 图片 | `data` ← 步骤 `series`；`path` ← 平台路径、文件名 `current.png` |
| `judge` | JudgeEngine | `judgekit` | 构造器 | — | `JudgeEngine` | 对象 | `thresholds` ← 字典，`mean` ← 启动参数 `mean`；命名对象 `currentJudge` |
| `verdicts` | JudgeEngine | `judgekit` | 实例方法 | `currentJudge` | `judge_series` | 对象 | `item` ← 步骤 `series` |
| `summary` | JudgeEngine | `judgekit` | 实例方法 | `currentJudge` | `summary` | 自动 | `verdicts` ← 步骤 `verdicts` |

参数来源都在下拉里选，几个容易卡住的：

- 「启动参数」→ 选 `device` / `activity`；
- 「操作参数」→ 选 `metric`；
- 「前面步骤」→ 名称填 `series`；
- 「平台路径」→ 选「输出文件」→ 文件名填 `current.png`；
- 「字典」→ 会显示一个 JSON 输入框，粘贴 `{"mean": {"input": "mean"}}`。

> `JudgeEngine` 默认还带 peak、trend 两条规则；只想判均值时，把 `peak` 阈值设高一点，
> 例如 `{"mean": {"input": "mean"}, "peak": {"lit": 999}}`。

4. 勾选「自动判断」：来源步骤 `summary`，判断字段 `overall`，依据字段 `reasons`，
   正常依据填「均值与趋势都未超过阈值」。
5. 点两次「+ 展示块」：
   - 类型「图片」，来源步骤 `chart`，图注「电流曲线」；
   - 类型「表格」，来源步骤 `summary`，字段路径 `verdicts`，列填 `label, result, reason`。
6. 画布上选中一个排查节点（或新建一个），右侧「组合操作」选 `currentCheck`，
   操作参数 `metric` 选「常量」并填 `current`。

保存 → 发布新版本 → 执行，这个节点就会走电流检查。

几条规则：

- 同一操作内 `saveAs` 名字不能重复；不同操作可以重名，平台按操作隔离。
- 操作只能顺序调用，没有 if / for；需要分支就拆成两个排查节点。
- 操作只负责「调谁、传什么、怎么判」，真正的算法（画图、阈值判断）仍在工程里。
- 想复用这套配置，复制整个操作到另一个流程即可；不用改工程代码。

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

## 5. 接入一个新接口

### 5.1 什么接口能被平台调用

| 允许 | 例子 |
|---|---|
| 模块级函数 | `save_plot`、`parse_time` |
| 类构造器 | `DataClient(root=None, interval=None)` |
| 类方法 / 静态方法 | `Dataset.load(path)` |
| 实例方法 | `client.fetch(...)`、`judge.summary(verdicts)` |
| 带 `**kwargs` 的函数 | 只绑定显式列出的参数，`save_plot(..., **savefig_kwargs)` 可直接用 |

不允许：协程、`*args` 可变位置参数、必须靠窗口按钮触发的逻辑。`_` 开头的私有名称不会被探测，也不能调用。

### 5.2 检查清单

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
