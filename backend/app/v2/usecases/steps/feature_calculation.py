"""v2 特征计算（纯逻辑）。

该模块将“特征计算”从 Celery/IO 中解耦出来，便于单元测试。
"""

from __future__ import annotations

import pandas as pd

from app.v2.domain.features.data_processor import DataProcessor


SUPPORTED_ALPHA_TYPES: dict[str, str] = {
    "alpha158": "generate_features_alpha158",
    "alpha216": "generate_features_alpha216",
    "alpha101": "generate_features_alpha101",
    "alpha191": "generate_features_alpha191",
    "alpha_ch": "generate_features_alpha_ch",
}


def calculate_features_df(
    *,
    raw_df: pd.DataFrame,
    alpha_types: list[str],
    instrument_name: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    if not alpha_types:
        raise ValueError("alpha_types 不能为空")

    normalized = [str(t).strip() for t in alpha_types]
    unknown = [t for t in normalized if t not in SUPPORTED_ALPHA_TYPES]
    if unknown:
        raise ValueError(f"不支持的 alpha 类型: {unknown}")

    processor = DataProcessor(raw_df, instrument_name=instrument_name or "")

    for alpha_type in normalized:
        method_name = SUPPORTED_ALPHA_TYPES[alpha_type]
        method = getattr(processor, method_name)
        method()

    feature_cols = list(processor.feature_columns)

    df = processor.df.copy()
    df = df.reset_index() if isinstance(df.index, pd.DatetimeIndex) else df

    cols_to_save: list[str] = []

    if "datetime" in df.columns:
        cols_to_save.append("datetime")

    base_cols = ["open", "high", "low", "close", "volume"]
    cols_to_save.extend([c for c in base_cols if c in df.columns])
    cols_to_save.extend([c for c in feature_cols if c in df.columns])

    features_df = df.loc[:, cols_to_save].copy()
    return features_df, feature_cols
