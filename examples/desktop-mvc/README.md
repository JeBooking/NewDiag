# 桌面 MVC 应用接入

一个用 Tkinter 写的最小桌面应用，**不改代码**直接接入诊断流程：应用的 Model 当处理槽，View 当展示槽，采集模块当拉取槽，判定规则另立工程。

## 这个示例想说明什么

桌面应用的 MVC 分层，天然就是平台需要的两块能力：

| 应用里的角色 | 在流程里的槽位 | 说明 |
|---|---|---|
| Sensor | 拉取槽 | 读传感器，返回原始读数 |
| Model | 处理槽 | 滑动平均、统计指标，纯计算、不碰界面 |
| View | 展示槽 | 画曲线（PNG）、出指标，同一份渲染代码供 Tk 与平台使用 |
| Controller | 不属于槽位 | 只负责把 Model 和 View 连起来，桌面入口用 |

所以接入不需要给应用加任何“平台适配层”，只要挑出 Sensor、Model、View 的公开方法。

## 目录

```
desktop-mvc/
├── app/                     # 桌面应用；登记成两个工程（见下表）
│   └── desktop_app/
│       ├── source.py        # 采集层：Sensor.read(points, seed)
│       ├── model.py         # Model：smooth / summary
│       ├── view.py          # View：chart / metrics / table / text / render_png
│       ├── controller.py    # Controller：reload()
│       └── __main__.py      # Tkinter 窗口入口
├── rules/
│   └── desktop_rules.py     # 判定：Rules.overheating(series)
└── workflow.json            # 流程定义，导入即用
```

三个工程这样登记：

| 名称 | 目录 | 模块 | 用到的接口 |
|---|---|---|---|
| 数据拉取 | `examples/desktop-mvc/app` | `desktop_app.source` | `Sensor`、`Sensor.read` |
| 桌面应用 | `examples/desktop-mvc/app` | `desktop_app.model` | `Model`、`Model.smooth`、`Model.summary`、`View`、`View.chart`、`View.metrics` |
| 判定规则 | `examples/desktop-mvc/rules` | `desktop_rules` | `Rules`、`Rules.overheating` |

工程登记的是「目录 + 模块」，不是目录本身：桌面应用和采集模块共用 `app` 目录，靠模块名区分。处理槽用 `desktop_app.model`，展示槽把模块改成 `desktop_app.view`；拉取槽是 `desktop_app.source`。

采集模块留在应用里，是因为桌面端本来就要读传感器；登记成独立的「数据拉取」工程，只是让流程的拉取槽有个明确落点。判定规则不属于应用，所以单独一个目录。

## 本地跑桌面应用

```bash
cd examples/desktop-mvc/app
"C:/Users/60273/AppData/Local/Programs/Python/Python39/python.exe" -m desktop_app
```

窗口里是 `Controller.reload()` 的结果：曲线（View 渲染的 PNG）、指标文字、读数表格。点「重新采样」重新生成。

注意：项目自带的 `backend/.venv` 是托管解释器，裁掉了 Tkinter；开窗口要用系统 Python（本机 3.9 自带 Tk 8.6）。**流程侧不需要 Tk**，平台只 import `desktop_app.model` 与 `desktop_app.view`，用哪个解释器都能跑。无显示环境只跑 `check_desktop_example.py`，不要启动窗口。

## 流程里怎么用

导入 `workflow.json`，登记上面三个工程，发布后启动，启动参数只有 `points`（默认 40）。

| 节点 | 槽位 | 配置 |
|---|---|---|
| 温度曲线检查 | 拉取 | `desktop_app.source` 的 `Sensor.read`，`points` 取启动输入，`seed` 固定 7 |
| | 处理 | `Model.smooth`，`series` 取上一槽，窗口 3 |
| | 展示 | `View.chart`，类型图片 |
| | 判定 | `Rules.overheating`，阈值 75（构造 `Rules` 时传入） |
| 统计指标复核 | 拉取 | 同参数再取一次，命中缓存 |
| | 处理 | `Model.summary` |
| | 展示 | `View.metrics`，类型指标 |
| | 判定 | 人工确认 |

曲线判定越线 → 指标复核 → 人工确认 → 结论「温度过高」→ 处置 → 问题解决；未越线走结论「温度正常」→ 上升。

## 平台侧与桌面侧的差别只有一处

`View.chart` 是给平台的出口：调用平台运行库把 PNG 存成内容寻址文件，返回摘要。桌面应用直接用 `View.render_png` 的字节喂 `tk.PhotoImage`。两者背后是同一个 `_chart()`，不存在两套画图逻辑。

## 自检

```bash
backend/.venv/Scripts/python scripts/check_desktop_example.py
```

会探测三个工程的真实接口、跑结构校验、用同一份 Model/View 本地算一遍，再把生成的 `flow.py` 真跑一次，确认图片与指标两个展示块都产出。
