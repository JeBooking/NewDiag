# 流程类（v1.14）

把「一个 flow 就是一个类，一次执行就是一个实例」变成平台能力：作者只声明导入、构造和方法，
发布时编译成一份真实的 Python 类，执行时交给已有的 `diagnosis/v1` 运行时。

示例与手把手手册：[`examples/flow-class`](../examples/flow-class/README.md)。

---

## 1. 为什么不是又一门 DSL

旧接入要求业务侧交一个「平台形状」的东西：`Diagnosis` 子类、四阶段适配器、或单函数绑定。
三个现成工程（取数、绘图、判断）各自完整，缺的只是把它们串起来的那层——于是有了这套声明。

它刻意不做「平台解释表达式」：声明被翻译成 Python 源码，语义、作用域、异常全部由解释器负责。
平台只多了一个模板生成器，没有多一个语言运行时。这带来三个直接结果：

- 方法体里能写的东西，就是 Python 能写的东西（虽然声明只覆盖直线调用）；
- 生成结果可以导出、可以读、可以 diff，发布后随版本冻结；
- 签名、默认值、类型注解都是真的，探测和表单不需要另一套元数据。

## 2. 模型

```python
import dataclient, dataset, plot_window, judgekit      # imports

class Flow(Diagnosis):
    def initialize(self, device, activity, ...):       # initialize，只跑一次
        self.client = dataclient.DataClient(root=self.artifact_root)
        self.data_file = self.client.fetch(...)
        self.data = dataset.Dataset.load(self.data_file)

    def check_metric(self, metric, peak, ...):         # methods
        ...
        return {"result": ..., "reason": ..., "figure": ..., "series": ...}
```

| 概念 | 声明位置 | 语义 |
|---|---|---|
| `imports` | `doc.flowClass.imports` | 别名 + 工程名 + 模块名；发布时校验能否 import |
| `initialize` | `doc.flowClass.initialize` | 构造过程，启动参数可见；`name` 变成 `self.<name>` |
| `methods` | `doc.flowClass.methods` | 参数、方法体（有序调用）、返回映射 |
| 节点 | `node.method` + `node.params` | 排查节点 = 选方法 + 给参数 |

一次执行 = `Flow(**启动参数)` 的一个实例：`initialize` 里的取数只发生一次，多个排查节点
共用同一个 `self.data`。人工等待后恢复实例时，运行时按启动参数重放 `initialize`。

## 3. 声明细节

### 3.1 步骤

```json
{"name": "figure", "call": "plot_window.save_plot", "args": {"data": {"var": "item"}, "path": {"file": "{metric}.png"}}}
```

- `call` 的第一个名字只有三种：导入别名、`initialize` 里构造的对象、同方法内前面的局部变量；
- `name` 省略表示只调用不保存；
- `initialize` 里带 `name` 的步骤变成实例属性，方法里可以直接引用。

### 3.2 值表达式

| 写法 | 生成 | 说明 |
|---|---|---|
| `{"lit": v}` | `v` 的 Python 字面量 | 只接受 JSON 可表示的类型 |
| `{"input": n}` | 形参 / `self.n` | 启动参数 |
| `{"param": n}` | `n` | 方法形参 |
| `{"var": n, "path": p}` | `x` / `_at(x, "a.b")` | 已保存的变量 |
| `{"file": t}` | `self._artifact(f"{metric}.png")` | 产物目录里的文件 |
| `{"artifactRoot": true}` | `self.artifact_root` | 产物目录 |
| `{"list": […]}` / `{"object": {…}}` | 数组 / 字典 | 可嵌套表达式 |
| `{"call": …, "args": …}` | 嵌套调用 | 可直接当参数 |

### 3.3 返回

```json
{"result": "summary.overall", "reason": "summary.reasons.0", "reasonFallback": "规则均未触发",
 "figure": "figure", "series": "item"}
```

- `result` 留空 → 方法不带判断，节点转人工；
- `figure` / `series` 由运行时物化：图片入库成附件并变成 `image` 块，序列变成 `line` 块；
- 依据取不到时用 `reasonFallback`，避免在记录里留下空字符串。

## 4. 发布

1. `probe_doc` 把类当成虚拟工程 `@flow:Flow` 注入签名表，`compiler` 不用区分它和真工程；
2. 用一个子进程、以流程声明的 `runtime` 解释器 import 所有工程，并校验别名根上的接口存在；
3. 编译产物 `plan.diagnosis` 额外带上 `source`、`module`、`projects`、`runtime`、`flowClass`；
4. 源码随版本冻结，不随 doc 或工程代码变化。

失败都发生在发布：缺依赖、别名拼错、接口不存在、`name` 冲突、`result` 指向不存在的字段。

## 5. 执行

1. 调度器把冻结的源码写到 `<artifactRoot>/_flowclass/<module>.py`；
2. 会话子进程 `sys.path` 加上所有被引工程的目录，再 import 这个模块；
3. 构造 `Flow` 实例 → `initialize(**启动参数)` → 每个排查节点调 `method(**参数)`；
4. 返回 `result` 决定走哪条出边，`presentation` 进运行记录与待办；
5. 人工等待、回退、并行/串行、附件登记全部沿用原调度器。

一个实例一个解释器，所以「跨工程」在运行时不存在：对象就是普通 Python 对象。
代价是依赖必须装在同一个环境里，发布期的导入探测会把这件事提前暴露。

## 6. 与 diagnosis/v1 的关系

| 项 | diagnosis/v1 | 流程类 |
|---|---|---|
| 类的来源 | 业务方写 `.py` | 平台生成 |
| 运行时 | `DiagnosisRuntime` | 同一个 |
| 指纹校验 | 发布期记录、执行前比对 | 跳过（源码已冻结，不会漂移） |
| 版本与恢复 | `DiagnosisState` 对象快照 | 同一个 |
| 适用 | 需要任意 Python 逻辑 | 取数/绘图/判断这类直线组装 |

两者不能在同一条流程里混用；不同流程可以各选一种。

## 7. 实现索引

| 文件 | 职责 |
|---|---|
| `backend/app/services/flowclass.py` | 声明校验、元信息、源码生成 |
| `backend/app/services/compiler.py` | `_flow_doc` / `_flow_class_issues` / 冻结源码进 plan |
| `backend/app/services/signatures.py` | 虚拟工程注入、发布期导入探测 |
| `backend/app/worker/probe.py` | `check=flowclass`：多路径 import 与接口存在性 |
| `backend/app/worker/session.py` | 会话 `paths` 支持 |
| `backend/app/services/scheduler.py` | `_flow_project` 落盘与虚拟工程、`frozen` 请求 |
| `backend/app/worker/diagnosis_sdk.py` | `artifact_root`、`figure`/`series` 物化 |
| `src/components/panels/FlowClassEditor.vue` | 类定义编辑器 |
| `src/components/panels/FlowClassMethodEditor.vue` | 排查节点选方法 |
