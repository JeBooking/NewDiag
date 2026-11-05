"""启动上传报告，走到排查节点再上传证据并填写数组、对象参数。"""

import json
import logging
from pathlib import Path

from diagnosis_sdk import Diagnosis

__all__ = ['FileDiagnosis']
logger = logging.getLogger(__name__)


class FileDiagnosis(Diagnosis):
    version = '1.0.0'

    def initialize(self, report_path: str, channels: list[str], options: dict):
        self.report = json.loads(Path(report_path).read_text(encoding='utf-8'))
        self.channels = channels
        self.options = options
        logger.info('初始化报告：%s；通道收到 %s；选项收到 %s', report_path, type(channels).__name__, type(options).__name__)

    def inspect(self, samples: list[float], thresholds: dict, evidence_path: str):
        if not samples:
            raise ValueError('至少提供一个采样值')
        self.samples = samples
        self.thresholds = thresholds
        self.evidence = json.loads(Path(evidence_path).read_text(encoding='utf-8'))
        self.peak = max(samples)
        logger.info('执行现场参数排查：samples=%s thresholds=%s evidence=%s', samples, thresholds, evidence_path)
        return {
            'presentation': {'blocks': [
                {'type': 'text', 'text': f"报告设备 {self.report['device_id']}；证据 {self.evidence['note']}"},
                {'type': 'line', 'values': samples},
                {'type': 'metrics', 'items': [
                    {'label': '通道数量', 'value': len(self.channels)},
                    {'label': '采样峰值', 'value': self.peak},
                ]},
            ]},
            'result': None,
        }

    def confirm(self):
        return {
            'presentation': {'blocks': [
                {'type': 'text', 'text': f'等待后保留 {len(self.samples)} 个采样值，峰值 {self.peak}'},
            ]},
            'result': self.peak > self.thresholds.get('max', 3),
            'reason': f"峰值 {self.peak}，阈值 {self.thresholds.get('max', 3)}",
            'data': {'channels': self.channels, 'samples': self.samples, 'options': self.options},
        }
