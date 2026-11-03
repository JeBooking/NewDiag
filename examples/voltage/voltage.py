"""四阶段接入示例：同一解析和展示，组合自动或人工采集、判断。"""

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from node_sdk import InvestigationNode

__all__ = ["ManualVoltage", "AutomaticAcquisitionVoltage", "AutomaticJudgmentVoltage", "AutomaticVoltage"]


class ManualVoltage(InvestigationNode):
    version = "1.0.0"
    inputs = [
        {"name": "device_id", "type": "text", "required": True},
        {"name": "time_point", "type": "datetime", "required": True},
    ]
    manual_fields = [
        {"name": "voltage", "label": "母线电压", "type": "number", "unit": "V", "min": 0},
        {"name": "source", "label": "数据来源", "type": "text",
         "description": "填写测量仪表、报告编号或原桌面工具名称"},
    ]

    def __init__(self, threshold: float = 210):
        self.threshold = threshold

    def parse(self, raw_data):
        voltage = raw_data["voltage"]
        if isinstance(voltage, bool) or not isinstance(voltage, (int, float)) or voltage < 0:
            raise ValueError("电压必须是非负数字，单位 V")
        return {"voltage": voltage, "source": raw_data.get("source", "数据中心")}

    def present(self, parsed_data, decision=None):
        blocks = [
            {"type": "metrics", "title": "母线电压", "items": [
                {"label": "实测值", "value": parsed_data["voltage"], "unit": "V"},
                {"label": "不足阈值", "value": self.threshold, "unit": "V"},
            ]},
            {"type": "text", "text": "节点问题：电压是否不足？低于阈值选择「是」。"},
        ]
        if decision is not None:
            blocks.append({"type": "text", "title": "判断依据", "text": decision["reason"]})
        return {"blocks": blocks}


class AutomaticAcquisitionVoltage(ManualVoltage):
    def __init__(self, data_url: str, threshold: float = 210, token_env: str = ""):
        super().__init__(threshold)
        self.data_url = data_url
        self.token_env = token_env

    def acquire(self, device_id: str, time_point: str):
        query = urlencode({"deviceId": device_id, "timePoint": time_point})
        headers = {}
        if self.token_env:
            headers["Authorization"] = f"Bearer {os.environ[self.token_env]}"
        separator = "&" if "?" in self.data_url else "?"
        request = Request(f"{self.data_url}{separator}{query}", headers=headers)
        with urlopen(request, timeout=20) as response:
            return json.load(response)


class AutomaticJudgmentVoltage(ManualVoltage):
    def judge(self, parsed_data):
        voltage = parsed_data["voltage"]
        result = "yes" if voltage < self.threshold else "no"
        return {"result": result, "reason": f"电压 {voltage} V，判定阈值 {self.threshold} V"}


class AutomaticVoltage(AutomaticAcquisitionVoltage, AutomaticJudgmentVoltage):
    pass
