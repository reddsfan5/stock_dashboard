"""相对低位缓涨后 5 日止盈策略的事件级回测。

研究问题：当前价位于近 30 日价格区间相对低位，随后连续 N=3/4/5 日
小幅波动但累计缓涨，下一交易日开盘买入，严格 T+1 后若价格触及 +5%
则止盈，否则第 5 个持有日收盘退出，是否具有可重复的交易优势。

本模块只保存信号、成交和统计逻辑；命令入口与 HTML 展示分别位于
``scripts/research/backtest_slow_rise.py`` 和 ``backtest/slow_rise_report.py``。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.proverbs import MAIN_BOARD_PREFIXES


@dataclass(frozen=True)
class SlowRiseConfig:
    start_date: str = "2018-01-01"
    end_date: Optional[str] = None
    n_values: Tuple[int, ...] = (3, 4, 5)
    daily_min_pct: float = -1.0
    daily_max_pct: float = 1.5
    cumulative_min_pct: float = 1.0
    cumulative_max_pct: float = 5.0
    min_positive_ratio: float = 0.6
    target_pct: float = 5.0
    hold_days: int = 5
    liquidity_window: int = 20
    min_avg_amount: float = 50_000_000.0
    relative_low_window: int = 30
    max_relative_position_pct: Optional[float] = 30.0
    enforce_t1: bool = True
    reject_one_price_limit_up: bool = True
    execution: ExecutionConfig = field(default_factory=lambda: ExecutionConfig(
        commission_rate=0.0003,
        stamp_tax_rate=0.0005,
        slippage_bps=2.0,
        min_commission=0.0,
    ))

    def __post_init__(self):
        if not self.n_values or min(self.n_values) < 2:
            raise ValueError("n_values 至少包含一个不小于2的观察天数")
        if self.daily_min_pct > self.daily_max_pct:
            raise ValueError("daily_min_pct 不能大于 daily_max_pct")
        if self.cumulative_min_pct > self.cumulative_max_pct:
            raise ValueError("累计涨幅下限不能大于上限")
        if not 0 < self.min_positive_ratio <= 1:
            raise ValueError("min_positive_ratio 必须在 (0,1] 内")
        if self.hold_days < 2:
            raise ValueError("hold_days 至少为2，才能执行 T+1")
        if self.target_pct <= 0:
            raise ValueError("target_pct 必须大于0")
        if self.relative_low_window < 2:
            raise ValueError("relative_low_window 至少为2")
        if (
            self.max_relative_position_pct is not None
            and not 0 <= self.max_relative_position_pct <= 100
        ):
            raise ValueError("max_relative_position_pct 必须在[0,100]内")


def _validate_columns(frame: pd.DataFrame, required: Iterable[str]) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"股票日线缺少字段: {', '.join(missing)}")


def _rolling(series: pd.Series, codes: pd.Series, window: int, method: str) -> pd.Series:
    roller = series.groupby(codes, sort=False).rolling(window, min_periods=window)
    return getattr(roller, method)().reset_index(level=0, drop=True).sort_index()


def prepare_slow_rise_panel(
    stock_data: pd.DataFrame,
    config: SlowRiseConfig,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """准备历史信号和未来 5 日成交所需字段。"""

    _validate_columns(
        stock_data, ("代码", "日期", "开盘", "最高", "最低", "收盘", "成交额")
    )
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date) if config.end_date else pd.Timestamp(stock_data["日期"].max())
    buffer_start = start - pd.Timedelta(days=90)
    columns = ["代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"]
    if "前收" in stock_data.columns:
        columns.append("前收")
    panel = stock_data.loc[
        stock_data["代码"].astype(str).str.startswith(MAIN_BOARD_PREFIXES)
        & (pd.to_datetime(stock_data["日期"]) >= buffer_start)
        & (pd.to_datetime(stock_data["日期"]) <= end),
        columns,
    ].copy()
    panel["日期"] = pd.to_datetime(panel["日期"]).dt.normalize()
    panel = panel.drop_duplicates(["代码", "日期"], keep="last")
    panel = panel.sort_values(["代码", "日期"]).reset_index(drop=True)
    numeric = ["开盘", "最高", "最低", "收盘", "成交额"]
    panel[numeric] = panel[numeric].apply(pd.to_numeric, errors="coerce")

    grouped = panel.groupby("代码", sort=False)
    previous_close = grouped["收盘"].shift(1)
    if "前收" in panel.columns:
        stored = pd.to_numeric(panel["前收"], errors="coerce")
        previous_close = stored.where(stored > 0, previous_close)
    panel["日涨跌%"] = np.where(
        previous_close > 0, (panel["收盘"] / previous_close - 1.0) * 100.0, np.nan
    )
    panel["20日平均成交额"] = _rolling(
        panel["成交额"], panel["代码"], config.liquidity_window, "mean"
    )
    panel["流动性合格"] = panel["20日平均成交额"] >= config.min_avg_amount
    rolling_low = _rolling(
        panel["最低"], panel["代码"], config.relative_low_window, "min"
    )
    rolling_high = _rolling(
        panel["最高"], panel["代码"], config.relative_low_window, "max"
    )
    price_range = rolling_high - rolling_low
    panel["区间相对位置%"] = np.where(
        price_range > 0,
        (panel["收盘"] - rolling_low) / price_range * 100.0,
        np.nan,
    )
    if config.max_relative_position_pct is None:
        panel["低位合格"] = True
    else:
        panel["低位合格"] = (
            panel["区间相对位置%"].notna()
            & (panel["区间相对位置%"] <= config.max_relative_position_pct)
        )
    panel["研究期"] = panel["日期"].between(start, end)

    for day in range(1, config.hold_days + 1):
        for field_name in ("日期", "开盘", "最高", "最低", "收盘"):
            panel[f"未来{day}日{field_name}"] = grouped[field_name].shift(-day)

    entry_open = panel["未来1日开盘"]
    execution = config.execution
    slip = execution.slippage_bps / 10_000.0
    entry_fill = entry_open * (1.0 + slip)
    target_price = entry_fill * (1.0 + config.target_pct / 100.0)
    panel["买入参考价"] = entry_open
    panel["买入成交价"] = entry_fill
    panel["止盈价"] = target_price

    # 开盘日一字涨停近似：高=低且相对信号日收盘接近10%涨停，视为无法排队买入。
    flat_bar = (
        (panel["未来1日最高"] - panel["未来1日最低"]).abs()
        <= panel["未来1日收盘"].abs() * 1e-8
    )
    limit_up = panel["未来1日收盘"] / panel["收盘"] - 1.0 >= 0.095
    panel["买入受阻"] = flat_bar & limit_up if config.reject_one_price_limit_up else False

    exit_reference = panel[f"未来{config.hold_days}日收盘"].to_numpy(float).copy()
    exit_day = np.full(len(panel), config.hold_days, dtype=int)
    exit_reason = np.full(len(panel), "第5日尾盘", dtype=object)
    target_hit = np.zeros(len(panel), dtype=bool)
    first_sell_day = 2 if config.enforce_t1 else 1
    target_array = target_price.to_numpy(float)
    for day in range(first_sell_day, config.hold_days + 1):
        open_values = panel[f"未来{day}日开盘"].to_numpy(float)
        high_values = panel[f"未来{day}日最高"].to_numpy(float)
        gap_hit = (~target_hit) & np.isfinite(open_values) & (open_values >= target_array)
        intraday_hit = (
            (~target_hit) & (~gap_hit) & np.isfinite(high_values) & (high_values >= target_array)
        )
        exit_reference[gap_hit] = open_values[gap_hit]
        exit_reference[intraday_hit] = target_array[intraday_hit]
        exit_day[gap_hit | intraday_hit] = day
        exit_reason[gap_hit] = "止盈(跳空)"
        exit_reason[intraday_hit] = "止盈"
        target_hit |= gap_hit | intraday_hit

    exit_dates = np.full(
        len(panel), np.datetime64("NaT", "ns"), dtype="datetime64[ns]"
    )
    for day in range(1, config.hold_days + 1):
        mask = exit_day == day
        exit_dates[mask] = panel.loc[mask, f"未来{day}日日期"].to_numpy(dtype="datetime64[ns]")

    exit_fill = exit_reference * (1.0 - slip)
    buy_cost = entry_fill * (1.0 + execution.commission_rate)
    sell_net = exit_fill * (
        1.0 - execution.commission_rate - execution.stamp_tax_rate
    )
    expiry_fill = panel[f"未来{config.hold_days}日收盘"] * (1.0 - slip)
    expiry_net = expiry_fill * (
        1.0 - execution.commission_rate - execution.stamp_tax_rate
    )
    panel["卖出日期"] = exit_dates
    panel["卖出参考价"] = exit_reference
    panel["卖出成交价"] = exit_fill
    panel["退出原因"] = exit_reason
    panel["是否止盈"] = target_hit
    panel["持有天数"] = exit_day
    panel["策略净收益%"] = (sell_net / buy_cost - 1.0) * 100.0
    panel["固定5日净收益%"] = (expiry_net / buy_cost - 1.0) * 100.0

    low_matrix = np.column_stack([
        panel[f"未来{day}日最低"].to_numpy(float)
        for day in range(1, config.hold_days + 1)
    ])
    high_matrix = np.column_stack([
        panel[f"未来{day}日最高"].to_numpy(float)
        for day in range(1, config.hold_days + 1)
    ])
    day_numbers = np.arange(1, config.hold_days + 1)[None, :]
    within_trade = day_numbers <= exit_day[:, None]
    lows = np.where(within_trade, low_matrix, np.nan)
    highs = np.where(within_trade, high_matrix, np.nan)
    low_extreme = np.min(np.where(np.isfinite(lows), lows, np.inf), axis=1)
    high_extreme = np.max(np.where(np.isfinite(highs), highs, -np.inf), axis=1)
    low_extreme[~np.isfinite(low_extreme)] = np.nan
    high_extreme[~np.isfinite(high_extreme)] = np.nan
    panel["最大浮亏%"] = (
        low_extreme / entry_fill.to_numpy(float) - 1.0
    ) * 100.0
    panel["最大浮盈%"] = (
        high_extreme / entry_fill.to_numpy(float) - 1.0
    ) * 100.0

    complete = (
        panel[f"未来{config.hold_days}日日期"].notna()
        & panel["买入参考价"].gt(0)
        & panel["卖出参考价"].gt(0)
        & panel["策略净收益%"].notna()
    )
    panel["可回测"] = complete & ~panel["买入受阻"]
    study = panel["研究期"]
    diagnostics = {
        "stock_rows": int(study.sum()),
        "stock_codes": int(panel.loc[study, "代码"].nunique()),
        "start_date": panel.loc[study, "日期"].min().strftime("%Y-%m-%d"),
        "end_date": panel.loc[study, "日期"].max().strftime("%Y-%m-%d"),
        "duplicate_stock_dates": int(panel.duplicated(["代码", "日期"]).sum()),
        "key_price_missing_rate": round(
            float(panel.loc[study, ["开盘", "最高", "最低", "收盘"]].isna().mean().mean()),
            8,
        ),
        "complete_outcome_rate": round(float(panel.loc[study, "可回测"].mean()), 6),
        "blocked_entry_rows": int((study & panel["买入受阻"]).sum()),
        "relative_position_available_rate": round(
            float(panel.loc[study, "区间相对位置%"].notna().mean()), 6
        ),
        "low_position_eligible_rate": round(
            float(panel.loc[study, "低位合格"].mean()), 6
        ),
    }
    return panel, diagnostics


def _non_overlapping_candidates(
    panel: pd.DataFrame,
    candidates: pd.Series,
    hold_days: int,
) -> pd.Series:
    """同股票持仓未结束前忽略新信号，防止一段行情被重复开仓。"""

    selected = pd.Series(False, index=panel.index)
    candidate_frame = panel.loc[candidates, ["代码"]]
    for _, indices in candidate_frame.groupby("代码", sort=False).groups.items():
        blocked_until = -1
        for index in indices:
            if int(index) <= blocked_until:
                continue
            selected.loc[index] = True
            blocked_until = int(index) + hold_days
    return selected


def build_slow_rise_signals(
    panel: pd.DataFrame,
    config: SlowRiseConfig,
) -> Dict[int, pd.Series]:
    codes = panel["代码"]
    grouped = panel.groupby("代码", sort=False)
    daily = panel["日涨跌%"]
    allowed = daily.between(config.daily_min_pct, config.daily_max_pct)
    positive = daily > 0
    eligible = (
        panel["研究期"] & panel["流动性合格"] & panel["低位合格"]
        & panel["可回测"]
    )
    signals: Dict[int, pd.Series] = {}
    for n_days in config.n_values:
        allowed_days = _rolling(allowed.astype(float), codes, n_days, "sum")
        positive_days = _rolling(positive.astype(float), codes, n_days, "sum")
        cumulative = (
            panel["收盘"] / grouped["收盘"].shift(n_days) - 1.0
        ) * 100.0
        condition = (
            (allowed_days == n_days)
            & (positive_days >= ceil(n_days * config.min_positive_ratio))
            & cumulative.between(config.cumulative_min_pct, config.cumulative_max_pct)
        ).fillna(False)
        # 只记录连续条件首次成立，再排除持仓期内的二次开仓。
        onset = condition & ~condition.groupby(codes, sort=False).shift(1, fill_value=False)
        signals[n_days] = _non_overlapping_candidates(
            panel, onset & eligible, config.hold_days
        )
        panel[f"{n_days}日累计涨幅%"] = cumulative
        panel[f"{n_days}日上涨天数"] = positive_days
    return signals


def _cluster_ci(values: pd.Series, dates: pd.Series) -> Tuple[float, float]:
    valid = values.notna() & dates.notna()
    x = values.loc[valid].astype(float)
    groups = dates.loc[valid]
    if len(x) < 2:
        return np.nan, np.nan
    mean = float(x.mean())
    influence = (x - mean).groupby(groups).sum()
    count = len(influence)
    if count < 2:
        return np.nan, np.nan
    variance = count / (count - 1) * float(np.square(influence).sum()) / len(x) ** 2
    radius = 1.96 * np.sqrt(max(variance, 0.0))
    return mean - radius, mean + radius


def _profit_factor(returns: pd.Series) -> float:
    gains = float(returns[returns > 0].sum())
    losses = abs(float(returns[returns < 0].sum()))
    return gains / losses if losses > 0 else np.nan


def _status(summary: Mapping[str, float]) -> str:
    if summary["trades"] < 100 or summary["signal_dates"] < 30:
        return "样本不足"
    if (
        summary["avg_net_return_pct"] > 0
        and summary["net_ci_low_pct"] > 0
        and summary["excess_ci_low_pct"] > 0
        and summary["target_rate_lift_pp"] > 0
        and summary["profit_factor"] > 1
    ):
        return "支持可操作"
    if (
        summary["avg_net_return_pct"] > 0
        and summary["avg_excess_pct"] > 0
        and summary["target_rate_lift_pp"] > 0
    ):
        return "部分支持"
    if (
        summary["net_ci_high_pct"] < 0
        or (
            summary["avg_net_return_pct"] <= 0
            and summary["profit_factor"] < 1
            and summary["target_rate_lift_pp"] < 0
        )
    ):
        return "不支持"
    return "证据不足"


def run_slow_rise_backtest(
    stock_data: pd.DataFrame,
    config: Optional[SlowRiseConfig] = None,
) -> Dict[str, object]:
    config = config or SlowRiseConfig()
    panel, diagnostics = prepare_slow_rise_panel(stock_data, config)
    signals = build_slow_rise_signals(panel, config)
    baseline_eligible = (
        panel["研究期"] & panel["流动性合格"] & panel["低位合格"]
        & panel["可回测"]
    )

    summaries = []
    yearly_rows = []
    trade_frames = []
    for n_days in config.n_values:
        trades = panel.loc[signals[n_days]].copy()
        signal_dates = set(trades["日期"])
        baseline = panel.loc[
            baseline_eligible & panel["日期"].isin(signal_dates),
            ["日期", "策略净收益%", "是否止盈"],
        ]
        baseline_daily = baseline.groupby("日期", sort=True).agg(
            基线平均收益=("策略净收益%", "mean"),
            基线止盈率=("是否止盈", "mean"),
        )
        trades["同日基线收益%"] = trades["日期"].map(baseline_daily["基线平均收益"])
        trades["超额收益%"] = trades["策略净收益%"] - trades["同日基线收益%"]
        net_ci_low, net_ci_high = _cluster_ci(trades["策略净收益%"], trades["日期"])
        ci_low, ci_high = _cluster_ci(trades["超额收益%"], trades["日期"])
        target_rate = float(trades["是否止盈"].mean() * 100) if len(trades) else np.nan
        baseline_target_rate = (
            float(baseline_daily["基线止盈率"].mean() * 100)
            if len(baseline_daily) else np.nan
        )
        summary = {
            "n_days": n_days,
            "trades": int(len(trades)),
            "signal_dates": int(trades["日期"].nunique()),
            "avg_net_return_pct": float(trades["策略净收益%"].mean()),
            "net_ci_low_pct": net_ci_low,
            "net_ci_high_pct": net_ci_high,
            "median_net_return_pct": float(trades["策略净收益%"].median()),
            "win_rate_pct": float((trades["策略净收益%"] > 0).mean() * 100),
            "target_exit_rate_pct": target_rate,
            "expiry_exit_rate_pct": 100.0 - target_rate,
            "avg_holding_days": float(trades["持有天数"].mean()),
            "profit_factor": _profit_factor(trades["策略净收益%"]),
            "avg_fixed5_return_pct": float(trades["固定5日净收益%"].mean()),
            "take_profit_effect_pct": float(
                (trades["策略净收益%"] - trades["固定5日净收益%"]).mean()
            ),
            "avg_baseline_return_pct": float(baseline_daily["基线平均收益"].mean()),
            "avg_excess_pct": float(trades["超额收益%"].mean()),
            "excess_ci_low_pct": ci_low,
            "excess_ci_high_pct": ci_high,
            "baseline_target_rate_pct": baseline_target_rate,
            "target_rate_lift_pp": target_rate - baseline_target_rate,
            "avg_max_adverse_pct": float(trades["最大浮亏%"].mean()),
            "avg_max_favorable_pct": float(trades["最大浮盈%"].mean()),
            "avg_relative_position_pct": float(trades["区间相对位置%"].mean()),
            "baseline_samples": int(len(baseline)),
            "positive_year_rate_pct": np.nan,
            "status": "",
        }

        trades["年份"] = pd.to_datetime(trades["卖出日期"]).dt.year
        yearly = trades.groupby("年份", sort=True).agg(
            交易数=("代码", "size"),
            平均净收益=("策略净收益%", "mean"),
            胜率=("策略净收益%", lambda values: (values > 0).mean() * 100),
            止盈率=("是否止盈", lambda values: values.mean() * 100),
            平均超额=("超额收益%", "mean"),
        ).reset_index()
        summary["positive_year_rate_pct"] = float(
            (yearly["平均净收益"] > 0).mean() * 100
        ) if len(yearly) else np.nan
        summary["status"] = _status(summary)
        summaries.append(summary)
        for row in yearly.to_dict("records"):
            yearly_rows.append({"n_days": n_days, **row})

        export = trades[[
            "代码", "日期", "未来1日日期", "买入参考价", "买入成交价", "止盈价",
            "卖出日期", "卖出参考价", "卖出成交价", "退出原因", "持有天数",
            "策略净收益%", "固定5日净收益%", "同日基线收益%", "超额收益%",
            "最大浮亏%", "最大浮盈%", "区间相对位置%",
            f"{n_days}日累计涨幅%", f"{n_days}日上涨天数",
        ]].copy()
        export.insert(0, "观察天数N", n_days)
        export = export.rename(columns={"日期": "信号日", "未来1日日期": "买入日"})
        export = export.rename(columns={
            f"{n_days}日累计涨幅%": "信号累计涨幅%",
            f"{n_days}日上涨天数": "信号上涨天数",
        })
        trade_frames.append(export)

    all_trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return {
        "config": asdict(config),
        "diagnostics": diagnostics,
        "summary": summaries,
        "yearly": yearly_rows,
        "trades": all_trades,
    }


def summary_frame(result: Mapping[str, object]) -> pd.DataFrame:
    return pd.DataFrame(result["summary"])
