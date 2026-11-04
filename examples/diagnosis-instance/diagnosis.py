"""完整诊断对象：启动准备报告，后续方法首次需要波形时加载并缓存。"""

import json
import logging
import os
from pathlib import Path

from diagnosis_sdk import Diagnosis, resource

__all__ = ["MotorDiagnosis"]
logger = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent


class MotorDiagnosis(Diagnosis):
    version = "1.0.0"

    def __init__(self, voltage_limit: float = 210, waveform_limit: float = 5):
        self.voltage_limit = voltage_limit
        self.waveform_limit = waveform_limit
        logger.info("创建诊断对象 pid=%s；此处不获取、不解析业务数据", os.getpid())

    def initialize(self, device_id: str, time_point: str):
        logger.info("启动获取报告 device=%s time=%s", device_id, time_point)
        raw = (BASE / "measurements.json").read_text(encoding="utf-8")
        logger.info("启动解析报告")
        records = json.loads(raw)
        selected = next((row for row in records
                         if row["device_id"] == device_id and row["time_point"] == time_point), None)
        if selected is None:
            raise ValueError("设备在指定时间没有报告")
        self.data["report"] = selected

    @resource(version="1")
    def waveform(self, device_id: str, time_point: str, channel: str = "A", scale: float = 1):
        logger.info("首次获取波形 device=%s time=%s channel=%s scale=%s",
                    device_id, time_point, channel, scale)
        raw = (BASE / "waveforms.json").read_text(encoding="utf-8")
        logger.info("首次解析波形")
        records = json.loads(raw)
        selected = next((row for row in records if row["device_id"] == device_id
                         and row["time_point"] == time_point and row["channel"] == channel), None)
        if selected is None:
            return None
        return {"samples": [value * scale for value in selected["samples"]],
                "device_id": device_id, "time_point": time_point, "channel": channel}

    def check_voltage(self):
        report = self.data["report"]
        value = report["voltage"]
        return {"presentation": {"blocks": [{"type": "metrics", "items": [
            {"label": "母线电压", "value": value, "unit": "V"},
            {"label": "阈值", "value": self.voltage_limit, "unit": "V"},
        ]}]}, "result": value < self.voltage_limit,
            "reason": f"启动报告电压 {value}V，与阈值 {self.voltage_limit}V 比较"}

    def check_waveform(self, channel: str = "A", scale: float = 1):
        value = self.waveform(self.inputs["device_id"], self.inputs["time_point"], channel, scale)
        return self._waveform_result(value)

    def review_waveform(self):
        value = self.waveform(device_id=self.inputs["device_id"], time_point=self.inputs["time_point"])
        result = self._waveform_result(value)
        result.pop("result")
        result.pop("reason")
        return result

    def confirm_waveform(self):
        value = self.waveform(self.inputs["device_id"], self.inputs["time_point"])
        return self._waveform_result(value)

    def _waveform_result(self, value):
        if value is None:
            raise ValueError("指定设备、时间、通道没有波形，不能当作正常判断")
        peak = max(value["samples"])
        return {"presentation": {"blocks": [
            {"type": "text", "text": f"设备 {value['device_id']}，时间 {value['time_point']}，通道 {value['channel']}"},
            {"type": "line", "values": value["samples"]},
            {"type": "metrics", "items": [{"label": "峰值", "value": peak}]},
        ]}, "result": peak > self.waveform_limit,
            "reason": f"波形峰值 {peak}，阈值 {self.waveform_limit}"}

    def close(self):
        logger.info("释放诊断对象 pid=%s", os.getpid())
