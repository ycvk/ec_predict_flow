"""v2 健康检查。

- live：进程存活
- ready：关键依赖可用（DB/Redis/迁移）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from redis import Redis
from sqlalchemy import inspect, text

from app.v2.core.config import settings
from app.v2.infra.db.engine import SessionLocal

router = APIRouter()


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready():
    # DB
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
        inspector = inspect(session.bind)
        tables = set(inspector.get_table_names())
        required = {"workflow_runs", "workflow_steps", "artifacts", "pipeline_templates"}
        if not required.issubset(tables):
            raise HTTPException(status_code=503, detail="database not migrated")
    finally:
        session.close()

    # Redis
    try:
        redis = Redis.from_url(settings.REDIS_URL)
        redis.ping()
    except Exception:
        raise HTTPException(status_code=503, detail="redis unavailable")

    return {"status": "ok"}
