# 流程类示例：三个工程拼成一个诊断类

这个示例把 `MockDataService`、`PlotEngine`、`JudgeEngine` 接进平台，**三个工程一行代码都不用改**，
平台在发布时把流程声明编译成一个真实的 Python 类，执行时就是给这个类创建一个实例。

| | 以前（diagnosis/v1） | 现在（流程类） |
|---|---|---|
| 谁写类 | 业务方写 `Diagnosis` 子类 | 平台按声明生成 |
| 胶水代码 | 取数、绘图、判断拼进方法 | 声明里按序调用，不写代码 |
| 三个工程要改吗 | 不用，但要认识平台协议 | 不用，也不认识平台 |
| 图怎么到人眼前 | 自己 `save_file` 拼展示块 | 返回图片路径，平台自动收成附件 |

读完这一篇你应该能：跑通示例 → 看懂声明 → 改出一个自己的流程。

---

## 1. 先跑通（2 分钟）

在仓库根目录执行：

```bash
backend/.venv/Scripts/python.exe examples/flow-class/run.py
```

预期输出（节选）：

```
实例 5f0c… 已启动：D001/满载
  人工处置：停机检查轴承（脚本自动完成）

实例状态：success
运行记录：
  start        ok
  vibration    ok      结论=yes 依据=实测 13.05 > 阈值 12
  temperature  ok      结论=no 依据=三个规则均未触发

展示物：图 2 张，曲线 2 条
```

再跑一次正常分支：

```bash
backend/.venv/Scripts/python.exe examples/flow-class/run.py --device D002 --activity 空载
```

`vibration` 变成 `结论=no`，流程走「设备正常」分支。

脚本做了五件事：登记三个工程 → 探测接口 → 发布（生成类）→ 起实例 → 自动完成人工待办。
它默认写平台自己的库（`backend/data`），跑完切到界面就能看到这条流程；
生成的类会写在同一目录的 `generated_flow.py`，可以直接打开看。
想隔离就加 `--data-dir <目录>`。

> 三个工程默认在 `D:\FILE\Projects`；换目录用 `--projects <父目录>`。
> 绘图依赖装在 `PlotEngine\.venv`，所以流程声明的运行解释器指向它。

---

## 2. 在界面里跑一遍

### 2.1 启动平台

```bash
pnpm install    # 第一次才需要
pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`。

### 2.2 登记三个工程

左侧「工程管理」→ 右上「登记工程」，按下面三行各登记一次：

| 名称 | 工程路径 | 模块 | 解释器 | 工厂 |
|---|---|---|---|---|
| MockDataService | `D:\FILE\Projects\MockDataService` | `dataclient` | 不填 | 不填 |
| PlotEngine | `D:\FILE\Projects\PlotEngine` | `dataset` | `D:\FILE\Projects\PlotEngine\.venv\Scripts\python.exe` | 不填 |
| JudgeEngine | `D:\FILE\Projects\JudgeEngine` | `judgekit` | 不填 | 不填 |

- 名称照抄：流程里按名字引用，改名所有引用都会断。
- 解释器只有 PlotEngine 必须填：它的 matplotlib 在 `.venv` 里，平台环境没有。
- 登记只是告诉平台代码在哪；登记完点每行的「接口」，能看到函数列表就说明这一行走通了。

### 2.3 导入示例流程

左侧「流程」→ 右上「导入」→ 拖入 `examples/flow-class/workflow.json` → 「导入」。
列表里出现「三工程流程类诊断（振动与温度）」。

### 2.4 看一眼类定义

点流程名进入设计页 → **点画布空白处**（取消选中节点）→ 右侧「流程属性」拉到「流程类」段：

| 段 | 内容 |
|---|---|
| 运行解释器 | PlotEngine 的 venv |
| 工程导入 | 四行：`dataclient` / `dataset` / `plot_window` / `judgekit` |
| 构造步骤 | `client` → `data_file` → `data` |
| 方法 | `check_metric`，四个参数、六步方法体、四个返回字段 |

这些格子就是类定义，**不是代码**。改这里等于改类，发布后生效。

### 2.5 发布

右侧属性栏最下面「发布新版本」→ 备注可空 → 确定。出现 v1 即发布成功。

发布时平台做了两件事：把声明编译成 `Flow` 类并冻结进版本；用流程声明的解释器真的
`import` 一遍四个模块。缺依赖、别名写错、`plot_window.save_plot` 拼错，都会在这一步被拦下。

