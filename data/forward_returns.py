"""短名单与观察池共用的前向窗口计算器。

输入已经按代码、截止日期过滤好的行情帧，避免两个监控模块对入场、峰值、
收盘和最大不利波动采用不同口径。

口径（A 股 T+1）：
- 买入日 D1 = 锚点日之后第一个交易日，按 D1 开盘价买入；
- 窗口为 D1…Dh（h 个交易日，含买入日）；
- 理论峰值只取 D2…Dh 的最高价——买入当日不能卖出，D1 的最高价不可兑现；
  h=1 时没有可卖日，峰值恒为空；h≥2 但只有 D1 行情时状态为 ``awaiting_sell``；
- 最大不利取 D1…Dh 最低价（买入当日已承担持仓波动）；
- 窗口收盘取 Dh 收盘；
- 沪深 300 基准同口径：D1 开盘为基准，峰值取 D2…Dh；
- 买入日开盘即涨停（含一字涨停）视为无法买入，状态 ``blocked``，不计入统计。
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

# 沪深主板风险警示股涨跌幅自 2026-07-06 起由 5% 调整为 10%。
MAIN_BOARD_ST_10PCT_FROM = date(2026, 7, 6)

OPEN_STATUSES = ("pending", "partial", "awaiting_sell", "unavailable")


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pct(value, base):
    value, base = _finite(value), _finite(base)
    if value is None or base is None or base <= 0:
        return None
    return (value / base - 1.0) * 100.0


def _date(value):
    return None if value is None or pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")


def price_limit_pct(code: str, name: str = "", on_date=None) -> float:
    """返回涨跌幅限制比例（小数）。无法判断时按主板 10%。"""
    value = str(code or "").strip().lower().replace("etf:", "")
    prefix = value[:2] if value[:2] in {"sh", "sz", "bj"} else ""
    digits = value[2:] if prefix else value
    if prefix == "bj" or digits.startswith(("4", "8", "92")):
        return 0.30
    if digits.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if digits.startswith(("5", "1")) and len(digits) == 6 and not digits.startswith(("60",)):
        return 0.10  # ETF / 场内基金
    if "ST" in str(name or "").upper():
        day = pd.Timestamp(on_date).date() if on_date is not None else None
        if day is not None and day < MAIN_BOARD_ST_10PCT_FROM:
            return 0.05
    return 0.10


def entry_block_reason(entry_row, prev_close, code: str = "", name: str = "") -> str | None:
    """买入日开盘即达涨停价时返回原因（一字涨停 / 开盘涨停），否则 None。"""
    prev_close = _finite(prev_close)
    open_price = _finite(entry_row.get("开盘")) if entry_row is not None else None
    if prev_close is None or prev_close <= 0 or open_price is None:
        return None
    limit = price_limit_pct(code, name, entry_row.get("日期"))
    limit_price = round(prev_close * (1 + limit) + 1e-9, 2)
    if open_price + 0.005 < limit_price:
        return None
    high, low, close = (_finite(entry_row.get(key)) for key in ("最高", "最低", "收盘"))
    one_price = high is not None and low is not None and abs(high - low) < 1e-9 and (close is None or abs(close - open_price) < 1e-9)
    return f"{'一字涨停' if one_price else '开盘涨停'}（涨停价 {limit_price:.2f}，限幅 {limit * 100:.0f}%）"


def calculate_forward_window(
    future: pd.DataFrame,
    benchmark: pd.DataFrame,
    horizon: int,
    *,
    prev_close=None,
    code: str = "",
    name: str = "",
) -> dict:
    """计算一个窗口，``future`` 必须只包含锚点日之后的交易日。

    ``prev_close`` 是买入日前一交易日收盘价，用于识别开盘涨停无法买入；缺失时不做判断。
    """
    horizon = int(horizon)
    result = {
        "status": "unavailable", "entry_date": None, "entry_open": None, "entry_note": None,
        "peak_high": None, "peak_high_date": None, "peak_return_pct": None,
        "close_value": None, "close_date": None, "close_return_pct": None,
        "low_value": None, "low_date": None, "adverse_return_pct": None,
        "benchmark_entry_open": None, "benchmark_peak_high": None,
        "benchmark_peak_return_pct": None, "benchmark_close_value": None,
        "benchmark_close_return_pct": None, "peak_excess_pct": None,
        "close_excess_pct": None,
    }
    if future is None or len(future) == 0:
        result["status"] = "pending"
        return result
    future = future.sort_values("日期")
    entry = future.iloc[0]
    entry_open = _finite(entry.get("开盘"))
    if entry_open is None or entry_open <= 0:
        return result
    result.update({"entry_date": _date(entry.get("日期")), "entry_open": entry_open})
    blocked = entry_block_reason(entry, prev_close, code, name)
    if blocked:
        result.update({"status": "blocked", "entry_note": f"无法买入：{blocked}"})
        return result
    window = future.head(horizon)
    sellable = window.iloc[1:]
    if len(window) >= horizon:
        status = "complete"
    elif horizon >= 2 and len(sellable) == 0:
        status = "awaiting_sell"
    else:
        status = "partial"
    result.update({
        "status": status,
        "close_value": _finite(window.iloc[-1].get("收盘")),
        "close_date": _date(window.iloc[-1].get("日期")),
        "low_value": _finite(window["最低"].min()),
        "low_date": _date(window.loc[window["最低"].idxmin(), "日期"]) if window["最低"].notna().any() else None,
    })
    if len(sellable) and sellable["最高"].notna().any():
        result["peak_high"] = _finite(sellable["最高"].max())
        result["peak_high_date"] = _date(sellable.loc[sellable["最高"].idxmax(), "日期"])
    result["peak_return_pct"] = _pct(result["peak_high"], entry_open)
    result["close_return_pct"] = _pct(result["close_value"], entry_open)
    result["adverse_return_pct"] = _pct(result["low_value"], entry_open)
    if benchmark is not None and len(benchmark):
        bench = benchmark[benchmark["日期"] >= entry["日期"]].sort_values("日期").head(horizon)
        if len(bench):
            bench_open = _finite(bench.iloc[0].get("开盘"))
            bench_sell = bench.iloc[1:]
            bench_high = _finite(bench_sell["最高"].max()) if len(bench_sell) else None
            bench_close = _finite(bench.iloc[-1].get("收盘"))
            result.update({
                "benchmark_entry_open": bench_open, "benchmark_peak_high": bench_high,
                "benchmark_peak_return_pct": _pct(bench_high, bench_open),
                "benchmark_close_value": bench_close,
                "benchmark_close_return_pct": _pct(bench_close, bench_open),
            })
            if result["peak_return_pct"] is not None and result["benchmark_peak_return_pct"] is not None:
                result["peak_excess_pct"] = result["peak_return_pct"] - result["benchmark_peak_return_pct"]
            if result["close_return_pct"] is not None and result["benchmark_close_return_pct"] is not None:
                result["close_excess_pct"] = result["close_return_pct"] - result["benchmark_close_return_pct"]
    return result


__all__ = ["OPEN_STATUSES", "calculate_forward_window", "entry_block_reason", "price_limit_pct"]
