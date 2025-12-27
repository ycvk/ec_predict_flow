"""v2 数据库引擎与 Session 工厂。

说明：该模块不在导入时建立连接，仅创建 Engine 配置。
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.v2.core.config import settings
from app.v2.infra.db.base import Base


def create_engine_from_url(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> Callable[[], Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


_engine = create_engine_from_url(settings.DATABASE_URL)
SessionLocal = create_session_factory(_engine)
