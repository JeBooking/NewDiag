# 开发者手册

适用版本：v1.15 通用组合接入和生成 Python 执行。前半部分面向外接模块开发者，后半部分面向平台开发者。操作步骤见[用户手册](user-manual.md)，字段及执行语义以 [spec.md](spec.md) 为准。

## 1. 接入边界

默认接入方式是配置已有 Python 接口，平台负责组织调用和生成执行代码。模块不必继承 `Diagnosis`，不必导入平台 SDK，也不必返回平台特定的判断类。平台通过配置选取结果、依据和展示字段。

| 已有接口情况 | 接入方式 |
|---|---|
| 同步函数，参数可按名称传入 | 直接选择函数并配置参数 |
| 普通类的构造器、公开方法、类方法、静态方法 | 创建或选择对象，再调用接口 |
| 工厂返回业务对象 | 返回类型选择 object，后续方法以该对象为目标 |
| 返回数组、字典、数值、文本 | 返回类型选择 value，配置字段映射 |
| 返回自定义对象 | 选择 object，读取公开属性或继续调用方法 |
| 只有 async、位置专用参数或 GUI 内部入口 | 提供少量同步、可独立调用的公开接口 |
| 状态含连接、锁、窗口等运行资源 | 将运行资源移出可恢复对象，或实现适合业务的序列化边界 |

目前按关键字调用，不支持配置位置参数列表；`*args` 可不传，`**kwargs` 可配置额外命名参数。原 Web 页面、插件注册系统、交互图形窗口不会自动转换为平台展示。

## 2. 准备可发现、可调用的工程

工程登记提供源码目录、默认模块及解释器。模块必须能在该解释器中导入，依赖由外接工程环境安装。组合模式的登记工厂留空，对象初始化在流程配置中完成。

接口目录按模块 `__all__` 或本模块公开定义发现函数与类，并列出类的公开方法。建议为对外入口写清 docstring 和参数含义；避免在导入模块时启动服务器、弹出窗口或等待 stdin。接口探测需要导入模块，但不调用业务构造器和业务方法。

对象类型应定义在可导入模块的顶层。函数内部临时类、动态闭包和不能序列化的成员可能无法保存。需要按名称传入的参数尽量显式声明，原函数默认值由原函数使用。

参考真实示例：

```python
# records.py：不引用平台代码
class Dataset:
    def __init__(self, values: list):
        self.samples = tuple(values)

    def series(self, scale: float = 1):
        return [value * scale for value in self.samples]
```

完整示例包括输入校验与文件解析，见 [records.py](../examples/composed-basic/data_tools/records.py)。判断工程的 [checks.py](../examples/composed-basic/analysis_tools/checks.py) 使用普通工厂和 `Verdict` 数据类。

跨工程传结构化数据或文件；业务对象句柄只能在所属工程使用。同工程不同模块可以共享该工程中的对象。模块间若本就需要同一个 Python 对象，应登记为同一个工程并选择各自模块。

## 3. 组合配置契约

以下是编辑态 `FlowDoc.composition` 片段，不是完整可导入流程。完整文档需要节点和连线，请直接参考 [workflow.json](../examples/composed-basic/workflow.json)。日常用户通过界面配置即可。

```json
{
  "objects": [{
    "id": "rules", "project": "组合判断示例", "module": "checks",
    "function": "create_rules",
    "arguments": {"limit": {"source": "input", "name": "limit"}}
  }],
  "resources": [{
    "id": "dataset", "trigger": "startup",
    "calls": [{
      "id": "loaded", "project": "组合数据示例", "module": "records",
      "function": "Dataset", "output": "object",
      "arguments": {"values": {"source": "input", "name": "samples"}}
    }],
    "result": {"source": "value", "name": "loaded"}
  }],
  "operations": [{
    "id": "inspect", "parameters": ["scale"],
    "calls": [{
      "id": "curve", "project": "组合数据示例", "module": "records",
      "interface": "Dataset.series", "function": "series",
      "target": {"source": "resource", "name": "dataset"},
      "arguments": {"scale": {"source": "parameter", "name": "scale"}}
    }],
    "presentation": [{"type": "line", "value": {"source": "value", "name": "curve"}}]
  }]
}
```

