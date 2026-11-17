"""MVC 的 View：只负责把数据画出来，不关心数据从哪来。

三种用法共用同一套渲染：
- 桌面应用：直接拿 render_png 的字节交给 Tk；
- 诊断流程：chart 把同一张图交给平台的内容寻址存储，返回摘要；
- metrics / table / text：桌面表格与平台展示块都用它。
"""

import struct
import zlib

__all__ = ['View']

BACKGROUND = (248, 250, 252)
GRID = (226, 232, 240)
LINE = (37, 99, 235)


class View:
    def __init__(self, width: int = 640, height: int = 220):
        self.width = max(120, int(width))
        self.height = max(80, int(height))

    # ---------------------------------------------------------------- 平台槽位

    def chart(self, series):
        """展示槽用：返回 PNG 摘要，平台按摘要取图。"""
        import node_sdk

        return node_sdk.save_file(self.render_png(series))

    def metrics(self, summary):
        unit = summary.get('unit', '')
        return [
            {'label': '峰值', 'value': summary['peak'], 'unit': unit},
            {'label': '均值', 'value': summary['average'], 'unit': unit},
            {'label': '最新', 'value': summary['last'], 'unit': unit},
            {'label': '点数', 'value': summary['count']},
        ]

    def table(self, series):
        return {
            'columns': ['采样点', '温度'],
            'rows': [[row['t'], row['celsius']] for row in series],
        }

    def text(self, summary):
        return (f"峰值 {summary['peak']}{summary['unit']}，"
                f"均值 {summary['average']}{summary['unit']}，"
                f"共 {summary['count']} 个采样点")

    # ---------------------------------------------------------------- 桌面应用

    def render_png(self, series) -> bytes:
        values = [float(row['celsius']) for row in series]
        return _chart(values, self.width, self.height)


def _chart(values, width: int, height: int) -> bytes:
    if not values:
        values = [0.0]
    pixels = [[BACKGROUND for _ in range(width)] for _ in range(height)]

    left, right, top, bottom = 28, width - 12, 14, height - 20
    for step in range(1, 4):
        y = top + (bottom - top) * step // 4
        for x in range(left, right):
            pixels[y][x] = GRID

    low, high = min(values), max(values)
    span = (high - low) or 1.0
    span_x = max(1, len(values) - 1)

    def point(index: int, value: float) -> tuple:
        x = left + (right - left) * index // span_x
        y = bottom - int((bottom - top) * (value - low) / span)
        return x, y

    previous = None
    for index, value in enumerate(values):
        current = point(index, value)
        if previous is not None:
            _line(pixels, previous, current, LINE)
        previous = current

    return _encode(width, height, pixels)


def _line(pixels, start, end, color) -> None:
    x0, y0 = start
    x1, y1 = end
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx - dy
    while True:
        for offset in (0, 1):
            y = y0 + offset
            if 0 <= y < len(pixels) and 0 <= x0 < len(pixels[0]):
                pixels[y][x0] = color
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * error
        if doubled > -dy:
            error -= dy
            x0 += sx
        if doubled < dx:
            error += dx
            y0 += sy


def _encode(width: int, height: int, pixels) -> bytes:
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for pixel in row:
            raw.extend(pixel)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (struct.pack('>I', len(payload)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(bytes(raw), 6))
            + chunk(b'IEND', b''))
