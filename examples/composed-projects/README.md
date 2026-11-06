# 三工程组合诊断

无需在业务项目中新建适配器或总控诊断类。登记工程后导入 workflow.json（启动获取解析）或 workflow-lazy.json（首次使用获取解析）。

配置与运行环境说明见 ../../docs/composition-integration.md。

两份示例直接调用现有 DataClient、Dataset、save_plot 和 JudgeEngine。原项目保持原样；PlotEngine 当前需要可导入 Tk 与 matplotlib 的环境。

尚未运行三工程示例；推送前后端现有回归为 `145 passed`，详情见接入文档。