### 调用与对象

每次调用包含 `id`、`project`、`function`、`arguments`，可选 `module`、`interface`、`target`、`output`。`interface` 是目录中的完整名，便于探测校验；有 target 时 `function` 为末级方法名，如 `series`，没有 target 时为完整可解析名，如 `Dataset.from_file`。

`objects` 中的调用创建命名对象，首次使用时执行；资源和操作的 `calls` 按顺序执行。名称在所属列表中唯一。对象／资源依赖不得成环；调用只能引用同列表此前结果。

### 表达式

| 写法 | 含义 |
|---|---|
| `{"lit": 1}` | JSON 固定值 |
| `{"source":"input","name":"limit"}` | 全局启动参数 |
| `{"source":"parameter","name":"scale"}` | 操作或资源请求参数 |
| `{"source":"value","name":"verdict","path":"peak"}` | 此前调用结果及公开字段 |
| `{"source":"object","name":"rules"}` | 命名对象 |
| `{"source":"resource","name":"dataset","arguments":{}}` | 资源请求；同名同参数命中缓存 |
| `{"source":"node","name":"check1","path":"data.curve"}` | 有效上游节点输出 |
| `{"system":"directory"}` / `{"system":"file"}` | 本次调用输出目录／图片路径，仅用于调用参数 |
| `{"items":[{"lit":1}]}` | 递归构造列表 |
| `{"fields":{"limit":{"lit":5}}}` | 递归构造字典 |

`path` 使用点分隔字段和数组下标，不执行 Python 表达式，不允许读取私有对象属性。对象属性读取必须得到可交换的数据；复杂子对象宜通过公开方法返回并选择 object。

资源 `defaults` 是请求参数默认值；先与请求合并，再生成缓存键。启动加载的资源必须满足请求参数需求。请求参数使用数据，不能把进程对象句柄当作缓存键。

### 节点绑定与人工输入

排查节点的 `operation` 选择操作，`operationArguments` 提供参数表达式。操作声明了参数而节点没有提供来源时，生成现场输入，默认 JSON 类型。`runtimeInputs` 可显式声明表单名称、类型和是否必填；同一参数不能同时配置固定来源与现场输入。

```json
{
  "operation": "inspect",
  "operationArguments": {},
  "runtimeInputs": [{"name":"scale","type":"number","required":true}]
}
```

不要在业务方法里使用 `input()` 代替平台待办。业务方法接收普通参数；平台或生成代码的终端入口负责等待和收集输入。

## 4. 返回值、文件和展示

| output | 原方法返回 | 平台处理 |
|---|---|---|
| value（默认） | 可转换为 JSON 的数据 | 供后续参数和展示使用，大结果可转为产物引用 |
| object | 普通业务对象 | 留在工程进程，用句柄引用并保存状态 |
| file | 文件路径或 bytes | 收集为本次执行的文件副本，结果含 `file` 路径 |
| image | 图片路径或图片 bytes | 存入平台文件库，结果含 `image` 摘要 |

返回文件路径须为后端业务进程可读取的文件，不是前端机器路径。可把 `system.directory/file` 传给原方法，也可返回原方法已有输出位置。文件结果传给下个接口时可选字段 `file`。

启动文件输入是上传引用。表达式 `{"source":"input","name":"source_file","file":true}` 将它转换为经过校验的本地路径。现场 `file` 类型参数在提交时已经转换路径。

操作的 presentation 映射如下，原方法无需自行包装成平台展示协议：

| 展示类型 | 映射值示例 |
|---|---|
| text | `"峰值偏高"`，或可显示为 JSON 的对象 |
| line | `[1,2,8,3]`，非空数值数组 |
| metrics | `[{"label":"峰值","value":8,"unit":"V"}]` |
| table | `{"columns":["时间","电压"],"rows":[[0,1],[1,8]]}` |
| image | image 返回结果或其中的图片摘要 |

自动判断 `decision.result` 接布尔值或 `yes`／`no`；`reason` 接非空文本或原因列表。空列表可使用 `normalReason`。异常不是“否”，异常应明确报告；不配置 decision 则显示结果后等待人工判断。

## 5. 固定数据、可变状态与恢复

### 模块开发规则

