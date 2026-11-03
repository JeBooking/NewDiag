"""模拟外部模块原有公共函数，不依赖平台 SDK 或适配器基类。"""

import os
from dataclasses import asdict

from external_module import DataArchive, RawReading, VoltageAnalyzer

__all__ = ["lookup_measurement", "decode_voltage", "describe_measurement", "is_under_voltage"]


def lookup_measurement(device_id: str, time_point: str):
    archive = DataArchive(
        records_file=os.environ.get("VOLTAGE_RECORDS", "data/readings.json"),
        channel=os.environ.get("VOLTAGE_CHANNEL", "bus"),
    )
    return asdict(archive.read_at(device_id, time_point))


def decode_voltage(payload):
    analyzer = VoltageAnalyzer(calibration=float(os.environ.get("VOLTAGE_CALIBRATION", "1")))
    return asdict(analyzer.parse(RawReading(**payload)))


def describe_measurement(measurement, result=None):
    text = f"实测电压：{measurement['voltage']:g} V；来源：{measurement['source']}"
    if result is not None:
        text += f"\n判断：{result['result']}；依据：{result['reason']}"
    return text


def is_under_voltage(value: float, limit: float = 210) -> bool:
    return value < limit
