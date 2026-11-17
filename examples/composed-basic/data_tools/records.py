"""普通数据模块，可在任何 Python 程序中独立使用。"""
import json
from pathlib import Path

__all__ = ['Dataset']

class Dataset:
    def __init__(self, values: list):
        if not values or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in values):
            raise ValueError('需要非空数值列表')
        self.samples = tuple(values)

    @classmethod
    def from_file(cls, path: str):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    def series(self, scale: float = 1):
        return [value * scale for value in self.samples]
