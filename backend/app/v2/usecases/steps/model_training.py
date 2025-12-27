"""v2 模型训练（数据准备逻辑）。

说明：
- 该模块专注于“对齐特征与标签、选择特征列、构造训练矩阵”。
- 训练本身由 worker 任务执行（避免在单元测试中引入重依赖）。
"""

from __future__ import annotations

import pandas as pd


def prepare_training_data(
    *,
    features_df: pd.DataFrame,
    labels_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    if "label" not in labels_df.columns:
        raise ValueError("labels_df 缺少 label 列")

    # 对齐数据 - 使用 datetime 列进行 merge
    if "datetime" in features_df.columns and "datetime" in labels_df.columns:
        merged_df = pd.merge(features_df, labels_df, on="datetime", how="inner")
    else:
        merged_df = features_df.copy()
        merged_df["label"] = labels_df["label"].values[: len(features_df)]

    merged_df = merged_df.dropna(subset=["label"])

    exclude_cols = {"datetime", "open", "high", "low", "close", "volume", "label"}
    feature_cols = [col for col in merged_df.columns if col not in exclude_cols]

    if not feature_cols:
        raise ValueError("未找到可用于训练的特征列")

    X = merged_df.loc[:, feature_cols].replace([float("inf"), float("-inf")], pd.NA)
    y = merged_df["label"].copy()

    return X, y, feature_cols
