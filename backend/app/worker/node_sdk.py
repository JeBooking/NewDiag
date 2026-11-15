"""平台内置的节点工具：值投影、图片落盘、字段校验与展示块校验；只依赖标准库。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
# 与 services/storage.py 的 _HASH_RE 保持一致；worker 只依赖标准库，不能跨包导入。
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# 业务生成的图片：只收位图。SVG 能嵌脚本，一旦被当图片内联就是 XSS。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
_IMAGE_ALIASES = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp", "gif": "gif"}

# 每次调用由 session 设置；业务代码只拿到 save_file，不需要知道目录从哪来。
_file_root: Path | None = None
_files: dict[str, dict] = {}


def json_value(value):
    # 禁止 repr 兜底，避免历史记录与下一阶段收到的是两种不同数据。
    return json.loads(json.dumps(_materialize(value), ensure_ascii=False, allow_nan=False))


def _materialize(value):
    """把 bytes 图片换成附件摘要；其余结构与 json.dumps 的输入保持一致。"""
    if isinstance(value, bytes):
        return save_file(value)
    if isinstance(value, (bytearray, memoryview)):
        # 只接受 bytes：save_file 算摘要要的是不可变字节串
        raise ValueError(
            f"展示数据不支持 {type(value).__name__}，请先转换为 bytes"
        )
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_materialize(item) for item in value]
    return value


def configure_files(root) -> None:
    """父进程每次调用前设置可写目录并清空待登记清单；root 为空表示本次不可落盘。"""
    global _file_root
    _file_root = Path(root) if root else None
    _files.clear()


def take_files() -> list[dict]:
    """取出本次调用新写的文件，交给父进程登记。"""
    result = list(_files.values())
    _files.clear()
    return result


def save_file(data: bytes, ext: str | None = None) -> str:
    """把图片写进内容寻址目录，返回 sha256 摘要，可直接放进 image 块的 src。

    ext 省略时按文件头识别；目录由父进程下发，业务代码不必关心存储路径。
    """
    if not isinstance(data, bytes):
        raise ValueError(f"save_file 只接受 bytes 图片数据，收到 {type(data).__name__}")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片 {len(data)} 字节，超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB 上限"
        )
    if _file_root is None:
        raise ValueError("当前调用没有可用的文件目录，无法保存图片（fileRoot 缺失）")
    # 内容必须真的是位图：扩展名由调用方给，但类型永远以文件头为准，
    # 否则随便什么字节都能挂上 .png 被当成图片下载。
    detected = _sniff(data)
    if detected is None:
        raise ValueError("内容不是可识别的图片：仅支持 png、jpg、jpeg、webp、gif")
    if ext is None:
        kind = detected
    else:
        if not isinstance(ext, str):
            raise ValueError("图片扩展名必须是字符串")
        kind = _IMAGE_ALIASES.get(ext.strip().lower().lstrip("."), "")
        if not kind:
            raise ValueError(f"不支持的文件类型 {ext}：只接受 png、jpg、jpeg、webp、gif")
        if kind != detected:
            raise ValueError(f"扩展名 {ext} 与文件内容（{detected}）不一致")
    digest = hashlib.sha256(data).hexdigest()
    target = _file_root / digest[:2] / digest
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件放 .tmp 并与 storage 同后缀：进程中途退出留下的残片由启动清扫回收
        temp_dir = _file_root / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        pending = temp_dir / f"{digest}.{uuid.uuid4().hex}.part"
        try:
            pending.write_bytes(data)
            os.replace(pending, target)
        except BaseException:
            try:
                pending.unlink()
            except OSError:
                pass
            raise
    # 同内容复用同一条记录：父进程按摘要幂等登记，不需要区分第几次生成
    _files[digest] = {"hash": digest, "name": f"{digest}.{kind}",
                      "media_type": _IMAGE_MEDIA[kind], "size": len(data)}
    logger.info("图片落盘 hash=%s bytes=%s kind=%s", digest, len(data), kind)
    return digest


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_fields(fields: list[dict], data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("人工数据必须是对象")
    known = {field["name"] for field in fields}
    if set(data) - known:
        raise ValueError(f"存在未声明字段：{', '.join(sorted(set(data) - known))}")
    result = {}
    for field in fields:
        name = field["name"]
        value = data.get(name, field.get("default"))
        if ((value is None or value == "")
                and not (field.get("type") == "json" and (name in data or "default" in field))):
            if field.get("required", True):
                raise ValueError(f"{field.get('label', name)}不能为空")
            continue
        kind = field.get("type", "text")
        valid = {
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "text": isinstance(value, str),
            "datetime": isinstance(value, str),
            "json": True,
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
            "file": isinstance(value, dict) and isinstance(value.get("hash"), str),
        }.get(kind, False)
        if not valid:
            raise ValueError(f"{field.get('label', name)}类型必须为 {kind}")
        if kind == "number":
            if "min" in field and value < field["min"]:
                raise ValueError(f"{name}不得小于 {field['min']}")
            if "max" in field and value > field["max"]:
                raise ValueError(f"{name}不得大于 {field['max']}")
        if kind == "datetime":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{name}必须是 ISO8601 时间") from exc
            if parsed.tzinfo is None:
                raise ValueError(f"{name}必须包含时区，例如 +08:00")
        result[name] = value
    return json_value(result)


def presentation(value) -> dict:
    # 先过一遍 json_value：业务直接返回的图片 bytes 在这里换成摘要，下面的校验只认字符串。
    value = json_value(value)
    if not isinstance(value, dict) or not isinstance(value.get("blocks"), list):
        raise ValueError("present 必须返回 {blocks: [...]} 展示描述")
    for block in value["blocks"]:
        if not isinstance(block, dict) or block.get("type") not in ("text", "metrics", "table", "line", "image"):
            raise ValueError("展示块仅支持 text、metrics、table、line、image")
        kind = block["type"]
        if kind == "text" and not isinstance(block.get("text"), str):
            raise ValueError("text 展示块需要 text 字符串")
        if kind == "metrics" and (
            not isinstance(block.get("items"), list)
            or any(not isinstance(x, dict) or "label" not in x or "value" not in x for x in block["items"])
        ):
            raise ValueError("metrics 展示块需要 items: [{label, value, unit?}]")
        if kind == "table" and (
            not isinstance(block.get("columns"), list)
            or any(not isinstance(x, str) for x in block["columns"])
            or not isinstance(block.get("rows"), list)
            or any(not isinstance(x, list) or len(x) != len(block["columns"]) for x in block["rows"])
        ):
            raise ValueError("table 展示块需要 columns 字符串数组及等宽 rows 数组")
        if kind == "line" and (
            not isinstance(block.get("values"), list)
            or not block["values"]
            or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in block["values"])
        ):
            raise ValueError("line 展示块需要非空数字数组 values")
        if kind == "image":
            src = block.get("src")
            if not isinstance(src, str) or not _DIGEST_RE.fullmatch(src):
                raise ValueError(
                    "image 展示块的 src 必须是附件摘要：64 位小写十六进制 sha256 字符串；"
                    "不接受文件路径或 URL，请先上传图片再填返回的摘要"
                )
            for field in ("alt", "caption"):
                if field in block and not isinstance(block[field], str):
                    raise ValueError(f"image 展示块的 {field} 必须是字符串（可省略）")
    return value
