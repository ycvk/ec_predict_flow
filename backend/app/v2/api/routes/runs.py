"""v2 Run API。

说明：对外以 run_id 为中心，不直接暴露 Celery backend 的状态作为权威来源。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.v2.api.dependencies import get_db, get_task_queue
from app.v2.api.schemas import (
    RunCreateRequest,
    RunCreateResponse,
    RunResponse,
    StepResponse,
    ArtifactResponse,
    RunSummaryResponse,
)
from app.v2.core.config import settings
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.queue.task_queue import TaskQueue
from app.v2.infra.storage.artifact_store import ArtifactStore
from app.v2.usecases.runs import (
    StepParamsValidationError,
    UnknownStepError,
    cancel_run,
    create_run_and_enqueue,
)

router = APIRouter()


@router.post("/runs", response_model=RunCreateResponse)
def create_run(
    request: RunCreateRequest,
    db: Session = Depends(get_db),
    queue: TaskQueue = Depends(get_task_queue),
):
    try:
        run_id, step_id, queue_task_id = create_run_and_enqueue(
            session=db,
            queue=queue,
            workflow_name=request.workflow_name,
            step_name=request.step_name,
            params=request.params,
        )
    except (UnknownStepError, StepParamsValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    run = RunRepository(db).get_run(run_id)
    status = run.status if run else "unknown"

    return RunCreateResponse(
        run_id=run_id, step_id=step_id, status=status, queue_task_id=queue_task_id
    )


@router.get("/runs", response_model=list[RunResponse])
def list_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    repo = RunRepository(db)
    runs = repo.list_runs(limit=limit, offset=offset)
    return [
        RunResponse(
            run_id=r.id,
            workflow_name=r.workflow_name,
            step_name=r.step_name,
            status=r.status,
            params=r.params,
            error=r.error,
            created_at=r.created_at,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str, db: Session = Depends(get_db)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    return RunResponse(
        run_id=run.id,
        workflow_name=run.workflow_name,
        step_name=run.step_name,
        status=run.status,
        params=run.params,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.get("/runs/{run_id}/steps", response_model=list[StepResponse])
def list_steps(run_id: str, db: Session = Depends(get_db)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    steps = repo.list_steps(run_id)
    return [
        StepResponse(
            step_id=s.id,
            run_id=s.run_id,
            name=s.name,
            status=s.status,
            progress=s.progress,
            message=s.message,
            error=s.error,
            queue_task_id=s.queue_task_id,
            created_at=s.created_at,
            started_at=s.started_at,
            finished_at=s.finished_at,
        )
        for s in steps
    ]


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
def list_artifacts(run_id: str, db: Session = Depends(get_db)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    artifacts = repo.list_artifacts(run_id=run_id)
    return [
        ArtifactResponse(
            artifact_id=a.id,
            run_id=a.run_id,
            step_id=a.step_id,
            kind=a.kind,
            uri=a.uri,
            sha256=a.sha256,
            bytes=a.bytes,
            metadata=a.metadata_,
            created_at=a.created_at,
        )
        for a in artifacts
    ]


@router.get("/runs/{run_id}/summary", response_model=RunSummaryResponse)
def run_summary(run_id: str, db: Session = Depends(get_db)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    steps = repo.list_steps(run_id)
    artifacts = repo.list_artifacts(run_id=run_id)

    store = ArtifactStore(settings.artifacts_path())

    def _filename(uri: str) -> str:
        return Path(uri).name

    def _read_json_artifact(artifact_id: str) -> dict:
        a = repo.get_artifact(artifact_id)
        if a is None:
            return {}
        try:
            path = store.resolve_uri(a.uri)
            if not path.exists():
                return {}
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    artifacts_by_filename: dict[str, str] = {}
    for a in artifacts:
        artifacts_by_filename[_filename(a.uri)] = a.id

    summary: dict[str, object] = {"charts": {}, "pipeline": (run.params or {}).get("pipeline")}

    # backtest charts
    equity_json_id = artifacts_by_filename.get("equity_curve.json")
    stats_json_id = artifacts_by_filename.get("backtest_stats.json")
    if equity_json_id or stats_json_id:
        summary["charts"] = dict(summary.get("charts") or {})
        summary["charts"]["backtest"] = {
            "equity_curve": _read_json_artifact(equity_json_id) if equity_json_id else {},
            "stats": _read_json_artifact(stats_json_id) if stats_json_id else {},
            "equity_curve_artifact_id": equity_json_id,
            "stats_artifact_id": stats_json_id,
        }

    # walk-forward charts
    wf_equity_json_id = artifacts_by_filename.get("walk_forward_equity_curve.json")
    wf_stats_json_id = artifacts_by_filename.get("walk_forward_stats.json")
    if wf_equity_json_id or wf_stats_json_id:
        summary["charts"] = dict(summary.get("charts") or {})
        summary["charts"]["walk_forward"] = {
            "equity_curve": _read_json_artifact(wf_equity_json_id) if wf_equity_json_id else {},
            "stats": _read_json_artifact(wf_stats_json_id) if wf_stats_json_id else {},
            "equity_curve_artifact_id": wf_equity_json_id,
            "stats_artifact_id": wf_stats_json_id,
        }

    # shap charts
    shap_meta_id = artifacts_by_filename.get("shap_metadata.json")
    shap_bar_id = artifacts_by_filename.get("shap_summary_bar.png")
    shap_dot_id = artifacts_by_filename.get("shap_summary_dot.png")
    if shap_meta_id or shap_bar_id or shap_dot_id:
        summary["charts"] = dict(summary.get("charts") or {})
        summary["charts"]["shap"] = {
            "metadata": _read_json_artifact(shap_meta_id) if shap_meta_id else {},
            "metadata_artifact_id": shap_meta_id,
            "summary_bar_artifact_id": shap_bar_id,
            "summary_dot_artifact_id": shap_dot_id,
        }

    # model analysis rules
    rules_id = artifacts_by_filename.get("surrogate_rules.json")
    if rules_id:
        summary["charts"] = dict(summary.get("charts") or {})
        summary["charts"]["surrogate_rules"] = {
            "data": _read_json_artifact(rules_id),
            "artifact_id": rules_id,
        }

    # model training importance
    model_artifact = next((a for a in artifacts if a.kind == "model"), None)
    if model_artifact and isinstance(model_artifact.metadata_, dict):
        top = model_artifact.metadata_.get("top20_importance")
        if isinstance(top, dict) and top:
            summary["charts"] = dict(summary.get("charts") or {})
            summary["charts"]["training"] = {"top20_importance": top, "model_artifact_id": model_artifact.id}

    return RunSummaryResponse(
        run=RunResponse(
            run_id=run.id,
            workflow_name=run.workflow_name,
            step_name=run.step_name,
            status=run.status,
            params=run.params,
            error=run.error,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        ),
        steps=[
            StepResponse(
                step_id=s.id,
                run_id=s.run_id,
                name=s.name,
                status=s.status,
                progress=s.progress,
                message=s.message,
                error=s.error,
                queue_task_id=s.queue_task_id,
                created_at=s.created_at,
                started_at=s.started_at,
                finished_at=s.finished_at,
            )
            for s in steps
        ],
        artifacts=[
            ArtifactResponse(
                artifact_id=a.id,
                run_id=a.run_id,
                step_id=a.step_id,
                kind=a.kind,
                uri=a.uri,
                sha256=a.sha256,
                bytes=a.bytes,
                metadata=a.metadata_,
                created_at=a.created_at,
            )
            for a in artifacts
        ],
        summary=summary,
    )


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, db: Session = Depends(get_db)):
    ok = cancel_run(session=db, run_id=run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"status": "ok"}