### 2.6 执行

回到「流程」列表 → 这一行右侧「执行」→ 表单里填：

| 字段 | 填什么 |
|---|---|
| `device` | `D001` |
| `activity` | `满载` |
| `start_time` | `2026-11-05 08:00` |
| `end_time` | `2026-11-05 10:00` |

点「开始执行」，5～10 秒后：

1. 画布上「振动是否超标」变绿，「温度是否偏高」也执行了。
2. 运行记录里两条：`结论=是，依据=实测 13.05 > 阈值 12`；`结论=否`。
3. 每条记录里有一张振动/温度曲线，点开能放大。
4. 走到「停机检查轴承」停下，右侧待办点「完成」。
5. 实例状态变「成功」。

再执行一次 `D002` + `空载`：振动的依据变成「三个规则均未触发」，走「设备正常」分支。

---

## 3. 看懂它：一个 flow 就是一个类

### 3.1 平台生成的类

发布时 `Flow` 类被写进版本里（`backend/data/generated_flow.py` 是同一份内容）：

```python
class Flow(Diagnosis):
    version = "1.0.0"

    def initialize(self, device, activity, start_time, end_time):
        self.device = device
        self.activity = activity
        self.start_time = start_time
        self.end_time = end_time
        self.client = dataclient.DataClient(root=self.artifact_root)
        self.data_file = self.client.fetch(device_id=self.device, activity=self.activity,
                                          start_time=self.start_time, end_time=self.end_time)
        self.data = dataset.Dataset.load(path=self.data_file)

    def check_metric(self, metric: str = 'vibration', peak: float = 10.0,
                     mean: float = 40.0, trend: float = 0.05):
        record = self.data.get(device=self.device, activity=self.activity)
        item = record.plot_series(metric=metric)
        figure = plot_window.save_plot(data=item, path=self._artifact(f'{metric}.png'),
                                       title=_at(item, 'label'), xlabel='采样点',
                                       ylabel=_at(item, 'unit'))
        engine = judgekit.JudgeEngine(thresholds={'peak': peak, 'mean': mean, 'trend': trend})
        verdicts = engine.judge_file(path=self.data_file, metric=metric)
        summary = engine.summary(verdicts=verdicts)
        return {'result': _at(summary, 'overall'),
                'reason': _at(summary, 'reasons.0', '三个规则均未触发'),
                'figure': figure, 'series': item}
```

`initialize` 只跑一次，`check_metric` 每个排查节点调一次。取数只发生一次，两个节点共用
`self.data`——这就是「一个执行 = 一个实例」带来的东西。

### 3.2 doc 里的三段声明

`workflow.json` 的 `flowClass` 与上面的类一一对应：

| 声明 | 对应 Python | 说明 |
|---|---|---|
| `imports` | `import dataclient as dataclient` | 别名 + 工程名 + 模块名 |
| `initialize` | `__init__` / `initialize` | 按顺序执行，`name` 就是 `self.<name>` |
| `methods` | `def check_metric(...)` | `body` 是方法体，`returns` 是 `return` |

`initialize` 里三个特殊的参数来源：

| 写法 | 生成什么 | 用途 |
|---|---|---|
| `{"input": "device"}` | `device`（形参） | 开始节点声明的启动参数 |
| `{"artifactRoot": true}` | `self.artifact_root` | 本次实例的产物目录 |
| `{"var": "data_file"}` | `self.data_file` | 引用前面构造出来的对象 |

### 3.3 节点只做两件事

```json
{
  "id": "vibration", "kind": "investigation",
  "method": "check_metric",
  "params": { "metric": { "lit": "vibration" }, "peak": { "lit": 12 } }
}
```

选方法 → 给参数。没给的参数用方法声明的默认值（`mean=40`、`trend=0.05`）。
`result` 是 `yes`/`no`，决定走哪条出边；`reason` 进运行记录；图自动变成待办和历史里的证据。

### 3.4 表达式小抄

方法体和构造步骤的 `args` 里，值只有这几种写法：

