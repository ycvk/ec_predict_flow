"""v2 模型训练任务。

输入：features artifact + labels artifact（均通过 artifact_id 引用）
输出：model artifact（LightGBM 模型文件）
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
from app.v2.usecases.steps.model_training import prepare_training_data
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.celery_app import celery_app






class _Canceled(Exception):
    pass


class _DependencyUnavailable(Exception):
    pass


@celery_app.task(name="v2.model_training")
def model_training(
    *,
    run_id: str,
    step_id: str,
    features_artifact_id: str,
    labels_artifact_id: str,
    num_boost_round: int = 500,
    num_threads: int = 4,
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
        repo.set_step_status(step, StepStatus.RUNNING, progress=0, message="加载输入 artifacts")
        session.commit()

        features_artifact = repo.get_artifact(features_artifact_id)
        labels_artifact = repo.get_artifact(labels_artifact_id)

        if features_artifact is None:
            raise ValueError("features_artifact_id 不存在")
        if labels_artifact is None:
            raise ValueError("labels_artifact_id 不存在")

        features_path = artifacts.resolve_uri(features_artifact.uri)
        labels_path = artifacts.resolve_uri(labels_artifact.uri)

        if not features_path.exists():
            raise FileNotFoundError("features 文件缺失")
        if not labels_path.exists():
            raise FileNotFoundError("labels 文件缺失")

        features_df = _read_dataframe(features_path)
        labels_df = _read_dataframe(labels_path)

        repo.set_step_status(
            step, StepStatus.RUNNING, progress=15, message="对齐数据并构造训练矩阵"
        )
        session.commit()

        session.refresh(run)
        if run.status == RunStatus.CANCELED.value:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
            repo.set_run_status(run, RunStatus.CANCELED)
            session.commit()
            return {"status": "canceled"}

        X, y, feature_cols = prepare_training_data(features_df=features_df, labels_df=labels_df)

        repo.set_step_status(
            step,
            StepStatus.RUNNING,
            progress=30,
            message=f"开始训练（样本={len(X)}, 特征={len(feature_cols)}）",
        )
        session.commit()

        try:
            import lightgbm as lgb
        except Exception as e:
            raise _DependencyUnavailable(str(e))

        lgb_train = lgb.Dataset(X, label=y, feature_name=feature_cols, free_raw_data=False)

        params = {
            "objective": "regression",
            "metric": "l2",
            "verbosity": -1,
            "num_threads": int(num_threads),
            "seed": 42,
        }

        num_boost_round = int(num_boost_round)
        progress_callback_data = {"total": max(1, num_boost_round)}

        def progress_callback(env):
            # 训练迭代进度
            cur = int(env.iteration)
            total = int(progress_callback_data["total"])

            # 软取消：每 50 次迭代检查一次
            if cur % 50 == 0:
                session.refresh(run)
                if run.status == RunStatus.CANCELED.value:
                    raise _Canceled()

            progress_percent = 30 + (cur / total) * 55
            repo.set_step_status(
                step,
                StepStatus.RUNNING,
                progress=int(min(85, max(30, progress_percent))),
                message=f"训练中 {cur}/{total}",
            )
            session.commit()

        try:
            gbm = lgb.train(
                params,
                lgb_train,
                num_boost_round=num_boost_round,
                callbacks=[progress_callback],
            )
        except _Canceled:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
            repo.set_run_status(run, RunStatus.CANCELED)
            session.commit()
            return {"status": "canceled"}

        repo.set_step_status(step, StepStatus.RUNNING, progress=90, message="计算特征重要性")
        session.commit()

        importance = pd.Series(
            gbm.feature_importance(importance_type="gain"),
            index=feature_cols,
        ).sort_values(ascending=False)

        top20_importance = {k: float(v) for k, v in importance.head(20).to_dict().items()}

        repo.set_step_status(step, StepStatus.RUNNING, progress=95, message="保存模型产物")
        session.commit()

        filename = "model_lgb.txt"
        uri = artifacts.artifact_uri(run_id=run_id, kind=ArtifactKind.MODEL, filename=filename)
        out_path = artifacts.resolve_uri(uri)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        gbm.save_model(out_path.as_posix())

        sha256 = _sha256_file(out_path)
        bytes_ = out_path.stat().st_size

        model_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.MODEL,
            uri=uri,
            sha256=sha256,
            bytes_=bytes_,
            metadata={
                "features_artifact_id": features_artifact_id,
                "labels_artifact_id": labels_artifact_id,
                "num_boost_round": num_boost_round,
                "num_threads": int(num_threads),
                "train_samples": int(len(X)),
                "num_features": int(len(feature_cols)),
                "top20_importance": top20_importance,
                "params": params,
            },
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session,
            repo=repo,
            celery_app=celery_app,
            run=run,
            step=step,
            produced_state_patch={"model_artifact_id": model_artifact.id},
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "artifact_id": model_artifact.id,
            "train_samples": int(len(X)),
            "num_features": int(len(feature_cols)),
        }

    except _DependencyUnavailable as e:
        err = ErrorPayload(
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            message=f"依赖不可用: {e}",
            traceback=traceback.format_exc(),
        )

        repo.set_step_status(step, StepStatus.FAILED, message="依赖不可用", error=err)
        repo.set_run_status(run, RunStatus.FAILED, error=err)
        session.commit()
        return {"status": "failed", "error": str(e)}

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
