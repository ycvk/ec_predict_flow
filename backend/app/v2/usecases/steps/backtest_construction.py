"""v2 回测构建与执行。

说明：
- 历史版本为“简化版”：开仓后固定 look_forward_bars 观察未来价格决定胜负，
  盈利/亏损使用固定金额（win_profit/loss_cost）。
- 当前版本支持更真实的收益计算：
  - 基于价格变化计算 PnL（long/short）
  - 手续费（fee_rate，按名义仓位双边收取）
  - 滑点（slippage_bps，按进出场价格不利方向偏移）
  - 仓位（position_fraction / position_notional）
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from app.v2.domain.indicators import calculate_RSI, calculate_fast_cti


def generate_open_signal(
    *,
    df: pd.DataFrame,
    decision_rules: list[dict[str, Any]],
    backtest_type: Literal["long", "short"] = "long",
    min_confidence: float = 0.0,
) -> pd.Series:
    open_signal = pd.Series([False] * len(df), index=df.index)

    target_class = 1 if backtest_type == "long" else 0
    min_confidence = float(min_confidence)

    for rule in decision_rules:
        if int(rule.get("predicted_class", 1)) != target_class:
            continue
        if float(rule.get("confidence", 0.0)) < min_confidence:
            continue

        rule_condition = pd.Series([True] * len(df), index=df.index)

        for threshold in rule.get("thresholds", []) or []:
            feature = threshold.get("feature")
            operator = threshold.get("operator")
            value = threshold.get("value")

            if not feature or feature not in df.columns:
                rule_condition &= False
                continue

            if operator == "<=":
                rule_condition &= df[feature] <= value
            elif operator == ">":
                rule_condition &= df[feature] > value
            elif operator == "<":
                rule_condition &= df[feature] < value
            elif operator == ">=":
                rule_condition &= df[feature] >= value
            elif operator == "==":
                rule_condition &= df[feature] == value
            elif operator == "!=":
                rule_condition &= df[feature] != value
            else:
                rule_condition &= False

        open_signal |= rule_condition

    return open_signal


def backtest_strategy(
    *,
    df: pd.DataFrame,
    look_forward_bars: int = 10,
    win_profit: float = 4.0,
    loss_cost: float = 5.0,
    initial_balance: float = 1000.0,
    backtest_type: Literal["long", "short"] = "long",
    filter_type: Literal["rsi", "cti"] = "rsi",
    order_interval_minutes: int = 30,
    pnl_mode: Literal["fixed", "price"] = "price",
    fee_rate: float = 0.0004,
    slippage_bps: float = 0.0,
    position_fraction: float = 1.0,
    position_notional: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if "open_signal" not in df.columns:
        raise ValueError("df 缺少 open_signal 列")

    look_forward_bars = int(look_forward_bars)
    if look_forward_bars < 1:
        raise ValueError("look_forward_bars 必须 >= 1")

    if len(df) <= look_forward_bars:
        raise ValueError("数据行数不足")

    order_interval_minutes = int(order_interval_minutes)
    if order_interval_minutes < 0:
        raise ValueError("order_interval_minutes 必须 >= 0")

    pnl_mode = str(pnl_mode or "price").lower()  # type: ignore[assignment]
    if pnl_mode not in {"fixed", "price"}:
        raise ValueError("pnl_mode 必须为 fixed 或 price")

    fee_rate = float(fee_rate)
    if fee_rate < 0:
        raise ValueError("fee_rate 必须 >= 0")

    slippage_bps = float(slippage_bps)
    if slippage_bps < 0:
        raise ValueError("slippage_bps 必须 >= 0")
    slippage_rate = slippage_bps / 10000.0

    position_fraction = float(position_fraction)
    if position_fraction <= 0 or position_fraction > 1:
        raise ValueError("position_fraction 必须在 (0, 1] 范围内")

    if position_notional is not None:
        position_notional = float(position_notional)
        if position_notional <= 0:
            position_notional = None

    # 确保索引是 datetime
    work_df = df.copy()
    if not isinstance(work_df.index, pd.DatetimeIndex):
        if "datetime" in work_df.columns:
            work_df["datetime"] = pd.to_datetime(work_df["datetime"])
            work_df = work_df.set_index("datetime")
        else:
            raise ValueError("数据必须包含 datetime 列或 DatetimeIndex")

    # 过滤指标
    if filter_type == "rsi":
        work_df["filter_indicator"] = calculate_RSI(work_df, 14)
    else:
        work_df["filter_indicator"] = calculate_fast_cti(work_df)

    results: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    current_balance = float(initial_balance)
    last_order_time: pd.Timestamp | None = None

    total_fee = 0.0
    total_gross_pnl = 0.0

    for i in range(0, len(work_df) - look_forward_bars):
        row = work_df.iloc[i]
        current_time = work_df.index[i]

        can_order = True
        if last_order_time is not None and order_interval_minutes > 0:
            time_diff = (current_time - last_order_time).total_seconds() / 60
            if time_diff < order_interval_minutes:
                can_order = False

        if filter_type == "rsi":
            if backtest_type == "long":
                filter_condition = row["filter_indicator"] < 30
            else:
                filter_condition = row["filter_indicator"] > 70
        else:
            if backtest_type == "long":
                filter_condition = row["filter_indicator"] < -0.5
            else:
                filter_condition = row["filter_indicator"] > 0.5

        if bool(row["open_signal"]) and can_order and bool(filter_condition):
            entry_price = float(row["close"])
            future_price = float(work_df.iloc[i + look_forward_bars]["close"])

            if pnl_mode == "fixed":
                if backtest_type == "long":
                    is_win = future_price > entry_price
                else:
                    is_win = future_price < entry_price

                trade = {
                    "datetime": current_time,
                    "entry_price": entry_price,
                    "exit_price": future_price,
                    "entry_price_mid": entry_price,
                    "exit_price_mid": future_price,
                    "is_win": bool(is_win),
                    "win_profit": float(win_profit),
                    "loss_cost": float(loss_cost),
                    "balance_before": float(current_balance),
                    "backtest_type": backtest_type,
                    "pnl_mode": "fixed",
                }

                if is_win:
                    pnl = float(win_profit)
                else:
                    pnl = -float(loss_cost)

                current_balance += pnl
                total_gross_pnl += pnl

                trade["gross_pnl"] = float(pnl)
                trade["fee"] = 0.0
                trade["net_pnl"] = float(pnl)
                trade["balance_after"] = float(current_balance)
                trades.append(trade)
                last_order_time = current_time
            else:
                if not np.isfinite(entry_price) or not np.isfinite(future_price):
                    # 跳过异常价格
                    pass
                elif current_balance <= 0:
                    # 余额不足不再下单
                    pass
                else:
                    if position_notional is not None:
                        notional = min(float(position_notional), float(current_balance))
                    else:
                        notional = float(current_balance) * float(position_fraction)

                    if notional <= 0:
                        pass
                    else:
                        if backtest_type == "long":
                            entry_exec = float(entry_price) * (1.0 + slippage_rate)
                            exit_exec = float(future_price) * (1.0 - slippage_rate)
                        else:
                            entry_exec = float(entry_price) * (1.0 - slippage_rate)
                            exit_exec = float(future_price) * (1.0 + slippage_rate)

                        if entry_exec <= 0 or exit_exec <= 0:
                            pass
                        else:
                            qty = float(notional) / float(entry_exec)

                            if backtest_type == "long":
                                gross_pnl = qty * (exit_exec - entry_exec)
                            else:
                                gross_pnl = qty * (entry_exec - exit_exec)

                            fee = (qty * entry_exec + qty * exit_exec) * float(fee_rate)
                            net_pnl = gross_pnl - fee

                            total_fee += float(fee)
                            total_gross_pnl += float(gross_pnl)

                            trade = {
                                "datetime": current_time,
                                "entry_price": float(entry_exec),
                                "exit_price": float(exit_exec),
                                "entry_price_mid": float(entry_price),
                                "exit_price_mid": float(future_price),
                                "qty": float(qty),
                                "notional": float(notional),
                                "gross_pnl": float(gross_pnl),
                                "fee": float(fee),
                                "net_pnl": float(net_pnl),
                                "is_win": bool(net_pnl > 0),
                                "balance_before": float(current_balance),
                                "balance_after": float(current_balance + net_pnl),
                                "backtest_type": backtest_type,
                                "pnl_mode": "price",
                                "fee_rate": float(fee_rate),
                                "slippage_bps": float(slippage_bps),
                                "position_fraction": float(position_fraction),
                                "position_notional": float(position_notional)
                                if position_notional is not None
                                else None,
                            }

                            current_balance += float(net_pnl)
                            trades.append(trade)
                            last_order_time = current_time

        results.append({"datetime": current_time, "balance": float(current_balance)})

    # 尾部无法开新仓的区间：继续填充持平的资金曲线，便于前端画图连续
    for i in range(len(work_df) - look_forward_bars, len(work_df)):
        current_time = work_df.index[i]
        results.append({"datetime": current_time, "balance": float(current_balance)})

    results_df = pd.DataFrame(results).set_index("datetime")
    trades_df = pd.DataFrame(trades)
    if len(trades_df) > 0:
        trades_df = trades_df.set_index("datetime")

    total_trades = int(len(trades))
    winning_trades = int(sum(1 for t in trades if bool(t.get("is_win"))))
    losing_trades = int(total_trades - winning_trades)
    win_rate = float(winning_trades / total_trades) if total_trades > 0 else 0.0

    max_balance = float(initial_balance)
    max_drawdown = 0.0
    for balance in results_df["balance"]:
        if balance > max_balance:
            max_balance = float(balance)
        drawdown = (max_balance - float(balance)) / max_balance if max_balance > 0 else 0.0
        if drawdown > max_drawdown:
            max_drawdown = float(drawdown)

    stats = {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "initial_balance": float(initial_balance),
        "final_balance": float(current_balance),
        "profit": float(current_balance - float(initial_balance)),
        "profit_rate": float((current_balance - float(initial_balance)) / float(initial_balance))
        if float(initial_balance) > 0
        else 0.0,
        "max_drawdown": float(max_drawdown),
        "pnl_mode": str(pnl_mode),
        "fee_rate": float(fee_rate),
        "slippage_bps": float(slippage_bps),
        "position_fraction": float(position_fraction),
        "position_notional": float(position_notional) if position_notional is not None else None,
        "gross_pnl": float(total_gross_pnl),
        "fees_paid": float(total_fee),
        "net_pnl": float(current_balance - float(initial_balance)),
        "avg_net_pnl_per_trade": float((current_balance - float(initial_balance)) / total_trades)
        if total_trades > 0
        else 0.0,
    }

    return results_df, trades_df, stats
