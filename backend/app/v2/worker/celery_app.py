"""v2 Celery 应用实例。

约定：
- Redis 仅作为 broker。
- 对外状态以 PostgreSQL 为准（Run/Step/Artifact）。
"""

from __future__ import annotations

from celery import Celery

from app.v2.core.config import settings


celery_app = Celery(
    "ec_predict_flow_v2",
    broker=settings.CELERY_BROKER_URL,
    include=[
        "app.v2.worker.tasks.data_download",
        "app.v2.worker.tasks.feature_calculation",
        "app.v2.worker.tasks.label_calculation",
        "app.v2.worker.tasks.model_training",
        "app.v2.worker.tasks.model_interpretation",
        "app.v2.worker.tasks.model_analysis",
        "app.v2.worker.tasks.backtest_construction",
        "app.v2.worker.tasks.walk_forward_evaluation",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
)
