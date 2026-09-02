"""只使用已揭示行情计算训练页指标。"""

from typing import Dict, Sequence

import numpy as np
import pandas as pd


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if np.isfinite(number) else None


def compute_revealed_indicators(
    historical_bars: Sequence[Dict],
    partial_bar: Dict,
    current_point: Dict,
    prev_close: float,
) -> Dict:
    """计算某个模拟分钟能够合法看到的量价和风险指标。"""
    rows = [dict(row) for row in historical_bars] + [dict(partial_bar)]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {}
    for field in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if field not in frame:
            frame[field] = np.nan
        frame[field] = pd.to_numeric(frame[field], errors="coerce")

    close = frame["close"]
    prior = close.shift(1)
    if len(frame):
        prior.iloc[-1] = float(prev_close)
    returns = close / prior.replace(0, np.nan) - 1
    true_range = pd.Series(np.maximum.reduce([
        (frame["high"] - frame["low"]).to_numpy(),
        (frame["high"] - prior).abs().to_numpy(),
        (frame["low"] - prior).abs().to_numpy(),
    ]))

    atr14 = true_range.rolling(14, min_periods=14).mean().iloc[-1]
    volatility20 = returns.rolling(20, min_periods=20).std().iloc[-1]
    momentum20 = (
        (close.iloc[-1] / close.iloc[-21] - 1) * 100
        if len(close) >= 21 and close.iloc[-21] else np.nan
    )
    high60 = close.tail(60).max() if len(close) >= 60 else np.nan
    drawdown60 = (
        (close.iloc[-1] / high60 - 1) * 100
        if pd.notna(high60) and high60 else np.nan
    )
    history_turnover = frame["turnover_rate"].iloc[:-1].dropna().tail(20)
    turnover_ma20 = history_turnover.mean() if len(history_turnover) >= 20 else np.nan
    current_high = float(partial_bar["high"])
    current_low = float(partial_bar["low"])
    day_position = (
        (float(partial_bar["close"]) - current_low) / (current_high - current_low) * 100
        if current_high > current_low else 50.0
    )
    vwap = current_point.get("vwap")
    vwap_deviation = (
        (float(current_point["close"]) / float(vwap) - 1) * 100
        if vwap not in (None, 0) else np.nan
    )

    return {
        "intraday_volume_ratio": _finite(current_point.get("intraday_volume_ratio")),
        "vwap": _finite(vwap),
        "vwap_deviation_pct": _finite(vwap_deviation),
        "turnover_ma20_pct": _finite(turnover_ma20),
        "momentum_20_pct": _finite(momentum20),
        "atr14_pct": _finite(atr14 / close.iloc[-1] * 100 if close.iloc[-1] else np.nan),
        "volatility_20_ann_pct": _finite(volatility20 * np.sqrt(252) * 100),
        "drawdown_from_high_60_pct": _finite(drawdown60),
        "day_position_pct": _finite(day_position),
    }
