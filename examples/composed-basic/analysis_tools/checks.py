"""普通判断模块；返回自己的业务对象，不依赖平台 SDK。"""
from dataclasses import dataclass

__all__ = ['ThresholdRules', 'Verdict', 'create_rules']

@dataclass(frozen=True)
class Verdict:
    exceeded: bool
    explanation: str
    peak: float

class ThresholdRules:
    def __init__(self, limit: float = 5):
        self.limit = limit

    def evaluate(self, samples: list):
        peak = max(samples)
        return Verdict(peak > self.limit, f'峰值 {peak}，阈值 {self.limit}', peak)

def create_rules(limit: float = 5):
    return ThresholdRules(limit)
