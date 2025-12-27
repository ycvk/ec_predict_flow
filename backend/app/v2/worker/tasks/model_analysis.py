"""v2 代理模型分析任务（规则/阈值提取）。

输入：model artifact（可选显式传 features/labels artifacts，否则从 model metadata 推导）
输出：analysis artifacts（json + tree txt）

注意：避免在模块导入时加载重依赖（scikit-learn）。
"""

from __future__ import annotations

from app.v2.worker.utils import _sha256_file, _read_dataframe
import json
import traceback
from pathlib import Path

import pandas as pd

from app.v2.core.config import settings
from app.v2.domain.types import ArtifactKind, ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.engine import SessionLocal
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.storage.artifact_store import ArtifactStore
from app.v2.usecases.steps.model_analysis import extract_decision_rules, prepare_surrogate_data
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.celery_app import celery_app






class _DependencyUnavailable(Exception):
    pass


@celery_app.task(name="v2.model_analysis")
def model_analysis(
    *,
    run_id: str,
    step_id: str,
    model_artifact_id: str,
    features_artifact_id: str | None = None,
    labels_artifact_id: str | None = None,
    selected_features: list[str] | None = None,
    max_features: int = 8,
    max_depth: int = 3,
    min_samples_split: int = 100,
    min_samples_leaf: int = 50,
    min_rule_samples: int = 50,
    label_threshold: float | None = None,
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

        repo.set_step_status(step, StepStatus.RUNNING, progress=20, message="准备代理模型训练数据")
        session.commit()

        # 软取消
        session.refresh(run)
        if run.status == RunStatus.CANCELED.value:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
            repo.set_run_status(run, RunStatus.CANCELED)
            session.commit()
            return {"status": "canceled"}

        # 自动选择特征：优先使用模型训练的 top20 importance
        auto_selected = False
        if not selected_features:
            auto_selected = True
            max_features = int(max(1, max_features))
            top = meta.get("top20_importance")
            if isinstance(top, dict) and top:
                selected_features = [k for k, _v in list(top.items())[:max_features]]
            else:
                # fallback：尝试从 features_df 找出所有 feature_* 列
                selected_features = [c for c in features_df.columns if c.startswith("feature_")][
                    :max_features
                ]

        if not selected_features:
            raise ValueError("selected_features 为空，且无法自动推导")

        X, y_bin, used_threshold = prepare_surrogate_data(
            features_df=features_df,
            labels_df=labels_df,
            selected_features=list(selected_features),
            label_threshold=label_threshold,
        )

        repo.set_step_status(step, StepStatus.RUNNING, progress=40, message="训练决策树代理模型")
        session.commit()

        try:
            from sklearn.tree import DecisionTreeClassifier
            from sklearn.tree import export_text
        except Exception as e:
            raise _DependencyUnavailable(str(e))

        dt_model = DecisionTreeClassifier(
            max_depth=int(max_depth),
            min_samples_split=int(min_samples_split),
            min_samples_leaf=int(min_samples_leaf),
            random_state=42,
        )
        dt_model.fit(X, y_bin)

        train_accuracy = float(dt_model.score(X, y_bin))
        feature_importance = {
            str(name): float(val)
            for name, val in zip(list(X.columns), dt_model.feature_importances_)
        }

        repo.set_step_status(step, StepStatus.RUNNING, progress=70, message="提取阈值规则")
        session.commit()

        rules = extract_decision_rules(
            tree_model=dt_model,
            feature_names=list(X.columns),
            min_samples=int(min_rule_samples),
        )

        tree_text = export_text(dt_model, feature_names=list(X.columns))

        result_payload = {
            "status": "success",
            "model_artifact_id": model_artifact_id,
            "features_artifact_id": str(features_artifact_id),
            "labels_artifact_id": str(labels_artifact_id),
            "selected_features": list(X.columns),
            "auto_selected": bool(auto_selected),
            "label_threshold": used_threshold,
            "tree": {
                "max_depth": int(max_depth),
                "min_samples_split": int(min_samples_split),
                "min_samples_leaf": int(min_samples_leaf),
                "min_rule_samples": int(min_rule_samples),
                "train_accuracy": train_accuracy,
            },
            "feature_importance": feature_importance,
            "decision_rules": rules,
            "total_samples": int(len(X)),
            "message": f"提取 {len(rules)} 条规则",
        }

        repo.set_step_status(step, StepStatus.RUNNING, progress=90, message="保存分析产物")
        session.commit()

        # 1) JSON
        json_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.ANALYSIS, filename="surrogate_rules.json"
        )
        json_path = artifacts.resolve_uri(json_uri)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        json_sha = _sha256_file(json_path)
        json_bytes = json_path.stat().st_size

        rules_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.ANALYSIS,
            uri=json_uri,
            sha256=json_sha,
            bytes_=json_bytes,
            metadata={"analysis_type": "surrogate_rules", "model_artifact_id": model_artifact_id},
        )

        # 2) tree text
        tree_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.ANALYSIS, filename="surrogate_tree.txt"
        )
        tree_path = artifacts.resolve_uri(tree_uri)
        tree_path.parent.mkdir(parents=True, exist_ok=True)
        tree_path.write_text(tree_text, encoding="utf-8")

        tree_sha = _sha256_file(tree_path)
        tree_bytes = tree_path.stat().st_size

        repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.ANALYSIS,
            uri=tree_uri,
            sha256=tree_sha,
            bytes_=tree_bytes,
            metadata={"analysis_type": "surrogate_tree", "model_artifact_id": model_artifact_id},
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session,
            repo=repo,
            celery_app=celery_app,
            run=run,
            step=step,
            produced_state_patch={"analysis_artifact_id": rules_artifact.id},
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "artifact_id": rules_artifact.id,
            "rules": len(rules),
            "train_accuracy": train_accuracy,
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
