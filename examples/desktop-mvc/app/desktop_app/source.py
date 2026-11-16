"""数据采集：模拟一个温度传感器，按点数生成可复现的读数。

它属于应用本身（桌面上要显示实时读数）；在诊断流程里单独登记成「数据拉取」工程，
只是为了让拉取槽有个明确的落点。
"""

__all__ = ['Sensor']


class Sensor:
    def __init__(self, base: float = 62.0, slope: float = 0.55):
        self.base = float(base)
        self.slope = float(slope)

    def read(self, points: int = 40, seed: int = 7) -> list:
        """返回 [{t, celsius}]；同参数同结果，平台侧命中缓存也不会失真。"""
        rows = []
        state = int(seed)
        for index in range(max(1, int(points))):
            # 线性同余造一点噪声，避免曲线像直尺；不用 random，保证可复现
            state = (state * 1103515245 + 12345) % (2 ** 31)
            noise = (state / 2 ** 31 - 0.5) * 1.6
            rows.append({"t": index, "celsius": round(self.base + self.slope * index + noise, 2)})
        return rows
