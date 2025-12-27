"""v2 Pipeline 串联逻辑（worker 侧）。

约定：
- pipeline run 的 `workflow_runs.step_name == "pipeline"`
- 每个 step 仍写入 workflow_steps 与 artifacts
- step 成功后由 worker 继续入队下一步（最后一步成功才标记 run=SUCCEEDED）
"""

from __future__ import annotations

import copy
from typing import Any

from celery import Celery
from sqlalchemy.orm import Session

from app.v2.domain.types import ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.models import WorkflowRun, WorkflowStep
from app.v2.infra.db.repositories import RunRepository
from app.v2.usecases.pipeline import PIPELINE_DEFAULT_STEPS, PIPELINE_RUN_STEP_NAME
from app.v2.usecases.runs import TASK_NAME_BY_STEP


def is_pipeline_run(run: WorkflowRun) -> bool:
    return run.step_name == PIPELINE_RUN_STEP_NAME


def _require_state_id(*, state: dict[str, Any], key: str) -> str:
    value = state.get(key)
    if value is None:
        raise ValueError(f"pipeline state 缺少 {key}")
    value_str = str(value).strip()
    if not value_str or value_str.lower() == "none":
        raise ValueError(f"pipeline state {key} 无效")
    return value_str


def _get_pipeline_config(run: WorkflowRun) -> dict[str, Any]:
    params = run.params or {}
    pipeline = params.get("pipeline") or {}
    cfg = pipeline.get("config") or {}
    return cfg if isinstance(cfg, dict) else {}


def _get_pipeline_steps(run: WorkflowRun) -> list[str]:
    cfg = _get_pipeline_config(run)
    steps = cfg.get("steps")
    if isinstance(steps, list) and steps:
        return [str(s) for s in steps if str(s).strip()]
    return list(PIPELINE_DEFAULT_STEPS)


def _get_pipeline_state(run: WorkflowRun) -> dict[str, Any]:
    params = run.params or {}
    pipeline = params.get("pipeline") or {}
    state = pipeline.get("state") or {}
    return state if isinstance(state, dict) else {}


def patch_pipeline_state(run: WorkflowRun, *, patch: dict[str, Any]) -> None:
    params = copy.deepcopy(run.params or {})
    pipeline = dict(params.get("pipeline") or {})
    state = dict(pipeline.get("state") or {})
    state.update(patch or {})
    pipeline["state"] = state
    params["pipeline"] = pipeline
    run.params = params


def build_next_step_kwargs(*, run: WorkflowRun, next_step_name: str) -> dict[str, Any]:
    cfg = _get_pipeline_config(run)
    state = _get_pipeline_state(run)

    if next_step_name == "feature_calculation":
        raw_artifact_id = _require_state_id(state=state, key="raw_artifact_id")
        feature_cfg = cfg.get("feature_calculation") or {}
        return {
            "raw_artifact_id": raw_artifact_id,
            "alpha_types": list(feature_cfg.get("alpha_types") or ["alpha158"]),
            "instrument_name": feature_cfg.get("instrument_name"),
        }

    if next_step_name == "label_calculation":
        raw_artifact_id = _require_state_id(state=state, key="raw_artifact_id")
        label_cfg = cfg.get("label_calculation") or {}
        return {
            "raw_artifact_id": raw_artifact_id,
            "window": int(label_cfg.get("window", 29)),
            "look_forward": int(label_cfg.get("look_forward", 10)),
            "label_type": str(label_cfg.get("label_type", "up")),
            "filter_type": str(label_cfg.get("filter_type", "rsi")),
            "threshold": label_cfg.get("threshold"),
        }

    if next_step_name == "model_training":
        train_cfg = cfg.get("model_training") or {}
        return {
            "features_artifact_id": _require_state_id(state=state, key="features_artifact_id"),
            "labels_artifact_id": _require_state_id(state=state, key="labels_artifact_id"),
            "num_boost_round": int(train_cfg.get("num_boost_round", 500)),
            "num_threads": int(train_cfg.get("num_threads", 4)),
        }

    if next_step_name == "model_interpretation":
        interp_cfg = cfg.get("model_interpretation") or {}
        model_artifact_id = _require_state_id(state=state, key="model_artifact_id")
        return {
            "model_artifact_id": model_artifact_id,
            "features_artifact_id": state.get("features_artifact_id"),
            "labels_artifact_id": state.get("labels_artifact_id"),
            "max_samples": int(interp_cfg.get("max_samples", 5000)),
            "max_display": int(interp_cfg.get("max_display", 20)),
        }

    if next_step_name == "model_analysis":
        analysis_cfg = cfg.get("model_analysis") or {}
        model_artifact_id = _require_state_id(state=state, key="model_artifact_id")
        return {
            "model_artifact_id": model_artifact_id,
            "features_artifact_id": state.get("features_artifact_id"),
            "labels_artifact_id": state.get("labels_artifact_id"),
            "selected_features": analysis_cfg.get("selected_features"),
            "max_features": int(analysis_cfg.get("max_features", 8)),
            "max_depth": int(analysis_cfg.get("max_depth", 3)),
            "min_samples_split": int(analysis_cfg.get("min_samples_split", 100)),
            "min_samples_leaf": int(analysis_cfg.get("min_samples_leaf", 50)),
            "min_rule_samples": int(analysis_cfg.get("min_rule_samples", 50)),
            "label_threshold": analysis_cfg.get("label_threshold"),
        }

    if next_step_name == "backtest_construction":
        bt_cfg = cfg.get("backtest_construction") or {}
        return {
            "features_artifact_id": _require_state_id(state=state, key="features_artifact_id"),
            "analysis_artifact_id": _require_state_id(state=state, key="analysis_artifact_id"),
            "look_forward_bars": int(bt_cfg.get("look_forward_bars", 10)),
            "win_profit": float(bt_cfg.get("win_profit", 4.0)),
            "loss_cost": float(bt_cfg.get("loss_cost", 5.0)),
            "initial_balance": float(bt_cfg.get("initial_balance", 1000.0)),
            "pnl_mode": str(bt_cfg.get("pnl_mode", "price")),
            "fee_rate": float(bt_cfg.get("fee_rate", 0.0004)),
            "slippage_bps": float(bt_cfg.get("slippage_bps", 0.0)),
            "position_fraction": float(bt_cfg.get("position_fraction", 1.0)),
            "position_notional": bt_cfg.get("position_notional"),
            "backtest_type": str(bt_cfg.get("backtest_type", "long")),
            "filter_type": str(bt_cfg.get("filter_type", "rsi")),
            "order_interval_minutes": int(bt_cfg.get("order_interval_minutes", 30)),
            "min_rule_confidence": float(bt_cfg.get("min_rule_confidence", 0.0)),
        }

    if next_step_name == "walk_forward_evaluation":
        wf_cfg = cfg.get("walk_forward_evaluation") or {}
        return {
            "features_artifact_id": _require_state_id(state=state, key="features_artifact_id"),
            "labels_artifact_id": _require_state_id(state=state, key="labels_artifact_id"),
            "train_bars": int(wf_cfg.get("train_bars", 20000)),
            "test_bars": int(wf_cfg.get("test_bars", 5000)),
            "step_bars": int(wf_cfg.get("step_bars", 5000)),
            "max_windows": int(wf_cfg.get("max_windows", 10)),
        }

    raise ValueError(f"未知 next step: {next_step_name}")


