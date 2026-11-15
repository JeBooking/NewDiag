"""旧业务类协议的兼容入口。

平台只按组合配置执行：对象、固定资源和排查操作都在流程里声明，
不再读取 `Diagnosis` 子类、`@resource` 方法或诊断实例状态。

保留这三个名字是为了让已经写好 `from diagnosis_sdk import ...` 的业务模块
仍能被 import（探针和运行都会加载原模块）；它们不参与任何调度。
"""

from __future__ import annotations

if __package__:
    from .data_store import Data
else:  # 作为顶层模块加载时（子进程里的业务工程）
    from data_store import Data  # type: ignore[no-redef]

__all__ = ["Data", "Diagnosis", "resource"]


class Diagnosis:
    """旧业务类的基类；现在只是一个普通父类，没有任何平台语义。"""

    version = "1.0.0"


def resource(*, version="1"):
    """旧装饰器；原样返回函数，不注册缓存，也不改变调用方式。"""

    def decorate(function):
        return function

    return decorate
