"""工程登记与接口发现。接口签名在运行时发现，不落库（spec 第 3 节）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db, new_id, now_iso
from app.models import Project
from app.schemas import (
    ProjectCreate,
    ProjectFunction,
    ProjectFunctions,
    ProjectOut,
    make_project,
)
from app.services.projects import probe

logger = logging.getLogger(__name__)
router = APIRouter(tags=["projects"])


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="工程不存在")
    return project


def _functions(project: Project) -> ProjectFunctions:
    result = probe(project)
    functions = [ProjectFunction(**item) for item in result.get("functions", [])]
    return ProjectFunctions(functions=functions, error=result.get("error"))


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    rows = db.scalars(select(Project).order_by(Project.created_at.desc()))
    return [make_project(item) for item in rows]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    # 工程名是绑定与签名的唯一键，重名会让 plan/signature 无法唯一定位
    if db.scalar(select(Project.id).where(Project.name == payload.name)) is not None:
        raise HTTPException(status_code=409, detail=f"工程名已存在：{payload.name}")

    project = Project(
        id=new_id(),
        name=payload.name,
        path=payload.path,
        module=payload.module,
        python=payload.python,
        factory=payload.factory,
        created_at=now_iso(),
    )
    db.add(project)
    try:
        db.commit()
    except IntegrityError as exc:  # 并发下预查可能落空，交给唯一约束兜底
        db.rollback()
        raise HTTPException(status_code=409, detail=f"工程名已存在：{payload.name}") from exc
    db.refresh(project)
    return make_project(project)


@router.post("/projects/{project_id}/refresh", response_model=ProjectFunctions)
def refresh_project(project_id: str, db: Session = Depends(get_db)) -> ProjectFunctions:
    project = get_project_or_404(db, project_id)
    return _functions(project)


@router.get("/projects/{project_id}/functions", response_model=list[ProjectFunction])
def project_functions(project_id: str, db: Session = Depends(get_db)) -> list[ProjectFunction]:
    project = get_project_or_404(db, project_id)
    return _functions(project).functions
