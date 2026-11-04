"""不可变 JSON 产物及字段偏移索引，避免共享大结果反复经过会话协议。"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from pathlib import Path
from contextlib import closing

logger = logging.getLogger(__name__)
_ID = re.compile(r"^[0-9a-f]{32}$")


def is_reference(value):
    return isinstance(value, dict) and set(value) == {"$artifact", "path"}


def select(reference, path=""):
    if not is_reference(reference):
        raise ValueError("数据产物引用无效")
    parts = list(reference["path"])
    parts.extend(part for part in path.split(".") if part)
    return {"$artifact": reference["$artifact"], "path": parts}


def _directory(root, reference):
    identifier = reference.get("$artifact")
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError("数据产物标识无效")
    parts = reference.get("path")
    if not isinstance(parts, list) or not all(isinstance(part, str) for part in parts):
        raise ValueError("数据产物字段路径无效")
    directory = Path(root).resolve() / identifier
    if not directory.is_dir():
        raise ValueError("共享数据产物不存在，请重新执行数据准备节点")
    return directory


def save(root, value):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    pending = root / (identifier + ".part")
    pending.mkdir()
    # 目录改名是可见性边界，失败的写入不会成为可消费产物。
    with closing(sqlite3.connect(pending / "index.sqlite")) as index, (pending / "data.json").open("wb") as data:
        index.execute("CREATE TABLE fields (path TEXT PRIMARY KEY, offset INTEGER, size INTEGER)")

        def emit(item, path):
            offset = data.tell()
            if isinstance(item, dict):
                data.write(b"{")
                for number, (key, child) in enumerate(item.items()):
                    if number:
                        data.write(b",")
                    data.write(json.dumps(key, ensure_ascii=False).encode("utf-8") + b":")
                    emit(child, [*path, key])
                data.write(b"}")
            elif isinstance(item, list):
                data.write(b"[")
                for number, child in enumerate(item):
                    if number:
                        data.write(b",")
                    emit(child, [*path, str(number)])
                data.write(b"]")
            else:
                data.write(json.dumps(item, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            index.execute("INSERT INTO fields VALUES (?, ?, ?)",
                          (json.dumps(path, ensure_ascii=False), offset, data.tell() - offset))

        emit(value, [])
        index.commit()
    os.replace(pending, root / identifier)
    logger.info("共享数据产物写入 id=%s bytes=%s", identifier, (root / identifier / "data.json").stat().st_size)
    return {"$artifact": identifier, "path": []}


def load(root, reference):
    directory = _directory(root, reference)
    try:
        with closing(sqlite3.connect((directory / "index.sqlite").as_uri() + "?mode=ro", uri=True)) as index:
            row = index.execute("SELECT offset, size FROM fields WHERE path = ?",
                                (json.dumps(reference["path"], ensure_ascii=False),)).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("共享数据索引缺失或损坏，请重新执行数据准备节点") from exc
    if row is None:
        raise ValueError(f"共享数据字段不存在：{'.'.join(reference['path'])}")
    try:
        with (directory / "data.json").open("rb") as data:
            data.seek(row[0])
            value = json.loads(data.read(row[1]))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("共享数据内容缺失或损坏，请重新执行数据准备节点") from exc
    logger.info("共享数据产物读取 id=%s path=%s bytes=%s", reference["$artifact"], reference["path"], row[1])
    return value
