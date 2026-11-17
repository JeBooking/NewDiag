"""数据源：从数据中心取已归档的数据，不连设备。

团队 A 维护。只依赖标准库，不知道平台存在。
"""

import math

__all__ = ['DataCenter']


class DataCenter:
    def __init__(self, dataset: str, device: str = ''):
        self.dataset = dataset
        self.device = device
        self.fetched = 0

    def catalog(self):
        """可用通道一览，给排查节点展示成表格。"""
        return {
            'columns': ['通道', '说明', '单位'],
            'rows': [
                ['vibration', '轴承振动加速度', 'mm/s'],
                ['temperature', '轴承温度', '℃'],
            ],
        }

    def fetch(self, channel: str, points: int = 24):
        """按通道取一段已归档数据；同一参数重复取不会变。"""
        self.fetched += 1
        size = max(4, int(points))
        base = 6.0 if channel == 'vibration' else 62.0
        samples = [round(base + base * 0.35 * math.sin(index / 3.0), 2) for index in range(size)]
        return {'dataset': self.dataset, 'device': self.device, 'channel': channel,
                'samples': samples, 'points': size, 'sequence': self.fetched}

    def summary(self):
        return {'dataset': self.dataset, 'device': self.device, 'fetched': self.fetched}
