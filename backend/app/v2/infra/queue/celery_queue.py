"""v2 Celery 队列适配。

API 通过 send_task 按字符串触发任务，避免导入重任务模块。
"""

from __future__ import annotations

from celery.result import AsyncResult

from app.v2.infra.queue.celery_client import celery_client
from app.v2.infra.queue.task_queue import TaskQueue


class CeleryTaskQueue(TaskQueue):
    def enqueue(self, task_name: str, *, kwargs: dict) -> str:
        result: AsyncResult = celery_client.send_task(task_name, kwargs=kwargs)
        return result.id
