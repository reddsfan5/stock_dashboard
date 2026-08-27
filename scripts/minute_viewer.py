#!/usr/bin/env python3
"""
分时图查看器 — 展示缓存中的单标的单日 1 分钟数据

分时价格线（红/绿对照昨收）+ 均价线（VWAP）+ 前收基准虚线，下方面板成交量。

用法
----
$ python scripts/minute_viewer.py --code sh600519            # 最新交易日
$ python scripts/minute_viewer.py --code 510050 --date 2026-08-25
$ python scripts/minute_viewer.py --code sh600519 --date 2026-08-21 --out output/m.html

输出: output/minute_view.html（本地 vendor echarts，离线可用）

⚠ 运行环境: 必须用 /usr/local/bin/python 3.9.9（pyecharts 只装在这个解释器）
"""

import argparse
import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

from data.minute import MinuteData

try:
    from pyecharts import options as opts
    from pyecharts.charts import Bar, Grid, Line
except ImportError:
    sys.exit("未安装 pyecharts: pip install -r demos/requirements.txt")

OUT_HTML = os.path.join(PROJECT_DIR, "output", "minute_view.html")


def normalize_code(code: str) -> str:
    """510050 → sh510050（ETF 纯数字代码补前缀）"""
    if code.startswith(("sh", "sz")):
        return code
    return ("sh" if code.startswith(("5", "56", "58")) else "sz") + code


def get_prev_close(code: str, date: str) -> "float|None":
    """目标日前一交易日的收盘价（先查 ETF 缓存再查股票缓存）"""
    from data.etf import ETFData
    from data.kline import StockData
    t = pd.Timestamp(date)
    for cache in (ETFData().cache_with_prefix, StockData().cache):
        df = cache[(cache["代码"] == code) & (cache["日期"] < t)]
        if len(df) > 0:
            return float(df.sort_values("日期")["收盘"].iloc[-1])
    return None


def get_name(code: str) -> str:
    from data.etf import ETFData
    from data.kline import StockData
    try:
        return StockData().get_stock_name(code) or code
    except Exception:
        return code


def main():
    parser = argparse.ArgumentParser(description="分时图查看器")
    parser.add_argument("--code", required=True, help="代码（sh600519 或 510050）")
    parser.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认最近）")
    parser.add_argument("--out", default=OUT_HTML)
    args = parser.parse_args()

    code = normalize_code(args.code)
    m = MinuteData()
    df = m.get_minute(code, args.date)
    if len(df) == 0:
        dates = m.dates_of(code)
        sys.exit(f"✗ {code} 无分时数据。有数据的日期: {dates[-5:] if dates else '无'}")

    day = df["时间"].dt.date.iloc[0]
    prev_close = get_prev_close(code, str(day))
    name = get_name(code)

    # ---- 指标 ----
    x = df["时间"].dt.strftime("%H:%M").tolist()
    price = df["收盘"].round(2).tolist()
    vol = df["成交量"].tolist()
    vwap = (df["成交额"].cumsum() / df["成交量"].cumsum()).round(2).tolist()  # 均价线

    chg_vs_prev = ((df["收盘"].iloc[-1] / prev_close - 1) * 100) if prev_close else 0
    line_color = "#ef232a" if chg_vs_prev > 0 else ("#14b143" if chg_vs_prev < 0 else "#666")

    title = f"{name} {code} · {day}  收 {price[-1]}  ({chg_vs_prev:+.2f}%)"

    # 价格面板
    price_line = (
        Line()
        .add_xaxis(x)
        .add_yaxis("分时", price, is_symbol_show=False, is_smooth=False,
                   linestyle_opts=opts.LineStyleOpts(width=1.2, color=line_color),
                   itemstyle_opts=opts.ItemStyleOpts(color=line_color))
        .add_yaxis("均价", vwap, is_symbol_show=False,
                   linestyle_opts=opts.LineStyleOpts(width=1, color="#f59e0b"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title=title,
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=14)),
            yaxis_opts=opts.AxisOpts(min_="dataMin", max_="dataMax", splitline_opts=opts.SplitLineOpts(is_show=True)),
            datazoom_opts=[opts.DataZoomOpts(xaxis_index=[0, 1], range_start=0, range_end=100)],
        )
    )
    if prev_close:  # 前收基准虚线
        price_line.set_series_opts(
            markline_opts=opts.MarkLineOpts(
                data=[opts.MarkLineItem(y=prev_close)],
                linestyle_opts=opts.LineStyleOpts(type_="dashed", color="#999", width=1),
                label_opts=opts.LabelOpts(formatter="前收 {c}"),
            )
        )

    # 成交量面板
    vol_bar = (
        Bar()
        .add_xaxis(x)
        .add_yaxis("成交量", vol, xaxis_index=1, yaxis_index=1,
                   itemstyle_opts=opts.ItemStyleOpts(color=line_color))
    )

    grid = (
        Grid(init_opts=opts.InitOpts(width="1100px", height="640px"))
        .add(price_line, grid_opts=opts.GridOpts(pos_top="12%", pos_bottom="28%"))
        .add(vol_bar, grid_opts=opts.GridOpts(pos_top="76%"))
    )
    grid.render(args.out)

    # 本地化 echarts（局域网/file:// 可离线打开）
    from best_worst_windows import _localize_echarts
    _localize_echarts(args.out)
    size = os.path.getsize(args.out) / 1024
    print(f"✓ {args.out} ({size:.0f} KB, {len(df)} 根分钟线)")


if __name__ == "__main__":
    main()
