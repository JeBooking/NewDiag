# 示例：三个现成库装配成一条诊断流程

三个各自独立、本来互不相识的工程：

| 工程 | 目录 | 平台怎么用它 |
|---|---|---|
| 取数服务 | `MockDataService` | `dataclient.DataClient().fetch(...)` 取一段数据落成 JSON |
| 绘图引擎 | `PlotEngine` | `dataset.Dataset.load` → `plot_series` → `plot_window.save_plot` 出 PNG |
| 判断引擎 | `JudgeEngine` | `judgekit.JudgeEngine(...)` → `judge_file` → `summary` 出结论 |

三个库的源码**一行都没改**，也没有为它们新增任何 Python 文件。
平台侧只有一份流程文档 [workflow.json](workflow.json)。

---

## 流程长什么样

```
开始 ──► 取数 ──► 装配并绘图 ──► 振动是否超限 ──是──► 振动超限 ──► 停机检查轴承 ──► 问题解决
   (参数)  (能力)     (能力)          (排查)      └─否──► 指标在阈值内 ──► 记录并归档 ──► 问题解决
```

| 节点 | 类型 | 配方在做什么 | 留下什么 |
|---|---|---|---|
| 取数 | 能力步骤 | `fetch(device, activity, start, end, filename=$file)` | 数据文件路径 |
| 装配并绘图 | 能力步骤 | `Dataset.load` → `plot_series` → `save_plot` | 曲线块 + matplotlib PNG |
| 振动是否超限 | 排查 | `JudgeEngine({peak, mean, trend})` → `judge_file` → `summary` | 判定表 + `overall` 分支 |
| 停机检查轴承 | 处置 | 人工 | 待办记录 |

判断节点写了 `"evidence": "n2"`，所以判定那条运行记录里除了规则明细，
还会带上绘图节点的图——出问题时不用在两个节点之间来回翻。

## 跑起来

前提：后端已启动（`uvicorn app.main:app --port 8000`），三个工程在本机可访问。

```bash
backend/.venv/Scripts/python scripts/demo_library.py
```

脚本会依次完成：登记三个工程 → 导入流程 → 发布 → 启动实例 → 打印每个节点的展示块 →
停在处置待办 → 提交完成 → 确认收敛到 `success`。

输出大致如下（节选）：

```
装配关系（画布节点 → 被调用的现成库）：
    n1 取数  <-  取数服务
        fetch
    n2 装配并绘图  <-  绘图引擎
        Dataset.load
        on dataset.plot_series
        plot_window.save_plot
    n3 振动是否超限  <-  判断引擎
        JudgeEngine
        on engine.judge_file
        on engine.summary
  节点 n1: text
  节点 n2: line, image
  节点 n3: line, image, table  {'overall': 'yes', 'result': 'yes', 'reason': '判定 yes；触发依据：实测 13.05 > 阈值 12'}
```

工程路径不在 `D:\FILE\Projects` 时用环境变量覆盖：

```bash
NEWDIAG_MOCK=/path/MockDataService \
NEWDIAG_PLOT=/path/PlotEngine \
NEWDIAG_JUDGE=/path/JudgeEngine \
NEWDIAG_PLOT_PYTHON=/path/PlotEngine/.venv/Scripts/python.exe \
backend/.venv/Scripts/python scripts/demo_library.py --device D002 --activity 启停
```

也可以只导入文档，手工在页面上看：流程页「导入」选 `workflow.json`，
再按「开始执行」。

## 换设备会怎样

`fetch` 与 `plot_series` 读的是同一组启动参数，所以换 `device` / `activity` / `metric`
三者会一起变，不会出现"图画的是 D001、判断读的是 D002"。

`peak_limit` 是唯一参与判定的阈值，配方里用 `{"$snapshot": "peak_limit", "cast": "number"}`
把它变成数字——启动表单传进来的是字符串，不写 `cast` 就会拿字符串和峰值比较。

## 为什么不直接调 `FlowClass.draw_1`

`PlotEngine/Flow.py` 的 `FlowClass.draw_1` 最后调的是 `show_plot`，它在服务端会尝试
创建一个 Tk 桌面窗口：局域网里的执行人看不到，进程还可能卡在窗口事件循环上。

同一个工程里已经有为无显示环境准备的 `save_plot` / `render_figure`，配方走的是这条路：

- `Dataset.load` + `plot_series`：拿到数据，和 `draw_1` 里的取数逻辑等价；
- `save_plot`：出 PNG，由平台登记成附件，浏览器里能直接看；
- 平台不代渲染桌面窗口，也不假装窗口存在。

如果某个库只有弹窗入口，要么库方补一个导出函数，要么在配方里改调它的渲染层——
这是接入方唯一需要判断的地方，其余都不需要改代码。

## 看什么能确认方案成立

1. **运行记录**里三个节点各自只有一份输入输出，没有跨进程传对象；
2. **绘图节点**的 evidence 里有 `line` 和 `image` 两种块，后者是 110 KB 左右的 PNG 附件；
3. **判断节点**的 evidence 里多了一张来自绘图节点的图（`evidence` 生效）；
4. 整条链走完，`runs` 只有 3 条自动记录 + 1 条人工动作，没有"胶水代码"产生的中间节点；
5. 把 `peak_limit` 改成 `20` 重跑，分支会走到「指标在阈值内」，配方不用改。
