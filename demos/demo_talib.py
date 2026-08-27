#!/usr/bin/env python3
"""
Demo 3 — TA-Lib vs 自写指标互验 + TA-Lib 独有指标展示

项目自写指标在 backtest/indicators.py（rolling 实现），TA-Lib 是 C 库实现。
本 demo 对同一份数据跑两套实现，验证数值一致（互验 = 给自写指标加了一道保险），
再展示 TA-Lib 有而项目没有的指标（RSI/ATR/MACD）。

运行: /usr/local/bin/python demos/demo_talib.py
"""

import os
import sys

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    import talib
except ImportError:
    sys.exit("未安装 TA-Lib: brew install ta-lib && pip install TA-Lib")

from data.kline import StockData

# 抽样 5 只主板蓝筹
CODES = ["sh600519", "sz000001", "sh601318", "sz000858", "sh600036"]


def load_close() -> pd.DataFrame:
    """缓存长表 → 收盘价透视表（index=日期, columns=代码）"""
    cache = StockData().cache
    cache = cache[cache["代码"].isin(CODES)]
    return cache.pivot_table(index="日期", columns="代码", values="收盘", aggfunc="last")


def main():
    close = load_close()
    print(f"样本: {close.shape[1]} 只, 区间 {close.index.min().date()} ~ {close.index.max().date()}\n")

    print("===== 互验（自写 rolling vs TA-Lib，容差 1e-8）=====")
    # talib 0.7 只接受 1-D numpy 数组，逐列转
    # 对齐方式：talib 输出数组贴到索引尾部（兼容"前裁 N 个"与"前补 NaN"两种行为）
    def _to_series(arr, idx):
        return pd.Series(arr, index=idx[-len(arr):])

    for n in (5, 20, 60):
        talib_ma = pd.DataFrame(index=close.index)
        for c in close.columns:
            px = close[c].dropna()
            talib_ma[c] = _to_series(talib.SMA(px.to_numpy(), n), px.index)
        # 与 backtest/indicators.py 同公式: maN = close.rolling(N).mean()
        diff = (close.rolling(n).mean() - talib_ma).abs().max().max()
        print(f"  MA{n:<3} (rolling vs talib.SMA): 最大差 {diff:.2e}  {'✓' if diff < 1e-8 else '✗'}")

    # mom10 = close.pct_change(10) * 100（%），talib.ROCP 同定义
    # 注意: talib 0.7 的 ROCP 返回小数（历史版本返回百分数），×100 对齐
    # 必须在同一条 dropna 序列上对比：全序列 pct_change 会因停牌缺口
    # 参照到 NaN 行（第10个日历行≠第10个有效值），ROCP 参照的是有效值
    talib_rocp = pd.DataFrame(index=close.index)
    mom10_self = pd.DataFrame(index=close.index)
    for c in close.columns:
        px = close[c].dropna()
        talib_rocp[c] = _to_series(talib.ROCP(px.to_numpy(), 10), px.index) * 100
        mom10_self[c] = px.pct_change(10) * 100
    diff = (mom10_self - talib_rocp).abs().max().max()
    print(f"  MOM10 (pct_change×100 vs talib.ROCP×100): 最大差 {diff:.2e}  {'✓' if diff < 1e-8 else '✗'}")

    print("\n===== TA-Lib 独有指标（sh600519 最新值）=====")
    px = close["sh600519"].dropna()
    k = StockData().cache
    k = k[k["代码"] == "sh600519"].sort_values("日期")
    rsi = talib.RSI(px.to_numpy(), 14)[-1]
    atr = talib.ATR(k["最高"].to_numpy(), k["最低"].to_numpy(),
                    k["收盘"].to_numpy(), timeperiod=14)[-1]
    macd, signal, hist = talib.MACD(px.to_numpy(), 12, 26, 9)
    print(f"  RSI(14): {rsi:.1f}   {'超买' if rsi > 70 else '超卖' if rsi < 30 else '中性'}")
    print(f"  MACD(12,26,9): DIF {macd[-1]:.3f} / DEA {signal[-1]:.3f} / "
          f"柱 {hist[-1]:.3f}")
    print(f"  ATR(14): {atr:.2f} 元（日内波动幅度参考）")


if __name__ == "__main__":
    main()
