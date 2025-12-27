"""v2 仓储层：封装对 ORM 的访问。

目标：让上层用例层在不关心 SQLAlchemy 细节的情况下完成读写。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.v2.domain.types import ArtifactKind, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.models import Artifact, PipelineTemplate, WorkflowRun, WorkflowStep


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class RunRepository:
    def __init__(self, session: Session):
        self._session = session

    def create_run(
        self, *, workflow_name: str, step_name: str, params: dict[str, Any]
    ) -> WorkflowRun:
        run = WorkflowRun(
            workflow_name=workflow_name,
            step_name=step_name,
            status=RunStatus.CREATED.value,
            params=params,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._session.get(WorkflowRun, run_id)

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> list[WorkflowRun]:
        stmt = (
            select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
        )
        return list(self._session.execute(stmt).scalars().all())

    def set_run_status(
        self, run: WorkflowRun, status: RunStatus, *, error: ErrorPayload | None = None
    ) -> None:
        run.status = status.value
        if status in {RunStatus.RUNNING} and run.started_at is None:
            run.started_at = _utcnow()
        if status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}:
            run.finished_at = _utcnow()
        run.error = error.to_dict() if error else None
        self._session.add(run)

    def create_step(self, *, run: WorkflowRun, name: str) -> WorkflowStep:
        step = WorkflowStep(run_id=run.id, name=name, status=StepStatus.PENDING.value, progress=0)
        self._session.add(step)
        self._session.flush()
        return step

    def set_step_queue_task_id(self, step: WorkflowStep, task_id: str | None) -> None:
        step.queue_task_id = task_id
        self._session.add(step)

    def list_steps(self, run_id: str) -> list[WorkflowStep]:
        stmt = (
            select(WorkflowStep)
            .where(WorkflowStep.run_id == run_id)
            .order_by(WorkflowStep.created_at.asc())
        )
        return list(self._session.execute(stmt).scalars().all())

    def get_step(self, step_id: str) -> WorkflowStep | None:
        return self._session.get(WorkflowStep, step_id)

    def set_step_status(
        self,
        step: WorkflowStep,
        status: StepStatus,
        *,
        progress: int | None = None,
        message: str | None = None,
        error: ErrorPayload | None = None,
    ) -> None:
        step.status = status.value
        if progress is not None:
            step.progress = max(0, min(100, int(progress)))
        if message is not None:
            step.message = message
        if status in {StepStatus.RUNNING} and step.started_at is None:
            step.started_at = _utcnow()
        if status in {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELED}:
            step.finished_at = _utcnow()
            if step.progress < 100 and status == StepStatus.SUCCEEDED:
                step.progress = 100
        step.error = error.to_dict() if error else None
        self._session.add(step)

    def add_artifact(
        self,
        *,
        run_id: str,
        step_id: str | None,
        kind: ArtifactKind,
        uri: str,
        sha256: str | None = None,
        bytes_: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        artifact = Artifact(
            run_id=run_id,
            step_id=step_id,
            kind=kind.value,
            uri=uri,
            sha256=sha256,
            bytes=bytes_,
            metadata_=(metadata or {}),
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def list_artifacts(self, *, run_id: str) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at.asc())
        return list(self._session.execute(stmt).scalars().all())

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self._session.get(Artifact, artifact_id)


class PipelineTemplateRepository:
    def __init__(self, session: Session):
        self._session = session

    def list_templates(self, *, limit: int = 200, offset: int = 0) -> list[PipelineTemplate]:
        stmt = (
            select(PipelineTemplate)
            .order_by(PipelineTemplate.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(stmt).scalars().all())

    def get_template(self, template_id: str) -> PipelineTemplate | None:
        return self._session.get(PipelineTemplate, template_id)

    def get_default_template(self) -> PipelineTemplate | None:
        stmt = select(PipelineTemplate).where(PipelineTemplate.is_default.is_(True)).limit(1)
        return self._session.execute(stmt).scalars().first()

    def create_template(
        self, *, name: str, config: dict[str, Any] | None = None, is_default: bool = False
    ) -> PipelineTemplate:
        tpl = PipelineTemplate(name=name, config=(config or {}), is_default=bool(is_default))
        self._session.add(tpl)
        self._session.flush()
        return tpl

    def update_template(
        self,
        tpl: PipelineTemplate,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        is_default: bool | None = None,
    ) -> PipelineTemplate:
        if name is not None:
            tpl.name = name
        if config is not None:
            tpl.config = config
        if is_default is not None:
            tpl.is_default = bool(is_default)
        self._session.add(tpl)
        self._session.flush()
        return tpl

    def delete_template(self, tpl: PipelineTemplate) -> None:
        self._session.delete(tpl)

    def set_default(self, tpl: PipelineTemplate) -> None:
        # 简化实现：先清除全部 default，再设置当前。
        for other in self.list_templates(limit=1000, offset=0):
            if other.is_default and other.id != tpl.id:
                other.is_default = False
                self._session.add(other)
        tpl.is_default = True
        self._session.add(tpl)
