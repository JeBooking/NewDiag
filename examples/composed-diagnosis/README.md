# 三工程组合诊断示例

用 MockDataService、PlotEngine、JudgeEngine 三个**未改一行代码**的既有工程，跑通一条完整的振动／温度诊断：取数 → 解析 → 绘图 → 判断 → 分支 → 人工处置。

配置在 [`workflow.json`](workflow.json)，平台侧的接入说明见 [`docs/composition-manual.md`](../../docs/composition-manual.md)。

## 先跑起来

### 1. 前置

- 在**仓库根目录**执行。
- 后端虚拟环境已就绪：`backend/.venv/Scripts/python.exe` 存在，且装了 `backend/requirements.txt`。
  没有的话先建：
  ```bash
  python -m venv backend/.venv
  backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
  ```
- 三个工程在本机，默认路径 `D:\FILE\Projects\{MockDataService,PlotEngine,JudgeEngine}`；
  PlotEngine 必须有自己的 `.venv`（能 `import matplotlib` 和 `tkinter`）。
- **不需要前端，也不需要起 uvicorn**：脚本用 FastAPI TestClient 在进程内跑完整链路。

### 2. 一条命令

Git Bash：

```bash
backend/.venv/Scripts/python.exe examples/composed-diagnosis/run.py
```

PowerShell：

```powershell
backend\.venv\Scripts\python.exe examples\composed-diagnosis\run.py
```

一定要用 `backend/.venv` 的解释器，不要用系统 `python`（没装 fastapi/httpx）。

首次运行约 10 秒：要拉起三个工程 worker、探测接口、发布、跑两个排查节点。

### 3. 看到什么算成功

```text
工程 MockDataService: D:\FILE\Projects\MockDataService（模块 dataclient，解释器 平台自身）
工程 PlotEngine: D:\FILE\Projects\PlotEngine（模块 dataset，解释器 ...\PlotEngine\.venv\Scripts\python.exe）
工程 JudgeEngine: D:\FILE\Projects\JudgeEngine（模块 judgekit，解释器 平台自身）
流程已导入：三工程组合诊断（振动与温度）（...，10 个节点）
已发布 v1：13 个接口调用，启动参数 ['device', 'activity', 'start_time', 'end_time', 'peak', 'mean', 'trend']
实例已启动：...（D001/满载）
  （示例脚本处理人工待办：停机检查轴承 → done）

实例状态：success
  s1: 启动资源 main(2 步)
  n1: yes — 实测 13.05 > 阈值 12
      - series: value
      - chart: image fa59...
      ...
生成的图片 1 个：
  C:\Users\...\Temp\newdiag-xxxx\files\fa\fa59...（111492 字节）
```

判据：退出码 0，且最后是 `实例状态：success`。默认用临时目录，跑完自动删；加 `--keep` 保留并打印产物路径。

### 4. 换机器 / 换路径

```bash
backend/.venv/Scripts/python.exe examples/composed-diagnosis/run.py \
  --mock-data D:\somewhere\MockDataService \
  --plot      D:\somewhere\PlotEngine \
  --judge     D:\somewhere\JudgeEngine \
  --plot-python D:\somewhere\PlotEngine\.venv\Scripts\python.exe
```

常见失败对照：

| 现象 | 原因 |
|---|---|
| `No module named 'fastapi'` / `'httpx'` | 用错解释器，换成 `backend/.venv/Scripts/python.exe` |
| 发布返回 422「接口探测失败」 | 工程路径或模块名不对；或 PlotEngine 解释器没指到它的 `.venv` |
| `No module named 'matplotlib'` | 同上，执行 PlotEngine 的解释器不对 |
| `结束时间必须晚于起始时间` | `--start-time` / `--end-time` 写反或格式不对 |
| `实例状态：waiting` 且不再前进 | 用了 `--manual` 但没给 `--answer`；这是人工判断，不是卡死 |

### 5. 在平台界面里跑（可选）

脚本之外，同一份配置也能在界面上走：

```bash
pnpm dev     # 同时起后端 8000 与前端 5173
```

1. 「工程管理」登记三行（名称 / 路径 / 模块 / 解释器，见上表；PlotEngine 解释器填它的 `.venv`）。
2. 流程列表 →「导入」→ 选 `examples/composed-diagnosis/workflow.json`。
3. 打开发布，再点「执行」，按表单填设备、工况、时间范围和阈值。
4. 到「停机检查轴承」会生成人工处置待办；点完成后走到问题解决。

界面方式会把数据写到 `backend/data`，脚本方式默认用临时目录，两者互不影响。

## 常用参数

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
