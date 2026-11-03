"""只在适配层对齐接口，保留外部业务类、构造参数和数据对象。"""

import math
from dataclasses import asdict

from external_module import DataArchive, ParsedVoltage, RawReading, VoltageAnalyzer
from voltage import ManualVoltage

__all__ = [
    "ClassBasedManualVoltage",
    "ClassBasedAcquisitionVoltage",
    "ClassBasedJudgmentVoltage",
    "ClassBasedAutomaticVoltage",
]


class _ObjectVoltageBase(ManualVoltage):
    version = "1.0.0"
    manual_fields = [
        {"name": "raw_value", "label": "电压原始读数", "type": "number", "min": 0},
        {"name": "unit", "label": "原始单位", "type": "text", "default": "mV",
         "description": "填写 mV 或 V；例如 205000 mV 等于 205 V"},
        {"name": "source", "label": "数据来源", "type": "text"},
    ]

    def __init__(self, calibration: float, threshold: float = 210):
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("threshold 必须为数字")
        if not math.isfinite(threshold) or threshold <= 0:
            raise ValueError("threshold 必须是大于 0 的有限数值")
        super().__init__(threshold)
        self.calibration = calibration
        self._analyzer = None

    def _get_analyzer(self):
        # 避免接口探测或创建待办时初始化外部业务资源。
        if self._analyzer is None:
            self._analyzer = VoltageAnalyzer(calibration=self.calibration)
        return self._analyzer

    def parse(self, raw_data):
        reading = RawReading(**raw_data) if isinstance(raw_data, dict) else raw_data
        return self._get_analyzer().parse(reading)

    def present(self, parsed_data, decision=None):
        return super().present(asdict(parsed_data), decision)

    def dump_raw(self, value):
        return asdict(value) if isinstance(value, RawReading) else value

    def dump_parsed(self, value):
        return asdict(value)

    def load_parsed(self, value):
        return ParsedVoltage(**value)


class ClassBasedManualVoltage(_ObjectVoltageBase):
    pass


class ClassBasedJudgmentVoltage(_ObjectVoltageBase):
    def judge(self, parsed_data):
        assessment = self._get_analyzer().assess(parsed_data, self.threshold)
        return {
            "result": "yes" if assessment.abnormal else "no",
            "reason": assessment.explanation,
        }


class ClassBasedAcquisitionVoltage(_ObjectVoltageBase):
    def __init__(
        self, records_file: str, channel: str, calibration: float, threshold: float = 210,
    ):
        super().__init__(calibration, threshold)
        self.records_file = records_file
        self.channel = channel
        self._archive = None

    def acquire(self, device_id: str, time_point: str):
        if self._archive is None:
            self._archive = DataArchive(records_file=self.records_file, channel=self.channel)
        return self._archive.read_at(device_id, time_point)


class ClassBasedAutomaticVoltage(ClassBasedAcquisitionVoltage, ClassBasedJudgmentVoltage):
    pass
