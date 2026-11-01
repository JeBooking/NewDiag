"""流程目录（树形）接口。

只返回扁平列表，由前端组树：树的形状随前端切换（折叠、拖拽）而变化，
后端存 parentId 就够了，没必要每次拼一遍嵌套结构。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db, new_id, now_iso
from app.models import Flow, Folder
from app.schemas import FolderCreate, FolderOut, FolderPatch, make_folder

router = APIRouter(tags=["folders"])


def _clean_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="目录名不能为空")
    return name


def _get_or_404(db: Session, folder_id: str) -> Folder:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="目录不存在")
    return folder


def _same_parent_condition(parent_id: str | None):
    column = Folder.parent_id
    return column.is_(None) if parent_id is None else column == parent_id


def _require_parent(db: Session, parent_id: str | None) -> None:
    if parent_id is None:
        return
    if db.get(Folder, parent_id) is None:
        raise HTTPException(status_code=404, detail="上级目录不存在")


def _name_taken(db: Session, name: str, parent_id: str | None, *, exclude: str | None = None) -> bool:
    statement = select(Folder.id).where(Folder.name == name, _same_parent_condition(parent_id))
    if exclude:
        statement = statement.where(Folder.id != exclude)
    return db.scalar(statement.limit(1)) is not None


def _flow_counts(db: Session) -> dict[str, int]:
    """目录 → 流程数（含子目录合计）：列表页只显示一层树时，父目录的数字才说得通。"""
    rows = db.execute(
        select(Flow.folder_id, func.count(Flow.id))
        .where(Flow.folder_id.is_not(None))
        .group_by(Flow.folder_id)
    ).all()
    totals = {str(folder_id): int(count) for folder_id, count in rows}
    parents = {folder.id: folder.parent_id for folder in db.scalars(select(Folder))}
    for folder_id in parents:
        count = totals.get(folder_id, 0)
        if not count:
            continue
        cursor = parents.get(folder_id)
        seen: set[str] = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            totals[cursor] = totals.get(cursor, 0) + count
            cursor = parents.get(cursor)
    return totals


def _descendants(db: Session, folder_id: str) -> set[str]:
    """广度优先取全部后代 id，用于拦「挂到自己的子孙下」。

    seen 同时兜住脏数据里的环，不会死循环。
    """
    seen: set[str] = set()
    queue = [folder_id]
    while queue:
        current = queue.pop(0)
        children = db.scalars(select(Folder.id).where(Folder.parent_id == current))
        for child in children:
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
    return seen


@router.get("/folders", response_model=list[FolderOut])
def list_folders(db: Session = Depends(get_db)) -> list[FolderOut]:
    counts = _flow_counts(db)
    folders = db.scalars(select(Folder).order_by(Folder.sort_order, Folder.created_at))
    return [make_folder(item, counts.get(item.id, 0)) for item in folders]


@router.post("/folders", response_model=FolderOut)
def create_folder(payload: FolderCreate, db: Session = Depends(get_db)) -> FolderOut:
    name = _clean_name(payload.name)
    _require_parent(db, payload.parent_id)
    if _name_taken(db, name, payload.parent_id):
        raise HTTPException(status_code=409, detail="同级目录下已有同名目录")

    sibling_max = db.scalar(select(func.max(Folder.sort_order)).where(_same_parent_condition(payload.parent_id)))
    folder = Folder(
        id=new_id(),
        name=name,
        parent_id=payload.parent_id,
        sort_order=int(sibling_max or 0) + 1,
        created_at=now_iso(),
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return make_folder(folder)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def patch_folder(folder_id: str, payload: FolderPatch, db: Session = Depends(get_db)) -> FolderOut:
    folder = _get_or_404(db, folder_id)
    name = folder.name
    parent_id = folder.parent_id

    if "name" in payload.model_fields_set and payload.name is not None:
        name = _clean_name(payload.name)
    if "parent_id" in payload.model_fields_set:
        parent_id = payload.parent_id
        if parent_id is not None:
            _require_parent(db, parent_id)
            if parent_id == folder_id or parent_id in _descendants(db, folder_id):
                raise HTTPException(status_code=409, detail="不能把目录挂到自己或自己的子目录下")

    if (name != folder.name or parent_id != folder.parent_id) and _name_taken(
        db, name, parent_id, exclude=folder_id
    ):
        raise HTTPException(status_code=409, detail="同级目录下已有同名目录")

    folder.name = name
    folder.parent_id = parent_id
    db.commit()
    db.refresh(folder)
    return make_folder(folder, db.scalar(select(func.count(Flow.id)).where(Flow.folder_id == folder.id)) or 0)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: str, db: Session = Depends(get_db)) -> None:
    folder = _get_or_404(db, folder_id)
    # 只有空目录能删：级联删流程等于删掉别人的工作，不该由一次点击决定
    if db.scalar(select(func.count(Folder.id)).where(Folder.parent_id == folder.id)):
        raise HTTPException(status_code=409, detail="目录下还有子目录，先移走或删除子目录")
    if db.scalar(select(func.count(Flow.id)).where(Flow.folder_id == folder.id)):
        raise HTTPException(status_code=409, detail="目录下还有流程，先移走再删除")

    db.delete(folder)
    db.commit()
    return None
