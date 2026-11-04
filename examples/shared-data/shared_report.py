"""一次准备报告，电压、电流排查共享解析结果。"""

import json
from pathlib import Path


def locate_report(filename: str = "report.json") -> str:
    print("获取报告路径")
    return str(Path(filename).resolve())


def decode_report(filename: str) -> dict:
    print("解析报告一次")
    with Path(filename).open(encoding="utf-8") as source:
        return json.load(source)


def describe_value(value: float, decision=None) -> dict:
    return {"blocks": [{"type": "metrics", "items": [{"label": "测量值", "value": value}]}]}


def above_limit(value: float, limit: float) -> dict:
    return {"result": "yes" if value > limit else "no", "reason": f"测量值 {value}，上限 {limit}"}
