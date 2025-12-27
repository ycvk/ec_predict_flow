"""v2 Run 相关用例。

该层编排仓储与队列，保持可测试性。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.v2.domain.types import ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.queue.task_queue import TaskQueue


TASK_NAME_BY_STEP: dict[str, str] = {
    "data_download": "v2.data_download",
    "feature_calculation": "v2.feature_calculation",
    "label_calculation": "v2.label_calculation",
    "model_training": "v2.model_training",
    "model_interpretation": "v2.model_interpretation",
    "model_analysis": "v2.model_analysis",
    "backtest_construction": "v2.backtest_construction",
    "walk_forward_evaluation": "v2.walk_forward_evaluation",
}


class UnknownStepError(ValueError):
    pass


class StepParamsValidationError(ValueError):
    pass


class _DataDownloadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    start_date: str = Field(min_length=1)
    end_date: str = Field(min_length=1)
    interval: str = Field(default="1m", min_length=1)
    proxy: str | None = None


class _FeatureCalculationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_artifact_id: str = Field(min_length=1)
    alpha_types: list[str] = Field(min_length=1)
    instrument_name: str | None = None


class _LabelCalculationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_artifact_id: str = Field(min_length=1)
    window: int = Field(default=29, ge=3)
    look_forward: int = Field(default=10, ge=1)
    label_type: Literal["up", "down"] = "up"
    filter_type: Literal["rsi", "cti"] = "rsi"
    threshold: float | None = None


class _ModelTrainingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features_artifact_id: str = Field(min_length=1)
    labels_artifact_id: str = Field(min_length=1)
    num_boost_round: int = Field(default=500, ge=1)
    num_threads: int = Field(default=4, ge=1)


class _ModelInterpretationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_artifact_id: str = Field(min_length=1)
    features_artifact_id: str | None = None
    labels_artifact_id: str | None = None
    max_samples: int = Field(default=5000, ge=1, le=200000)
    max_display: int = Field(default=20, ge=1, le=100)


class _ModelAnalysisParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_artifact_id: str = Field(min_length=1)
    features_artifact_id: str | None = None
    labels_artifact_id: str | None = None

    selected_features: list[str] | None = None
    max_features: int = Field(default=8, ge=1, le=50)

    max_depth: int = Field(default=3, ge=1, le=20)
    min_samples_split: int = Field(default=100, ge=2)
    min_samples_leaf: int = Field(default=50, ge=1)
    min_rule_samples: int = Field(default=50, ge=1)

    label_threshold: float | None = None


class _BacktestConstructionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features_artifact_id: str = Field(min_length=1)
    analysis_artifact_id: str = Field(min_length=1)

    look_forward_bars: int = Field(default=10, ge=1, le=5000)
    win_profit: float = Field(default=4.0)
    loss_cost: float = Field(default=5.0)
    initial_balance: float = Field(default=1000.0)

    pnl_mode: Literal["fixed", "price"] = "price"
    fee_rate: float = Field(default=0.0004, ge=0.0)
    slippage_bps: float = Field(default=0.0, ge=0.0)
    position_fraction: float = Field(default=1.0, gt=0.0, le=1.0)
    position_notional: float | None = Field(default=None, gt=0.0)

    backtest_type: Literal["long", "short"] = "long"
    filter_type: Literal["rsi", "cti"] = "rsi"
    order_interval_minutes: int = Field(default=30, ge=0, le=24 * 60)

    min_rule_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class _WalkForwardEvaluationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features_artifact_id: str = Field(min_length=1)
    labels_artifact_id: str = Field(min_length=1)

    train_bars: int = Field(default=20000, ge=1)
    test_bars: int = Field(default=5000, ge=1)
    step_bars: int = Field(default=5000, ge=1)
    max_windows: int = Field(default=10, ge=1, le=200)


_PARAMS_MODEL_BY_STEP: dict[str, type[BaseModel]] = {
    "data_download": _DataDownloadParams,
    "feature_calculation": _FeatureCalculationParams,
    "label_calculation": _LabelCalculationParams,
    "model_training": _ModelTrainingParams,
    "model_interpretation": _ModelInterpretationParams,
    "model_analysis": _ModelAnalysisParams,
    "backtest_construction": _BacktestConstructionParams,
    "walk_forward_evaluation": _WalkForwardEvaluationParams,
}


def _normalize_params(step_name: str, params: dict[str, Any]) -> dict[str, Any]:
    model_cls = _PARAMS_MODEL_BY_STEP.get(step_name)
    if not model_cls:
        raise UnknownStepError(f"未知 step: {step_name}")

    try:
        parsed = model_cls.model_validate(params)
    except ValidationError as e:
        raise StepParamsValidationError(str(e))

    return parsed.model_dump()


def create_run_and_enqueue(
    *,
    session: Session,
    queue: TaskQueue,
    workflow_name: str,
    step_name: str,
    params: dict[str, Any],
) -> tuple[str, str, str | None]:
    repo = RunRepository(session)

    task_name = TASK_NAME_BY_STEP.get(step_name)
    if not task_name:
        raise UnknownStepError(f"未知 step: {step_name}")

    normalized_params = _normalize_params(step_name, params)

    run = repo.create_run(
        workflow_name=workflow_name,
        step_name=step_name,
        params=normalized_params,
    )
    step = repo.create_step(run=run, name=step_name)

    repo.set_run_status(run, RunStatus.QUEUED)
    repo.set_step_status(step, StepStatus.QUEUED, progress=0, message="已入队")
    session.commit()

    queue_task_id = None
    try:
        queue_task_id = queue.enqueue(
            task_name, kwargs={"run_id": run.id, "step_id": step.id, **normalized_params}
        )
        repo.set_step_queue_task_id(step, queue_task_id)
        session.commit()
    except Exception as e:
        err = ErrorPayload(code=ErrorCode.DEPENDENCY_UNAVAILABLE, message=str(e))
        repo.set_step_status(step, StepStatus.FAILED, message="入队失败", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()

    return run.id, step.id, queue_task_id


def cancel_run(*, session: Session, run_id: str) -> bool:
    repo = RunRepository(session)
    run = repo.get_run(run_id)
    if run is None:
        return False

    if run.status in {RunStatus.SUCCEEDED.value, RunStatus.FAILED.value, RunStatus.CANCELED.value}:
        return True

    repo.set_run_status(run, RunStatus.CANCELED)

    for step in repo.list_steps(run_id):
        if step.status not in {
            StepStatus.SUCCEEDED.value,
            StepStatus.FAILED.value,
            StepStatus.CANCELED.value,
        }:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")

    session.commit()
    return True
