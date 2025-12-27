"""v2 API 依赖注入。

FastAPI 通过依赖提供 DB Session、队列实例等。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.v2.infra.db.engine import SessionLocal
from app.v2.infra.queue.celery_queue import CeleryTaskQueue
from app.v2.infra.queue.task_queue import TaskQueue


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_task_queue() -> TaskQueue:
    return CeleryTaskQueue()
