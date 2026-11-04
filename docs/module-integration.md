# 外部模块接入与改造手册

首选方式是在排查节点分别选择四阶段函数，由平台完成编排，不需要编写适配器类或继承 SDK。
[完整入门示例](../examples/voltage/README.md)包含可导入流程图、四阶段逐步数据、
界面配置、四种自动／人工组合，以及外部类初始化参数的归属说明；代码入口为
[`functions.py`](../examples/voltage/functions.py)。

原 `investigation/v1` 类模式继续兼容，后文第 1～9 节保留其开发说明。
相关[兼容示例](../examples/voltage/CLASS_ADAPTERS.md)使用 class_adapters.py 或 voltage.py，
SDK 位于 [`backend/app/worker/node_sdk.py`](../backend/app/worker/node_sdk.py)。

## 0. 在节点上配置四个函数（推荐）

### 登记并选择

1. 登记现有业务工程的目录、模块和 Python 解释器。模块可以公开普通函数，
   也可以通过原有工程工厂公开对象方法；无需定义 acquire、parse 等固定名称。
2. 新建排查节点，默认使用“分别选择四阶段函数”；旧节点可在“排查接入方式”切换。
3. 在“获取数据、解析数据、展示数据、判断结果”四栏分别选择工程和函数，可来自不同工程。
4. 获取、判断可留空，分别转人工取数、人工判断；解析、展示必须选择函数。
5. 获取函数的输入在开始节点声明，沿用启动上下文、常量、上游输出绑定规则。
   不需要把其他三个阶段的中间数据声明成启动参数。
   四阶段模式也可在开始节点手工添加全局诊断输入，供人工取数定位或其他阶段引用。
6. 获取留空时，在同一面板逐项配置人工字段的名称、显示名称、类型、必填性；
   人工提交生成以字段名为键的对象，交给解析函数。

### 参数怎么连接

| 阶段 | 默认连接 |
|---|---|
| 获取 | 按函数参数名绑定本次启动输入及节点 params |
| 解析 | 第一个参数接收完整原始数据 |
| 展示 | 第一个参数接收解析结果；第二个参数仅在名为 decision、judgment 或 result 时自动接收判断结果 |
| 判断 | 第一个参数接收解析结果 |

额外必填参数必须配置来源；可选参数可使用函数默认值。参数面板支持：

- 原始数据、解析结果、判断结果、本次启动输入。
- 字段路径，例如选择“解析结果”并填写 `voltage`，把该数值传给函数参数 value。
- 固定值，例如给 limit 配置数字 `210`；固定值用 JSON 表达，文本需带双引号。
- 使用函数默认值，不传该参数。

展示在判断前后各执行一次；绑定判断结果时，第一次收到 None，展示函数必须能处理它。
解析阶段不能引用尚未产生的解析／判断结果；判断阶段不能引用尚未产生的判断结果。
非法引用或额外必填参数未配置，会在发布时拦截。

### 输出与运行边界

四阶段函数的调用边界使用 JSON 数据：字典、数组、数字、字符串、布尔、空值。
函数必须同步执行，参数显式命名并支持关键字传参；不支持 *args、**kwargs 和位置专用参数。
返回自定义对象、DataFrame、窗口或连接句柄不会被自动猜测转换，也不会用 repr 冒充数据。
已有业务模块若只提供这些对象，应提供可交换的数据接口；需要自定义对象快照时可继续采用旧 SDK 模式。

展示函数可以返回普通文本、普通 JSON（平台显示文本）或本手册第 7 节的 blocks 描述；
不能仅打开桌面窗口而没有返回展示内容。判断函数支持 True／False、yes／no，
或 `{result:"yes"|"no", reason:"依据"}`。普通布尔／字符串结果由平台记录函数名及返回值作为依据。
执行失败不会自动转人工，也不能把失败记成“否”。

发布固定每阶段的函数引用、参数映射及参数签名；执行和人工恢复逐次核对签名。
签名指纹不归档实现代码，修改内部算法仍需按工程版本管理。
人工等待保存检查点并关闭业务进程；恢复不重新采集或解析，只继续剩余阶段。
跨阶段不得依赖对象的隐藏状态；跨工程也不共享 Python 对象。
每个阶段调用沿用 120 秒超时，超时或进程崩溃重试一次，业务异常不自动重试。

