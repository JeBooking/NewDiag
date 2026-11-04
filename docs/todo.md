# TODO

## 失败原因区分（2026-11-04 提出，待拍板）

**诉求**：把「程序执行错误」和「诊断没找到原因」分开。

**核实结论**：状态层其实已分开——没找到原因走 escalation 节点、落到 `escalated`；只有业务函数抛异常才落到 `failed`（`scheduler.py:1049` 的 `_refresh`）。真正混在一起的是 `failed` 这个桶，里面装了四类原因：

| error 前缀 | 含义 |
|---|---|
| `execution/*` | 业务函数真的报错 |
| `route/*` | 流程走不下去（返回值判不了是/否、分支没有后继） |
| `plan/*` | 计划层问题 |
| `signature/*` | 函数签名变了 |

run 层用前缀区分得很清楚（`scheduler.py:34-40`），但实例层和 UI 完全没暴露，界面统一显示「失败」。

**建议方案**（tech-lead）：给实例加可空字段 `failure_kind`（`execution` / `route` / `plan` / `signature` / `unknown`），从 run 的 error 前缀聚合，状态枚举不动。

- 影响面：`models.py:82` 加列、`schemas.py` Detail 加 `failureKind`、`InstanceList.vue:16` / `InstanceDetail.vue:29` 补标签、`docs/spec.md:106` 与 `:417-419` 各加一句
- 迁移：老数据可从 `runs.error` 前缀反向回填；不回填也不影响状态、筛选、回退，只显示「原因未知」
- 已否掉：新增 `error` / `executionError` 状态——与 `failed` 语义重叠，要动终态集与终止/回退白名单，且历史 `failed` 无法区分，统计断层

**关联问题（同源，需一并拍板）**：收敛优先级 `success > escalated > failed`（`scheduler.py:1049` 的 `_refresh`）。一条路径报错、另一条 success 时，实例最终算 `success`，报错被静默吞掉。建议不动收敛规则，只在 payload 带 `hasExecutionError`，详情页提示「某路径执行出错但整体已上升」。

**状态**：未开工。等用户决定是否做、做哪一档。
