"""v2 模型解释任务（SHAP）。

输入：model artifact（可选显式传 features/labels artifacts，否则从 model metadata 推导）
输出：plots artifacts（png + json 元数据）

注意：
- 避免在模块导入时加载重依赖（shap/matplotlib/lightgbm）。
"""

from __future__ import annotations

from app.v2.worker.utils import _sha256_file, _read_dataframe
import json
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from app.v2.core.config import settings
from app.v2.domain.types import ArtifactKind, ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.engine import SessionLocal
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.storage.artifact_store import ArtifactStore
from app.v2.usecases.steps.model_training import prepare_training_data
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.celery_app import celery_app






class _DependencyUnavailable(Exception):
    pass


@celery_app.task(name="v2.model_interpretation")
def model_interpretation(
    *,
    run_id: str,
    step_id: str,
    model_artifact_id: str,
    features_artifact_id: str | None = None,
    labels_artifact_id: str | None = None,
    max_samples: int = 5000,
    max_display: int = 20,
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

        model_artifact = repo.get_artifact(model_artifact_id)
        if model_artifact is None:
            raise ValueError("model_artifact_id 不存在")

        model_path = artifacts.resolve_uri(model_artifact.uri)
        if not model_path.exists():
            raise FileNotFoundError("model 文件缺失")

        # 尝试从模型 metadata 推导输入数据
        meta = model_artifact.metadata_ or {}
        inferred_features_artifact_id = meta.get("features_artifact_id")
        inferred_labels_artifact_id = meta.get("labels_artifact_id")

        if features_artifact_id is None:
            features_artifact_id = inferred_features_artifact_id
        if labels_artifact_id is None:
            labels_artifact_id = inferred_labels_artifact_id

        if not features_artifact_id or not labels_artifact_id:
            raise ValueError(
                "缺少 features_artifact_id/labels_artifact_id（可显式传入或从 model metadata 推导）"
            )

        features_artifact = repo.get_artifact(str(features_artifact_id))
        labels_artifact = repo.get_artifact(str(labels_artifact_id))

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

        repo.set_step_status(step, StepStatus.RUNNING, progress=10, message="加载模型并准备数据")
        session.commit()

        # 软取消
        session.refresh(run)
        if run.status == RunStatus.CANCELED.value:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
            repo.set_run_status(run, RunStatus.CANCELED)
            session.commit()
            return {"status": "canceled"}

        # 延迟导入重依赖
        try:
            import lightgbm as lgb
        except Exception as e:
            raise _DependencyUnavailable(f"lightgbm: {e}")

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as e:
            raise _DependencyUnavailable(f"matplotlib: {e}")

        try:
            import shap
        except Exception as e:
            raise _DependencyUnavailable(f"shap: {e}")

        booster = lgb.Booster(model_file=model_path.as_posix())

        X, y, feature_cols = prepare_training_data(features_df=features_df, labels_df=labels_df)

        # 清理/填充数据，避免 pd.NA 带来的兼容性问题
        X = X.replace({pd.NA: np.nan}).astype(float)
        X = X.fillna(X.median())

        total_rows = int(len(X))
        max_samples = int(max(1, max_samples))
        if total_rows > max_samples:
            X_sample = X.sample(n=max_samples, random_state=42)
        else:
            X_sample = X

        repo.set_step_status(step, StepStatus.RUNNING, progress=35, message="计算 SHAP 值")
        session.commit()

        explainer = shap.TreeExplainer(booster)
        shap_values = explainer.shap_values(X_sample)

        # 兼容分类模型返回 list 的情况（当前默认回归）
        if isinstance(shap_values, list):
            shap_values_arr = shap_values[0]
        else:
            shap_values_arr = shap_values

        mean_abs_shap = np.abs(shap_values_arr).mean(axis=0)
        shap_importance = dict(
            sorted(zip(feature_cols, mean_abs_shap.tolist()), key=lambda kv: kv[1], reverse=True)
        )

        repo.set_step_status(step, StepStatus.RUNNING, progress=70, message="生成 summary plots")
        session.commit()

        plot_files: list[tuple[str, str]] = []

        display_n = int(min(len(feature_cols), int(max_display)))
        # 让图更紧凑：避免“字太大/柱太粗”占满页面
        bar_figsize = (8.0, float(max(3.6, min(0.22 * display_n + 1.8, 6.0))))
        dot_figsize = (8.0, float(max(4.2, min(0.26 * display_n + 2.0, 7.2))))

        plot_rc = {
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
        }

        def _save_plot(filename: str, plot_fn, *, figsize: tuple[float, float]) -> tuple[str, Path, str, int]:
            uri = artifacts.artifact_uri(run_id=run_id, kind=ArtifactKind.PLOTS, filename=filename)
            out_path = artifacts.resolve_uri(uri)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with plt.rc_context(plot_rc):
                plt.figure(figsize=figsize)
                plot_fn()
                try:
                    plt.tight_layout()
                except Exception:
                    pass
                plt.savefig(out_path, bbox_inches="tight", dpi=150)
                plt.close()

            sha256 = _sha256_file(out_path)
            bytes_ = out_path.stat().st_size
            return uri, out_path, sha256, bytes_

        # 1) summary bar
        bar_uri, bar_path, bar_sha, bar_bytes = _save_plot(
            "shap_summary_bar.png",
            lambda: shap.summary_plot(
                shap_values_arr,
                X_sample,
                feature_names=feature_cols,
                plot_type="bar",
                max_display=int(max_display),
                show=False,
            ),
            figsize=bar_figsize,
        )
        repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.PLOTS,
            uri=bar_uri,
            sha256=bar_sha,
            bytes_=bar_bytes,
            metadata={
                "plot_type": "shap_summary_bar",
                "model_artifact_id": model_artifact_id,
            },
        )

        # 2) summary dot
        dot_uri, dot_path, dot_sha, dot_bytes = _save_plot(
            "shap_summary_dot.png",
            lambda: shap.summary_plot(
                shap_values_arr,
                X_sample,
                feature_names=feature_cols,
                max_display=int(max_display),
                show=False,
            ),
            figsize=dot_figsize,
        )
        repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.PLOTS,
            uri=dot_uri,
            sha256=dot_sha,
            bytes_=dot_bytes,
            metadata={
                "plot_type": "shap_summary_dot",
                "model_artifact_id": model_artifact_id,
            },
        )

        # 3) metadata json
        repo.set_step_status(step, StepStatus.RUNNING, progress=85, message="写入解释元数据")
        session.commit()

        metadata_payload = {
            "model_artifact_id": model_artifact_id,
            "features_artifact_id": str(features_artifact_id),
            "labels_artifact_id": str(labels_artifact_id),
            "total_rows": total_rows,
            "sampled_rows": int(len(X_sample)),
            "max_display": int(max_display),
            "feature_cols": feature_cols,
            "top20_importance": dict(list(shap_importance.items())[:20]),
        }

        meta_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.PLOTS, filename="shap_metadata.json"
        )
        meta_path = artifacts.resolve_uri(meta_uri)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        meta_sha = _sha256_file(meta_path)
        meta_bytes = meta_path.stat().st_size

        repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.PLOTS,
            uri=meta_uri,
            sha256=meta_sha,
            bytes_=meta_bytes,
            metadata={
                "plot_type": "shap_metadata",
                "model_artifact_id": model_artifact_id,
            },
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session, repo=repo, celery_app=celery_app, run=run, step=step
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "sampled_rows": int(len(X_sample)),
            "plots": [bar_uri, dot_uri, meta_uri],
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
