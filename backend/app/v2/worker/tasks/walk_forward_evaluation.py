"""v2 Walk-forward（滚动）验证任务。

输入：
- features artifact（parquet）
- labels artifact（parquet）

输出：
- walk_forward_equity_curve.json（用于前端画图）
- walk_forward_stats.json（总体 + 分窗口统计）

说明：
- 该 step 主要用于 pipeline run，默认从 run.params.pipeline.config 读取配置：
  - label_calculation：用于估计 label 的未来信息泄漏范围
  - model_analysis：用于规则提取（决策树）参数
  - backtest_construction：用于回测执行参数（含手续费/滑点/仓位）
"""

from __future__ import annotations

import json
import traceback
from typing import Any

import numpy as np
import pandas as pd

from app.v2.core.config import settings
from app.v2.domain.types import ArtifactKind, ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.engine import SessionLocal
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.storage.artifact_store import ArtifactStore
from app.v2.usecases.steps.backtest_construction import backtest_strategy, generate_open_signal
from app.v2.usecases.steps.model_analysis import extract_decision_rules, prepare_surrogate_data
from app.v2.worker.celery_app import celery_app
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.utils import _read_dataframe, _sha256_file


def _safe_datetime_series(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"])
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="datetime")
    raise ValueError("数据必须包含 datetime 列或 DatetimeIndex")


def _normalize_and_merge(*, features_df: pd.DataFrame, labels_df: pd.DataFrame) -> pd.DataFrame:
    f = features_df.copy()
    l = labels_df.copy()

    f["datetime"] = _safe_datetime_series(f)
    l["datetime"] = _safe_datetime_series(l)

    if "label" not in l.columns:
        raise ValueError("labels_df 缺少 label 列")

    merged = pd.merge(f, l[["datetime", "label"]], on="datetime", how="inner")
    merged = merged.sort_values("datetime").reset_index(drop=True)
    # 注意：label 可能因为 filter 条件而大量为 NaN，这里不做全局 dropna。
    # 训练/评估时再按窗口对 label 做 dropna，从而保持时间轴长度一致（便于滚动切分）。
    return merged


def _select_top_features_by_corr(
    *,
    df: pd.DataFrame,
    label_col: str,
    max_features: int,
    exclude_cols: set[str],
) -> list[str]:
    y_all = pd.to_numeric(df[label_col], errors="coerce")
    valid_mask = y_all.notna()
    if valid_mask.sum() < 20:
        return []

    df_valid = df.loc[valid_mask].copy()
    y = y_all.loc[valid_mask]
    candidates = [c for c in df.columns if c not in exclude_cols]
    scored: list[tuple[str, float]] = []

    for col in candidates:
        x = pd.to_numeric(df_valid[col], errors="coerce")
        if x.isna().all():
            continue
        corr = x.corr(y)
        if corr is None or not np.isfinite(float(corr)):
            continue
        scored.append((col, abs(float(corr))))

    scored.sort(key=lambda t: t[1], reverse=True)
    return [c for c, _ in scored[: max(1, int(max_features))]]


