"""v2 SQLAlchemy Declarative Base。

该模块不创建 Engine，便于测试场景自行创建数据库。
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
