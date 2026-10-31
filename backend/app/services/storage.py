"""内容寻址文件存储：backend/data/files/{hash[:2]}/{hash}。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from app.db import data_dir

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def files_dir() -> Path:
    root = data_dir() / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_digest(value: str) -> bool:
    return bool(_HASH_RE.match(value))


def object_path(digest: str) -> Path:
    return files_dir() / digest[:2] / digest


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store(data: bytes) -> tuple[str, Path]:
    digest = sha256_hex(data)
    target = object_path(digest)
    if target.exists():
        return digest, target
    target.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再改名，避免半个文件被当成命中
    temp = target.with_name(f"{digest}.part")
    temp.write_bytes(data)
    os.replace(temp, target)
    return digest, target


def resolve(digest: str) -> Path | None:
    if not is_digest(digest):
        return None
    target = object_path(digest)
    return target if target.is_file() else None
