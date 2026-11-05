# 文件上传与节点现场参数

一张图绑定 FileDiagnosis 类，一次执行保持一个逻辑实例。示例展示启动上传文件、到节点再填参数／上传，以及人工判断后恢复普通 self 成员。

## 登记工程并导入

1. 在「工程」登记名称 **现场参数示例**，路径填本目录的绝对路径，模块填 `diagnosis`，工厂留空，Python 选择可运行该工程的解释器。
2. 导入 [workflow.json](workflow.json)。点击画布空白处，可在「诊断实例」看到已选工程和 FileDiagnosis；自己的流程也从这里绑定。
3. 点击开始节点：report_path 的类型为「上传文件」，channels 为「数组」，options 为「对象」。
4. 点击现场排查节点：方法为 inspect，三个参数均选「执行到此节点时填写」，类型分别是数组、对象、上传文件。这些入口可以直接在页面修改。
5. 保存并发布，进入执行页。

## 执行过程

1. 启动页在 report_path 选择本目录的 [report.json](report.json)。channels 保留 `["A", "B"]`，options 保留 `{"mode":"offline","filters":[]}`。
2. 开始执行。平台将上传文件解析为服务器绝对路径字符串后调用 initialize；channels 收到 list，options 收到 dict。报告解析结果保存为普通 self.report。
3. 到达 inspect 后出现「填写参数」待办。samples 填 `[1, 2, 6, 3]`，thresholds 填 `{"max":3}`，evidence_path 上传 [evidence.json](evidence.json)。点「提交参数并执行」。
4. inspect 收到 list、dict、路径字符串，读取现场文件，保存 self.samples、self.thresholds、self.evidence、self.peak；页面显示曲线、指标和人工判断。
5. 判断选「是」。confirm 从恢复后的成员读取采样和阈值，显示「等待后保留 4 个采样值，峰值 6」，走异常上升分支；不重跑 initialize 或 inspect。

上传框不要求填写服务器目录。普通文本参数仍可用于已知服务器路径；上传引用由平台解析，客户端自填 path 不参与路径解析。

## 业务代码

[diagnosis.py](diagnosis.py) 是普通 Python 写法：

```python
def initialize(self, report_path: str, channels: list[str], options: dict):
    self.report = json.loads(Path(report_path).read_text(encoding='utf-8'))
    self.channels = channels
    self.options = options

def inspect(self, samples: list[float], thresholds: dict, evidence_path: str):
    # 业务规则由业务方法校验；平台负责数组、对象及上传引用的基本校验。
    ...
```

list/dict 注解会推荐对应控件；文件路径仍可写 str，在页面把该参数类型改为「上传文件」即可。
数组／对象按 JSON 填写，支持嵌套，Python 接收原结构，不用再次 json.loads。若入口允许不同顶层结构，可选「JSON（任意结构）」。
`[]`、`{}` 和 JSON 的 `null` 是有效值；本例 samples 在业务上要求非空，提交空数组会保留待办并显示错误，可修正后重新提交。

未选现场输入时，方法参数仍按固定来源、启动上下文、上游结果自动绑定；必填参数无来源时自动生成现场表单。
流程 JSON 的 runtimeInputs 用于保存页面配置，不需要业务类返回这段结构，也不要求把参数挪到 initialize。

更多说明见 [开发手册第 4 节](../../docs/diagnosis-development-guide.md#4-参数分别放在哪里)。
