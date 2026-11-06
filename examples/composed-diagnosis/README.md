# 三工程组合诊断示例

用 MockDataService、PlotEngine、JudgeEngine 三个**未改一行代码**的既有工程，跑通一条完整的振动／温度诊断：取数 → 解析 → 绘图 → 判断 → 分支 → 人工处置。

配置在 [`workflow.json`](workflow.json)，平台侧的接入说明见 [`docs/composition-manual.md`](../../docs/composition-manual.md)。

## 运行

```bash
# 默认 D001 / 满载：振动超标 → 异常结论 → 停机检查
python examples/composed-diagnosis/run.py

# D002 / 空载：振动正常 → 温度正常 → 正常结论
python examples/composed-diagnosis/run.py --device D002 --activity 空载
```

脚本会：注册/更新三个工程 → 导入流程 → 发布 → 启动实例 → 推进调度（人工动作由脚本代填）→ 打印状态、每个节点的判断与依据、生成的图片路径。

常用参数：

| 参数 | 说明 |
|---|---|
| `--device` / `--activity` / `--start-time` / `--end-time` | 启动表单，取数参数 |
| `--peak` / `--mean` / `--trend` | 判断阈值 |
| `--mock-data` / `--plot` / `--judge` | 三个工程的路径，默认 `D:\FILE\Projects\...` |
| `--plot-python` | PlotEngine 解释器，默认取它的 `.venv` |
| `--data-dir` | 数据库与产物目录，默认临时目录；配 `--keep` 保留 |
| `--manual` | 删掉操作的自动判断：节点只取数绘图，然后等人看图判断 |
| `--answer yes\|no` | 配合 `--manual` 由脚本代人选是/否；不填则停在人工待办 |

> 三个工程的路径是脚本参数，不是硬编码。换机器时用参数指过去即可。

## 流程长什么样

```
开始（设备、工况、时间范围、三条阈值）
  │
  ├─ 启动资源 main：DataClient.fetch → Dataset.load（saveAs=dataset）
  │
振动排查 n1（metric=vibration）
  ├─ dataset.plot_series        → 同一份曲线
  ├─ save_plot                  → 图片
  ├─ JudgeEngine(thresholds)    → 判断对象
  ├─ judge_series → summary     → overall / reasons / verdicts
  └─ 是 → 结论「振动异常」→ 处置「停机检查轴承」→ 问题解决
     否 → 温度排查 n2
            ├─ 复用 dataset，换 metric=temperature
            ├─ 显式构造 MeanThresholdRule + RisingTrendRule（不用默认 peak）
            └─ 是 → 上升；否 → 结论「指标均正常」→ 复核归档 → 问题解决
```

## 这个例子证明了什么

1. **不改原工程**：三个工程只提供公开接口，示例没有新增任何胶水 Python 文件。
2. **多工程多解释器**：MockDataService 用平台解释器、PlotEngine 用自带 `.venv`、JudgeEngine 用平台解释器，同一次诊断同时存活。
3. **对象留在本工程**：`Dataset` 只存在于 PlotEngine worker，`Verdict` 列表只存在于 JudgeEngine worker；跨工程传的是 JSON、文件和图片。
4. **一份数据两处用**：同一条 `series` 既画图又判断，不重复取数、不重复解析。
5. **配置能表达业务**：阈值来自启动表单，规则对象可以按参数构造，判断结果映射到分支和展示。
6. **复用平台能力**：发布冻结、签名检查、执行轨迹、人工待办、回退、终止都是现成的。

## 调整这个例子

| 想改什么 | 改哪里 |
|---|---|
| 换设备/工况/时间范围 | 启动表单；`run.py` 的参数 |
| 改阈值 | 启动表单的 `peak / mean / trend` |
| 只判断峰值 | 温度排查的 `meanRule` / `trendRule` 去掉，只保留 `PeakThresholdRule` |
| 加一个指标 | 复制一个 operation，换 `metric` 与阈值；`dataset` 与取数步骤不用动 |
| 换数据源 | 只改启动资源里的 `fetch` 步骤，`dataset` 之后的配置不变 |
| 让人看图判断 | 删掉 operation 的 `result`，保留 `presentation`；平台会生成带图的人工判断待办 |
