"""v2 回测构建与执行任务（简化版）。

输入：
- features artifact（parquet）
- analysis artifact（model_analysis 产物，包含 decision_rules）

输出：
- backtest artifacts（equity/trades/stats）

说明：
- 先跑通“rules → backtest artifacts”的可追溯闭环。
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
from app.v2.usecases.steps.backtest_construction import backtest_strategy, generate_open_signal
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.celery_app import celery_app






@celery_app.task(name="v2.backtest_construction")
def backtest_construction(
    *,
    run_id: str,
    step_id: str,
    features_artifact_id: str,
    analysis_artifact_id: str,
    look_forward_bars: int = 10,
    win_profit: float = 4.0,
    loss_cost: float = 5.0,
    initial_balance: float = 1000.0,
    backtest_type: str = "long",
    filter_type: str = "rsi",
    order_interval_minutes: int = 30,
    min_rule_confidence: float = 0.0,
    pnl_mode: str = "price",
    fee_rate: float = 0.0004,
    slippage_bps: float = 0.0,
    position_fraction: float = 1.0,
    position_notional: float | None = None,
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
        analysis_artifact = repo.get_artifact(analysis_artifact_id)

        if features_artifact is None:
            raise ValueError("features_artifact_id 不存在")
        if analysis_artifact is None:
            raise ValueError("analysis_artifact_id 不存在")

        features_path = artifacts.resolve_uri(features_artifact.uri)
        analysis_path = artifacts.resolve_uri(analysis_artifact.uri)

        if not features_path.exists():
            raise FileNotFoundError("features 文件缺失")
        if not analysis_path.exists():
            raise FileNotFoundError("analysis 文件缺失")

        df = _read_dataframe(features_path)

        analysis_payload = json.loads(analysis_path.read_text(encoding="utf-8"))
        decision_rules = analysis_payload.get("decision_rules") or []

        repo.set_step_status(step, StepStatus.RUNNING, progress=25, message="验证特征并生成信号")
        session.commit()

        required_features = set()
        for rule in decision_rules:
            for threshold in rule.get("thresholds") or []:
                feat = threshold.get("feature")
                if feat:
                    required_features.add(feat)

        missing = [f for f in required_features if f not in df.columns]
        if missing:
            raise ValueError(f"features 数据缺少列: {missing}")

        df = df.copy()
        df["open_signal"] = generate_open_signal(
            df=df,
            decision_rules=list(decision_rules),
            backtest_type=backtest_type,  # type: ignore[arg-type]
            min_confidence=float(min_rule_confidence),
        )

        repo.set_step_status(step, StepStatus.RUNNING, progress=50, message="执行回测")
        session.commit()

        # 软取消
        session.refresh(run)
        if run.status == RunStatus.CANCELED.value:
            repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
            repo.set_run_status(run, RunStatus.CANCELED)
            session.commit()
            return {"status": "canceled"}

        equity_df, trades_df, stats = backtest_strategy(
            df=df,
            look_forward_bars=int(look_forward_bars),
            win_profit=float(win_profit),
            loss_cost=float(loss_cost),
            initial_balance=float(initial_balance),
            backtest_type=backtest_type,  # type: ignore[arg-type]
            filter_type=filter_type,  # type: ignore[arg-type]
            order_interval_minutes=int(order_interval_minutes),
            pnl_mode=str(pnl_mode),
            fee_rate=float(fee_rate),
            slippage_bps=float(slippage_bps),
            position_fraction=float(position_fraction),
            position_notional=float(position_notional) if position_notional is not None else None,
        )

        repo.set_step_status(step, StepStatus.RUNNING, progress=85, message="保存回测产物")
        session.commit()

        # equity
        equity_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.BACKTEST, filename="equity_curve.parquet"
        )
        equity_path = artifacts.resolve_uri(equity_uri)
        equity_path.parent.mkdir(parents=True, exist_ok=True)
        equity_df.reset_index().to_parquet(equity_path, index=False)

        equity_sha = _sha256_file(equity_path)
        equity_bytes = equity_path.stat().st_size

        equity_parquet_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.BACKTEST,
            uri=equity_uri,
            sha256=equity_sha,
            bytes_=equity_bytes,
            metadata={"artifact": "equity_curve", "backtest_type": backtest_type},
        )

        # equity json（用于前端画图，避免解析 parquet）
        equity_payload_df = equity_df.reset_index()
        if "index" in equity_payload_df.columns and "datetime" not in equity_payload_df.columns:
            equity_payload_df = equity_payload_df.rename(columns={"index": "datetime"})
        if "datetime" in equity_payload_df.columns:
            equity_payload_df["datetime"] = equity_payload_df["datetime"].astype(str)

        max_points = 5000
        if len(equity_payload_df) > max_points:
            equity_payload_df = equity_payload_df.iloc[-max_points:].copy()

        equity_json_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.BACKTEST, filename="equity_curve.json"
        )
        equity_json_path = artifacts.resolve_uri(equity_json_uri)
        equity_json_path.parent.mkdir(parents=True, exist_ok=True)
        equity_json_path.write_text(
            json.dumps(
                {"points": equity_payload_df.to_dict(orient="records")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        equity_json_sha = _sha256_file(equity_json_path)
        equity_json_bytes = equity_json_path.stat().st_size

        equity_json_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.BACKTEST,
            uri=equity_json_uri,
            sha256=equity_json_sha,
            bytes_=equity_json_bytes,
            metadata={"artifact": "equity_curve_json", "source": equity_parquet_artifact.id},
        )

        # trades
        trades_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.BACKTEST, filename="trades.parquet"
        )
        trades_path = artifacts.resolve_uri(trades_uri)
        trades_path.parent.mkdir(parents=True, exist_ok=True)

        if len(trades_df) > 0:
            trades_df.reset_index().to_parquet(trades_path, index=False)
        else:
            pd.DataFrame([]).to_parquet(trades_path, index=False)

        trades_sha = _sha256_file(trades_path)
        trades_bytes = trades_path.stat().st_size

        repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.BACKTEST,
            uri=trades_uri,
            sha256=trades_sha,
            bytes_=trades_bytes,
            metadata={"artifact": "trades", "backtest_type": backtest_type},
        )

        # stats json
        stats_payload = {
            "status": "success",
            "inputs": {
                "features_artifact_id": features_artifact_id,
                "analysis_artifact_id": analysis_artifact_id,
            },
            "config": {
                "look_forward_bars": int(look_forward_bars),
                "win_profit": float(win_profit),
                "loss_cost": float(loss_cost),
                "initial_balance": float(initial_balance),
                "backtest_type": backtest_type,
                "filter_type": filter_type,
                "order_interval_minutes": int(order_interval_minutes),
                "min_rule_confidence": float(min_rule_confidence),
                "pnl_mode": str(pnl_mode),
                "fee_rate": float(fee_rate),
                "slippage_bps": float(slippage_bps),
                "position_fraction": float(position_fraction),
                "position_notional": float(position_notional) if position_notional is not None else None,
            },
            "stats": stats,
            "signals": {
                "open_signal_count": int(df["open_signal"].sum()),
                "rules_count": int(len(decision_rules)),
            },
        }

        stats_uri = artifacts.artifact_uri(
            run_id=run_id, kind=ArtifactKind.BACKTEST, filename="backtest_stats.json"
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
            metadata={"artifact": "backtest_stats", "backtest_type": backtest_type},
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session,
            repo=repo,
            celery_app=celery_app,
            run=run,
            step=step,
            produced_state_patch={
                "backtest_stats_artifact_id": stats_artifact.id,
                "equity_curve_json_artifact_id": equity_json_artifact.id,
            },
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "stats_artifact_id": stats_artifact.id,
            "stats": stats,
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
