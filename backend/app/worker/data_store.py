"""模块持有的一次写入数据；历史版本复用已保存成员。"""

from __future__ import annotations

from collections.abc import Mapping


class FrozenMap(Mapping):
    def __init__(self, values):
        self._values = {key: freeze(value) for key, value in values.items()}

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def freeze(value):
    if isinstance(value, dict):
        return FrozenMap(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    return value


class Data:
    """缺少成员表示尚未取得；None、零、空序列均为已取得的有效值。

    自定义数据类型由模块保证只读。保存引用由容器内部管理，
    业务代码无需知道平台的数据库和产物目录。
    """

    def __init__(self, **values):
        object.__setattr__(self, "_values", {})
        object.__setattr__(self, "_saved", {})
        for name, value in values.items():
            setattr(self, name, value)

    def __getattr__(self, name):
        values = object.__getattribute__(self, "_values")
        if name not in values:
            raise AttributeError(name)
        return values[name]

    def __setattr__(self, name, value):
        if name.startswith("_") or hasattr(type(self), name) or name in self._values:
            raise AttributeError(f"数据成员 {name} 已固定，请为另一种数据使用新成员")
        self._values[name] = freeze(value)

    def __delattr__(self, name):
        raise AttributeError("已取得的数据不能删除")

    def __contains__(self, name):
        return name in self._values

    def export(self, root, save):
        for name, value in self._values.items():
            if name not in self._saved:
                self._saved[name] = save(root, value)
        return dict(self._saved)

    @classmethod
    def restore(cls, root, references, load):
        result = cls()
        result._saved.update(references)
        result._values.update({name: load(root, ref) for name, ref in references.items()})
        return result
