"""v2 代理模型分析（规则/阈值提取）。

目标：
- 从 features/labels 构造训练数据
- 将连续 label 二值化
- 训练浅层决策树并提取规则，用于后续回测构建

注意：
- 该模块依赖 scikit-learn，用例层可单测。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.v2.usecases.steps.model_training import prepare_training_data


def prepare_surrogate_data(
    *,
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
    selected_features: list[str],
    label_threshold: float | None = None,
) -> tuple[pd.DataFrame, pd.Series, float | None]:
    if not selected_features:
        raise ValueError("selected_features 不能为空")

    X_all, y, _feature_cols = prepare_training_data(features_df=features_df, labels_df=labels_df)

    missing = [f for f in selected_features if f not in X_all.columns]
    if missing:
        raise ValueError(f"特征不存在: {missing}")

    X = X_all.loc[:, selected_features].copy()

    X = X.replace([np.inf, -np.inf], np.nan)
    X = X.fillna(X.median(numeric_only=True))

    valid_mask = ~y.isna()
    X = X.loc[valid_mask]
    y = y.loc[valid_mask]

    y_bin, used_threshold = binarize_label(y, threshold=label_threshold)
    return X, y_bin, used_threshold


def binarize_label(
    y: pd.Series, *, threshold: float | None = None
) -> tuple[pd.Series, float | None]:
    unique = pd.unique(y.dropna())

    if len(unique) <= 2:
        # 尽量保持 0/1
        y_int = y.astype(int)
        return y_int, None

    used_threshold = float(threshold if threshold is not None else y.median())
    y_bin = (y > used_threshold).astype(int)
    return y_bin, used_threshold


def extract_decision_rules(
    *,
    tree_model: Any,
    feature_names: list[str],
    min_samples: int = 50,
) -> list[dict[str, Any]]:
    """从 sklearn 决策树中提取规则与阈值。"""

    # 延迟导入以便测试/运行时可控
    from sklearn.tree import _tree

    tree = tree_model.tree_
    rules: list[dict[str, Any]] = []

    def recurse(
        node: int, path_conditions: list[str], path_features: list[str], path_thresholds: list[dict]
    ):
        if tree.feature[node] == _tree.TREE_UNDEFINED:
            samples = int(tree.n_node_samples[node])
            value = tree.value[node][0]

            class_dist = {str(i): int(v) for i, v in enumerate(value)}
            predicted_class = int(np.argmax(value))
            confidence = float(value[predicted_class] / max(1, samples))

            if samples >= int(min_samples):
                rules.append(
                    {
                        "rule_id": len(rules) + 1,
                        "path": " AND ".join(path_conditions) if path_conditions else "root",
                        "features_used": sorted(set(path_features)),
                        "thresholds": list(path_thresholds),
                        "samples": samples,
                        "class_distribution": class_dist,
                        "predicted_class": predicted_class,
                        "confidence": confidence,
                    }
                )
            return

        feature_idx = int(tree.feature[node])
        feature_name = feature_names[feature_idx]
        threshold = float(tree.threshold[node])

        left_condition = f"{feature_name} <= {threshold:.6f}"
        left_threshold = {"feature": feature_name, "operator": "<=", "value": threshold}
        recurse(
            int(tree.children_left[node]),
            path_conditions + [left_condition],
            path_features + [feature_name],
            path_thresholds + [left_threshold],
        )

        right_condition = f"{feature_name} > {threshold:.6f}"
        right_threshold = {"feature": feature_name, "operator": ">", "value": threshold}
        recurse(
            int(tree.children_right[node]),
            path_conditions + [right_condition],
            path_features + [feature_name],
            path_thresholds + [right_threshold],
        )

    recurse(0, [], [], [])

    rules.sort(key=lambda x: x["confidence"], reverse=True)
    return rules
