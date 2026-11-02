"""流程包：zip = workflow.json + manifest.json + attachments/。

导出给人或存档用；数据库仍是唯一数据源，包只是一次性快照。
解包不做落盘，全部在内存里完成，避免 zip 路径穿越。
"""

from __future__ import annotations

import io
import json
import re
import zipfile

DOC_NAME = "workflow.json"
MANIFEST_NAME = "manifest.json"
PACKAGE_KIND = "newdiag-flow-package"
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024

# Windows 文件名禁用字符 + 控制字符；包内路径只允许普通名字
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(raw: str, fallback: str) -> str:
    name = _BAD_NAME.sub("_", (raw or "").strip()).strip(". ")
    return (name[:120] or fallback)


def build_package(doc: dict, manifest: dict, attachments: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(DOC_NAME, json.dumps(doc, ensure_ascii=False, indent=2))
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        for path, data in attachments:
            archive.writestr(path, data)
    return buffer.getvalue()


def read_package(data: bytes) -> tuple[dict, dict | None, dict[str, bytes]]:
    """返回 (doc, manifest, 包内文件)。任何结构问题都抛 ValueError。"""
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("流程包超过 200MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 zip 文件") from exc

    files: dict[str, bytes] = {}
    total = 0
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"包内路径不合法：{info.filename}")
            total += info.file_size
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("流程包解压后超过 200MB")
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError(f"包内文件超过 50MB：{name}")
            files[name] = archive.read(info)

    doc_name = DOC_NAME
    if doc_name not in files:
        doc_name = next(
            (
                name
                for name in files
                if "/" not in name and name != MANIFEST_NAME and name.lower().endswith(".json")
            ),
            "",
        )
    if not doc_name:
        raise ValueError("包内找不到 workflow.json")
    try:
        doc = json.loads(files[doc_name].decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("workflow.json 不是合法的 JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("workflow.json 必须是 JSON 对象")

    manifest: dict | None = None
    raw_manifest = files.get(MANIFEST_NAME)
    if raw_manifest:
        try:
            parsed = json.loads(raw_manifest.decode("utf-8-sig"))
            manifest = parsed if isinstance(parsed, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            manifest = None
    return doc, manifest, files