def continue_pipeline_if_needed(
    *,
    session: Session,
    repo: RunRepository,
    celery_app: Celery,
    run: WorkflowRun,
    step: WorkflowStep,
    produced_state_patch: dict[str, Any] | None = None,
) -> bool:
    """step 成功后尝试继续 pipeline。返回值表示是否为 pipeline run。"""

    if not is_pipeline_run(run):
        return False

    if produced_state_patch:
        patch_pipeline_state(run, patch=produced_state_patch)
        session.add(run)

    # 关键：任何情况下都不应该在 run 被取消/失败后继续入队下一步
    # 注意：refresh 默认会刷新所有字段（包括 params），会覆盖本函数刚写入的 pipeline.state。
    # 这里只需要拿到最新 status，避免 state 丢失导致后续参数构造失败。
    session.refresh(run, attribute_names=["status"])
    if run.status in {RunStatus.CANCELED.value, RunStatus.FAILED.value}:
        session.commit()
        return True

    steps = _get_pipeline_steps(run)
    if step.name not in steps:
        err = ErrorPayload(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"step 不在 pipeline.steps 中: {step.name}",
        )
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()
        return True

    idx = steps.index(step.name)

    is_last = idx >= (len(steps) - 1)
    if is_last:
        repo.set_run_status(run, RunStatus.SUCCEEDED)
        session.commit()
        return True

    next_step_name = steps[idx + 1]
    task_name = TASK_NAME_BY_STEP.get(next_step_name)
    if not task_name:
        err = ErrorPayload(code=ErrorCode.VALIDATION_ERROR, message=f"未知 next step: {next_step_name}")
        next_step = repo.create_step(run=run, name=next_step_name)
        repo.set_step_status(next_step, StepStatus.FAILED, message="pipeline 配置错误", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()
        return True

    try:
        kwargs = build_next_step_kwargs(run=run, next_step_name=next_step_name)
    except Exception as e:
        err = ErrorPayload(code=ErrorCode.VALIDATION_ERROR, message=str(e))
        next_step = repo.create_step(run=run, name=next_step_name)
        repo.set_step_status(next_step, StepStatus.FAILED, message="pipeline 参数错误", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()
        return True

    next_step = repo.create_step(run=run, name=next_step_name)
    repo.set_step_status(next_step, StepStatus.QUEUED, progress=0, message="已入队")
    session.commit()

    try:
        async_result = celery_app.send_task(
            task_name, kwargs={"run_id": run.id, "step_id": next_step.id, **kwargs}
        )
        repo.set_step_queue_task_id(next_step, async_result.id)
        session.commit()
    except Exception as e:
        err = ErrorPayload(code=ErrorCode.DEPENDENCY_UNAVAILABLE, message=str(e))
        repo.set_step_status(next_step, StepStatus.FAILED, message="入队失败", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()

    return True
