"""泵组多通道诊断示例：分阶段填充 Data、按参数缓存频谱、失败只结束当前路径。

只使用标准库，部署方不需要额外安装 numpy / pandas / matplotlib。
"""

import json
import logging
import os
from pathlib import Path

from diagnosis_sdk import Data, Diagnosis, resource

__all__ = ["PumpDiagnosis"]
logger = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent


class PumpDiagnosis(Diagnosis):
    version = "1.0.0"

    def __init__(self, discharge_limit: float = 1.80, vibration_limit: float = 4.5,
                 bearing_limit: float = 80.0):
        self.discharge_limit = discharge_limit
        self.vibration_limit = vibration_limit
        self.bearing_limit = bearing_limit
        logger.info("创建泵组诊断对象 pid=%s；构造时不读取业务数据", os.getpid())

    # ------------------------------------------------------------ 启动准备
    def initialize(self, unit_id: str, time_point: str):
        self.unit_id = unit_id
        self.time_point = time_point
        logger.info("启动获取运行读数 unit=%s time=%s pid=%s", unit_id, time_point, os.getpid())
        records = json.loads((BASE / "readings.json").read_text(encoding="utf-8"))
        selected = next(
            (row for row in records
             if row["unit_id"] == unit_id and row["time_point"] == time_point),
            None,
        )
        if selected is None:
            raise ValueError(f"泵组 {unit_id} 在 {time_point} 没有运行读数")
        # 成员一：启动阶段填充，后续节点只读。
        self.dataset = Data(baseline=selected)
        logger.info("启动填充 Data 成员 baseline pid=%s", os.getpid())

    # ------------------------------------------------------------ 频谱资源
    @resource(version="1")
    def spectrum(self, unit_id: str, time_point: str, channel: str = "A", band: str = "hi"):
        """按设备、时间、通道、频段取频谱；同一参数组合只加载一次。"""
        logger.info("首次加载频谱 unit=%s time=%s channel=%s band=%s pid=%s",
                    unit_id, time_point, channel, band, os.getpid())
        records = json.loads((BASE / "spectra.json").read_text(encoding="utf-8"))
        selected = next(
            (row for row in records
             if row["unit_id"] == unit_id and row["time_point"] == time_point
             and row["channel"] == channel and row["band"] == band),
            None,
        )
        if selected is None:
            return None
        samples = [float(value) for value in selected["samples"]]
        return {"unit_id": unit_id, "time_point": time_point, "channel": channel,
                "band": band, "samples": samples,
                "energy": round(sum(value * value for value in samples) / max(len(samples), 1), 3)}

    # ------------------------------------------------------------ 振动分支
    def check_vibration(self):
        baseline = self.dataset.baseline
        rms = float(baseline["vibration_rms"])
        trend = self.spectrum(self.unit_id, self.time_point, channel="B", band="lo")
        if trend is None:
            raise ValueError("B 通道低频段频谱缺失，不能判断泵体振动")
        # 成员二：首个排查阶段填充；baseline 已在启动阶段固定。
        self.dataset.trend = {"rms": rms, "energy": trend["energy"]}
        logger.info("分阶段填充 Data 成员 trend pid=%s", os.getpid())
        return {"presentation": {"blocks": [
            {"type": "text", "text": f"泵组 {self.unit_id} 运行读数"},
            {"type": "line", "values": trend["samples"]},
            {"type": "metrics", "items": [
                {"label": "振动有效值", "value": rms, "unit": "mm/s"},
                {"label": "B 通道低频能量", "value": trend["energy"]},
            ]},
        ]}, "result": rms < self.vibration_limit,
            "reason": f"振动有效值 {rms} mm/s，阈值 {self.vibration_limit} mm/s"}

    def confirm_vibration(self):
        if "trend" not in self.dataset:
            raise ValueError("当前分支缺少启动阶段填充的 trend 数据")
        trend = self.dataset.trend
        again = self.spectrum(self.unit_id, self.time_point, channel="B", band="lo")
        if again is None:
            raise ValueError("B 通道低频段频谱缺失")
        logger.info("等待后继续，复用 Data.baseline + Data.trend pid=%s", os.getpid())
        return {"presentation": {"blocks": [
            {"type": "text", "text": f"等待人工后复测泵组 {self.unit_id}"},
            {"type": "line", "values": again["samples"]},
            {"type": "metrics", "items": [
                {"label": "等待前趋势能量", "value": trend["energy"]},
                {"label": "等待后缓存能量", "value": again["energy"]},
            ]},
        ]}, "result": again["energy"] <= trend["energy"] * 1.2,
            "reason": f"复测能量 {again['energy']} 与等待前 {trend['energy']} 比较（容差 20%）"}

    # ------------------------------------------------------------ 轴承分支
    def check_bearing(self, channel: str = "A"):
        baseline = self.dataset.baseline
        logger.info("轴承分支读取到 trend=%s pid=%s", "trend" in self.dataset, os.getpid())
        high = self.spectrum(self.unit_id, self.time_point, channel=channel, band="hi")
        low = self.spectrum(self.unit_id, self.time_point, channel=channel, band="lo")
        if high is None or low is None:
            raise ValueError(f"{channel} 通道频谱缺失，不能判断轴承状态")
        temperature = float(baseline["bearing_temp"])
        # 成员二（本分支）：轴承摘要；与启动阶段的 baseline 分两次落盘。
        self.dataset.bearing = {"channel": channel, "high": high["energy"],
                                "low": low["energy"], "temperature": temperature}
        logger.info("分阶段填充 Data 成员 bearing channel=%s pid=%s", channel, os.getpid())
        return {"presentation": {"blocks": [
            {"type": "text", "text": f"泵组 {self.unit_id} 轴承 {channel} 通道"},
            {"type": "metrics", "items": [
                {"label": "轴承温度", "value": temperature, "unit": "℃"},
                {"label": "高频能量", "value": high["energy"]},
                {"label": "低频能量", "value": low["energy"]},
            ]},
            {"type": "line", "values": high["samples"]},
        ]}, "result": temperature < self.bearing_limit,
            "reason": f"轴承温度 {temperature}℃，阈值 {self.bearing_limit}℃"}

    def review_bearing(self):
        """返回展示但不给判断，由平台生成人工判定待办。"""
        bearing = self.dataset.bearing
        value = self.spectrum(self.unit_id, self.time_point,
                              channel=bearing["channel"], band="hi")
        if value is None:
            raise ValueError("轴承高频频谱缺失")
        logger.info("返回展示，等待人工判断 pid=%s", os.getpid())
        return {"presentation": {"blocks": [
            {"type": "text", "text": "请结合现场声音人工判断轴承是否异常"},
            {"type": "metrics", "items": [
                {"label": "轴承温度", "value": bearing["temperature"], "unit": "℃"},
                {"label": "高频能量（缓存）", "value": value["energy"]},
                {"label": "通道", "value": bearing["channel"]},
            ]},
        ]}, "reason": "方法只提供展示，判断由人工完成"}

    def confirm_bearing(self):
        if "bearing" not in self.dataset:
            raise ValueError("当前分支缺少轴承摘要数据")
        bearing = self.dataset.bearing
        value = self.spectrum(self.unit_id, self.time_point,
                              channel=bearing["channel"], band="hi")
        if value is None:
            raise ValueError("轴承高频频谱缺失")
        logger.info("等待后继续，复用 Data.baseline + Data.bearing pid=%s", os.getpid())
        return {"presentation": {"blocks": [
            {"type": "text", "text": "人工判断后再次读取轴承频谱"},
            {"type": "metrics", "items": [
                {"label": "轴承温度", "value": bearing["temperature"], "unit": "℃"},
                {"label": "高频能量", "value": value["energy"]},
            ]},
        ]}, "result": bearing["temperature"] < self.bearing_limit,
            "reason": f"人工确认后轴承温度 {bearing['temperature']}℃，阈值 {self.bearing_limit}℃"}

    # ------------------------------------------------------------ 出口压力分支
    def check_discharge(self):
        baseline = self.dataset.baseline
        if "discharge_pressure" not in baseline:
            raise ValueError("运行读数缺少出口压力，不能带压力未知继续升压")
        pressure = float(baseline["discharge_pressure"])
        logger.info("检查出口压力 value=%s limit=%s pid=%s",
                    pressure, self.discharge_limit, os.getpid())
        return {"presentation": {"blocks": [
            {"type": "text", "text": f"泵组 {self.unit_id} 出口压力"},
            {"type": "metrics", "items": [
                {"label": "出口压力", "value": pressure, "unit": "MPa"},
                {"label": "保护阈值", "value": self.discharge_limit, "unit": "MPa"},
            ]},
        ]}, "result": pressure < self.discharge_limit,
            "reason": f"出口压力 {pressure} MPa，保护阈值 {self.discharge_limit} MPa"}

    def close(self):
        logger.info("释放泵组诊断对象 pid=%s", os.getpid())
