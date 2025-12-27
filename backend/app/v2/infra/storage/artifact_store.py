"""v2 产物存储（payload）实现。

安全目标：
- run_id 隔离目录，避免路径穿越。
- API 永远不接受客户端传入真实文件路径。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Tuple

from app.v2.domain.types import ArtifactKind


class ArtifactStore:
    def __init__(self, root_dir: Path):
        self._root_dir = root_dir

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def artifact_uri(self, *, run_id: str, kind: ArtifactKind, filename: str) -> str:
        safe_run_id = Path(run_id).name
        safe_filename = Path(filename).name
        return (Path("runs") / safe_run_id / kind.value / safe_filename).as_posix()

    def resolve_uri(self, uri: str) -> Path:
        uri_path = Path(uri)
        if uri_path.is_absolute():
            return self._safe_resolve(uri_path)
        return self._safe_resolve(self._root_dir / uri_path)

    def allocate_path(self, *, run_id: str, kind: ArtifactKind, filename: str) -> Path:
        uri = self.artifact_uri(run_id=run_id, kind=kind, filename=filename)
        path = self.resolve_uri(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_bytes(
        self, *, run_id: str, kind: ArtifactKind, filename: str, data: bytes
    ) -> Tuple[Path, str, int]:
        path = self.allocate_path(run_id=run_id, kind=kind, filename=filename)
        path.write_bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()
        return path, sha256, len(data)

    def _safe_resolve(self, path: Path) -> Path:
        root = self._root_dir.resolve()
        resolved = path.resolve()
        if resolved == root or root in resolved.parents:
            return resolved
        raise ValueError("非法路径：不允许逃逸 artifacts 根目录")
