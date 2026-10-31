"""冷却系统示例：平台只通过这里暴露的函数取数与判断，不直接碰设备。"""

# 真实工程里由数据服务器接口返回；示例用固定值，便于演示与回归。
PARAMETERS = {"voltage": 198.4, "temperature": 62.1, "fan_rpm": 1450}


def read_snapshot() -> dict:
    """返回一份采集快照。"""
    return dict(PARAMETERS)


def judge_voltage(voltage: float, threshold: float = 210.0) -> bool:
    """母线电压低于阈值判为异常。"""
    return float(voltage) < float(threshold)


def judge_temperature(temperature: float, limit: float = 70.0) -> bool:
    """冷却液温度超过上限判为异常。"""
    return float(temperature) > float(limit)
