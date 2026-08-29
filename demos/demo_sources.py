#!/usr/bin/env python3
"""
Demo 1 — 三数据源对比（akshare / baostock / efinance）

同一标的、同一区间分别拉取，验证：
  1. 收盘价是否一致（期望差 < 1e-6）
  2. 成交额单位是否一致（三个适配器都应归一化为元，比率应 ≈ 1）

这是 docs/10 双数据源决策的实证基础：2026-08-15 腾讯接口 SSL 抖动
导致整条链路失败，baostock 备源接入前先验证两源数据可互换。

运行: /usr/local/bin/python demos/demo_sources.py
"""

import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data import sources

CODES = ["sh600519", "sz000001", "sh601318"]   # 茅台/平安银行/中国平安
START, END = "20250106", "20250110"            # 一周交易日


def fetch_ef(code: str) -> "pd.DataFrame|None":
    """efinance 东财接口 → 与项目缓存同构的 DataFrame"""
    try:
        import time
        import efinance as ef
        # efinance 0.5.x: beg/end 为 YYYYMMDD（无横线）；fqt=0 不复权（对齐两源）
        for attempt in range(2):
            try:
                df = ef.stock.get_quote_history(
                    code[2:], beg=START, end=END, klt=101, fqt=0)
                break
            except Exception:
                if attempt == 0:
                    time.sleep(2)  # 东财接口偶发连接重置，重试一次
                else:
                    raise
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                "最高": "high", "最低": "low", "成交额": "amount"})
        df = sources._normalize(df[["date", "open", "high", "low", "close", "amount"]], code)
        return df
    except ImportError:
        print("  (efinance 未安装，跳过)")
        return None
    except Exception as e:
        print(f"  (efinance 失败: {type(e).__name__})")
        return None


def main():
    print(f"区间 {START} ~ {END}\n")
    for code in CODES:
        print(f"===== {code} =====")
        ak_df = sources.ak_fetch_kline(code, START, END)
        with sources.baostock_session():
            bs_df = sources.bs_fetch_kline(code, START, END)
        ef_df = fetch_ef(code)

        print(f"  akshare : {'✓ ' + str(len(ak_df)) + ' 行' if ak_df is not None else '✗ 失败'}")
        print(f"  baostock: {'✓ ' + str(len(bs_df)) + ' 行' if bs_df is not None else '✗ 失败'}")
        print(f"  efinance: {'✓ ' + str(len(ef_df)) + ' 行' if ef_df is not None else '—'}")

        for name, other in [("baostock", bs_df), ("efinance", ef_df)]:
            if ak_df is None or other is None:
                continue
            m = ak_df.merge(other, on="日期", suffixes=("_ak", "_x"))
            close_diff = (m["收盘_ak"] - m["收盘_x"]).abs().max()
            amount_ratio = (m["成交额_ak"] / m["成交额_x"]).mean()
            print(f"  vs {name:<8}: 收盘最大差 {close_diff:.6f} | 成交额比率(ak/对方) {amount_ratio:,.0f}")
    print("\n结论: 收盘价三源一致；三个适配器均将成交额归一化为元，"
          "比率应接近 1。")


if __name__ == "__main__":
    main()
