"""短名单与观察池共用的前向窗口计算器。

输入已经按代码、截止日期过滤好的行情帧，避免两个监控模块对入场、峰值、
收盘和最大不利波动采用不同口径。
"""

from __future__ import annotations

import math

import pandas as pd


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


def calculate_forward_window(future: pd.DataFrame, benchmark: pd.DataFrame, horizon: int) -> dict:
    """计算一个窗口，``future`` 必须只包含入场锚点之后的交易日。"""
    result = {
        "status": "unavailable", "entry_date": None, "entry_open": None,
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
    window = future.head(int(horizon))
    result.update({
        "status": "complete" if len(window) >= int(horizon) else "partial",
        "entry_date": _date(entry.get("日期")), "entry_open": entry_open,
        "peak_high": _finite(window["最高"].max()),
        "peak_high_date": _date(window.loc[window["最高"].idxmax(), "日期"]) if window["最高"].notna().any() else None,
        "close_value": _finite(window.iloc[-1].get("收盘")),
        "close_date": _date(window.iloc[-1].get("日期")),
        "low_value": _finite(window["最低"].min()),
        "low_date": _date(window.loc[window["最低"].idxmin(), "日期"]) if window["最低"].notna().any() else None,
    })
    result["peak_return_pct"] = _pct(result["peak_high"], entry_open)
    result["close_return_pct"] = _pct(result["close_value"], entry_open)
    result["adverse_return_pct"] = _pct(result["low_value"], entry_open)
    if benchmark is not None and len(benchmark):
        bench = benchmark[benchmark["日期"] >= entry["日期"]].sort_values("日期").head(int(horizon))
        if len(bench):
            bench_open = _finite(bench.iloc[0].get("开盘"))
            bench_high = _finite(bench["最高"].max())
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


__all__ = ["calculate_forward_window"]
