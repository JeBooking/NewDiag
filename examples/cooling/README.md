# 冷却系统诊断示例

一个可导入 NewDiag 平台的示例工程：诊断「设备超温」问题，超温后**并列**排查滤网（风量）与风机（转速）两条思路，各自走结论 → 处置 → 解决；未超温记录正常。

## 结构

- `workflow.json`：诊断流程图文档，在流程页用「导入」进设计页
- `cooling/diagnosis.py`：业务判断函数 `is_overheated` / `filter_is_blocked` / `fan_is_faulty`
- `assets/limits.csv`：阈值（70°C / 0.6 m³/s / 1000 rpm），诊断函数运行时读取

## 试用

1. 工程页「登记工程」：
   - 名称：`冷却系统`
   - 路径：本目录的绝对路径
   - 模块：`cooling.diagnosis`
2. 流程页「导入」：选择本目录的 `workflow.json`
3. 版本页「发布」，再「执行」，启动输入例如：
   - 风机故障：`{"temperature": 85, "airflow": 1.0, "fan_rpm": 300}`
   - 滤网堵塞：`{"temperature": 85, "airflow": 0.18, "fan_rpm": 1800}`
   - 正常：`{"temperature": 42, "airflow": 1.0, "fan_rpm": 1800}`