### 构造参数归谁处理

本模式不把外部大类的构造参数生成成流程填写项。可选择模块公开函数，或工程工厂创建对象后公开的方法。
工厂沿用现有无参创建约定，真实所需的连接、凭证等由模块部署配置或已有工厂提供。
若外部大类仍强制依赖无关窗口、控制器，必须由模块维护者拆开依赖或延迟初始化；
平台不会绕过构造函数、生成假对象或忽略真实必需依赖。

### 可直接导入的函数版示例

登记名称 `电压函数示例`、路径 `examples/voltage` 的绝对路径、模块 `functions`、工厂留空。
导入 [`workflow-functions.json`](../examples/voltage/workflow-functions.json)。
对应函数在 [`functions.py`](../examples/voltage/functions.py)，既不继承 SDK，也不使用固定方法名：

| 节点栏位 | 选择函数 | 参数连接 |
|---|---|---|
| 获取 | lookup_measurement | device_id、time_point 来自启动输入 |
| 解析 | decode_voltage | payload 自动接收原始数据 |
| 展示 | describe_measurement | measurement 自动接收解析结果，result 自动接收判断结果 |
| 判断 | is_under_voltage | value 来自解析结果的 voltage；limit 固定为 210 |

另有 workflow-functions-auto-acquire.json、workflow-functions-auto-judge.json、
workflow-functions-manual.json，分别覆盖自动取数／人工判断、人工取数／自动判断、全人工边界。
四份流程均使用同一业务图；人工数据填写 raw_value=205000、unit=mV、source=现场仪表。
外部业务类的 records_file、channel、calibration 由公共业务函数从部署环境读取，
示例有本地默认值，流程用户不填这些构造参数。可用 VOLTAGE_RECORDS、VOLTAGE_CHANNEL、
VOLTAGE_CALIBRATION 环境变量覆盖；必需依赖不会因此消失，只是归模块部署方管理。

手工验收步骤：按上述登记、导入、发布后，以 motor-01 和示例时间启动，查看四阶段数据和是分支；
再导入人工变体提交数据与依据，确认待办继续推进。修改字段路径或漏选解析／展示时应显示配置错误。
这些是预期行为及验收步骤，不代表已执行浏览器手工验收。

## 1. 兼容类模式：接入对象与边界

平台绑定业务工程内的**适配器类**，不直接绑定桌面窗口，也不要求重写原有业务类。

```text
节点启动输入 → acquire／人工提供 → parse → present
                                           ↓
                                  judge／人工判断
                                           ↓
                                  present（含判断结果）
```

| 接口 | 是否必需 | 调用方式 |
|---|---|---|
| 构造器 | 可以使用默认构造器 | `Adapter(**config)`；config 为节点初始化配置 |
| `acquire(self, ...)` | 否 | 参数来自启动上下文、节点常量或上游输出 |
| `parse(self, raw_data)` | 是 | 接收自动采集结果或人工提交的数据 |
| `present(self, parsed_data, decision=None)` | 是 | 判断前调用一次，判断后再次调用 |
| `judge(self, parsed_data)` | 否 | 返回 `{"result": "yes"或"no", "reason": "非空依据"}` |
| `close(self)` | 否 | 每次调用完成或失败后关闭资源；默认空实现 |

未实现的 acquire、judge 保持 `None`，不要写一个返回 `None` 的方法，不要用异常表示“请转人工”。
配置了方法但执行失败时，平台报告执行错误，不自动改走人工。
“否”是业务判断结果；数据不足、读取失败、解析失败应抛异常，不能当作“否”。

排查节点之外，平台编译器有开始、排查动作、结论、处置、跳转、问题解决、上升等节点类。
它们继续按现有文本／人工／终止语义执行，不需要实现四阶段接口。
节点类生成发布计划；等待、分支、回退、终止由调度器管理。

## 2. 最小接入步骤

### 2.1 编写适配器

保存为业务工程目录下的 `diagnosis_adapter.py`：