@celery_app.task(name="v2.walk_forward_evaluation")
def walk_forward_evaluation(
    *,
    run_id: str,
    step_id: str,
    features_artifact_id: str,
    labels_artifact_id: str,
    train_bars: int = 20000,
    test_bars: int = 5000,
    step_bars: int = 5000,
    max_windows: int = 10,
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

        repo.set_step_status(step, StepStatus.RUNNING, progress=15, message="构造数据集（merge features + labels）")
        session.commit()

        merged = _normalize_and_merge(features_df=features_df, labels_df=labels_df)

        pipeline_cfg = ((run.params or {}).get("pipeline") or {}).get("config") or {}
        if not isinstance(pipeline_cfg, dict) or not pipeline_cfg:
            raise ValueError("walk_forward_evaluation 需要 pipeline.config（请通过 pipeline 一键运行）")

        wf_cfg = pipeline_cfg.get("walk_forward_evaluation") or {}
        if isinstance(wf_cfg, dict) and wf_cfg.get("enabled") is False:
            repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="已跳过（enabled=false）")
            is_pipeline = continue_pipeline_if_needed(
                session=session,
                repo=repo,
                celery_app=celery_app,
                run=run,
                step=step,
                produced_state_patch=None,
            )
            if not is_pipeline:
                repo.set_run_status(run, RunStatus.SUCCEEDED)
                session.commit()
            return {"status": "skipped"}

        label_cfg = pipeline_cfg.get("label_calculation") or {}
        label_window = int(label_cfg.get("window", 29))
        label_look_forward = int(label_cfg.get("look_forward", 10))
        label_leakage_bars = int(label_look_forward + (label_window // 2))

        analysis_cfg = pipeline_cfg.get("model_analysis") or {}
        selected_features_cfg = analysis_cfg.get("selected_features")
        max_features = int(analysis_cfg.get("max_features", 8))
        max_depth = int(analysis_cfg.get("max_depth", 3))
        min_samples_split = int(analysis_cfg.get("min_samples_split", 100))
        min_samples_leaf = int(analysis_cfg.get("min_samples_leaf", 50))
        min_rule_samples = int(analysis_cfg.get("min_rule_samples", 50))
        label_threshold = analysis_cfg.get("label_threshold")

        bt_cfg = pipeline_cfg.get("backtest_construction") or {}
        look_forward_bars = int(bt_cfg.get("look_forward_bars", 10))
        initial_balance = float(bt_cfg.get("initial_balance", 1000.0))
        backtest_type = str(bt_cfg.get("backtest_type", "long"))
        filter_type = str(bt_cfg.get("filter_type", "rsi"))
        order_interval_minutes = int(bt_cfg.get("order_interval_minutes", 30))
        min_rule_confidence = float(bt_cfg.get("min_rule_confidence", 0.0))
        pnl_mode = str(bt_cfg.get("pnl_mode", "price"))
        fee_rate = float(bt_cfg.get("fee_rate", 0.0004))
        slippage_bps = float(bt_cfg.get("slippage_bps", 0.0))
        position_fraction = float(bt_cfg.get("position_fraction", 1.0))
        position_notional = bt_cfg.get("position_notional")

        repo.set_step_status(step, StepStatus.RUNNING, progress=25, message="开始滚动窗口训练/回测")
        session.commit()

        train_bars = int(train_bars)
        test_bars = int(test_bars)
        step_bars = int(step_bars)
        max_windows = int(max_windows)

        if train_bars <= 0 or test_bars <= 0 or step_bars <= 0:
            raise ValueError("train_bars/test_bars/step_bars 必须 > 0")
        if max_windows <= 0:
            raise ValueError("max_windows 必须 > 0")

        exclude_cols = {
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "label",
            "open_signal",
            "filter_indicator",
        }

        windows: list[dict[str, Any]] = []
        equity_points: list[dict[str, Any]] = []
        current_balance = float(initial_balance)

        # 延迟导入重依赖
        try:
            from sklearn.tree import DecisionTreeClassifier
        except Exception as e:  # pragma: no cover
            raise ValueError(f"scikit-learn 不可用: {e}")

        n = int(len(merged))

        auto_adjusted = False
        train_bars_requested = int(train_bars)
        test_bars_requested = int(test_bars)
        step_bars_requested = int(step_bars)
        max_windows_requested = int(max_windows)

        min_rows_for_attempt = max(300, int(look_forward_bars) + 50)
        if n < min_rows_for_attempt:
            stats_payload: dict[str, Any] = {
                "status": "skipped",
                "reason": "数据点太少，无法进行滚动验证（建议扩大时间区间或调小周期）",
                "config": {
                    "requested": {
                        "train_bars": train_bars_requested,
                        "test_bars": test_bars_requested,
                        "step_bars": step_bars_requested,
                        "max_windows": max_windows_requested,
                    },
                    "effective": {
                        "train_bars": train_bars_requested,
                        "test_bars": test_bars_requested,
                        "step_bars": step_bars_requested,
                        "max_windows": max_windows_requested,
                    },
                    "auto_adjusted": False,
                    "label_window": int(label_window),
                    "label_look_forward": int(label_look_forward),
                    "label_leakage_bars": int(label_leakage_bars),
                    "rows": int(n),
                },
                "overall": {"windows": 0},
                "windows": [],
            }

            stats_uri = artifacts.artifact_uri(
                run_id=run_id, kind=ArtifactKind.BACKTEST, filename="walk_forward_stats.json"
            )
            stats_path = artifacts.resolve_uri(stats_uri)
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            stats_path.write_text(json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            stats_sha = _sha256_file(stats_path)
            stats_bytes = stats_path.stat().st_size
            stats_artifact = repo.add_artifact(
                run_id=run_id,
                step_id=step_id,
                kind=ArtifactKind.BACKTEST,
                uri=stats_uri,
                sha256=stats_sha,
                bytes_=stats_bytes,
                metadata={"artifact": "walk_forward_stats", "status": "skipped"},
            )

            repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="数据点太少，已跳过")
            is_pipeline = continue_pipeline_if_needed(
                session=session,
                repo=repo,
                celery_app=celery_app,
                run=run,
                step=step,
                produced_state_patch={"walk_forward_stats_artifact_id": stats_artifact.id},
            )
            if not is_pipeline:
                repo.set_run_status(run, RunStatus.SUCCEEDED)
                session.commit()

            return {"status": "skipped", "stats_artifact_id": stats_artifact.id}

        if n < (train_bars + test_bars + 1):
            auto_adjusted = True

            test_bars = max(int(look_forward_bars) + 20, int(n * 0.2))
            train_bars = max(200, int(n * 0.6))

            if train_bars + test_bars + 1 > n:
                train_bars = n - test_bars - 1

            if train_bars < 200:
                test_bars = max(int(look_forward_bars) + 20, int(n * 0.1))
                train_bars = n - test_bars - 1

            if train_bars < 200 or test_bars <= int(look_forward_bars):
                stats_payload = {
                    "status": "skipped",
                    "reason": "数据长度不足以生成 walk-forward 窗口（建议扩大时间区间或调小 train/test 参数）",
                    "config": {
                        "requested": {
                            "train_bars": train_bars_requested,
                            "test_bars": test_bars_requested,
                            "step_bars": step_bars_requested,
                            "max_windows": max_windows_requested,
                        },
                        "effective": {
                            "train_bars": int(train_bars),
                            "test_bars": int(test_bars),
                            "step_bars": int(step_bars_requested),
                            "max_windows": int(max_windows_requested),
                        },
                        "auto_adjusted": True,
                        "label_window": int(label_window),
                        "label_look_forward": int(label_look_forward),
                        "label_leakage_bars": int(label_leakage_bars),
                        "rows": int(n),
                    },
                    "overall": {"windows": 0},
                    "windows": [],
                }

                stats_uri = artifacts.artifact_uri(
                    run_id=run_id, kind=ArtifactKind.BACKTEST, filename="walk_forward_stats.json"
                )
                stats_path = artifacts.resolve_uri(stats_uri)
                stats_path.parent.mkdir(parents=True, exist_ok=True)
                stats_path.write_text(
                    json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                stats_sha = _sha256_file(stats_path)
                stats_bytes = stats_path.stat().st_size
                stats_artifact = repo.add_artifact(
                    run_id=run_id,
                    step_id=step_id,
                    kind=ArtifactKind.BACKTEST,
                    uri=stats_uri,
                    sha256=stats_sha,
                    bytes_=stats_bytes,
                    metadata={"artifact": "walk_forward_stats", "status": "skipped"},
                )

                repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="数据不足，已跳过")
                is_pipeline = continue_pipeline_if_needed(
                    session=session,
                    repo=repo,
                    celery_app=celery_app,
                    run=run,
                    step=step,
                    produced_state_patch={"walk_forward_stats_artifact_id": stats_artifact.id},
                )
                if not is_pipeline:
                    repo.set_run_status(run, RunStatus.SUCCEEDED)
                    session.commit()

                return {"status": "skipped", "stats_artifact_id": stats_artifact.id}

            # step_bars 不应大于 test_bars，否则每次移动过大可能只生成 1 个窗口
            step_bars = min(int(step_bars_requested), int(test_bars))
            max_windows = int(max_windows_requested)

            repo.set_step_status(
                step,
                StepStatus.RUNNING,
                progress=25,
                message=f"数据不足，已自动缩小窗口（train={int(train_bars)} test={int(test_bars)}）",
            )
            session.commit()

        window_idx = 0
        start = 0

        while window_idx < max_windows:
            train_start = int(start)
            train_end = int(train_start + train_bars)
            test_end = int(train_end + test_bars)

            if test_end > n:
                break

            # 训练集末尾丢弃可能引用未来数据的样本，避免信息泄漏
            train_end_effective = int(max(train_start, train_end - label_leakage_bars))
            train_slice = merged.iloc[train_start:train_end_effective].copy()
            test_slice = merged.iloc[train_end:test_end].copy()

            if len(train_slice) < 200 or len(test_slice) <= look_forward_bars:
                break

            if isinstance(selected_features_cfg, list) and selected_features_cfg:
                selected_features = [str(x) for x in selected_features_cfg]
            else:
                selected_features = _select_top_features_by_corr(
                    df=train_slice,
                    label_col="label",
                    max_features=max_features,
                    exclude_cols=set(exclude_cols),
                )

            if not selected_features:
                fallback_candidates: list[str] = []
                for col in train_slice.columns:
                    if col in exclude_cols:
                        continue
                    if not pd.api.types.is_numeric_dtype(train_slice[col]):
                        continue
                    if train_slice[col].isna().all():
                        continue
                    fallback_candidates.append(col)

                selected_features = fallback_candidates[: max(1, int(max_features))]

            if not selected_features:
                window_idx += 1
                start += step_bars
                continue

            X_train, y_train_bin, used_threshold = prepare_surrogate_data(
                features_df=train_slice.drop(columns=["label"], errors="ignore"),
                labels_df=train_slice[["datetime", "label"]],
                selected_features=selected_features,
                label_threshold=label_threshold,
            )

            if len(X_train) < 20:
                window_idx += 1
                start += step_bars
                continue

            min_samples_split_eff = min(int(min_samples_split), max(2, int(len(X_train) // 2)))
            min_samples_leaf_eff = min(int(min_samples_leaf), max(1, int(len(X_train) // 10)))
            min_rule_samples_eff = min(int(min_rule_samples), max(1, int(len(X_train) // 4)))

            dt_model = DecisionTreeClassifier(
                max_depth=max_depth,
                min_samples_split=min_samples_split_eff,
                min_samples_leaf=min_samples_leaf_eff,
                random_state=42,
            )
            try:
                dt_model.fit(X_train, y_train_bin)
            except Exception:
                window_idx += 1
                start += step_bars
                continue
            train_accuracy = float((dt_model.predict(X_train) == y_train_bin).mean()) if len(X_train) else 0.0

            rules = extract_decision_rules(
                tree_model=dt_model,
                feature_names=list(X_train.columns),
                min_samples=min_rule_samples_eff,
            )

            # 在测试集上生成信号并回测
            test_slice = test_slice.copy()
            test_slice["open_signal"] = generate_open_signal(
                df=test_slice.drop(columns=["label"], errors="ignore"),
                decision_rules=list(rules),
                backtest_type=backtest_type,  # type: ignore[arg-type]
                min_confidence=float(min_rule_confidence),
            )

            equity_df, _trades_df, bt_stats = backtest_strategy(
                df=test_slice.drop(columns=["label"], errors="ignore"),
                look_forward_bars=int(look_forward_bars),
                initial_balance=float(current_balance),
                backtest_type=backtest_type,  # type: ignore[arg-type]
                filter_type=filter_type,  # type: ignore[arg-type]
                order_interval_minutes=int(order_interval_minutes),
                pnl_mode=str(pnl_mode),
                fee_rate=float(fee_rate),
                slippage_bps=float(slippage_bps),
                position_fraction=float(position_fraction),
                position_notional=float(position_notional) if position_notional is not None else None,
            )

            # 追加到整体 equity（去重衔接点）
            points_df = equity_df.reset_index()
            if "index" in points_df.columns and "datetime" not in points_df.columns:
                points_df = points_df.rename(columns={"index": "datetime"})
            if "datetime" in points_df.columns:
                points_df["datetime"] = points_df["datetime"].astype(str)
            window_points = points_df.to_dict(orient="records")
            if equity_points and window_points:
                if equity_points[-1].get("datetime") == window_points[0].get("datetime"):
                    window_points = window_points[1:]
            equity_points.extend(window_points)

            current_balance = float(bt_stats.get("final_balance", current_balance))

            windows.append(
                {
                    "window_index": int(window_idx + 1),
                    "train_start": str(train_slice["datetime"].iloc[0]),
                    "train_end": str(train_slice["datetime"].iloc[-1]),
                    "test_start": str(test_slice["datetime"].iloc[0]),
                    "test_end": str(test_slice["datetime"].iloc[-1]),
                    "train_rows": int(len(train_slice)),
                    "test_rows": int(len(test_slice)),
                    "selected_features": list(selected_features),
                    "label_threshold_used": float(used_threshold) if used_threshold is not None else None,
                    "rules_count": int(len(rules)),
                    "train_accuracy": float(train_accuracy),
                    "backtest_stats": bt_stats,
                }
            )

            window_idx += 1
            start += step_bars

            progress = 25 + int((window_idx / max_windows) * 55)
            repo.set_step_status(step, StepStatus.RUNNING, progress=min(80, progress), message=f"窗口 {window_idx}/{max_windows}")
            session.commit()

            # 软取消
            session.refresh(run, attribute_names=["status"])
            if run.status == RunStatus.CANCELED.value:
                repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
                repo.set_run_status(run, RunStatus.CANCELED)
                session.commit()
                return {"status": "canceled"}

        if not windows:
            stats_payload = {
                "status": "skipped",
                "reason": "未生成任何有效窗口（可能数据过短或窗口参数不合理）",
                "config": {
                    "requested": {
                        "train_bars": int(train_bars_requested),
                        "test_bars": int(test_bars_requested),
                        "step_bars": int(step_bars_requested),
                        "max_windows": int(max_windows_requested),
                    },
                    "effective": {
                        "train_bars": int(train_bars),
                        "test_bars": int(test_bars),
                        "step_bars": int(step_bars),
                        "max_windows": int(max_windows),
                    },
                    "auto_adjusted": bool(auto_adjusted),
                    "label_window": int(label_window),
                    "label_look_forward": int(label_look_forward),
                    "label_leakage_bars": int(label_leakage_bars),
                    "rows": int(n),
                },
                "overall": {"windows": 0},
                "windows": [],
            }

            stats_uri = artifacts.artifact_uri(
                run_id=run_id, kind=ArtifactKind.BACKTEST, filename="walk_forward_stats.json"
            )
            stats_path = artifacts.resolve_uri(stats_uri)
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            stats_path.write_text(json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            stats_sha = _sha256_file(stats_path)
            stats_bytes = stats_path.stat().st_size
            stats_artifact = repo.add_artifact(
                run_id=run_id,
                step_id=step_id,
                kind=ArtifactKind.BACKTEST,
                uri=stats_uri,
                sha256=stats_sha,
                bytes_=stats_bytes,
                metadata={"artifact": "walk_forward_stats", "status": "skipped"},
            )

            repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="未生成窗口，已跳过")
            is_pipeline = continue_pipeline_if_needed(
                session=session,
                repo=repo,
                celery_app=celery_app,
                run=run,
                step=step,
                produced_state_patch={"walk_forward_stats_artifact_id": stats_artifact.id},
            )
            if not is_pipeline:
                repo.set_run_status(run, RunStatus.SUCCEEDED)
                session.commit()

            return {"status": "skipped", "stats_artifact_id": stats_artifact.id}

        # overall stats
        overall_profit = float(current_balance - initial_balance)
        overall_profit_rate = float(overall_profit / initial_balance) if initial_balance > 0 else 0.0
        window_profit_rates = [
            float((w.get("backtest_stats") or {}).get("profit_rate", 0.0)) for w in windows
        ]
        profitable_windows = int(sum(1 for r in window_profit_rates if r > 0))

        overall = {
            "windows": int(len(windows)),
            "profitable_windows": int(profitable_windows),
            "avg_window_profit_rate": float(np.mean(window_profit_rates)) if window_profit_rates else 0.0,
            "median_window_profit_rate": float(np.median(window_profit_rates)) if window_profit_rates else 0.0,
            "initial_balance": float(initial_balance),
            "final_balance": float(current_balance),
            "profit": float(overall_profit),
            "profit_rate": float(overall_profit_rate),
        }

        # max_drawdown（基于拼接后的 equity 曲线）
        balances: list[float] = []
        for p in equity_points:
            try:
                b = float(p.get("balance"))  # type: ignore[arg-type]
            except Exception:
                continue
            if np.isfinite(b):
                balances.append(b)
        if balances:
            max_balance = balances[0]
            max_dd = 0.0
            for b in balances:
                if b > max_balance:
                    max_balance = b
                if max_balance > 0:
                    dd = (max_balance - b) / max_balance
                    if dd > max_dd:
                        max_dd = dd
            overall["max_drawdown"] = float(max_dd)

        # 保存 equity json
        max_points = 5000
        if len(equity_points) > max_points:
            equity_points = equity_points[-max_points:]

        equity_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.BACKTEST, filename="walk_forward_equity_curve.json"
        )
        equity_path = artifacts.resolve_uri(equity_uri)
        equity_path.parent.mkdir(parents=True, exist_ok=True)
        equity_path.write_text(json.dumps({"points": equity_points}, ensure_ascii=False), encoding="utf-8")

        equity_sha = _sha256_file(equity_path)
        equity_bytes = equity_path.stat().st_size
        equity_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.BACKTEST,
            uri=equity_uri,
            sha256=equity_sha,
            bytes_=equity_bytes,
            metadata={"artifact": "walk_forward_equity_curve"},
        )

        # 保存 stats json
        stats_payload: dict[str, Any] = {
            "status": "success",
            "config": {
                "requested": {
                    "train_bars": int(train_bars_requested),
                    "test_bars": int(test_bars_requested),
                    "step_bars": int(step_bars_requested),
                    "max_windows": int(max_windows_requested),
                },
                "effective": {
                    "train_bars": int(train_bars),
                    "test_bars": int(test_bars),
                    "step_bars": int(step_bars),
                    "max_windows": int(max_windows),
                },
                "auto_adjusted": bool(auto_adjusted),
                "label_window": int(label_window),
                "label_look_forward": int(label_look_forward),
                "label_leakage_bars": int(label_leakage_bars),
                "rows": int(n),
                "analysis": {
                    "selected_features": selected_features_cfg,
                    "max_features": int(max_features),
                    "max_depth": int(max_depth),
                    "min_samples_split": int(min_samples_split),
                    "min_samples_leaf": int(min_samples_leaf),
                    "min_rule_samples": int(min_rule_samples),
                    "label_threshold": label_threshold,
                },
                "backtest": {
                    "look_forward_bars": int(look_forward_bars),
                    "backtest_type": str(backtest_type),
                    "filter_type": str(filter_type),
                    "order_interval_minutes": int(order_interval_minutes),
                    "min_rule_confidence": float(min_rule_confidence),
                    "pnl_mode": str(pnl_mode),
                    "fee_rate": float(fee_rate),
                    "slippage_bps": float(slippage_bps),
                    "position_fraction": float(position_fraction),
                    "position_notional": position_notional,
                },
            },
            "overall": overall,
            "windows": windows,
        }

        stats_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.BACKTEST, filename="walk_forward_stats.json"
        )
        stats_path = artifacts.resolve_uri(stats_uri)
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        stats_sha = _sha256_file(stats_path)
        stats_bytes = stats_path.stat().st_size
        stats_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.BACKTEST,
            uri=stats_uri,
            sha256=stats_sha,
            bytes_=stats_bytes,
            metadata={"artifact": "walk_forward_stats"},
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session,
            repo=repo,
            celery_app=celery_app,
            run=run,
            step=step,
            produced_state_patch={
                "walk_forward_equity_curve_artifact_id": equity_artifact.id,
                "walk_forward_stats_artifact_id": stats_artifact.id,
            },
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "equity_artifact_id": equity_artifact.id,
            "stats_artifact_id": stats_artifact.id,
            "windows": int(len(windows)),
            "overall": overall,
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
