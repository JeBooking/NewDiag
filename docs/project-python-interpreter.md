# 工程登记里的「Python 解释器」字段：实际效果是什么

> 对应 `projects` 表的 `python` 字段（`models.py` 里 `python: Mapped[str | None]`）。
> 本文回答一个问题：登记工程时选一个 Python 解释器，它到底拿来干什么。

## 结论（一句话）

它指定「**用哪个 Python 解释器去 import 并执行这个工程里的函数**」，仅此而已，不参与别的逻辑。

## 实际生效的三处

| 时机 | 代码位置 | 干什么 |
|---|---|---|
| 接口发现（probe） | `projects.resolve_python()` / `signatures.py:247` | 用该解释器起子进程跑 `app/worker/probe.py`，`import` 工程模块、读函数签名 |
| 执行（进程树） | `runner._python()` | 用该解释器起子进程跑生成好的 `flow.py`，业务函数真正在这里被调用 |
| 遗留 scheduler | `sessions.py:169` | 起 worker 会话进程，同样用它 |

三处都是同一件事：**让业务工程的代码在它自己的解释器环境里跑**。

## 为什么必须单独指定解释器

业务工程通常有自己的虚拟环境和第三方依赖。如果拿平台自身的解释器（`backend/.venv`）去 import 工程模块，会 `ModuleNotFoundError`，或 import 到错误版本的依赖。所以登记时要显式指向**工程自己 venv 里的解释器**。

## 与相邻字段的分工（别混）

| 字段 | 职责 |
|---|---|
| `python` | 用**哪个解释器**跑 |
| `path` | 工程代码在磁盘的哪里（插进 `sys.path`） |
| `module` | import **哪个模块** |
| `factory` | 从模块里取**哪个类/函数**暴露成接口 |

四个字段各管一段：`python` 管「运行环境」，后三个管「定位代码」。互不替代。

## 为空时的行为

`python` 为空 → 全部 fallback 到 `sys.executable`，也就是**平台自身解释器**（`backend/.venv` 里那个）。代码里统一写 `getattr(project, "python", None) or sys.executable`。

## 一个已知约束

`runner._python()` 注释写明：**一个进程只能有一个解释器**，多工程时取 plan 里第一个非空工程的 `python`。所以多个工程若依赖不同解释器，执行进程只会用其中一个。
