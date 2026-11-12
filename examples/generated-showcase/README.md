# 泵组多通道综合示例

本例只依赖标准库，类在 [diagnosis.py](diagnosis.py)，数据是同目录的 [readings.json](readings.json)
和 [spectra.json](spectra.json)，全部按 `Path(__file__)` 相对读取。
覆盖生成 Python 执行、人工等待常驻、并行限额排队、串行共享数据、`Data` 分阶段填充、
`@resource` 按参数缓存、回退复用，以及「没有后继只结束当前路径」。

## 登记、导入、启动

1. 登记工程名称 `泵组诊断示例`，路径为本目录绝对路径，模块 `diagnosis`，工厂留空。
2. 导入 [workflow.json](workflow.json)，查看流程属性中的 `PumpDiagnosis` 和各节点的实例方法绑定。
3. 保存并发布。默认设备 `pump-01`、时间 `2026-11-12T09:00:00+08:00`。
4. 启动时选并行、上限 `2`。`pump-01` 的三条同级分支里，前两条先占满名额，第三条排队；
   人工等待也占名额，有分支结束才补位。

| 文件 | 用途 |
|---|---|
| [workflow.json](workflow.json) | 三条同级分支：振动、轴承、出口压力；限额 2 时第三个分支排队 |
| [workflow-serial.json](workflow-serial.json) | 串行启动，一个进程依次处理同级思路，共享已取得的 `Data` |
| [workflow-rollback.json](workflow-rollback.json) | 单链到处置措施，回退到已完成的人工判定，另选后继 |

## 运行行为

| 步骤 | 行为 |
|---|---|
| 启动 | `initialize` 读取运行读数，`self.dataset = Data(baseline=...)`，只填第一个成员 |
| 振动排查 | 首次加载 B 通道低频频谱，随后填 `Data.trend`（第二个成员） |
| 现场动作 | 进程与对象保持存活，提交后继续；日志里的分支 pid 不变 |
| 等待后复测 | 同参数再次取频谱命中缓存，继续读 `Data.baseline + Data.trend` |
| 轴承排查 | A 通道 `hi`、`lo` 两个参数各自首次加载；随后填 `Data.bearing` |
| 人工判断 | `review_bearing` 只返回展示不给 result，平台生成判定待办 |
| 判断后复查 | 同参数取 A/hi 命中缓存，读 `Data.baseline + Data.bearing` |
| 出口压力 | `pump-01` 的 `1.92 MPa` 超过阈值，`no` 分支没有出线，只结束该路径 |
| 问题解决 | 其余分支继续到结论、处置措施和问题解决，互不影响 |

`pump-02` 是另一组数据：振动和轴承都超限走上升；读数里没有 `discharge_pressure`，
`check_discharge` 直接抛错，也只结束它自己那条分支。

## 每个特性看哪条日志

| 特性 | 关键日志 / 现象 |
|---|---|
| 生成 Python 执行 | `data/executions/<实例>/flow.py`；`实例 <实例> 分支启动 pid=...` |
| 人工等待常驻 | 平台日志没有单独的「等待人工」行；看业务方法 `返回展示，等待人工判断 pid=...`，实例详情 `status=waiting`、token `status=waiting`，以及提交后同一 pid 的 `等待后继续...` |
| 限额并行 | `分支启动 pid=... 活跃=1 上限=2`、`活跃=2 上限=2`；第三个分支在 token 列表里 `queued`，前一个退出后才出现第三条 `分支启动` |
| 串行 / 并行 | 串行只有一条 `分支启动 pid=... 活跃=1 上限=1`；并行有两条不同 pid 的 `分支启动` |
| `Data` 分阶段填充 | `启动填充 Data 成员 baseline` → `分阶段填充 Data 成员 trend` / `bearing` |
| `@resource` 按参数缓存 | 不同参数各自 `首次加载频谱 ... channel=A band=hi` / `band=lo`，缓存键不同；再次调用出现 `诊断资源缓存命中 resource=spectrum key=...` |
| 回退复用 | `回退到访 <到访>，复用结果选择 no，新代 2`；新进程 `诊断对象由快照恢复`，没有新的 `启动获取运行读数` / `首次加载频谱` |
| 没有后继只结束当前路径 | `节点 discharge route/no-successor: 当前分支没有后继`，其他分支和实例整体状态不受影响 |

