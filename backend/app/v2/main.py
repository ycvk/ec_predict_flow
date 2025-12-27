"""v2 FastAPI 应用入口。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.v2.api.router import api_router
from app.v2.api.routes.health import router as health_router
from app.v2.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_PREFIX)
    app.include_router(health_router)

    @app.on_event("startup")
    def _startup() -> None:
        settings.artifacts_path().mkdir(parents=True, exist_ok=True)

    return app


app = create_app()