固定资源结果及其对象图一旦产生，应一直保持同一含义和内容。新格式、新统计结果保存为新结果或新资源，不原地改写原始采样数据。平台不为任意第三方对象安装只读代理，也不扫描整个堆检测修改；这个约束需要模块开发和编排共同遵守。

可变业务对象放在 objects，按快照恢复。大数据放在固定资源，让可变对象引用该资源，避免每步快照反复包含大块数据。固定引用按实际对象身份复用；重新构造一个内容相同的对象，并不保证自动去重。

### 实现边界

工程 worker 用 pickle 保存可变对象图。固定资源对象登记后，后续快照用持久引用代替该固定对象内容；结构化资源存入产物库，资源清单保存引用。首次创建、固定登记及中间调用可能分别生成快照，因此“增量”指后续不反复保存已登记固定内容，不代表任意可变对象都做成员级差分。

网络连接、锁、窗口、活动文件句柄等不属于可恢复数据。调用中临时打开并及时关闭资源，比把资源长期挂在业务对象上更容易接入。无法序列化时明确报错，不静默丢掉字段。

人工等待时执行进程仍活着，已有对象无需靠反复反序列化维持。回退时从完成到访中选择目标，复用目标输出，并把普通对象恢复到目标快照；该分支后来获得的固定资源仍可复用。后续节点输出移出新路径，审计记录保留。

这不是 IDE 调用栈回退：不恢复任意代码行，不撤销外部写入，也不重跑目标节点。模块调用产生的外部副作用不在快照恢复范围内。

## 6. 发布、生成和并行模型

发布保存流程编排与接入配置。启动复制当前工程源码，探测副本接口，重新编译并生成 `flow.py` 和运行配置。接口实现摘要用于同一次执行的源码一致性，不作为发布版本永久绑定的源码版本。Python 解释器和第三方依赖仍来自登记环境，并非被完整打包冻结。

每个独立排查分支拥有自己的执行进程，并按工程建立业务 worker；同一工程的多个模块使用对应 worker。人工等待保留这些进程。并行上限统计排查分支名额，不是操作系统 Python 进程总数。

串行的无后继路径可继续尝试同级思路；并行取消这一自动补跑，因为其他同级分支已经单独调度。达到并行上限的新分支排队；当前分支可以继续其中一条子路径，其余按额度启动。各分支数据和缓存独立。

当前不承诺后端重启自动续跑、任意 Web 应用自动移植或动态插件协议自动推断。旧诊断类、函数与四阶段协议仍保留；诊断类专用说明见[诊断类开发手册](diagnosis-development-guide.md)，不要把它误当成组合模式的必要条件。

### 在终端运行已生成的代码

实例启动生成的目录位于 `<数据目录>/executions/<实例ID>`，其中包含 `flow.py`、`configuration.json`、`runtime` 和 `modules`。在该目录使用具备运行依赖的 Python 执行：

也可在实例详情“导出代码”取得纯源码包（`GET /api/instances/{id}/code-export`），包含上述 Python 源码和配置模板。导出会清空实际启动输入和原机器解释器路径；先根据 inputs.json 填写配置。上传文件、非 Python 业务资源和执行现场不包含在代码包中。

```powershell
python flow.py --help
python flow.py --mode serial
python flow.py --mode parallel --max-parallel 2
```

终端入口是一次独立运行，不是接管平台中已有的活动实例。它沿用生成配置里的输入，待办由终端接收，产物写入本地 `local-artifacts`。`--worker` 是平台内部通信入口，普通终端运行不要使用。

搬到其他机器时，不能只拷一个 `flow.py`：还需上述运行目录、可用的业务解释器及依赖，以及启动文件引用需要的文件库。必要时按目标环境调整 `configuration.json` 的解释器与文件路径。流程 ZIP 导出与这份生成目录是不同产物，当前流程 ZIP 不包含它。

## 7. 平台代码入口

