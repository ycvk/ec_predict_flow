"""v2 领域类型定义。

该模块尽量保持纯净，避免引入 Web/DB/队列等基础设施依赖。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Optional


class RunStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class StepStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ArtifactKind(str, Enum):
    RAW = "raw"
    FEATURES = "features"
    LABELS = "labels"
    MODEL = "model"
    PLOTS = "plots"
    ANALYSIS = "analysis"
    BACKTEST = "backtest"


class ErrorCode(str, Enum):
    UNKNOWN = "unknown"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TASK_FAILED = "task_failed"


@dataclass(frozen=True)
class ErrorPayload:
    code: ErrorCode
    message: str
    detail: Optional[Any] = None
    traceback: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Enum -> 值
        data["code"] = self.code.value
        return data
