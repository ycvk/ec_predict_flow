"""v2 API 侧 Celery client。

注意：该 client 仅用于 `send_task`，不会导入任务模块，避免 API 与重依赖耦合。
"""

from __future__ import annotations

from celery import Celery

from app.v2.core.config import settings


celery_client = Celery("ec_predict_flow_v2_client", broker=settings.CELERY_BROKER_URL)
celery_client.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
