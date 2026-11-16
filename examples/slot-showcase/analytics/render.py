"""处理与展示：把数据加工成曲线、指标、表格和图片。

团队 B 维护。只有画图需要平台运行库提供的 node_sdk，其余都是普通计算。
"""

import struct
import zlib

__all__ = ['Processor', 'Renderer']


class Processor:
    def smooth(self, raw, window: int = 3):
        """滑动平均；raw 是上一槽的返回值。"""
        samples = raw['samples']
        size = max(1, int(window))
        result = []
        for index in range(len(samples)):
            part = samples[max(0, index - size + 1):index + 1]
            result.append(round(sum(part) / len(part), 2))
        return {**raw, 'samples': result, 'window': size}


class Renderer:
    def line(self, data):
        return data['samples']

    def metrics(self, data):
        samples = data['samples']
        return [
            {'label': '通道', 'value': data['channel']},
            {'label': '峰值', 'value': max(samples), 'unit': 'mm/s'},
            {'label': '均值', 'value': round(sum(samples) / len(samples), 2), 'unit': 'mm/s'},
            {'label': '点数', 'value': len(samples)},
        ]

    def table(self, data):
        return {'columns': data['columns'], 'rows': data['rows']}

    def png(self, data):
        """把曲线画成 PNG，交给平台的内容寻址存储；返回 64 位摘要。"""
        import node_sdk

        return node_sdk.save_file(_chart(data['samples']))


def _chart(samples, width: int = 240, height: int = 96):
    """最小 PNG 编码：不引第三方库，够演示图片展示即可。"""
    low, high = min(samples), max(samples)
    span = (high - low) or 1.0
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend((28, 30, 36))
        rows.append(row)
    step = max(1, (width - 20) // max(1, len(samples) - 1))
    previous = None
    for index, value in enumerate(samples):
        x = 10 + index * step
        y = height - 10 - int((value - low) / span * (height - 20))
        if previous is not None:
            _line(rows, previous, (x, y))
        previous = (x, y)
    raw = b''.join(b'\x00' + bytes(row) for row in rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def _line(rows, start, end):
    (x0, y0), (x1, y1) = start, end
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for step in range(steps + 1):
        x = x0 + (x1 - x0) * step // steps
        y = y0 + (y1 - y0) * step // steps
        if 0 <= y < len(rows) and 0 <= x < len(rows[0]) // 3:
            rows[y][x * 3:x * 3 + 3] = bytes((90, 200, 160))
