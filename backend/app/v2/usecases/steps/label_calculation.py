"""v2 标签计算（纯逻辑）。

说明：当前复用 v1 的标签计算公式，但修复了其导入路径问题，
使其可作为包内模块被正常引用。
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from app.v2.domain.labels import calculate_label_with_filter


def calculate_labels_df(
    *,
    raw_df: pd.DataFrame,
    window: int = 29,
    look_forward: int = 10,
    label_type: Literal["up", "down"] = "up",
    filter_type: Literal["rsi", "cti"] = "rsi",
    threshold: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = raw_df.copy()

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index().rename(columns={"index": "datetime"})
    else:
        raise ValueError("raw_df 必须包含 datetime 列或 DatetimeIndex")

    if "close" not in df.columns:
        raise ValueError("raw_df 缺少 close 列")

    labels = calculate_label_with_filter(
        df,
        window=int(window),
        look_forward=int(look_forward),
        label_type=label_type,
        filter_type=filter_type,
        threshold=threshold,
    )

    label_series = pd.Series(labels, index=df.index if hasattr(labels, "index") else None)

    labels_df = pd.DataFrame({"datetime": df["datetime"], "label": label_series.values})

    non_nan = labels_df["label"].notna().sum()
    label_stats = {
        "total_samples": int(len(labels_df)),
        "non_nan_labels": int(non_nan),
        "label_mean": float(labels_df["label"].mean()) if non_nan > 0 else 0.0,
        "label_std": float(labels_df["label"].std()) if non_nan > 0 else 0.0,
        "positive_ratio": float((labels_df["label"] > 0.5).sum() / non_nan) if non_nan > 0 else 0.0,
    }

    # 确保不会引入 numpy 类型
    for k, v in list(label_stats.items()):
        if isinstance(v, (np.floating, np.integer)):
            label_stats[k] = v.item()

    return labels_df, label_stats
