"""MVC 的 Model：只谈数据与计算，不碰界面。

桌面应用在本地窗口里用它；平台在处理槽里也用它，两边是同一份逻辑。
"""

__all__ = ['Model']


class Model:
    def __init__(self, unit: str = '°C'):
        self.unit = unit
        self.rows = []

    def load(self, series) -> int:
        """桌面应用用：把读数装进模型并返回条数。"""
        self.rows = list(series)
        return len(self.rows)

    def smooth(self, series, window: int = 3) -> list:
        """滑动平均；series 是上一槽（拉取）的返回值。"""
        rows = list(series)
        size = max(1, int(window))
        result = []
        for index, row in enumerate(rows):
            part = rows[max(0, index - size + 1):index + 1]
            average = sum(item['celsius'] for item in part) / len(part)
            result.append({'t': row['t'], 'celsius': round(average, 2)})
        return result

    def summary(self, series) -> dict:
        values = [row['celsius'] for row in series]
        return {
            'count': len(values),
            'peak': max(values),
            'average': round(sum(values) / len(values), 2),
            'last': values[-1],
            'unit': self.unit,
        }
