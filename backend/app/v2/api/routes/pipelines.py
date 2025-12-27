"""v2 Pipeline API。

面向 UI 的“一键跑完”入口：创建一个 pipeline run 并入队第一个 step。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.v2.api.dependencies import get_db, get_task_queue
from app.v2.api.schemas import PipelineRunRequest as PipelineRunRequestSchema
from app.v2.api.schemas import PipelineRunResponse
from app.v2.infra.queue.task_queue import TaskQueue
from app.v2.usecases.pipeline import PipelineRunRequest, create_pipeline_run_and_enqueue

router = APIRouter()


@router.post("/pipelines/run", response_model=PipelineRunResponse)
def run_pipeline(
    request: PipelineRunRequestSchema,
    db: Session = Depends(get_db),
    queue: TaskQueue = Depends(get_task_queue),
):
    try:
        run_id, step_id, queue_task_id = create_pipeline_run_and_enqueue(
            session=db,
            queue=queue,
            request=PipelineRunRequest.model_validate(request.model_dump()),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return PipelineRunResponse(run_id=run_id, step_id=step_id, status="queued", queue_task_id=queue_task_id)

