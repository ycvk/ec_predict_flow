"""v2 特征计算任务。

输入：raw artifact（通常为 parquet）
输出：features artifact（parquet）
"""

from __future__ import annotations

from app.v2.worker.utils import _sha256_file, _read_dataframe
import traceback
from pathlib import Path

import pandas as pd

from app.v2.core.config import settings
from app.v2.domain.types import ArtifactKind, ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.engine import SessionLocal
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.storage.artifact_store import ArtifactStore
from app.v2.usecases.steps.feature_calculation import calculate_features_df
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.celery_app import celery_app






@celery_app.task(name="v2.feature_calculation")
def feature_calculation(
    *,
    run_id: str,
    step_id: str,
    raw_artifact_id: str,
    alpha_types: list[str],
    instrument_name: str | None = None,
) -> dict:
    artifacts = ArtifactStore(settings.artifacts_path())

    session = SessionLocal()
    repo = RunRepository(session)

    run = repo.get_run(run_id)
    step = repo.get_step(step_id)

    if run is None or step is None:
        session.close()
        return {"status": "failed", "error": "run/step not found"}

    try:
        repo.set_run_status(run, RunStatus.RUNNING)
        repo.set_step_status(step, StepStatus.RUNNING, progress=0, message="加载输入 artifact")
        session.commit()

        input_artifact = repo.get_artifact(raw_artifact_id)
        if input_artifact is None:
            raise ValueError("raw_artifact_id 不存在")

        raw_path = artifacts.resolve_uri(input_artifact.uri)
        if not raw_path.exists():
            raise FileNotFoundError("输入产物文件缺失")

        df = _read_dataframe(raw_path)

        repo.set_step_status(step, StepStatus.RUNNING, progress=10, message="开始计算特征")
        session.commit()

        # 软取消：按阶段检查
        session.refresh(run)
        if run.status == RunStatus.CANCELED.value:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
            repo.set_run_status(run, RunStatus.CANCELED)
            session.commit()
            return {"status": "canceled"}

        features_df, feature_cols = calculate_features_df(
            raw_df=df,
            alpha_types=alpha_types,
            instrument_name=instrument_name,
        )

        repo.set_step_status(step, StepStatus.RUNNING, progress=90, message="写入 features 产物")
        session.commit()

        alpha_suffix = "_".join(sorted({str(t).strip() for t in alpha_types}))
        filename = f"features_{alpha_suffix}.parquet"
        uri = artifacts.artifact_uri(run_id=run_id, kind=ArtifactKind.FEATURES, filename=filename)
        out_path = artifacts.resolve_uri(uri)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        features_df.to_parquet(out_path, index=False)

        sha256 = _sha256_file(out_path)
        bytes_ = out_path.stat().st_size

        features_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.FEATURES,
            uri=uri,
            sha256=sha256,
            bytes_=bytes_,
            metadata={
                "raw_artifact_id": raw_artifact_id,
                "alpha_types": list(alpha_types),
                "total_features": int(len(feature_cols)),
                "feature_columns": feature_cols,
                "rows": int(len(features_df)),
            },
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session,
            repo=repo,
            celery_app=celery_app,
            run=run,
            step=step,
            produced_state_patch={"features_artifact_id": features_artifact.id},
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "artifact_id": features_artifact.id,
            "rows": int(len(features_df)),
            "total_features": int(len(feature_cols)),
        }

    except Exception as e:
        err = ErrorPayload(
            code=ErrorCode.TASK_FAILED,
            message=str(e),
            traceback=traceback.format_exc(),
        )

        repo.set_step_status(step, StepStatus.FAILED, message="失败", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()
        return {"status": "failed", "error": str(e)}

    finally:
        session.close()
