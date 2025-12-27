"""v2 数据下载任务。

说明：
- 任务进度与结果写入 PostgreSQL（Run/Step/Artifact）。
- 产物文件写入 ArtifactStore（payload），并记录元数据与校验信息。
"""

from __future__ import annotations

from app.v2.worker.utils import _sha256_file
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from binance.um_futures import UMFutures

from app.v2.core.config import settings
from app.v2.domain.types import ArtifactKind, ErrorCode, ErrorPayload, RunStatus, StepStatus
from app.v2.infra.db.engine import SessionLocal
from app.v2.infra.db.repositories import RunRepository
from app.v2.infra.storage.artifact_store import ArtifactStore
from app.v2.worker.pipeline import continue_pipeline_if_needed
from app.v2.worker.celery_app import celery_app


def _get_interval_delta(interval: str) -> timedelta:
    if interval.endswith("m"):
        return timedelta(minutes=int(interval[:-1] or "1"))
    if interval.endswith("h"):
        return timedelta(hours=int(interval[:-1] or "1"))
    if interval.endswith("d"):
        return timedelta(days=int(interval[:-1] or "1"))
    if interval.endswith("w"):
        return timedelta(weeks=int(interval[:-1] or "1"))
    return timedelta(minutes=1)




@celery_app.task(name="v2.data_download")
def data_download(
    *,
    run_id: str,
    step_id: str,
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "1m",
    proxy: str | None = None,
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
        repo.set_step_status(step, StepStatus.RUNNING, progress=0, message="初始化 Binance 客户端")
        session.commit()

        # 初始化 Binance 客户端
        proxies = None
        if proxy:
            proxies = {"https": proxy}
        elif settings.BINANCE_PROXY:
            proxies = {"https": settings.BINANCE_PROXY}

        client = UMFutures(proxies=proxies)

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        interval_delta = _get_interval_delta(interval)

        repo.set_step_status(
            step, StepStatus.RUNNING, progress=5, message=f"开始下载 {symbol} {interval}"
        )
        session.commit()

        all_data: list[list] = []
        limit = 1000
        current_start = start_dt

        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()
        total_duration = max(1.0, end_ts - start_ts)

        batch_count = 0

        while current_start < end_dt:
            # 软取消：如果 run 被标记取消则停止
            session.refresh(run)
            if run.status == RunStatus.CANCELED.value:
                repo.set_step_status(step, StepStatus.CANCELED, message="已取消")
                repo.set_run_status(run, RunStatus.CANCELED)
                session.commit()
                return {"status": "canceled"}

            batch_count += 1
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "startTime": int(current_start.timestamp() * 1000),
                "endTime": int(end_dt.timestamp() * 1000),
            }

            klines = client.klines(**params)
            if not klines:
                break

            all_data.extend(klines)

            last_time = int(klines[-1][0]) / 1000
            current_start = datetime.fromtimestamp(last_time) + interval_delta

            progress_percent = 5 + ((last_time - start_ts) / total_duration) * 80
            progress_percent = int(max(5, min(90, progress_percent)))

            repo.set_step_status(
                step,
                StepStatus.RUNNING,
                progress=progress_percent,
                message=f"第 {batch_count} 批：累计 {len(all_data)} 条",
            )
            session.commit()

            if len(klines) < limit:
                break

        if not all_data:
            raise ValueError("未获取到任何数据")

        repo.set_step_status(step, StepStatus.RUNNING, progress=92, message="处理并写入产物")
        session.commit()

        df = pd.DataFrame(
            all_data,
            columns=[
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_volume",
                "taker_buy_quote_volume",
                "ignore",
            ],
        )

        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df["datetime"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert("Asia/Shanghai")

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        filename = f"{symbol}_BINANCE_{start_date}_{end_date}_{interval}.parquet"
        uri = artifacts.artifact_uri(run_id=run_id, kind=ArtifactKind.RAW, filename=filename)
        path = artifacts.resolve_uri(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)

        sha256 = _sha256_file(path)
        bytes_ = path.stat().st_size

        raw_artifact = repo.add_artifact(
            run_id=run_id,
            step_id=step_id,
            kind=ArtifactKind.RAW,
            uri=uri,
            sha256=sha256,
            bytes_=bytes_,
            metadata={
                "symbol": symbol,
                "interval": interval,
                "start_date": start_date,
                "end_date": end_date,
                "rows": int(len(df)),
            },
        )

        repo.set_step_status(step, StepStatus.SUCCEEDED, progress=100, message="完成")
        is_pipeline = continue_pipeline_if_needed(
            session=session,
            repo=repo,
            celery_app=celery_app,
            run=run,
            step=step,
            produced_state_patch={"raw_artifact_id": raw_artifact.id},
        )
        if not is_pipeline:
            repo.set_run_status(run, RunStatus.SUCCEEDED)
            session.commit()

        return {
            "status": "success",
            "artifact_id": raw_artifact.id,
            "artifact": filename,
            "rows": int(len(df)),
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
