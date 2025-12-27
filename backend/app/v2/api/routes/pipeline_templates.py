"""v2 Pipeline Template API。

用于保存/复用“一键跑完”的高级配置（实验模板）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.v2.api.dependencies import get_db
from app.v2.api.schemas import (
    PipelineTemplateCreateRequest,
    PipelineTemplateResponse,
    PipelineTemplateUpdateRequest,
)
from app.v2.infra.db.repositories import PipelineTemplateRepository

router = APIRouter()


def _to_response(tpl) -> PipelineTemplateResponse:
    return PipelineTemplateResponse(
        template_id=tpl.id,
        name=tpl.name,
        config=tpl.config or {},
        is_default=bool(tpl.is_default),
        created_at=tpl.created_at,
        updated_at=tpl.updated_at,
    )


@router.get("/pipeline-templates", response_model=list[PipelineTemplateResponse])
def list_templates(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    repo = PipelineTemplateRepository(db)
    return [_to_response(t) for t in repo.list_templates(limit=limit, offset=offset)]


@router.post("/pipeline-templates", response_model=PipelineTemplateResponse)
def create_template(request: PipelineTemplateCreateRequest, db: Session = Depends(get_db)):
    repo = PipelineTemplateRepository(db)
    tpl = repo.create_template(name=request.name, config=request.config, is_default=request.is_default)
    if request.is_default:
        repo.set_default(tpl)
    db.commit()
    return _to_response(tpl)


@router.put("/pipeline-templates/{template_id}", response_model=PipelineTemplateResponse)
def update_template(template_id: str, request: PipelineTemplateUpdateRequest, db: Session = Depends(get_db)):
    repo = PipelineTemplateRepository(db)
    tpl = repo.get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    repo.update_template(tpl, name=request.name, config=request.config, is_default=request.is_default)
    if request.is_default:
        repo.set_default(tpl)
    db.commit()
    return _to_response(tpl)


@router.delete("/pipeline-templates/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    repo = PipelineTemplateRepository(db)
    tpl = repo.get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    repo.delete_template(tpl)
    db.commit()
    return {"status": "ok"}


@router.post("/pipeline-templates/{template_id}/set-default")
def set_default(template_id: str, db: Session = Depends(get_db)):
    repo = PipelineTemplateRepository(db)
    tpl = repo.get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    repo.set_default(tpl)
    db.commit()
    return {"status": "ok"}