```python
from node_sdk import InvestigationNode

__all__ = ["VoltageCheck"]


class VoltageCheck(InvestigationNode):
    version = "1.0.0"
    inputs = [
        {"name": "device_id", "type": "text", "required": True},
        {"name": "time_point", "type": "datetime", "required": True},
    ]
    manual_fields = [
        {"name": "voltage", "label": "电压", "type": "number", "unit": "V", "min": 0},
        {"name": "source", "label": "测量来源", "type": "text"},
    ]

    def __init__(self, threshold: float = 210):
        self.threshold = threshold

    def parse(self, raw_data):
        voltage = raw_data["voltage"]
        if isinstance(voltage, bool) or not isinstance(voltage, (int, float)):
            raise ValueError("电压必须是数字")
        if voltage < 0:
            raise ValueError("电压不得为负数")
        return {"voltage": voltage, "source": raw_data["source"]}

    def present(self, parsed_data, decision=None):
        blocks = [{"type": "metrics", "items": [
            {"label": "电压", "value": parsed_data["voltage"], "unit": "V"},
            {"label": "阈值", "value": self.threshold, "unit": "V"},
        ]}]
        if decision is not None:
            blocks.append({"type": "text", "text": decision["reason"]})
        return {"blocks": blocks}
```

这个例子不提供 acquire、judge，因此依次出现“提供数据”和“人工判断”待办。
`inputs` 是本次诊断的定位参数，平台在人工取数待办里显示它们；`manual_fields` 才是人工需要提交的原始数据。
增加 acquire 后，启动输入按 acquire 的真实参数签名探测，`inputs` 不再参与探测。

### 2.2 登记工程

在平台“工程”页登记：

| 字段 | 示例 |
|---|---|
| 名称 | `电压诊断`，登记后不可改名 |
| 工程路径 | `D:\Business\Voltage`，必须是后端机器上的目录 |
| 模块 | `diagnosis_adapter`，不加 `.py` |
| Python | `D:\Business\Voltage\.venv\Scripts\python.exe` |
| 工厂 | **留空**；四阶段模式按节点配置创建适配器，不使用旧的无参工程工厂 |

业务工程的第三方依赖装在自己的虚拟环境中。SDK 只依赖 Python 标准库；平台启动探测和执行子进程时可直接导入
`node_sdk`，不需要业务工程依赖 FastAPI 或 SQLAlchemy。
不要把不同版本的 SDK 复制到业务工程中覆盖平台提供的模块。

在业务工程中独立运行或让 IDE 识别 SDK 时，将平台的 `backend/app/worker` 加入该工程的 Python 搜索路径。
例如在 PowerShell 的当前终端临时设置：

```powershell
$env:PYTHONPATH = 'D:\FILE\Projects\NewDiag\backend\app\worker'
```

离线部署包中的对应目录是 `app/worker`。这只是独立开发时的设置；平台调用不依赖用户手动设置它。

### 2.3 绑定、配置、发布

1. 工程页查看“接口”，确认列出 `VoltageCheck`。探测只检查类与接口，不创建业务对象、不连接数据中心。
2. 排查节点的“参数绑定”选择工程和该类，界面显示四阶段模式、版本、采集／判断方式。
3. 在“初始化配置（JSON）”填写 `{"threshold": 210}`。配置在失焦且 JSON 合法时写回节点；不要忽略格式错误提示。
4. 开始节点声明 `device_id`、`time_point`，或通过节点 `params` 绑定常量／上游输出。
5. 填写排查说明，连接“是／否”后继，发布。
6. 启动诊断后提交原始数据；平台解析并展示；填写判断依据后选择是／否。

发布前检查：适配器必须实现 parse、present；人工采集必须有 manual_fields；构造参数不能缺少，也不能填写未知参数。
必需运行参数必须有绑定来源。接口探测失败时不允许发布。

可直接使用仓库示例：工程路径填仓库 `examples/voltage` 的绝对路径，模块填 `voltage`，工厂留空。
`ManualVoltage` 不需要数据服务器；`AutomaticAcquisitionVoltage` 和 `AutomaticVoltage` 需要真实的 `data_url` 配置。

