"""纯标准库演示：固定样本来自代码，不访问文件、网络或用户输入。"""

__all__ = ['Analyzer']


class Analyzer:
    def inspect(self):
        samples = [1, 2, 8, 3]
        threshold = 5
        peak = max(samples)
        return {
            'samples': samples,
            'peak': peak,
            'threshold': threshold,
            'abnormal': peak > threshold,
            'reason': f'内置样本峰值 {peak}，固定阈值 {threshold}，计算完成',
        }
