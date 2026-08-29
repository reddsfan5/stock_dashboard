#!/usr/bin/env python3
"""
Demo 5 — pyecharts 交互式 K 线（510050 上证50ETF，120 日）

输出自包含 HTML（内嵌 echarts.js），可离线打开，契合项目 LAN 报告体系
（现有报告靠 CDN echarts，pyecharts 输出则完全本地化）。

双面板：K线 + MA5/MA20 叠加（上），成交额柱状（下），带缩放/十字光标。

运行: /usr/local/bin/python demos/demo_pyecharts.py

⚠ 运行环境: 必须用 /usr/local/bin/python 3.9.9（pyecharts 只装在这个解释器，
选错解释器会 ImportError）
"""

import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from pyecharts import options as opts
    from pyecharts.charts import Bar, Grid, Kline, Line
except ImportError:
    sys.exit("未安装 pyecharts: pip install -r demos/requirements.txt")

from data.etf import ETFData

OUT_HTML = os.path.join(PROJECT_DIR, "output", "demo_kline.html")


def main():
    df = ETFData().get_kline("510050", days=120)
    if len(df) == 0:
        sys.exit("✗ ETF 缓存无数据，先跑 python data/etf.py")

    dates = df["日期"].dt.strftime("%Y-%m-%d").tolist()
    # ECharts 蜡烛图数据顺序: [open, close, low, high]（注意不是 ohlc）
    ohlc = df[["开盘", "收盘", "最低", "最高"]].values.tolist()
    volume = (df["成交额"] / 10000).round(0).tolist()  # 缓存元 → 展示万元

    kline = (
        Kline(init_opts=opts.InitOpts(width="1100px", height="640px"))
        .add_xaxis(dates)
        .add_yaxis("510050", ohlc, itemstyle_opts=opts.ItemStyleOpts(
            color="#ef232a", color0="#14b143"))  # A股红涨绿跌
        .set_global_opts(
            title_opts=opts.TitleOpts(title="510050 上证50ETF（近120日）"),
            datazoom_opts=[opts.DataZoomOpts(range_start=60, xaxis_index=[0, 1])],
            axispointer_opts=opts.AxisPointerOpts(is_show=True, link=[{"xAxisIndex": "all"}]),
        )
    )

    ma5 = df["收盘"].rolling(5).mean().round(3).tolist()
    ma20 = df["收盘"].rolling(20).mean().round(3).tolist()
    ma_line = (
        Line()
        .add_xaxis(dates)
        .add_yaxis("MA5", ma5, is_smooth=True, is_symbol_show=False,
                   linestyle_opts=opts.LineStyleOpts(width=1))
        .add_yaxis("MA20", ma20, is_smooth=True, is_symbol_show=False,
                   linestyle_opts=opts.LineStyleOpts(width=1))
    )
    kline.overlap(ma_line)

    vol_bar = (
        Bar()
        .add_xaxis(dates)
        .add_yaxis("成交额(万元)", volume, xaxis_index=1, yaxis_index=1,
                   itemstyle_opts=opts.ItemStyleOpts(color="#5470c6"))
    )

    grid = (
        Grid(init_opts=opts.InitOpts(width="1100px", height="640px"))
        .add(kline, grid_opts=opts.GridOpts(pos_top="12%", pos_bottom="28%"))
        .add(vol_bar, grid_opts=opts.GridOpts(pos_top="76%"))
    )
    grid.render(OUT_HTML)
    # pyecharts 默认引用 CDN（局域网打不开）→ 换本地 vendor + CDN 兜底
    with open(OUT_HTML, encoding="utf-8") as f:
        html = f.read()
    html = html.replace(
        '<script type="text/javascript" src="https://assets.pyecharts.org/assets/v6/echarts.min.js"></script>',
        '<script src="vendor/echarts6.min.js" onerror="this.onerror=null;'
        "var s=document.createElement('script');"
        "s.src='https://cdn.jsdelivr.net/npm/echarts@6/dist/echarts.min.js';"
        'document.head.appendChild(s)"></script>',
    )
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    size = os.path.getsize(OUT_HTML) / 1024
    print(f"✓ {OUT_HTML} ({size:.0f} KB, 离线可用——依赖本地 vendor/echarts6.min.js)")


if __name__ == "__main__":
    main()
