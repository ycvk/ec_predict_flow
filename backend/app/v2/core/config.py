"""v2 配置管理。

约定：避免在导入时产生副作用（如创建目录、连接外部服务）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "EC Predict Flow"
    VERSION: str = "2.0.0"
    API_PREFIX: str = "/api/v2"

    # 数据库
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ec_predict_flow"

    # 队列（仅 broker）
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"

    # 产物存储（payload）
    ARTIFACTS_DIR: str = ""

    # 外部服务
    BINANCE_PROXY: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True

    def artifacts_path(self) -> Path:
        if self.ARTIFACTS_DIR:
            return Path(self.ARTIFACTS_DIR).expanduser().resolve()

        # backend/app/v2/core/config.py -> backend
        backend_dir = Path(__file__).resolve().parents[3]
        return (backend_dir / "data_v2").resolve()


settings = Settings()