如果要直接导入配套流程，按示例 README 登记名称 `电压类模块示例`、模块 `class_adapters`，
导入 `examples/voltage/workflow.json`。该版本使用本地离线数据，无需 HTTP 服务；
另有三份 workflow 变体覆盖其他自动／人工组合，并完整演示外部类必填构造参数的传递。

## 3. 带初始化参数的类如何适配

初始化配置和执行输入分开：

```text
config：服务地址、算法选项、阈值、凭证环境变量名
inputs：本次设备编号、时间点等
```

适配器构造器支持显式位置或关键字参数、仅关键字参数，不支持位置专用参数、`*args` 或 `**kwargs`。
必填参数没有默认值，可选参数应提供可 JSON 表达的默认值。配置类型与业务约束由构造器检查。
配置随流程版本保存，密钥不要直接填写在配置中；填写环境变量名称，再由业务进程读取。

例如旧类同时承担分析和设备控制：

```python
class OldApplication:
    def __init__(self, server, controller, window):
        ...
```

先确认当前功能是否真的依赖 controller、window：

| 实际情况 | 改造方法 |
|---|---|
| 已支持传 `None`，当前路径不会访问它们 | 适配器传 `None`，保留原类 |
| 只有其他功能需要，却在构造器里强制连接 | 改成可选依赖，把连接延迟到使用它的功能 |
| 解析算法直接使用窗口控件 | 提取独立的分析核心，窗口与平台共用核心 |
| 第三方不可修改且必须打开窗口 | 保留桌面人工操作，通过上传文件／填写数据接入 |

不能通过包装自动消除真实的必需依赖，不要塞假连接、假窗口让构造器勉强通过。

也可以延迟创建原有业务对象：

```python
def __init__(self, server_url: str):
    self.server_url = server_url
    self.client = None

def acquire(self, device_id: str, time_point: str):
    self.client = ExistingClient(self.server_url, device_id)
    return self.client.read_at(time_point)

def close(self):
    if self.client is not None:
        self.client.close()
```

缺省采集直接进入待办，不创建业务对象。处理人工数据或判断时才创建适配器。
每次开始／人工恢复都会重新构造适配器，因此 parse、present、judge 不能依赖“上次 acquire 写进 self 的数据”。
进入四阶段人工待办后关闭业务子进程，恢复时重新导入模块并检查版本。
同一次自动执行内部可以复用连接，但跨人工等待必须通过显式数据恢复。

## 4. 桌面应用如何改造

推荐保留桌面应用，并提取业务核心：

```text
原桌面窗口 ─┐
            ├→ 分析核心（采集、解析、判断）
平台适配器 ─┘
```

| 原代码中的耦合 | 替换方式 |
|---|---|
| `line_edit.text()` 读取设备编号 | acquire 显式接收 `device_id` |
| 算法往窗口表格写值 | 返回结构化结果，桌面窗口自行显示，平台 present 返回展示描述 |
| 弹窗询问是／否 | 不实现 judge，由平台人工判断 |
| 文件选择对话框 | 人工 file 字段上传，parse 接收平台解析后的文件引用 |
| 构造时创建主窗口或启动事件循环 | 放到桌面程序专用入口，不在模块导入和适配器构造时执行 |
| 使用全局“当前设备”“当前打开文件” | 明确写入本次输入或数据结果 |
| 图中选点影响后续判断 | 将选点结果变成显式输入并保存，不能只保存在画布内存中 |

业务方法必须可在无窗口环境调用，不弹窗、不等待键盘输入，不启动 GUI 主事件循环。
原 Qt/Tk 等控件不能直接嵌入浏览器。本版提供指标、表格、文本、折线展示；特殊交互视图需另行开发网页组件，
当前协议不加载外部 HTML、JavaScript 或桌面窗口。

暂时无法拆分的桌面软件，可以由人工在本机操作后导出数据，再上传给平台解析。
本版不控制用户电脑上的桌面进程，也不包含桌面进程间通信服务。后端机器弹出的窗口不是用户浏览器窗口。

## 5. 数据格式与原有对象

