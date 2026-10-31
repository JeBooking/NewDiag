"""数据库连接、路径解析与会话工厂。

路径全部从环境变量推导，便于测试用临时目录替换真实数据目录。
`configure()` 支持在进程内换库（测试用），所以所有调用点必须在使用时
读取本模块的 `engine` / `SessionLocal`，不要在 import 时绑定到局部变量。
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    pass


def data_dir() -> Path:
    raw = os.environ.get("NEWDIAG_DATA_DIR")
    path = Path(raw).expanduser().resolve() if raw else BACKEND_DIR / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    raw = os.environ.get("NEWDIAG_DB")
    if raw and "://" in raw:
        return raw
    target = Path(raw).expanduser() if raw else data_dir() / "newdiag.db"
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + target.as_posix()


def _create_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False, "timeout": 30}

    def _on_connect(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    created = create_engine(url, connect_args=connect_args)
    event.listen(created, "connect", _on_connect)
    return created


def configure(target: str | None = None) -> None:
    """重建引擎。传 URL 或文件路径，缺省按环境变量解析。"""
    global engine, SessionLocal
    url = target or database_url()
    engine.dispose()
    engine = _create_engine(url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    logger.info("数据库指向 %s", url)


engine: Engine = _create_engine(database_url())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  建表前必须加载全部模型

    Base.metadata.create_all(engine)


def get_db():
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