| 写法 | 例子 | 含义 |
|---|---|---|
| `{"lit": …}` | `{"lit": 12}` | 常量 |
| `{"input": "device"}` | | 启动参数（方法里读 `self.device`） |
| `{"param": "metric"}` | | 方法形参 |
| `{"var": "item"}` | `{"var": "item", "path": "y"}` | 已保存的变量，可带字段路径 |
| `{"file": "x.png"}` | `{"file": "{metric}.png"}` | 产物目录里的文件名，花括号里是变量 |
| `{"artifactRoot": true}` | | 产物目录本身 |
| `{"list": [...]}` / `{"object": {…}}` | | 数组 / 对象 |
| `{"call": "a.b", "args": {…}}` | `{"call": "engine.judge_file", …}` | 嵌套调用，可直接当参数 |

`call` 的第一个名字只能是：导入别名（`plot_window`）、构造出来的对象（`client`）、
方法里的局部变量（`item`）。写错会在发布前报“既不是导入别名、也不是已构造的对象”。

---

## 4. 改一改（15 分钟）

### 4.1 让分支翻转

改 `vibration` 节点的 `peak`：`12` → `20`，保存 → 发布新版本 → 用 `D001` 再执行。
振动的实测峰值是 13.05，结论从「是」变「否」，流程走「设备正常」。

### 4.2 复用同一个方法加第三个检查

在 `temperature` 的「是」分支上再加一个排查节点「压力是否偏高」：

1. 左侧节点面板拖一个「排查」到画布。
2. 选中它 → 标题/详细内容填上；排查说明三项都填。
3. 「排查方法」选 `check_metric`。
4. 节点参数（JSON）填：

```json
{
  "metric": { "lit": "pressure" },
  "peak": { "lit": 8 },
  "mean": { "lit": 5 },
  "trend": { "lit": 0.05 }
}
```

5. 连边：`temperature` 的「是」→ 新节点；新节点的「是/否」接回原来的结论节点。

不用改类定义：同构检查只差参数，这正是方法存在的意义。

### 4.3 加一个判据不同的方法

比如「两个指标都异常才算异常」这种组合判断，就不适合复用 `check_metric`。在「流程类」→
「方法」里加一个 `check_pair`：构造步骤复用 `data`，方法体里调两次 `record.plot_series` 和
`engine.judge_file`，`returns.result` 用一个自己拼的字段。

> 组合逻辑超出「字段路径」的表达力时，别硬拼声明——直接写一个 `Diagnosis` 子类，
> 用文件流程属性里的「诊断实例」接进来。两种入口可以在不同流程里共存，不能在同一条流程里混用。

---

## 5. 从零开发一条新流程

假设你要做「冷却水温度诊断」，数据来自 `MockDataService`，判据用 `JudgeEngine`，
图用 `PlotEngine`。顺序是：先想类，再填声明，最后画图。

### 5.1 先在本子上写出类

```python
class 冷却水诊断:
    def initialize(self, device, start_time, end_time):
        self.client = ...
        self.data_file = self.client.fetch(...)
        self.data = dataset.Dataset.load(self.data_file)

    def check(self, metric="temperature", mean=30):
        ...  # 取序列、出图、判断
        return {"result": ..., "reason": ..., "figure": ..., "series": ...}
```

写不出来就别往下走：**声明只能表达你已经想清楚的直线调用**。

### 5.2 工程导入

在「流程属性 → 流程类 → 工程导入」加行：

| 别名 | 工程 | 模块 | 什么时候需要 |
|---|---|---|---|
| `dataclient` | MockDataService | `dataclient` | 取数 |
| `dataset` | PlotEngine | `dataset` | 解析数据文件 |
| `plot_window` | PlotEngine | `plot_window` | 无头出图 |
| `judgekit` | JudgeEngine | `judgekit` | 判断 |

同一个工程可以有多行，别名不同、模块不同（`dataset` 和 `plot_window` 就是这样）。

### 5.3 构造步骤

按依赖顺序排，后面的步骤可以引用前面的 `name`：

1. `client` = `dataclient.DataClient`，参数 `{"root": {"artifactRoot": true}}`
2. `data_file` = `client.fetch`，四个参数都从 `input` 取
3. `data` = `dataset.Dataset.load`，参数 `{"path": {"var": "data_file"}}`

**命名注意**：构造出来的名字不要和导入别名重名（比如别名 `dataset` 就不能再存一个叫
`dataset` 的变量），发布前会报“name 与已有名字冲突”。

### 5.4 方法与返回

方法体按「取 → 画 → 判」三步写：

