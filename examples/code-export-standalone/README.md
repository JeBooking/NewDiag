# 无第三方依赖、无资源、无输入的代码导出示例

目的：查看平台实际导出的代码，并在只装有 Python 的机器上重新运行。业务代码不导入平台 SDK，只使用内置数值样本；不联网、不读数据文件、不需要启动参数或人工回复。

## 在平台中生成自己的导出包

1. 登记工程：名称 `代码导出演示`，目录填本目录下 project 文件夹的绝对路径，模块 `business`，工厂留空，解释器使用本机 Python。
2. 导入本目录 `workflow.json`，发布并启动；输入表单为空。
3. 打开实例详情，点击“导出代码”。
4. 将 ZIP 拷到另一目录或另一台有 Python 的机器上解压。

## 本地运行

在解压目录打开终端：

```powershell
python flow.py --mode serial
```

不需要 pip install，也不需要修改 configuration.json。仅需 Python 3.11 或更高版本。查看环境用 `python --version`。

程序应自动进入 `start → check → end`，输出 `samples: [1,2,8,3]`、`peak: 8`、`threshold: 5`、`result: yes` 及计算依据，全程不等待输入。

示例用平台现有“上升”节点结束，平台状态为“已上升”。当前运行库对未到达“问题解决”节点的流程返回退出码 1，因此本例结束时退出码 1 是预期，不代表 Python 异常。没有 Traceback 且输出了上述结果即完成示例；示例避免了需要人工确认的处置节点。

## 从哪里看代码

| 文件 | 内容 |
|---|---|
| `flow.py` | 平台生成的节点函数、分支和入口 |
| `modules/<工程标识>/business.py` | 原工程的 Analyzer 类，与本目录 project/business.py 相同 |
| `runtime/generated_runtime.py` | 终端运行与平台运行共用的执行库 |
| `runtime/composition_runtime.py` | 对象、参数、调用与展示映射 |
| `configuration.json` | 无输入的运行配置；本例无需修改 |
| `inputs.json` | 空数组，表示没有启动参数 |

运行时自动生成的 local-artifacts、module-configs 是本地结果及临时调用配置，不是需要预先准备的资源。可在任意有写入权限的新解压目录运行。

## 维护者重新生成现成包

在仓库根目录，使用已安装平台依赖的后端解释器：

```powershell
backend/.venv/Scripts/python examples/code-export-standalone/build_demo.py
```

脚本走真实登记、发布、启动和代码导出 API，产物为 `generated/standalone-code.zip`；生成过程需要平台依赖，解压后的运行不需要。脚本使用隔离数据库，不改当前平台数据。generated 目录不提交 Git，避免重复保存运行库和临时产物。

2026-11-12 已验证：通过真实平台 API 发布、自动执行并导出；在全新、不安装 pip 的 Python 3.13 虚拟环境中解压运行，关闭标准输入，全程无需输入，输出峰值 8、判断 yes，达到 end 节点，无 Traceback，退出码为预期的 1。
