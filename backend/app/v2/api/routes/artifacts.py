"""v2 Artifact API。

注意：只能通过 artifact_id 访问文件，禁止客户端传路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.v2.api.dependencies import get_db
from app.v2.core.config import settings
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.storage.artifact_store import ArtifactStore

router = APIRouter()


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)):
    repo = RunRepository(db)
    artifact = repo.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    store = ArtifactStore(settings.artifacts_path())

    try:
        path = store.resolve_uri(artifact.uri)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid artifact path")

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file missing")

    return FileResponse(path=str(path), filename=path.name)
