"""v2 API Schema（Pydantic）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    workflow_name: str = Field(default="default")
    step_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class RunCreateResponse(BaseModel):
    run_id: str
    step_id: str
    status: str
    queue_task_id: Optional[str] = None


class RunResponse(BaseModel):
    run_id: str
    workflow_name: str
    step_name: str
    status: str
    params: dict[str, Any]
    error: Optional[dict[str, Any]] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class StepResponse(BaseModel):
    step_id: str
    run_id: str
    name: str
    status: str
    progress: int
    message: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    queue_task_id: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ArtifactResponse(BaseModel):
    artifact_id: str
    run_id: str
    step_id: Optional[str] = None
    kind: str
    uri: str
    sha256: Optional[str] = None
    bytes: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class PipelineRunRequest(BaseModel):
    workflow_name: str = Field(default="default")
    template_id: Optional[str] = None

    symbol: str
    start_date: str
    end_date: str
    interval: str = Field(default="1m")

    config_overrides: dict[str, Any] = Field(default_factory=dict)


class PipelineRunResponse(BaseModel):
    run_id: str
    step_id: str
    status: str
    queue_task_id: Optional[str] = None


class PipelineTemplateResponse(BaseModel):
    template_id: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class PipelineTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class PipelineTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None


class RunSummaryResponse(BaseModel):
    run: RunResponse
    steps: list[StepResponse]
    artifacts: list[ArtifactResponse]
    summary: dict[str, Any] = Field(default_factory=dict)
