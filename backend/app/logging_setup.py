"""日志装配：控制台 + 按大小轮转的文件。

环境变量：
- NEWDIAG_LOG_LEVEL：DEBUG / INFO / WARNING，默认 INFO
- NEWDIAG_LOG_DIR：日志目录，默认 backend/logs
- NEWDIAG_LOG_PAYLOAD：设为 1 时把调用参数与返回值写进 DEBUG 日志（默认只记字段名与长度）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = BACKEND_ROOT / 'logs'
_FORMAT = '%(asctime)s %(levelname)-5s [%(name)s] %(message)s'


def log_payloads() -> bool:
    return os.environ.get('NEWDIAG_LOG_PAYLOAD', '').strip() in {'1', 'true', 'yes'}


def brief(value: object, limit: int = 300) -> str:
    """把参数/返回值压成一行；默认不泄露完整内容。"""
    if log_payloads():
        try:
            text = json.dumps(value, ensure_ascii=False, default=repr)
        except (TypeError, ValueError):
            text = repr(value)
        return text[:2000]
    if isinstance(value, dict):
        return 'keys=' + ','.join(map(str, value.keys()))[:limit]
    if isinstance(value, (list, tuple)):
        return f'list(len={len(value)})'
    return type(value).__name__


def setup_logging() -> None:
    level_name = os.environ.get('NEWDIAG_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    log_dir = Path(os.environ.get('NEWDIAG_LOG_DIR') or DEFAULT_LOG_DIR)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = DEFAULT_LOG_DIR

    root = logging.getLogger()
    root.setLevel(level)

    # 重复 import / reload 时只调级别，不叠加 handler
    if getattr(root, '_newdiag_configured', False):
        for handler in root.handlers:
            handler.setLevel(level)
        return

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)

    file_handler = RotatingFileHandler(
        log_dir / 'app.log', maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    root.addHandler(console)
    root.addHandler(file_handler)
    root._newdiag_configured = True  # type: ignore[attr-defined]

    # uvicorn 自己的 logger 不向上冒泡，单独接一份到同一文件
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers = [console, file_handler]
        logger.propagate = False

    # 访问日志由 app.main 的中间件统一输出（带 request id 与耗时），关掉 uvicorn 那份避免重复
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)

    logging.getLogger(__name__).info('日志级别 %s，文件 %s', level_name, file_handler.baseFilename)
