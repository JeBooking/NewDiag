"""判定：峰值是否越线；返回 {result, reason}，平台据此选分支并留依据。"""

__all__ = ['Rules']


class Rules:
    def __init__(self, limit: float = 75.0):
        self.limit = float(limit)

    def overheating(self, series) -> dict:
        peak = max(row['celsius'] for row in series)
        ok = peak >= self.limit
        return {
            'result': ok,
            'reason': f"峰值 {peak}°C，阈值 {self.limit}°C",
        }