| 职责 | 入口 |
|---|---|
| 契约与类型 | [spec.md](spec.md)、[types.ts](../src/domain/types.ts) |
| 流程分层保存 | [flow_format.py](../backend/app/services/flow_format.py) |
| 普通接口目录 | [probe.py](../backend/app/worker/probe.py)、[projects.py API](../backend/app/api/projects.py) |
| 组合校验、接口探测、回退状态合并 | [services/composition.py](../backend/app/services/composition.py) |
| 节点计划编译 | [compiler.py](../backend/app/services/compiler.py) |
| 源码复制与 Python 生成 | [codegen.py](../backend/app/services/codegen.py) |
| 平台侧调度及回退 | [generated_runner.py](../backend/app/services/generated_runner.py) |
| 生成流程运行、人工等待 | [generated_runtime.py](../backend/app/worker/generated_runtime.py) |
| 组合表达式、资源缓存、操作调用 | [composition_runtime.py](../backend/app/worker/composition_runtime.py) |
| 工程内对象调用与快照 | [worker/composition.py](../backend/app/worker/composition.py)、[session.py](../backend/app/worker/session.py) |
| 结构化产物与固定对象引用 | [artifacts.py](../backend/app/worker/artifacts.py) |
| 组合配置界面 | [CompositionEditor.vue](../src/components/panels/CompositionEditor.vue)、[CompositionCallsEditor.vue](../src/components/panels/CompositionCallsEditor.vue)、[CompositionArgumentEditor.vue](../src/components/panels/CompositionArgumentEditor.vue) |
| 节点绑定和现场输入 | [NodePanel.vue](../src/components/panels/NodePanel.vue) |

持久格式 2 将核心流程、bindings 和 presentation 分层。composition 放在 bindings，节点 operation、operationArguments 和 runtimeInputs 放在 bindings.nodes；运行模式和并行上限放在实例启动配置。不要把临时文件路径、进程句柄或生成代码写进流程核心。

## 8. 环境、调试与扩展

从仓库根目录执行，以下为 Windows PowerShell：

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
pnpm install
pnpm dev
```

环境要求及离线部署见 [README](../README.md) 和 [deploy.md](deploy.md)。源码开发启动器只监控 `backend/app`，由启动器管理后端停止和重启，不使用 uvicorn `--reload`。生成代码与运行数据变化不应触发平台重启；修改框架源码可能中断活动诊断。

排错先确认失败层次：接口目录错误看导入和探针；发布错误看配置校验；启动错误看源码副本和重新编译；节点错误看工程名、调用名、方法和完整 traceback。`NEWDIAG_LOG_LEVEL=DEBUG` 可增加日志；`NEWDIAG_LOG_PAYLOAD=1` 会记录实参返回值，仅在必要时开启。默认日志位置及请求 ID 见 README。

组合接口目录可通过 `GET /api/projects/{id}/functions?catalog=true&module=records` 获取；不带 catalog 的旧接口保留原协议。其余 API 以路由实现和运行中的 `/docs` 为准，避免依赖文档中复制后过期的完整请求结构。

扩展框架时按同一功能链更新：

1. 先在 spec 约定字段归属、兼容行为，再同步 TypeScript 和后端契约。
2. 增加参数来源时，同时更新编辑器、结构校验、依赖校验、运行求值和跨工程对象归属检查。
3. 增加返回或展示类型时，同时更新 worker 编码、产物保存、展示校验和前端渲染。
4. 改等待、回退或并行时，核对已完成结果复用、固定资源保留、旧执行代结果隔离和并行名额释放。
5. 保留旧流程读取路径；生成计划是派生产物，不把旧编译器内部格式变成流程升级的前提。

## 9. 交付检查与示例用途

仓库协作、提交与推送规则见 [AGENTS.md](../AGENTS.md)。需求开发阶段按该规则不要求运行测试；问题修复应先定位根因，推送前按规则执行后端回归检查。

```powershell
pnpm type-check
backend/.venv/Scripts/python -m pytest backend/tests -q
```

需要手工验收时，使用三份 composed-basic 流程核对发布启动、启动及按需资源、文件上传、现场参数和人工判断；再构造分支验证回退及并行限额。特意让数据获取接口记录调用次数，可确认等待回复和回退没有重复获取固定资源。

示例覆盖的是通用接入主链，不代表任意业务模块都兼容。2026-11-12 代码交付时前端类型检查和现有 158 项后端回归通过；新组合界面与完整流程尚未做端到端验收。后续交付应记录实际做过的检查，不把示例预期当成已验证结果。
