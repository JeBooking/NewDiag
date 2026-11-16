# 开发者手册

适用版本：v1.17 节点槽位模板和生成 Python 执行。前半部分面向外接模块开发者，后半部分面向平台开发者。操作步骤见[用户手册](user-manual.md)，字段与执行语义以 [spec.md](spec.md) 第 4、5、6 节为准。

## 1. 接入边界

平台只做四件事：实例化对象、按图顺序调用方法、缓存取数结果、保存对象状态并留痕。模块不必继承任何基类，不必导入平台 SDK，也不必返回平台专用结构；返回值按槽位约定给出即可。

| 已有接口情况 | 接入方式 |
|---|---|
| 同步函数，参数可按名称传入 | 直接填到槽位，不填目标对象 |
| 普通类的构造器、公开方法、类方法、静态方法 | 开始节点填构造槽，槽位按对象名引用 |
| 工厂返回业务对象 | 构造槽填工厂函数，对象名自己起 |
| 返回数组、字典、数值、文本 | 展示槽按形状选类型，判定槽返回 bool 或 {result, reason} |
| 只有 async、位置专用参数或 GUI 内部入口 | 需要补一个同步、可独立调用的公开入口 |
| 状态含连接、锁、窗口 | 把运行资源移出可恢复对象，或实现可序列化边界 |

平台按关键字调用，不支持位置参数列表；`*args` 可不传，`**kwargs` 可只绑显式参数。

## 2. 准备可发现、可调用的工程

工程登记提供源码目录、默认模块及解释器。模块必须能在该解释器中导入，依赖由外接工程环境安装。接口目录按模块 `__all__` 或公开定义发现函数与类，并列出类的公开方法。

- 导入模块时不要启动服务器、弹窗口或等 stdin，探针会 import 它
- 对象类型定义在可导入模块的顶层；函数内临时类、闭包、不可序列化成员无法保存
- 按名称传入的参数尽量显式声明，原函数默认值仍由原函数使用
- docstring 直接显示在槽位下面，写给编排者看

```python
# records.py：不引用平台代码
class Dataset:
    def __init__(self, values: list):
        self.samples = tuple(values)

    def series(self, scale: float = 1):
        return [value * scale for value in self.samples]
```

跨工程只传 JSON 和文件：业务对象句柄只在所属工程进程里有效。多个模块本就需要共享同一个对象时，登记为同一个工程。

## 3. 槽位契约

编辑态 `DocNode.slots`，持久化时放在 `bindings.nodes[id].slots`：

```json
{
  "constructs": [
    {"object": "dataset", "project": "组合数据示例", "module": "records",
     "interface": "Dataset", "function": "Dataset",
     "arguments": {"values": {"source": "input", "name": "samples"}}}
  ],
  "fetch": {"object": "dataset", "project": "组合数据示例", "module": "records",
            "interface": "Dataset.series", "function": "series", "arguments": {}},
  "process": {"object": "dataset", "project": "组合数据示例", "module": "records",
              "interface": "Dataset.series", "function": "series", "arguments": {}},
  "present": {"object": "dataset", "project": "组合数据示例", "module": "records",
              "interface": "Dataset.series", "function": "series", "view": "line",
              "arguments": {}},
  "decide": {"object": "rules", "project": "组合判断示例", "module": "checks",
             "interface": "ThresholdRules.check", "function": "check",
             "arguments": {"samples": {"source": "previous"}}}
}
```

| 字段 | 说明 |
|---|---|
| object | 构造槽里是对象名；其余槽是目标对象名，留空表示模块级函数 |
| project / module | 工程登记名与模块名 |
| interface | 目录里的完整名，用来探测校验 |
| function | 末级方法名，实例方法取 `类.方法` 的最后一段 |
| view | 只有展示槽有：`image` / `line` / `metrics` / `table` / `text` |
| human | 只有判定槽有：`true` 表示人工判断，不填接口 |
| useCache | 只有拉取槽有：默认使用缓存；置 `false` 表示重取一遍，取完仍写回缓存 |
| arguments | 参数名 → 来源表达式 |

参数来源只有四种：

| 写法 | 含义 |
|---|---|
| `{"lit": 1}` | JSON 固定值 |
| `{"source": "input", "name": "limit"}` | 开始节点声明的启动参数 |
| `{"source": "parameter", "name": "scale"}` | 节点现场参数，必须在 `runtimeInputs` 声明 |
| `{"source": "previous"}` | 本节点内上一步的结果；只能出现在处理／展示／判定，且要有拉取或处理槽 |

另有 `{"items": [...]}` 与 `{"fields": {...}}` 递归组合列表和字典。表达式可带 `path` 下钻、`file: true` 把上传引用转成本地路径。

**槽位链**：构造 → 拉取 → 处理 → 展示 → 判定，只在节点内传递。跨节点不传值：下一个节点要数据就自己取，或从对象、数据源里拿。开始节点的拉取与处理是「把数据装进对象」，返回值不外传。

## 4. 返回值、文件和展示