不同适配器不必使用相同业务数据结构，但本适配器的采集／人工数据、解析、展示、判断必须相互匹配。
SDK 不猜测单位，不自动转换 DataFrame、自定义对象或设备数据格式。

同一次调用内，acquire → parse → present／judge 可以传递原有 Python 对象。
为保存历史并在人工等待后恢复，原始数据和解析结果必须能生成 JSON 快照。
默认直接保存 JSON 值；不接受 NaN、Infinity，也不使用 repr 冒充业务数据。
复杂对象可在适配器覆写以下方法，不必修改原业务类：

```python
def dump_raw(self, value):
    return value.to_dict(orient="records")

def dump_parsed(self, value):
    return value.to_dict(orient="records")

def load_parsed(self, value):
    return pandas.DataFrame.from_records(value)
```

上述仅适用于采集和解析均使用 DataFrame 的模块，需要在业务环境安装并导入 pandas。
不同类型应按自己的数据结构实现；JSON 本身不能保留任意 Python 类型、dtype 或索引，
需要这些信息时应在快照中显式保存。`load_parsed` 只在人工判断恢复时调用；恢复不重新采集或解析。
历史输出的 `data` 是 dump_parsed 的结果，下游绑定路径例如 `output.data.voltage`。

大文件应上传文件，或由模块使用自己的稳定文件存储；不要把大量二进制内容塞进 JSON。
远程文件、设备句柄、数据库连接不能充当可恢复的数据快照。

## 6. 人工字段协议

`manual_fields` 为字段数组，每项的 `name` 唯一：

| 字段属性 | 说明 |
|---|---|
| name | 传给 parse 的键名 |
| label | 界面标签，缺省显示 name |
| type | text／number／boolean／datetime／json／file；缺省 text |
| required | 缺省 true |
| default | 可选默认值 |
| min、max | 数字范围 |
| unit、description | 单位与填写说明 |

datetime 使用带时区的 ISO8601 字符串，例如 `2026-11-03T10:00:00+08:00`。
json 字段可提交复杂数组或对象，其业务结构由 parse 校验。
file 字段上传后，平台验证存储记录，将 `{"hash": "...", "path": "后端绝对路径"}` 传入 parse。
这是平台存储的文件路径，不接受客户端自行指定的本地路径。

```python
import json

manual_fields = [{"name": "report", "label": "原始报告", "type": "file"}]

def parse(self, raw_data):
    with open(raw_data["report"]["path"], encoding="utf-8-sig") as stream:
        return json.load(stream)
```

自动取数返回的原始对象与人工上传的文件可以不同，由该适配器的 parse 识别并归一。
字段类型、必填、范围先由平台校验；业务完整性和数据质量由 parse 校验。
人工提交解析失败时，原待办保留，界面显示错误，用户可以修正后重新提交。

## 7. 展示协议

present 返回 `{"blocks": [...]}`，按数组顺序渲染；支持空数组，但不能缺少 blocks。
只输出数据描述，不输出可执行脚本或原生窗口。

```python
return {"blocks": [
    {"type": "text", "title": "说明", "text": "采集数据有效"},
    {"type": "metrics", "items": [
        {"label": "电压", "value": 205, "unit": "V"},
    ]},
    {"type": "table", "title": "测量记录",
     "columns": ["时间", "电压/V"], "rows": [["10:00", 205], ["10:01", 206]]},
    {"type": "line", "title": "电压趋势", "unit": "V", "values": [205, 206, 204]},
]}
```

折线当前按等间隔样本序号排列，提供可展开的数值列表；不支持自定义时间轴、选点和缩放。
展示描述用于人工判断待办和运行记录；自动判断结束后也会保存并可回看。
present 必须接受 decision=None；不能在第一次展示时假设已经有判断结果。

## 8. 执行、版本和故障约束

