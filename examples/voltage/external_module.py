"""模拟已有业务模块：不依赖平台 SDK，桌面端和平台共用分析核心。"""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RawReading:
    raw_value: float
    unit: str
    source: str


@dataclass(frozen=True)
class ParsedVoltage:
    voltage: float
    source: str


@dataclass(frozen=True)
class Assessment:
    abnormal: bool
    explanation: str


class DataArchive:
    def __init__(self, records_file: str, channel: str):
        if not channel.strip():
            raise ValueError("channel 不能为空")
        self.records_file = Path(records_file)
        self.channel = channel

    def read_at(self, device_id: str, time_point: str) -> RawReading:
        requested = datetime.fromisoformat(time_point)
        if requested.tzinfo is None:
            raise ValueError("time_point 必须包含时区")
        with self.records_file.open(encoding="utf-8") as stream:
            records = json.load(stream)
        matched = [
            item for item in records
            if item["device_id"] == device_id
            and item["channel"] == self.channel
            and datetime.fromisoformat(item["time_point"]) == requested
        ]
        if len(matched) != 1:
            raise ValueError(f"指定设备、时间和通道应对应一条数据，实际 {len(matched)} 条")
        item = matched[0]
        return RawReading(item["raw_value"], item["unit"], item["source"])


class VoltageAnalyzer:
    def __init__(self, calibration: float):
        if isinstance(calibration, bool) or not isinstance(calibration, (int, float)):
            raise ValueError("calibration 必须为数字")
        if not math.isfinite(calibration) or calibration <= 0:
            raise ValueError("calibration 必须是大于 0 的有限数值")
        self.calibration = calibration

    def parse(self, reading: RawReading) -> ParsedVoltage:
        value = reading.raw_value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("原始电压必须为数字")
        if not math.isfinite(value) or value < 0:
            raise ValueError("原始电压必须为非负有限数值")
        factors = {"mV": 0.001, "V": 1.0}
        if reading.unit not in factors:
            raise ValueError("仅支持 mV 或 V，不推测未知单位")
        if not isinstance(reading.source, str) or not reading.source.strip():
            raise ValueError("必须提供数据来源")
        return ParsedVoltage(value * factors[reading.unit] * self.calibration, reading.source)

    def assess(self, reading: ParsedVoltage, threshold: float) -> Assessment:
        return Assessment(
            abnormal=reading.voltage < threshold,
            explanation=f"校准后电压 {reading.voltage:g} V；不足阈值 {threshold:g} V",
        )


class DesktopApplication:
    """窗口和控制器只属于桌面功能，分析核心不反向依赖它们。"""

    def __init__(self, archive, analyzer, window, controller):
        self.archive = archive
        self.analyzer = analyzer
        self.window = window
        self.controller = controller

    def refresh(self, device_id: str, time_point: str):
        reading = self.analyzer.parse(self.archive.read_at(device_id, time_point))
        self.window.show_voltage(reading.voltage)

    def set_device_output(self, value: float):
        self.controller.set_output(value)