| 槽位 | 原方法返回 | 平台处理 |
|---|---|---|
| 拉取／处理 | 可 JSON 化的数据 | 交给本节点的展示与判定；拉取结果按参数缓存 |
| 展示（text） | 字符串或可 JSON 化对象 | 直接显示，对象按缩进 JSON 显示 |
| 展示（line） | 非空数值数组 | 画折线 |
| 展示（metrics） | `[{label, value, unit?}]` | 指标卡 |
| 展示（table） | `{columns: [...], rows: [[...]]}` | 表格 |
| 展示（image） | 附件摘要（`node_sdk.save_file` 的结果） | 图片块 |
| 判定 | bool、`yes`/`no`、`{result, reason}` | 走是／否分支，依据写进记录 |

图片要先用 `node_sdk.save_file(bytes)` 落盘，它返回 64 位摘要；不接受文件路径或 URL 直接当 `src`。业务生成的图片只收 png／jpg／webp／gif，单张上限 10 MB。

判定返回 `{result, reason}` 时 `reason` 会写进执行轨迹；只返回布尔值时平台记「由判定方法给出结论」。异常不等于「否」：接口抛异常会挂起该分支并生成「执行报错」待办，由人决定回退还是结束；判定后没有对应分支则记「排查失败」，不再询问。

## 5. 状态与恢复

工程 worker 用 pickle 保存对象图。人工等待期间进程存活，不需要反复反序列化；回退时对象恢复到目标到访的快照，已取得的拉取结果继续保留。

- 网络连接、锁、窗口、活动文件句柄不属于可恢复数据
- 调用中临时打开并及时关闭资源，比长期挂在对象上更容易接入
- 无法序列化时明确报错，不静默丢字段

拉取结果按「工程 + 模块 + 接口 + 对象 + 归一化参数」缓存，同一实例只调用一次；回退不重新取数。处理槽不缓存，每次到访都执行。

拉取槽可取消「使用缓存」：跳过缓存读取、重新调用，结果写回缓存，后续节点用新值。适合底层数据会变、参数却不变的接口。

## 6. 发布、生成和并行

发布保存编排与槽位配置，并探测接口签名。启动时复制当前工程源码到实例目录，按副本重新探测、编译、生成 `flow.py`、`runtime/`、`configuration.json`、`modules/`。

每个排查分支一个执行进程，每个工程一个常驻子进程，按工程登记解释器启动。并行上限统计排查分支名额，不是 Python 进程总数。人工等待保留进程；后端重启不自动续跑。

### 在终端运行生成的代码

```powershell
python flow.py --help
python flow.py --mode serial
python flow.py --mode parallel --max-parallel 2
```

终端入口是一次独立运行，不是接管平台的实例；待办由终端接收，产物写入本地 `local-artifacts`。搬机器时要带上整个运行目录和业务解释器，不能只拷 `flow.py`。

## 7. 平台代码入口

| 职责 | 入口 |
|---|---|
| 契约与类型 | [spec.md](spec.md)、[types.ts](../src/domain/types.ts) |
| 槽位校验与探测 | [services/slots.py](../backend/app/services/slots.py) |
| 节点计划编译 | [services/compiler.py](../backend/app/services/compiler.py) |
| 源码复制与生成 | [services/codegen.py](../backend/app/services/codegen.py) |
| 平台侧调度与回退 | [services/generated_runner.py](../backend/app/services/generated_runner.py) |
| 槽位链执行 | [worker/slot_runtime.py](../backend/app/worker/slot_runtime.py) |
| 工程内对象与快照 | [worker/objects.py](../backend/app/worker/objects.py)、[session.py](../backend/app/worker/session.py) |
| 流程分层保存 | [services/flow_format.py](../backend/app/services/flow_format.py) |
| 槽位面板 | [SlotCallEditor.vue](../src/components/panels/SlotCallEditor.vue)、[SlotArgumentEditor.vue](../src/components/panels/SlotArgumentEditor.vue) |

持久格式 2 把流程核心、`bindings`、`presentation` 分层；`bindings.nodes` 存 `slots` 与 `runtimeInputs`，运行模式放在实例启动配置。不要把临时路径、进程句柄或生成代码写进流程核心。

## 8. 环境、调试与扩展

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
pnpm install
pnpm dev
```

排错先分层：接口目录错误看导入和探针；发布错误看槽位校验；启动错误看源码副本和重新编译；节点错误看工程名、接口、方法与 traceback。实例卡在某个节点时，先在 `backend/logs/app.log` 找该实例的「分支启动 / 分支退出」行，再看后端启动时的遗留实例告警；重启原因在 `backend/logs/dev-all.log`。实例卡在某个节点时，先在 `backend/logs/app.log` 找该实例的「分支启动 / 分支退出」行，再看后端启动时的遗留实例告警；重启原因在 `backend/logs/dev-all.log`。`NEWDIAG_LOG_LEVEL=DEBUG` 增加日志，`NEWDIAG_LOG_PAYLOAD=1` 记录实参与返回值。

扩展按同一链路更新：先在 spec 约定字段，再同步 TypeScript 与后端契约；增加参数来源要同时改编辑器、结构校验、运行求值；改等待或回退要核对结果复用、缓存保留与旧执行代隔离。

## 9. 交付检查

```powershell
pnpm type-check
pnpm build
backend/.venv/Scripts/python -m pytest backend/tests -q
```

需要手工验收时用 `examples/composed-basic` 的三类节点核对：自动判定、人工判断、现场参数各走一遍，再构造分支验证回退与并行限额。