- 所有接口均为同步方法；需要异步库的模块应在适配层处理，不直接返回协程。
- 采集、解析、展示、判断必须只读且允许重复调用。默认一次适配器调用总超时 120 秒；超时或进程崩溃重试一次，业务异常不自动重试。
- 初次自动调用包含多个阶段，120 秒是整次调用预算，不是每个方法各 120 秒。人工等待不占用这个预算。
- 子进程 stdout 是协议通道，业务 print 会被重定向到 stderr；模块仍应优先使用 logging，不直接操作 stdout 文件描述符。
- 人工待办保存输入、原始数据、解析快照和展示描述；最终 run 保存结果、依据、阶段耗时、模块标识与版本。
- 人工提交有提交者与时间；非法提交不会推进分支。回退／终止取消未完成待办，不能用旧待办推进新令牌。
- 等待不依赖业务对象常驻。当前平台仍不承诺服务重启后自动续跑；这是原有进程恢复能力的边界，不能以适配器对象存活替代它。
- 发布固定适配器版本及配置，运行及人工恢复时检查版本。改动算法、输入、输出、展示或人工字段后必须递增 version，并重新发布。
- 版本号不是代码归档。平台不会保存外部源码、依赖或服务响应；需要严格复现时，模块维护者必须保留每版代码、依赖和数据源，旧流程指向独立的版本目录。
- 同实例切换不同业务工程时会切换子进程。不能依赖另一个工程或另一个节点留在内存中的对象。

## 9. 旧接口兼容与手工验收步骤

旧的普通函数／无参工厂接入仍有效；不含 protocol 的绑定保持原来布尔返回和人工节点语义。
新四阶段模式必须选择适配器；旧函数返回字典不会自动被当成四阶段结果。
无绑定的旧人工排查节点也不自动改成四阶段节点，需显式选择适配器。

接入方可按以下步骤验收自己的模块：

1. 用 `ManualVoltage` 发布流程，确认人工取数后显示电压与阈值，再出现判断待办。
2. 尝试输入负数或清空必填项，确认范围受限或错误可见，待办保留且未推进后继；通过接口提交不合规数据时也应拒绝。
3. 填写判断依据并选择是／否，确认对应分支执行，历史可见输入、数据和依据。
4. 换成 `AutomaticJudgmentVoltage`，确认人工取数后直接自动分支。
5. 配置真实数据服务后，分别使用 `AutomaticAcquisitionVoltage`、`AutomaticVoltage` 验证另外两种组合。
6. 在等待阶段刷新页面，确认待办与已展示数据保留；回退后旧待办不可提交，重执行使用新令牌。
7. 修改模块 version 后处理旧待办，确认拒绝使用新版本覆盖旧结果；重新选择适配器并发布新流程后再使用。



## 共享获取与解析

同一文件供多个排查使用时，在指定位置添加「数据准备」节点，选择获取、解析函数。
准备节点可以同时连接多个排查，也可放在分支中按需执行。
下游排查切换到「使用数据准备结果」，选择准备节点；保留展示和判断函数，删除原先的获取、解析配置。
展示／判断参数仍选择「解析结果」并填写字段路径，例如 voltage、measurements.0.current。
「数据范围」可将解析结果限制为某个子对象，阶段参数路径随后相对于这个子对象取值。

平台只执行一次该准备到访的获取与解析；共享模式用独立 JSON 产物和字段索引传递数据，
函数仍接收普通 Python JSON 值。不要在多个消费者中再次打开并解析源文件；让准备函数返回
所需结构。大文件获取函数可以返回本地文件路径，解析函数读取该文件并返回字典／列表。
读取整个结果仍会加载全部数据，建议展示和判断参数只绑定所需字段。

来源节点须处于当前节点每条进入路径的前面；旁支准备不会自动触发或等待。
人工等待、不同工程切换不会丢失准备结果。重跑排查沿用准备数据，重跑准备节点产生新版本，
历史保留旧版本。超时重试可能再次调用准备函数，因此函数须只读且可重复调用。
备份时包含 backend/data/artifacts；产物暂不自动删除。

手工验证：创建「开始 → 数据准备 → 两个排查 → 上升」；获取与解析函数分别打印调用计数，
两个排查绑定不同字段。启动后应各获取／解析一次，两份运行记录引用同一产物。
将其中一个排查改成人工判断，提交后只新增最终展示调用；重跑该排查不新增获取／解析调用；
重跑准备节点应生成新产物并再次调用获取／解析，旧运行仍保留原产物。
