"""选股信号 → 可交易回测（Phase 3）。

将 screen 模块的形态定义落到历史信号日 T，次日（可配置）开盘买入，
持有 N 日或按退出规则平仓；费用/滑点走统一 ExecutionConfig。

设计约束：
- 信号只使用 T 日及之前已知的数据（禁止用次日涨跌给信号打分）
- 样本内/外切分复用 validation.chronological_holdout
- 绩效指标复用 metrics.calculate_metrics（权益曲线）
- 每条策略都必须对照基线库（指数买入持有、同日随机等权、行业中性占位）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.metrics import calculate_metrics
from backtest.proverbs import MAIN_BOARD_PREFIXES
from backtest.sim_types import EquityPoint, Trade
from backtest.validation import chronological_holdout


MODULE_TITLES = {
    "hammer": "金针探底",
    "continuity": "K线连续性",
    "sideways": "横盘震荡",
}


@dataclass(frozen=True)
class ScreenToTradeConfig:
    """筛到交易研究口径。百分比参数均为百分数单位。"""

    module: str = "hammer"
    start_date: str = "2018-01-01"
    end_date: Optional[str] = None
    hold_days: int = 5
    entry_timing: str = "next_open"  # next_open / next_close
    exit_timing: str = "close"       # close of hold day
    max_signals_per_day: int = 20
    min_avg_amount: float = 50_000_000.0
    liquidity_window: int = 20
    capital: float = 100_000.0
    notional_per_trade: float = 10_000.0
    validation_size: Optional[int] = None  # 信号日个数；None=按比例
    validation_ratio: float = 0.3
    embargo_size: int = 5
    random_seed: int = 42
    index_code: str = "sh000300"
    include_industry_neutral: bool = True
    # hammer
    min_shadow_ratio: float = 3.0
    max_body_ratio: float = 0.20
    max_bottom_pos: float = 20.0
    max_price_pos: float = 30.0
    hammer_lookback: int = 30
    # continuity
    continuity_lookback: int = 10
    min_gap_pct: float = 1.5
    continuity_strict: bool = False
    max_break_days: int = 0
    max_gain_pct: float = 30.0
    # sideways
    sideways_days: int = 10
    max_amplitude_pct: float = 15.0
    max_slope_pct: float = 0.5
    min_overlap_pct: float = 30.0
    r2_max: float = 0.3
    position_low: float = 25.0
    position_high: float = 75.0
    execution: ExecutionConfig = field(default_factory=lambda: ExecutionConfig(
        commission_rate=0.0003,
        stamp_tax_rate=0.0005,
        slippage_bps=2.0,
        min_commission=0.0,
    ))
    reject_one_price_limit_up: bool = True
    weak_slice_threshold_pct: float = -0.5  # 切片平均净收益低于此值则标记偏弱

    def __post_init__(self):
        if self.module not in MODULE_TITLES:
            raise ValueError(f"未知模块: {self.module}，可选 {list(MODULE_TITLES)}")
        if self.hold_days < 1:
            raise ValueError("hold_days 必须 >= 1")
        if self.entry_timing not in {"next_open", "next_close"}:
            raise ValueError("entry_timing 仅支持 next_open/next_close")
        if self.exit_timing not in {"close"}:
            raise ValueError("exit_timing 目前仅支持 close")
        if self.max_signals_per_day <= 0:
            raise ValueError("max_signals_per_day 必须 > 0")
        if not 0 < self.validation_ratio < 1:
            raise ValueError("validation_ratio 必须在 (0,1) 内")
        if self.embargo_size < 0:
            raise ValueError("embargo_size 不能为负")

    @property
    def module_title(self) -> str:
        return MODULE_TITLES[self.module]


def _validate_columns(frame: pd.DataFrame, required: Iterable[str]) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"行情缺少字段: {', '.join(missing)}")


def _rolling(series: pd.Series, codes: pd.Series, window: int, method: str) -> pd.Series:
    roller = series.groupby(codes, sort=False).rolling(window, min_periods=window)
    return getattr(roller, method)().reset_index(level=0, drop=True).sort_index()


def prepare_panel(
    stock_data: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> pd.DataFrame:
    """主板过滤、流动性与未来路径字段。"""
    _validate_columns(
        stock_data, ("代码", "日期", "开盘", "最高", "最低", "收盘", "成交额")
    )
    start = pd.Timestamp(config.start_date)
    end = (
        pd.Timestamp(config.end_date)
        if config.end_date
        else pd.Timestamp(stock_data["日期"].max())
    )
    buffer_start = start - pd.Timedelta(days=120)
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
    for col in ("开盘", "最高", "最低", "收盘", "成交额"):
        panel[col] = pd.to_numeric(panel[col], errors="coerce")

    grouped = panel.groupby("代码", sort=False)
    previous_close = grouped["收盘"].shift(1)
    if "前收" in panel.columns:
        stored = pd.to_numeric(panel["前收"], errors="coerce")
        previous_close = stored.where(stored > 0, previous_close)
    panel["前收有效"] = previous_close
    panel["日涨跌%"] = np.where(
        previous_close > 0, (panel["收盘"] / previous_close - 1.0) * 100.0, np.nan
    )
    panel["均成交额"] = _rolling(
        panel["成交额"], panel["代码"], config.liquidity_window, "mean"
    )
    panel["流动性合格"] = panel["均成交额"] >= config.min_avg_amount
    panel["研究期"] = panel["日期"].between(start, end)

    # 未来路径：入场在信号次日，持有 hold_days 个交易日
    max_shift = config.hold_days + 1
    for day in range(1, max_shift + 1):
        for field_name in ("日期", "开盘", "最高", "最低", "收盘"):
            panel[f"未来{day}日{field_name}"] = grouped[field_name].shift(-day)
    return panel


def _hammer_mask(panel: pd.DataFrame, config: ScreenToTradeConfig) -> pd.Series:
    o = panel["开盘"].to_numpy(float)
    c = panel["收盘"].to_numpy(float)
    h = panel["最高"].to_numpy(float)
    l = panel["最低"].to_numpy(float)
    body = np.abs(c - o)
    total = h - l
    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    with np.errstate(divide="ignore", invalid="ignore"):
        shadow_ratio = np.where(body > 0, lower / body, 0.0)
        body_ratio = np.where(total > 0, body / total, 1.0)
    is_hammer = (
        (body > 0)
        & (total > 0)
        & (shadow_ratio >= config.min_shadow_ratio)
        & (body_ratio <= config.max_body_ratio)
        & (lower >= upper)
    )
    roll_high = _rolling(panel["最高"], panel["代码"], config.hammer_lookback, "max")
    roll_low = _rolling(panel["最低"], panel["代码"], config.hammer_lookback, "min")
    rng = roll_high - roll_low
    bottom_pos = np.where(rng > 0, (panel["最低"] - roll_low) / rng * 100.0, np.nan)
    price_pos = np.where(rng > 0, (panel["收盘"] - roll_low) / rng * 100.0, np.nan)
    return (
        is_hammer
        & panel["流动性合格"].fillna(False)
        & (bottom_pos <= config.max_bottom_pos)
        & (price_pos <= config.max_price_pos)
        & panel["研究期"]
    )


def _continuity_mask(panel: pd.DataFrame, config: ScreenToTradeConfig) -> pd.Series:
    prev_low = panel.groupby("代码", sort=False)["最低"].shift(1)
    prev_high = panel.groupby("代码", sort=False)["最高"].shift(1)
    prev_close = panel["前收有效"]
    gap_ok = panel["最高"] > prev_low + prev_close * (config.min_gap_pct / 100.0)
    if config.continuity_strict:
        gap_ok = gap_ok & (panel["最低"] <= prev_high)
    break_flag = (~gap_ok).astype(float)
    # 窗口内相邻日断裂次数：lookback 天对应 lookback-1 个相邻对，用 rolling sum
    window = config.continuity_lookback
    break_sum = _rolling(break_flag, panel["代码"], window, "sum")
    # 首日无前值，rolling 会把 NaN 传播；要求完整窗口
    valid_bars = _rolling(
        panel["收盘"].notna().astype(float), panel["代码"], window, "sum"
    )
    gain = (
        panel["收盘"]
        / panel.groupby("代码", sort=False)["收盘"].shift(window - 1)
        - 1.0
    ) * 100.0
    window_amount = _rolling(
        panel["成交额"], panel["代码"], window, "mean"
    )
    return (
        panel["研究期"]
        & (valid_bars >= window)
        & (break_sum <= config.max_break_days)
        & (gain <= config.max_gain_pct)
        & (window_amount >= config.min_avg_amount)
        & gap_ok.fillna(False)
    )


def _sideways_mask(panel: pd.DataFrame, config: ScreenToTradeConfig) -> pd.Series:
    days = config.sideways_days
    roll_high = _rolling(panel["最高"], panel["代码"], days, "max")
    roll_low = _rolling(panel["最低"], panel["代码"], days, "min")
    amp = (roll_high - roll_low) / roll_low * 100.0
    pos = np.where(
        (roll_high - roll_low) > 0,
        (panel["收盘"] - roll_low) / (roll_high - roll_low) * 100.0,
        50.0,
    )
    # 斜率近似：窗口首尾收盘差 / 天数 / 均价
    start_close = panel.groupby("代码", sort=False)["收盘"].shift(days - 1)
    mean_close = _rolling(panel["收盘"], panel["代码"], days, "mean")
    slope_pct = np.where(
        mean_close > 0,
        (panel["收盘"] - start_close) / days / mean_close * 100.0,
        np.nan,
    )
    # 重叠率：相邻日重叠比例的滚动均值
    prev_h = panel.groupby("代码", sort=False)["最高"].shift(1)
    prev_l = panel.groupby("代码", sort=False)["最低"].shift(1)
    oh = np.minimum(panel["最高"], prev_h)
    ol = np.maximum(panel["最低"], prev_l)
    uh = np.maximum(panel["最高"], prev_h)
    ul = np.minimum(panel["最低"], prev_l)
    denom = uh - ul
    overlap = np.where(denom > 0, np.maximum(oh - ol, 0.0) / denom, 0.0)
    overlap = pd.Series(overlap, index=panel.index)
    avg_overlap = _rolling(overlap, panel["代码"], days - 1, "mean") * 100.0
    window_amount = _rolling(panel["成交额"], panel["代码"], days, "mean")
    # R² 近似：用收益方差相对总变差；若 |累计收益| 很小则视为平坦
    r2_proxy = np.minimum(np.abs(slope_pct) / max(config.max_slope_pct, 1e-6), 2.0) * 0.5
    flat_ok = (
        pd.Series(True, index=panel.index)
        if config.r2_max <= 0
        else pd.Series(r2_proxy <= config.r2_max, index=panel.index)
    )
    return (
        panel["研究期"]
        & (amp <= config.max_amplitude_pct)
        & (pos >= config.position_low)
        & (pos <= config.position_high)
        & (np.abs(slope_pct) < config.max_slope_pct)
        & (avg_overlap >= config.min_overlap_pct)
        & (window_amount >= config.min_avg_amount)
        & flat_ok
    )


def scan_signals(panel: pd.DataFrame, config: ScreenToTradeConfig) -> pd.DataFrame:
    """按模块扫描历史信号；同一股票同一日至多一条。"""
    if config.module == "hammer":
        mask = _hammer_mask(panel, config)
    elif config.module == "continuity":
        mask = _continuity_mask(panel, config)
    else:
        mask = _sideways_mask(panel, config)

    hits = panel.loc[mask, ["代码", "日期", "收盘", "开盘", "最高", "最低", "成交额"]].copy()
    hits = hits.rename(columns={"日期": "信号日"})
    if hits.empty:
        return hits.assign(信号排名=pd.Series(dtype=int))

    # 每日按成交额降序截断，避免信号爆炸导致不可交易的换手
    hits = hits.sort_values(["信号日", "成交额"], ascending=[True, False])
    hits["信号排名"] = hits.groupby("信号日").cumcount() + 1
    hits = hits[hits["信号排名"] <= config.max_signals_per_day].reset_index(drop=True)
    return hits


def _entry_blocked(row: Mapping[str, Any], config: ScreenToTradeConfig) -> bool:
    if not config.reject_one_price_limit_up:
        return False
    entry_high = row.get("入场最高")
    entry_low = row.get("入场最低")
    entry_close = row.get("入场收盘")
    signal_close = row.get("收盘")
    if not all(np.isfinite(x) for x in (entry_high, entry_low, entry_close, signal_close)):
        return True
    flat = abs(entry_high - entry_low) <= abs(entry_close) * 1e-8
    limit_up = entry_close / signal_close - 1.0 >= 0.095
    return bool(flat and limit_up)


def link_trades(
    signals: pd.DataFrame,
    panel: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> pd.DataFrame:
    """信号日 T → 次日入场 → 持有 hold_days 后收盘退出。"""
    if signals.empty:
        return pd.DataFrame(columns=[
            "代码", "信号日", "入场日", "退出日", "买入参考价", "卖出参考价",
            "买入成交价", "卖出成交价", "净收益%", "退出原因", "受阻",
        ])

    keyed = panel.set_index(["代码", "日期"], drop=False)
    rows = []
    slip = config.execution.slippage_bps / 10_000.0
    buy_fee = config.execution.commission_rate
    sell_fee = config.execution.commission_rate + config.execution.stamp_tax_rate

    for rec in signals.itertuples(index=False):
        code = rec.代码
        signal_date = pd.Timestamp(rec.信号日)
        try:
            bar = keyed.loc[(code, signal_date)]
        except KeyError:
            continue
        if isinstance(bar, pd.DataFrame):
            bar = bar.iloc[-1]

        entry_day = 1
        if config.entry_timing == "next_open":
            entry_ref = bar.get("未来1日开盘")
        else:
            entry_ref = bar.get("未来1日收盘")
        exit_ref = bar.get(f"未来{config.hold_days}日收盘")
        entry_date = bar.get("未来1日日期")
        exit_date = bar.get(f"未来{config.hold_days}日日期")
        entry_high = bar.get("未来1日最高")
        entry_low = bar.get("未来1日最低")
        entry_close = bar.get("未来1日收盘")

        row = {
            "代码": code,
            "信号日": signal_date,
            "入场日": entry_date,
            "退出日": exit_date,
            "收盘": float(bar["收盘"]),
            "买入参考价": float(entry_ref) if pd.notna(entry_ref) else np.nan,
            "卖出参考价": float(exit_ref) if pd.notna(exit_ref) else np.nan,
            "入场最高": float(entry_high) if pd.notna(entry_high) else np.nan,
            "入场最低": float(entry_low) if pd.notna(entry_low) else np.nan,
            "入场收盘": float(entry_close) if pd.notna(entry_close) else np.nan,
            "信号排名": int(getattr(rec, "信号排名", 0) or 0),
            "持有天数": config.hold_days,
            "退出原因": f"持有{config.hold_days}日收盘",
        }
        row["受阻"] = _entry_blocked(row, config) or not (
            np.isfinite(row["买入参考价"])
            and np.isfinite(row["卖出参考价"])
            and row["买入参考价"] > 0
            and row["卖出参考价"] > 0
            and pd.notna(row["入场日"])
            and pd.notna(row["退出日"])
        )
        if row["受阻"]:
            row["买入成交价"] = np.nan
            row["卖出成交价"] = np.nan
            row["净收益%"] = np.nan
        else:
            buy_fill = row["买入参考价"] * (1.0 + slip)
            sell_fill = row["卖出参考价"] * (1.0 - slip)
            buy_cost = buy_fill * (1.0 + buy_fee)
            sell_net = sell_fill * (1.0 - sell_fee)
            row["买入成交价"] = buy_fill
            row["卖出成交价"] = sell_fill
            row["净收益%"] = (sell_net / buy_cost - 1.0) * 100.0
        rows.append(row)

    trades = pd.DataFrame(rows)
    if not trades.empty:
        trades["信号日"] = pd.to_datetime(trades["信号日"])
        trades["入场日"] = pd.to_datetime(trades["入场日"])
        trades["退出日"] = pd.to_datetime(trades["退出日"])
    return trades


def build_equity_curve(
    trades: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> Tuple[List[EquityPoint], List[Trade], float]:
    """等额名义本金重叠持仓的盯市权益；用于回撤/换手等报告卡指标。"""
    filled = trades.loc[~trades["受阻"]].copy() if not trades.empty else trades
    if filled is None or filled.empty:
        return [], [], float(config.capital)

    filled = filled.sort_values(["入场日", "代码"]).reset_index(drop=True)
    dates = sorted({
        *pd.to_datetime(filled["入场日"]).tolist(),
        *pd.to_datetime(filled["退出日"]).tolist(),
    })
    # 扩展为连续交易日序列：用入场/退出并集即可满足报告；更细粒度用全部日历
    cash = float(config.capital)
    open_book: Dict[str, Dict[str, Any]] = {}
    equity_curve: List[EquityPoint] = []
    closed: List[Trade] = []
    traded_notional = 0.0
    equity_sum = 0.0

    # 为盯市构造 代码->日期->收盘 映射成本较高；事件路径用线性插值近似：
    # 入场按买入成本扣减现金，退出按卖出净额加回，持仓期间按成本盯市（偏保守，
    # 不引入额外行情依赖）。这样 maxDD 反映资金占用与已实现路径。
    events: List[Tuple[pd.Timestamp, str, Any]] = []
    for i, row in filled.iterrows():
        events.append((pd.Timestamp(row["入场日"]), "buy", i))
        events.append((pd.Timestamp(row["退出日"]), "sell", i))
    events.sort(key=lambda x: (x[0], 0 if x[1] == "sell" else 1, x[2]))

    last_date = None
    for date, side, idx in events:
        row = filled.loc[idx]
        key = f"{row['代码']}:{idx}"
        if side == "buy":
            cost = float(config.notional_per_trade)
            if cost > cash:
                continue
            cash -= cost
            open_book[key] = {
                "cost": cost,
                "buy_price": float(row["买入成交价"]),
                "shares_value": cost,
                "code": row["代码"],
                "buy_date": pd.Timestamp(row["入场日"]).strftime("%Y-%m-%d"),
                "net_return": float(row["净收益%"]),
            }
            traded_notional += cost
        else:
            pos = open_book.pop(key, None)
            if pos is None:
                continue
            proceeds = pos["cost"] * (1.0 + pos["net_return"] / 100.0)
            cash += proceeds
            traded_notional += pos["cost"]
            closed.append(Trade(
                code=str(pos["code"]),
                name="",
                buy_date=pos["buy_date"],
                buy_price=pos["buy_price"],
                sell_date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                sell_price=pos["buy_price"] * (1.0 + pos["net_return"] / 100.0),
                return_pct=round(pos["net_return"], 4),
                pnl=round(proceeds - pos["cost"], 4),
                filled=pos["net_return"] > 0,
                lots=1,
                exit_reason="expiry",
            ))

        position_value = sum(p["cost"] for p in open_book.values())
        equity = cash + position_value
        if last_date != date:
            equity_curve.append(EquityPoint(
                date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                equity=round(equity, 4),
                cash=round(cash, 4),
                positions=len(open_book),
                position_value=round(position_value, 4),
            ))
            equity_sum += equity
            last_date = date

    final_equity = equity_curve[-1].equity if equity_curve else float(config.capital)
    return equity_curve, closed, final_equity


def _trade_stats(trades: pd.DataFrame) -> Dict[str, Any]:
    filled = trades.loc[~trades["受阻"]] if not trades.empty else trades
    if filled is None or filled.empty or filled["净收益%"].dropna().empty:
        return {
            "trades": 0,
            "signal_dates": 0,
            "blocked": int(trades["受阻"].sum()) if not trades.empty else 0,
            "avg_net_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "median_net_return_pct": 0.0,
            "profit_factor": None,
        }
    rets = filled["净收益%"].dropna()
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    return {
        "trades": int(len(rets)),
        "signal_dates": int(filled["信号日"].nunique()),
        "blocked": int(trades["受阻"].sum()),
        "avg_net_return_pct": round(float(rets.mean()), 4),
        "win_rate_pct": round(float((rets > 0).mean() * 100), 4),
        "median_net_return_pct": round(float(rets.median()), 4),
        "profit_factor": (
            round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
        ),
    }


def compute_turnover(
    trades: pd.DataFrame,
    equity_curve: Sequence[EquityPoint],
    config: ScreenToTradeConfig,
) -> float:
    filled = trades.loc[~trades["受阻"]] if not trades.empty else trades
    if filled is None or filled.empty or not equity_curve:
        return 0.0
    traded = float(len(filled) * config.notional_per_trade * 2)
    avg_equity = float(np.mean([p.equity for p in equity_curve]))
    if avg_equity <= 0:
        return 0.0
    return round(traded / avg_equity, 4)


def buy_hold_index_baseline(
    index_data: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> Dict[str, Any]:
    """指数买入持有基线。"""
    _validate_columns(index_data, ("代码", "日期", "收盘"))
    idx = index_data.loc[index_data["代码"] == config.index_code].copy()
    if idx.empty:
        # 兜底：任取第一条代码
        codes = index_data["代码"].dropna().unique()
        if len(codes) == 0:
            return {"name": "指数买入持有", "available": False, "reason": "无指数数据"}
        idx = index_data.loc[index_data["代码"] == codes[0]].copy()
        index_code = str(codes[0])
    else:
        index_code = config.index_code
    idx["日期"] = pd.to_datetime(idx["日期"]).dt.normalize()
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date) if config.end_date else idx["日期"].max()
    idx = idx[idx["日期"].between(start, end)].sort_values("日期")
    if len(idx) < 2:
        return {"name": "指数买入持有", "available": False, "reason": "样本不足"}
    first = float(idx.iloc[0]["收盘"])
    last = float(idx.iloc[-1]["收盘"])
    total = (last / first - 1.0) * 100.0
    # 与策略相同 hold_days 的滚动收益均值，便于事件对照
    future = idx["收盘"].shift(-config.hold_days)
    roll = (future / idx["收盘"] - 1.0) * 100.0
    roll = roll.dropna()
    return {
        "name": "指数买入持有",
        "available": True,
        "index_code": index_code,
        "total_return_pct": round(total, 4),
        "avg_hold_return_pct": round(float(roll.mean()), 4) if len(roll) else None,
        "start": idx.iloc[0]["日期"].strftime("%Y-%m-%d"),
        "end": idx.iloc[-1]["日期"].strftime("%Y-%m-%d"),
    }


def random_pick_baseline(
    panel: pd.DataFrame,
    signal_dates: Sequence[pd.Timestamp],
    counts_by_date: Mapping[pd.Timestamp, int],
    config: ScreenToTradeConfig,
) -> Dict[str, Any]:
    """每个信号日随机抽取相同数量的流动性合格股票，相同持有期。"""
    rng = np.random.default_rng(config.random_seed)
    liquid = panel.loc[
        panel["研究期"] & panel["流动性合格"],
        ["代码", "日期", "收盘"] + [f"未来{config.hold_days}日收盘", "未来1日开盘",
                                      "未来1日最高", "未来1日最低", "未来1日收盘",
                                      "未来1日日期", f"未来{config.hold_days}日日期"],
    ].copy()
    if liquid.empty or not signal_dates:
        return {
            "name": "同日随机等权",
            "available": False,
            "reason": "无可用样本",
        }

    slip = config.execution.slippage_bps / 10_000.0
    buy_fee = config.execution.commission_rate
    sell_fee = config.execution.commission_rate + config.execution.stamp_tax_rate
    rets = []
    for raw_date in signal_dates:
        date = pd.Timestamp(raw_date)
        n = int(counts_by_date.get(date, 0))
        if n <= 0:
            continue
        day = liquid.loc[liquid["日期"] == date]
        if day.empty:
            continue
        take = min(n, len(day))
        chosen = day.sample(n=take, random_state=int(rng.integers(0, 1_000_000_000)))
        for _, row in chosen.iterrows():
            entry = row["未来1日开盘"] if config.entry_timing == "next_open" else row["未来1日收盘"]
            exit_px = row[f"未来{config.hold_days}日收盘"]
            if not (pd.notna(entry) and pd.notna(exit_px) and entry > 0 and exit_px > 0):
                continue
            buy_fill = float(entry) * (1.0 + slip)
            sell_fill = float(exit_px) * (1.0 - slip)
            net = (sell_fill * (1.0 - sell_fee)) / (buy_fill * (1.0 + buy_fee)) - 1.0
            rets.append(net * 100.0)

    if not rets:
        return {"name": "同日随机等权", "available": False, "reason": "无法成交"}
    arr = np.asarray(rets, dtype=float)
    return {
        "name": "同日随机等权",
        "available": True,
        "trades": int(len(arr)),
        "avg_net_return_pct": round(float(arr.mean()), 4),
        "win_rate_pct": round(float((arr > 0).mean() * 100), 4),
        "median_net_return_pct": round(float(np.median(arr)), 4),
    }


def industry_neutral_placeholder() -> Dict[str, Any]:
    """行业中性基线占位（Phase 3 轻量；完整中性化留待后续）。"""
    return {
        "name": "行业中性(占位)",
        "available": False,
        "reason": "Phase3 仅占位：需时点行业权重与中性化组合，成本较高故暂缓",
    }


def split_in_out_sample(
    trades: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> Dict[str, Any]:
    filled = trades.loc[~trades["受阻"]].copy() if not trades.empty else trades
    if filled is None or filled.empty:
        return {"in_sample": _trade_stats(trades), "out_of_sample": _trade_stats(trades),
                "split": None}
    dates = sorted(pd.to_datetime(filled["信号日"]).unique())
    if len(dates) < 10:
        return {
            "in_sample": _trade_stats(filled),
            "out_of_sample": _trade_stats(pd.DataFrame(columns=filled.columns)),
            "split": None,
            "note": "信号日过少，跳过样本外切分",
        }
    val_size = config.validation_size
    if val_size is None:
        val_size = max(1, int(round(len(dates) * config.validation_ratio)))
    embargo = min(config.embargo_size, max(0, len(dates) - val_size - 1))
    try:
        split = chronological_holdout(
            dates, validation_size=val_size, embargo_size=embargo, min_train_size=1,
        )
    except ValueError as exc:
        return {
            "in_sample": _trade_stats(filled),
            "out_of_sample": _trade_stats(pd.DataFrame(columns=filled.columns)),
            "split": None,
            "note": str(exc),
        }
    train_set = set(split.train)
    val_set = set(split.validation)
    is_trades = filled[filled["信号日"].isin(train_set)]
    oos_trades = filled[filled["信号日"].isin(val_set)]
    return {
        "in_sample": _trade_stats(is_trades),
        "out_of_sample": _trade_stats(oos_trades),
        "split": {
            "train_start": str(split.train[0].date()),
            "train_end": str(split.train[-1].date()),
            "embargo_start": str(split.embargo[0].date()) if split.embargo else None,
            "embargo_end": str(split.embargo[-1].date()) if split.embargo else None,
            "validation_start": str(split.validation[0].date()),
            "validation_end": str(split.validation[-1].date()),
            "train_days": len(split.train),
            "validation_days": len(split.validation),
            "embargo_days": len(split.embargo),
        },
    }


def regime_slices(
    trades: pd.DataFrame,
    index_data: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> Dict[str, Any]:
    """按年与牛/熊代理切片，标记偏弱区间。"""
    filled = trades.loc[~trades["受阻"]].copy() if not trades.empty else trades
    if filled is None or filled.empty:
        return {"by_year": [], "by_regime": [], "weak_slices": []}

    filled = filled.copy()
    filled["年"] = pd.to_datetime(filled["信号日"]).dt.year
    by_year = []
    weak = []
    for year, group in filled.groupby("年"):
        stats = _trade_stats(group)
        stats["slice"] = str(year)
        stats["kind"] = "year"
        by_year.append(stats)
        if stats["trades"] >= 20 and stats["avg_net_return_pct"] < config.weak_slice_threshold_pct:
            weak.append({"slice": str(year), "kind": "year",
                         "avg_net_return_pct": stats["avg_net_return_pct"],
                         "trades": stats["trades"]})

    idx = index_data.loc[index_data["代码"] == config.index_code, ["日期", "收盘"]].copy()
    by_regime = []
    if not idx.empty:
        idx["日期"] = pd.to_datetime(idx["日期"]).dt.normalize()
        idx = idx.sort_values("日期")
        idx["ma20"] = idx["收盘"].rolling(20, min_periods=20).mean()
        idx["ma60"] = idx["收盘"].rolling(60, min_periods=60).mean()
        idx["regime"] = np.where(idx["ma20"] >= idx["ma60"], "牛市代理", "熊市代理")
        idx = idx.dropna(subset=["ma60"])
        regime_map = dict(zip(idx["日期"], idx["regime"]))
        filled["regime"] = filled["信号日"].map(regime_map)
        for regime, group in filled.dropna(subset=["regime"]).groupby("regime"):
            stats = _trade_stats(group)
            stats["slice"] = str(regime)
            stats["kind"] = "regime"
            by_regime.append(stats)
            if stats["trades"] >= 20 and stats["avg_net_return_pct"] < config.weak_slice_threshold_pct:
                weak.append({"slice": str(regime), "kind": "regime",
                             "avg_net_return_pct": stats["avg_net_return_pct"],
                             "trades": stats["trades"]})

    return {"by_year": by_year, "by_regime": by_regime, "weak_slices": weak}


def failure_examples(trades: pd.DataFrame, n: int = 8) -> List[Dict[str, Any]]:
    filled = trades.loc[~trades["受阻"]].copy() if not trades.empty else trades
    if filled is None or filled.empty:
        return []
    worst = filled.nsmallest(n, "净收益%")
    out = []
    for _, row in worst.iterrows():
        out.append({
            "代码": row["代码"],
            "信号日": pd.Timestamp(row["信号日"]).strftime("%Y-%m-%d"),
            "入场日": pd.Timestamp(row["入场日"]).strftime("%Y-%m-%d") if pd.notna(row["入场日"]) else None,
            "退出日": pd.Timestamp(row["退出日"]).strftime("%Y-%m-%d") if pd.notna(row["退出日"]) else None,
            "净收益%": round(float(row["净收益%"]), 4),
            "退出原因": row.get("退出原因", ""),
        })
    return out


def build_report_card(
    trades: pd.DataFrame,
    equity_curve: Sequence[EquityPoint],
    closed_trades: Sequence[Trade],
    final_equity: float,
    config: ScreenToTradeConfig,
    baselines: Mapping[str, Any],
    splits: Mapping[str, Any],
    slices: Mapping[str, Any],
) -> Dict[str, Any]:
    stats = _trade_stats(trades)
    metrics = calculate_metrics(
        equity_curve, closed_trades, config.capital, final_equity,
    )
    turnover = compute_turnover(trades, equity_curve, config)
    strategy_avg = stats["avg_net_return_pct"]
    comparisons = []
    for key, base in baselines.items():
        if not base.get("available"):
            comparisons.append({
                "baseline": base.get("name", key),
                "available": False,
                "reason": base.get("reason", ""),
            })
            continue
        base_avg = base.get("avg_net_return_pct")
        if base_avg is None:
            base_avg = base.get("avg_hold_return_pct")
        excess = None if base_avg is None else round(strategy_avg - float(base_avg), 4)
        comparisons.append({
            "baseline": base.get("name", key),
            "available": True,
            "baseline_avg_pct": base_avg,
            "strategy_avg_pct": strategy_avg,
            "excess_pct": excess,
        })

    return {
        "module": config.module,
        "module_title": config.module_title,
        "hold_days": config.hold_days,
        "event_stats": stats,
        "portfolio_metrics": asdict(metrics),
        "turnover": turnover,
        "final_equity": final_equity,
        "in_sample": splits.get("in_sample"),
        "out_of_sample": splits.get("out_of_sample"),
        "split": splits.get("split"),
        "split_note": splits.get("note"),
        "baselines": comparisons,
        "slices": slices,
        "failure_examples": failure_examples(trades),
    }


def run_screen_to_trade(
    stock_data: pd.DataFrame,
    index_data: pd.DataFrame,
    config: ScreenToTradeConfig,
) -> Dict[str, Any]:
    """完整研究流水线：信号 → 成交 → 基线 → 切分 → 切片 → 报告卡。"""
    panel = prepare_panel(stock_data, config)
    signals = scan_signals(panel, config)
    trades = link_trades(signals, panel, config)
    equity_curve, closed, final_equity = build_equity_curve(trades, config)

    signal_dates = sorted(pd.to_datetime(trades["信号日"]).unique()) if not trades.empty else []
    counts = (
        trades.loc[~trades["受阻"]].groupby("信号日").size().to_dict()
        if not trades.empty else {}
    )
    baselines = {
        "buy_hold_index": buy_hold_index_baseline(index_data, config),
        "random_pick": random_pick_baseline(panel, signal_dates, counts, config),
    }
    if config.include_industry_neutral:
        baselines["industry_neutral"] = industry_neutral_placeholder()

    splits = split_in_out_sample(trades, config)
    slices = regime_slices(trades, index_data, config)
    card = build_report_card(
        trades, equity_curve, closed, final_equity, config, baselines, splits, slices,
    )
    diagnostics = {
        "panel_rows": int(len(panel)),
        "study_rows": int(panel["研究期"].sum()) if len(panel) else 0,
        "signal_rows": int(len(signals)),
        "trade_rows": int(len(trades)),
        "filled_trades": int((~trades["受阻"]).sum()) if not trades.empty else 0,
        "module": config.module,
        "start_date": config.start_date,
        "end_date": config.end_date,
    }
    return {
        "config": {**asdict(config), "execution": asdict(config.execution)},
        "diagnostics": diagnostics,
        "signals": signals,
        "trades": trades,
        "equity_curve": [asdict(p) for p in equity_curve],
        "report_card": card,
        "baselines": baselines,
    }
