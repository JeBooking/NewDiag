"""内容寻址文件存储：backend/data/files/{hash[:2]}/{hash}。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from app.db import data_dir

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
# 统一的超限文案。前端用同一句，改这里要同步改 src/services/files.ts。
UPLOAD_TOO_LARGE_DETAIL = "文件超过 5 GB 上限"
# 临时文件固定后缀，启动清扫靠它定位；随机名避免并发上传互踩。
TEMP_SUFFIX = ".part"
TEMP_MAX_AGE_SECONDS = 24 * 3600

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class UploadTooLarge(Exception):
    """上传体超过 MAX_UPLOAD_BYTES。"""


def files_dir() -> Path:
    root = data_dir() / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def temp_dir() -> Path:
    root = files_dir() / ".tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_temp_path() -> Path:
    """随机临时名：流式上传读完才知道摘要，用摘要命名会让并发写同一个临时文件。"""
    return temp_dir() / f"{uuid.uuid4().hex}{TEMP_SUFFIX}"


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("删除临时文件失败：%s", path, exc_info=True)


def is_digest(value: str) -> bool:
    return bool(_HASH_RE.match(value))


def object_path(digest: str) -> Path:
    return files_dir() / digest[:2] / digest


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commit_temp(temp: Path, digest: str) -> Path:
    """把已写完的临时文件原子提交到内容寻址路径。"""
    target = object_path(digest)
    if target.is_file():
        # 同内容并发上传：已有对象直接复用，别覆盖
        _unlink_quiet(temp)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp, target)
    return target


def store_stream(fileobj: BinaryIO, limit: int = MAX_UPLOAD_BYTES) -> tuple[str, Path, int]:
    """边读边算摘要边落盘，不把整个文件留在内存。

    超限或任何异常都会删掉临时文件后抛出，最终路径只会出现完整对象。
    """
    hasher = hashlib.sha256()
    total = 0
    temp = new_temp_path()
    try:
        with temp.open("wb") as out:
            while True:
                chunk = fileobj.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UploadTooLarge
                hasher.update(chunk)
                out.write(chunk)
        digest = hasher.hexdigest()
        return digest, commit_temp(temp, digest), total
    except BaseException:
        _unlink_quiet(temp)
        raise


def store(data: bytes) -> tuple[str, Path]:
    digest = sha256_hex(data)
    target = object_path(digest)
    if target.exists():
        return digest, target
    temp = new_temp_path()
    try:
        temp.write_bytes(data)
        return digest, commit_temp(temp, digest)
    except BaseException:
        _unlink_quiet(temp)
        raise


def resolve(digest: str) -> Path | None:
    if not is_digest(digest):
        return None
    target = object_path(digest)
    return target if target.is_file() else None


def cleanup_temp_files(max_age_seconds: int = TEMP_MAX_AGE_SECONDS) -> int:
    """启动清扫：进程在上传中途退出会留下 .part，超过时限的删掉。"""
    root = temp_dir()
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in root.glob(f"*{TEMP_SUFFIX}"):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("清理残留临时文件失败：%s", path, exc_info=True)
    if removed:
        logger.info("启动清理：删除 %d 个残留上传临时文件", removed)
    return removed
