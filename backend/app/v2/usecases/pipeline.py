"""v2 Pipeline 用例（多步骤串联的一键运行）。"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.v2.domain.types import ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.repositories import PipelineTemplateRepository, RunRepository
from app.v2.infra.queue.task_queue import TaskQueue
from app.v2.usecases.runs import TASK_NAME_BY_STEP


PIPELINE_RUN_STEP_NAME = "pipeline"

PIPELINE_DEFAULT_STEPS: list[str] = [
    "data_download",
    "feature_calculation",
    "label_calculation",
    "model_training",
    "model_interpretation",
    "model_analysis",
    "backtest_construction",
    "walk_forward_evaluation",
]


class PipelineConfigDataDownload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    start_date: str = Field(min_length=1)
    end_date: str = Field(min_length=1)
    interval: str = Field(default="1m", min_length=1)
    proxy: str | None = None


class PipelineConfigFeatureCalculation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alpha_types: list[str] = Field(default_factory=lambda: ["alpha158"], min_length=1)
    instrument_name: str | None = None


class PipelineConfigLabelCalculation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: int = Field(default=29, ge=3)
    look_forward: int = Field(default=10, ge=1)
    label_type: Literal["up", "down"] = "up"
    filter_type: Literal["rsi", "cti"] = "rsi"
    threshold: float | None = None


class PipelineConfigModelTraining(BaseModel):
    model_config = ConfigDict(extra="forbid")

    num_boost_round: int = Field(default=500, ge=1)
    num_threads: int = Field(default=4, ge=1)


class PipelineConfigModelInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_samples: int = Field(default=5000, ge=1, le=200000)
    max_display: int = Field(default=20, ge=1, le=100)


class PipelineConfigModelAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_features: list[str] | None = None
    max_features: int = Field(default=8, ge=1, le=50)

    max_depth: int = Field(default=3, ge=1, le=20)
    min_samples_split: int = Field(default=100, ge=2)
    min_samples_leaf: int = Field(default=50, ge=1)
    min_rule_samples: int = Field(default=50, ge=1)

    label_threshold: float | None = None


class PipelineConfigBacktestConstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class PipelineConfigWalkForwardEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    train_bars: int = Field(default=20000, ge=1)
    test_bars: int = Field(default=5000, ge=1)
    step_bars: int = Field(default=5000, ge=1)
    max_windows: int = Field(default=10, ge=1, le=200)


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[str] = Field(default_factory=lambda: list(PIPELINE_DEFAULT_STEPS), min_length=1)

    data_download: PipelineConfigDataDownload
    feature_calculation: PipelineConfigFeatureCalculation = Field(
        default_factory=PipelineConfigFeatureCalculation
    )
    label_calculation: PipelineConfigLabelCalculation = Field(
        default_factory=PipelineConfigLabelCalculation
    )
    model_training: PipelineConfigModelTraining = Field(default_factory=PipelineConfigModelTraining)
    model_interpretation: PipelineConfigModelInterpretation = Field(
        default_factory=PipelineConfigModelInterpretation
    )
    model_analysis: PipelineConfigModelAnalysis = Field(default_factory=PipelineConfigModelAnalysis)
    backtest_construction: PipelineConfigBacktestConstruction = Field(
        default_factory=PipelineConfigBacktestConstruction
    )
    walk_forward_evaluation: PipelineConfigWalkForwardEvaluation = Field(
        default_factory=PipelineConfigWalkForwardEvaluation
    )


class PipelineRunRequest(BaseModel):
    """API 层用于创建 pipeline run 的输入模型。"""

    model_config = ConfigDict(extra="forbid")

    workflow_name: str = Field(default="default")
    template_id: str | None = None

    # 一键跑完的最小必填项（作为 overrides 写入 data_download）
    symbol: str = Field(min_length=1)
    start_date: str = Field(min_length=1)
    end_date: str = Field(min_length=1)
    interval: str = Field(default="1m", min_length=1)

    # 允许前端通过 key-path 覆盖（例如 {"feature_calculation": {"alpha_types": [...]}})
    config_overrides: dict[str, Any] = Field(default_factory=dict)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def default_pipeline_config_for(*, symbol: str, start_date: str, end_date: str, interval: str) -> dict[str, Any]:
    return PipelineConfig(
        data_download=PipelineConfigDataDownload(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            interval=interval,
        )
    ).model_dump()


def resolve_pipeline_config(
    *, session: Session, request: PipelineRunRequest
) -> tuple[dict[str, Any], str | None]:
    """返回（pipeline_config_dict, template_id_used）。"""

    template_repo = PipelineTemplateRepository(session)

    template_id_used: str | None = None
    base: dict[str, Any] | None = None

    if request.template_id:
        tpl = template_repo.get_template(request.template_id)
        if tpl is None:
            raise ValueError("template_id 不存在")
        template_id_used = tpl.id
        base = dict(tpl.config or {})
    else:
        default_tpl = template_repo.get_default_template()
        if default_tpl is not None:
            template_id_used = default_tpl.id
            base = dict(default_tpl.config or {})

    if not base:
        base = default_pipeline_config_for(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            interval=request.interval,
        )

    # 覆盖 data_download（强制以本次请求为准）
    base = _deep_merge(
        base,
        {
            "data_download": {
                "symbol": request.symbol,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "interval": request.interval,
            }
        },
    )

    # 合并前端 overrides（高级设置）
    if request.config_overrides:
        base = _deep_merge(base, request.config_overrides)

    # 校验并归一化
    try:
        parsed = PipelineConfig.model_validate(base)
    except ValidationError as e:
        raise ValueError(f"pipeline config 校验失败: {e}")

    return parsed.model_dump(), template_id_used


def create_pipeline_run_and_enqueue(
    *,
    session: Session,
    queue: TaskQueue,
    request: PipelineRunRequest,
) -> tuple[str, str, str | None]:
    repo = RunRepository(session)

    pipeline_config, template_id_used = resolve_pipeline_config(session=session, request=request)

    steps = pipeline_config.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError("pipeline.steps 不能为空")
    first_step = str(steps[0])
    if first_step != "data_download":
        raise ValueError("当前版本要求 pipeline.steps 的第一步必须是 data_download")
    task_name = TASK_NAME_BY_STEP.get(first_step)
    if not task_name:
        raise ValueError(f"未知 pipeline step: {first_step}")

    run_params = {
        "pipeline": {
            "config": pipeline_config,
            "state": {},
            "template_id": template_id_used,
        }
    }

    run = repo.create_run(workflow_name=request.workflow_name, step_name=PIPELINE_RUN_STEP_NAME, params=run_params)
    step = repo.create_step(run=run, name=first_step)

    repo.set_run_status(run, RunStatus.QUEUED)
    repo.set_step_status(step, StepStatus.QUEUED, progress=0, message="已入队")
    session.commit()

    queue_task_id = None
    try:
        kwargs = dict(pipeline_config.get("data_download") or {})
        queue_task_id = queue.enqueue(task_name, kwargs={"run_id": run.id, "step_id": step.id, **kwargs})
        repo.set_step_queue_task_id(step, queue_task_id)
        session.commit()
    except Exception as e:
        err = ErrorPayload(code=ErrorCode.DEPENDENCY_UNAVAILABLE, message=str(e))
        repo.set_step_status(step, StepStatus.FAILED, message="入队失败", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()

    return run.id, step.id, queue_task_id
