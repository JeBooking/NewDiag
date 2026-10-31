"""SQLAlchemy 模型。字段名与 docs/spec.md 第 3 节一致，引擎层会直接读这些列。"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, new_id, now_iso


class Flow(Base):
    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="parallel")
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    current_revision_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    editing_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    editing_revision_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    editing_since: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    flow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False
    )
    json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="working")
    author: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    committed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Release(Base):
    __tablename__ = "releases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    flow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    signature_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    published_by: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    published_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    flow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False
    )
    release_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    snapshot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_by: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    started_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    captured_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    branch: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_token_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    token_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    token_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="decision")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    decision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)
    decided_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FileBlob(Base):
    __tablename__ = "files"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    path: Mapped[str] = mapped_column(Text, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 工程名是节点绑定与签名 key 的唯一口径，必须唯一
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str] = mapped_column(String(200), nullable=False)
    python: Mapped[str | None] = mapped_column(Text, nullable=True)
    factory: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now_iso)


Index("ix_revisions_flow", Revision.flow_id)
Index("ix_releases_flow", Release.flow_id)
Index("ix_instances_flow", Instance.flow_id)
Index("ix_tokens_instance", Token.instance_id)
Index("ix_runs_instance", Run.instance_id)
Index("ix_reviews_instance", Review.instance_id)
