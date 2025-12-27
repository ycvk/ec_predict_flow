"""v2 队列抽象。

API 层依赖抽象接口，避免直接导入 Celery 任务实现。
"""

from __future__ import annotations

from typing import Protocol


class TaskQueue(Protocol):
    def enqueue(self, task_name: str, *, kwargs: dict) -> str:
        """入队任务。

        返回值通常为队列系统生成的 task_id（用于排障，不作为系统 SSOT）。
        """
