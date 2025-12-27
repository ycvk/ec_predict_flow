"""v2 数据库 ORM 模型。

表结构以“Run/Step/Artifact”为中心，支持追溯与复现。
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.v2.domain.types import ArtifactKind, RunStatus, StepStatus
from app.v2.infra.db.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    workflow_name: Mapped[str] = mapped_column(String(64), default="default")
    step_name: Mapped[str] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(32), default=RunStatus.CREATED.value, index=True)

    params: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True)

    # 仅用于排障：不作为系统状态来源（SSOT）
    queue_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(32), default=StepStatus.PENDING.value, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[WorkflowRun] = relationship(back_populates="steps")
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(32), index=True, default=ArtifactKind.RAW.value)

    uri: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    run: Mapped[WorkflowRun] = relationship(back_populates="artifacts")
    step: Mapped[WorkflowStep | None] = relationship(back_populates="artifacts")


class PipelineTemplate(Base):
    __tablename__ = "pipeline_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
