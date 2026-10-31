"""冷却系统诊断示例：平台只通过这里暴露的判断函数取数与判断，不直接碰设备。

诊断逻辑对齐旧项目 cooling_project/workflow.json：
超温后并列排查滤网（风量）与风机（转速）两条思路。阈值来自 assets/limits.csv，
三个判断函数都是无状态纯函数，参数由平台从启动快照绑定，返回布尔值供是/否分支。
"""

import csv
from pathlib import Path

_ASSETS = Path(__file__).resolve().parents[1] / "assets"

with (_ASSETS / "limits.csv").open(encoding="utf-8-sig", newline="") as _file:
    _limits = {row["name"]: float(row["value"]) for row in csv.DictReader(_file)}


def is_overheated(temperature: float) -> bool:
    """初始温度超过 max_temperature 判为超温。"""
    return float(temperature) > _limits["max_temperature"]


def filter_is_blocked(airflow: float) -> bool:
    """风量低于 min_airflow 判为滤网堵塞。"""
    return float(airflow) < _limits["min_airflow"]


def fan_is_faulty(fan_rpm: float) -> bool:
    """风机转速低于 min_fan_rpm 判为异常。"""
    return float(fan_rpm) < _limits["min_fan_rpm"]
