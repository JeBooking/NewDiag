"""内容寻址附件上传与下载。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import FileBlob
from app.schemas import FileOut
from app.services import storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])


@router.post("/files", response_model=FileOut)
async def upload(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> FileOut:
    buffer = bytearray()
    while True:
        chunk = await file.read(storage.CHUNK_SIZE)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > storage.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件超过 50MB 限制")

    data = bytes(buffer)
    digest, path = storage.store(data)
    existing = db.get(FileBlob, digest)
    if existing is None:
        existing = FileBlob(
            hash=digest,
            filename=file.filename or digest,
            media_type=file.content_type or "application/octet-stream",
            size=len(data),
            path=str(path),
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
    return FileOut(
        hash=existing.hash,
        name=existing.filename,
        media_type=existing.media_type,
        size=existing.size,
    )


@router.get("/files/{digest}")
def download(digest: str, db: Session = Depends(get_db)) -> FileResponse:
    blob = db.get(FileBlob, digest)
    if blob is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = storage.resolve(digest)
    if path is None:
        logger.warning("文件记录存在但磁盘缺失：%s", digest)
        raise HTTPException(status_code=410, detail="文件内容已丢失")
    return FileResponse(path, media_type=blob.media_type, filename=blob.filename)