```json
[
  {"name": "record", "call": "data.get", "args": {"device": {"input": "device"}, "activity": {"input": "activity"}}},
  {"name": "item", "call": "record.plot_series", "args": {"metric": {"param": "metric"}}},
  {"name": "figure", "call": "plot_window.save_plot", "args": {
     "data": {"var": "item"}, "path": {"file": "{metric}.png"},
     "title": {"var": "item", "path": "label"}}},
  {"name": "engine", "call": "judgekit.JudgeEngine", "args": {"thresholds": {"object": {"mean": {"param": "mean"}}}}},
  {"name": "verdicts", "call": "engine.judge_file", "args": {"path": {"var": "data_file"}, "metric": {"param": "metric"}}},
  {"name": "summary", "call": "engine.summary", "args": {"verdicts": {"var": "verdicts"}}}
]
```

返回映射四个字段：

| 字段 | 填什么 | 不填会怎样 |
|---|---|---|
| `result` | `summary.overall` 这样的字段路径 | 留空 = 算完停下，等人判断 |
| `reason` | `summary.reasons.0` | 自动判断必须有依据，空字符串会被拒 |
| `reasonFallback` | 依据为空时的兜底文本 | 依据里出现空值 |
| `figure` | 图片变量名 | 运行记录里没有图 |
| `series` | 曲线变量（含 `y` 的字典） | 没有可交互的曲线块 |

### 5.5 节点与连边

- 开始节点声明启动参数，名字要和 `{"input": …}` 对得上。
- 排查节点选方法，给参数；没给的用默认值。
- 排查节点必须连「是」「否」两条出线；结论 → 处置 → 问题解决是最短的合法链路。
- 发布前平台会编译一遍：缺标题、缺排查说明、连边类型不对、参数名不在方法签名里，都会拦下并指出节点。

### 5.6 发布前检查清单

| 检查 | 不通过的表现 |
|---|---|
| 三个工程已在「工程管理」登记，名字一字不差 | 「工程 X 未登记」 |
| 运行解释器能 import 所有模块 | 「导入 xxx 失败：No module named …」 |
| 别名根上的接口拼写正确 | 「plot_window.save_plot 不存在」 |
| 构造步骤的顺序没有引用后面的名字 | 「引用了不存在的变量」 |
| 方法与节点的参数名一致 | 「参数 xxx 不在函数签名里」 |
| `result` 指向一个 `yes`/`no` 字段 | 执行报「result 必须为 yes/no」 |

### 5.7 排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| 发布报「导入 matplotlib 失败」 | 运行解释器选错 | 填 PlotEngine 的 `.venv` |
| 图不显示 | 方法体写的是 `show_plot`（弹桌面窗口） | 换 `save_plot`，它返回文件路径 |
| 待办里没有图 | `returns.figure` 没填，或填了变量名但该步没有 `name` | 两步都补齐 |
| 依据是「三个规则均未触发」但结论是「是」 | 只改了 `mean`，`peak` 还是默认 10 | 把不关心的阈值一起显式给 |
| 报「类定义与诊断实例只能选一种」 | 两个入口都配了 | 删掉其中一个 |

---

## 6. 接入第四个工程

1. 「工程管理」登记它（路径、模块、解释器）。
2. 在「流程类 → 工程导入」加一行：别名 + 工程名 + 模块名。
3. 在构造步骤或方法体里调它，参数按真实签名写。发布时会校验别名根上的接口是否存在；
   `对象.方法` 这种运行时才知道类型的调用，校验放到执行期（报错信息带调用名和参数名）。

什么时候要在构造步骤里 `name` 一个对象：需要连续调它多个方法时（像 `client` 和 `data`）。
一次性的调用直接写成嵌套 `{"call": …}` 即可，不必起名字。

---

## 7. 这套东西的边界

| 边界 | 现状 | 绕法 |
|---|---|---|
| 方法体只有直线调用 | 不支持 if/for | 分支放图里（排查节点的 yes/no）；循环用回退 |
| 只能同步 | 与平台其它协议一致 | 异步库自己包一层同步入口 |
| 不能弹桌面窗口 | 服务端无显示环境 | 用 `save_plot` 这类无头导出 |
| 组合逻辑超出字段路径 | `result` 只能指向一个字段 | 写 `Diagnosis` 子类，用「诊断实例」接入 |
| 一个实例一个解释器 | 依赖必须装在一起 | 冲突时拆成两个流程，或用同一个 venv 装齐 |

需要这些边界之外的东西时，不是把声明写得更花，而是换回「诊断实例」——那是同一套运行时的另一张皮，
可以和人写的 Python 类共存。
