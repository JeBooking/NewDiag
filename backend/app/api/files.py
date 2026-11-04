"""内容寻址附件上传与下载。"""

from __future__ import annotations

import logging
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import FileBlob
from app.schemas import FileOut
from app.services import storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])

# 在线预览白名单。判断用文件名扩展名，不用上传时客户端报的 media_type：
# Windows 给 Office 文件的类型常常是空或 octet-stream，不可信。
# 值是我们主动声明的类型，前端据此内联，改这张表要同步改 src/services/files.ts。
INLINE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".csv": "text/plain",
    ".md": "text/plain",
    ".json": "application/json",
    ".yaml": "text/plain",
    ".yml": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

# 内联响应必须禁止浏览器猜类型，否则改名成图片的 HTML 会被当页面执行。
NO_SNIFF = {"X-Content-Type-Options": "nosniff"}


def inline_media_type(filename: str) -> str | None:
    return INLINE_TYPES.get(PurePath(filename).suffix.lower())


@router.post("/files", response_model=FileOut)
async def upload(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> FileOut:
    # UploadFile 大文件已落到临时文件，这里分块拷贝到自己的临时对象，不在内存里攒整份；
    # 同步 IO 丢线程池，避免大文件阻塞事件循环。
    try:
        digest, path, size = await run_in_threadpool(storage.store_stream, file.file)
    except storage.UploadTooLarge:
        await file.close()
        raise HTTPException(
            status_code=413, detail=storage.UPLOAD_TOO_LARGE_DETAIL
        ) from None

    existing = db.get(FileBlob, digest)
    if existing is None:
        existing = FileBlob(
            hash=digest,
            filename=file.filename or digest,
            media_type=file.content_type or "application/octet-stream",
            size=size,
            path=str(path),
        )
        db.add(existing)
        try:
            db.commit()
        except IntegrityError:
            # 并发上传同一文件：另一个请求先插了，回滚后取它那条即可
            db.rollback()
            existing = db.get(FileBlob, digest)
            if existing is None:
                logger.warning("写库冲突且回查不到记录：%s", digest)
                raise HTTPException(
                    status_code=503, detail="文件记录写入冲突，请重试"
                ) from None
        else:
            db.refresh(existing)
    return FileOut(
        hash=existing.hash,
        name=existing.filename,
        media_type=existing.media_type,
        size=existing.size,
    )


@router.get("/files/{digest}")
def download(
    digest: str,
    disposition: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> FileResponse:
    blob = db.get(FileBlob, digest)
    if blob is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = storage.resolve(digest)
    if path is None:
        logger.warning("文件记录存在但磁盘缺失：%s", digest)
        raise HTTPException(status_code=410, detail="文件内容已丢失")
    # 不在白名单却要内联时不报错，退回下载：前端猜错扩展名时页面还能拿到文件。
    inline = inline_media_type(blob.filename) if disposition == "inline" else None
    return FileResponse(
        path,
        media_type=inline or blob.media_type,
        filename=blob.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers=NO_SNIFF,
    )