> 旧解释引擎的 `复用数据准备`、`状态 running -> waiting` 在生成执行里不再打印；
> 生成引擎用 `回退到访 ...复用结果选择...` 和实例 / token 的 `status` 表达同一件事。

## 实测日志摘录

主流程（并行，上限 2；`pump-01`）：

```text
实例 <A> 分支启动 pid=23332 活跃=1 上限=2
诊断 <A> pid=23332 INFO 启动填充 Data 成员 baseline pid=34500
实例 <A> 分支启动 pid=33912 活跃=2 上限=2
诊断 <A> pid=23332 INFO 首次加载频谱 ... channel=B band=lo pid=34500
诊断 <A> pid=23332 INFO 分阶段填充 Data 成员 trend pid=34500
诊断 <A> pid=33912 INFO 首次加载频谱 ... channel=A band=hi pid=31668
诊断 <A> pid=33912 INFO 首次加载频谱 ... channel=A band=lo pid=31668
诊断 <A> pid=33912 INFO 分阶段填充 Data 成员 bearing channel=A pid=31668
诊断 <A> pid=33912 INFO 诊断资源缓存命中 resource=spectrum key=77d12e...
诊断 <A> pid=33912 INFO 返回展示，等待人工判断 pid=31668
诊断 <A> pid=23332 INFO 诊断资源缓存命中 resource=spectrum key=d4ec1e...
诊断 <A> pid=23332 INFO 等待后继续，复用 Data.baseline + Data.trend pid=34500
实例 <A> 分支启动 pid=27908 活跃=2 上限=2      ← 前一条分支结束后第三个补位
诊断 <A> pid=27908 INFO 诊断对象由快照恢复 class=PumpDiagnosis
实例 <A> 节点 discharge route/no-successor: 当前分支没有后继
诊断 <A> pid=33912 INFO 等待后继续，复用 Data.baseline + Data.bearing pid=31668
```

等待前后的分支 pid 都是 `23332 / 33912`，没有因为人工等待重新拉起进程。

串行变体：

```text
实例 <B> 分支启动 pid=26120 活跃=1 上限=1
诊断 <B> pid=26120 INFO 分阶段填充 Data 成员 trend pid=18848
诊断 <B> pid=26120 INFO 轴承分支读取到 trend=True pid=18848   ← 串行复用前一分支的 Data
```

并行时同一行是 `trend=False`，两条分支各自持有独立状态。

回退变体：

```text
实例 <C> 回退到访 2f2a690781324464928f0a6323016a7d，复用结果选择 no，新代 2
实例 <C> 分支启动 pid=26344 活跃=1 上限=2
诊断 <C> pid=26344 INFO 诊断对象由快照恢复 class=PumpDiagnosis
诊断 <C> pid=26344 INFO 诊断资源缓存命中 resource=spectrum key=77d12e...
诊断 <C> pid=26344 INFO 等待后继续，复用 Data.baseline + Data.bearing pid=28588
```

回退后没有重新初始化，也没有重新加载频谱，原人工判定不会再次提出。

## 模块数据约定

`Data` 成员只能填充一次。本例 `baseline` 在 `initialize` 固定，`trend` / `bearing` 在各自首个排查阶段补齐；
序列化只保存新增成员，已保存的 `baseline` 复用引用，不重复落盘。
`@resource` 的缓存键包含类、配置、初始化输入、资源版本和有效参数；不同参数各自缓存，
读取返回副本，加载异常不写成功缓存。

启动时固定当前模块源码，运行中修改原工程不影响已启动实例。生成引擎没有 retry 模式，
回退只选择已完成到访和它的新后继，复用该到访输出；开始和终止节点不能作为回退目标。
详细协议见 [诊断类开发手册](../../docs/diagnosis-development-guide.md)。
