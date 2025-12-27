"""v2 API 路由总入口。"""

from __future__ import annotations

from fastapi import APIRouter

from app.v2.api.routes.artifacts import router as artifacts_router
from app.v2.api.routes.pipeline_templates import router as pipeline_templates_router
from app.v2.api.routes.pipelines import router as pipelines_router
from app.v2.api.routes.runs import router as runs_router

api_router = APIRouter()
api_router.include_router(runs_router, tags=["runs"])
api_router.include_router(pipelines_router, tags=["pipelines"])
api_router.include_router(pipeline_templates_router, tags=["pipeline-templates"])
api_router.include_router(artifacts_router, tags=["artifacts"])
