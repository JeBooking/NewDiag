"""判断模块：按阈值给出是／否与依据。

团队 C 维护。返回值是平台能直接消费的 result 与 reason，不含平台类型。
"""

__all__ = ['Judge']


class Judge:
    def __init__(self, limit: float = 5.0):
        self.limit = float(limit)

    def check(self, data):
        samples, channel = _pick(data)
        peak = max(samples)
        return {'result': peak > self.limit,
                'reason': f'{channel} 峰值 {peak}，阈值 {self.limit}'}

    def describe(self, data):
        samples, channel = _pick(data)
        return (f'通道 {channel}：{len(samples)} 个点，峰值 {max(samples)}，'
                f'均值 {round(sum(samples) / len(samples), 2)}，阈值 {self.limit}')


def _pick(data):
    if isinstance(data, dict):
        return list(data['samples']), data.get('channel', '信号')
    return list(data), '信号'
