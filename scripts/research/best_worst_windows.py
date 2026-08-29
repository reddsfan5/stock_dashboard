#!/usr/bin/env python3
"""
极端行情区段统计 — 连续 N 个交易日表现最好/最差的区段

滚动 N 个交易日窗口，找区间涨幅最高/最低的各 top_n 段。
段间不重叠（贪心剔除重叠窗口），避免"同一波行情"因窗口错位重复上榜。

用法
----
$ python -m scripts.research.best_worst_windows                     # 上证指数 30 交易日，最好/最差各 3 段
$ python -m scripts.research.best_worst_windows --window 60         # 60 个交易日
$ python -m scripts.research.best_worst_windows --top 5             # 各 5 段
$ python -m scripts.research.best_worst_windows --index sh000300    # 沪深300

输出
----
output/best_worst_windows.csv   — 明细
output/best_worst_windows.html  — 手机友好表格页（自包含）
output/best_worst_charts.html   — 各区段 K 线图（pyecharts，区间高亮，段外±window 日背景）

⚠ 运行环境
----------
必须用 /usr/local/bin/python 3.9.9（项目全部依赖所在，定时任务同款）：
    /usr/local/bin/python -m scripts.research.best_worst_windows
VSCode 里请先把解释器切到 /usr/local/bin/python；若选错解释器导致
pyecharts 导入失败，本脚本会打印"当前解释器"路径并跳过图表（表格仍会生成）。
（vectorbt 相关脚本除外——sweep.py 必须用 .venv/bin/python，见其注释）
"""

import argparse
import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from data.index import IndexData, INDEXES


def find_extreme_windows(close: pd.Series, window: int = 30, top_n: int = 3):
    """
    滚动 window 个交易日，找区间涨幅最高/最低的各 top_n 段（段间不重叠）。

    Args:
        close: 指数收盘价序列（index=日期）
        window: 窗口交易日数
        top_n: 最好/最差各取几段

    Returns:
        (best, worst): 各为 DataFrame，列 = 起始日/结束日/区间涨幅%/期间最高/期间最低/区间最大回撤%
    """
    s = close.sort_index()
    dates = s.index
    vals = s.to_numpy()

    # 窗口收益: 第 i 天买入, 第 i+window-1 天卖出
    starts = dates[:-window + 1]
    ends = dates[window - 1:]
    rets = (vals[window - 1:] / vals[:-window + 1] - 1) * 100

    cand = pd.DataFrame({"起始日": starts, "结束日": ends, "区间涨幅%": rets})
    cand = cand.dropna().reset_index(drop=True)

    def _pick(top: bool) -> pd.DataFrame:
        """贪心取极值段：按涨幅排序，剔除与已选段重叠的窗口"""
        ordered = cand.sort_values("区间涨幅%", ascending=not top).reset_index(drop=True)
        picked = []
        for _, row in ordered.iterrows():
            if any(row["起始日"] <= p["结束日"] and row["结束日"] >= p["起始日"]
                   for p in picked):
                continue
            picked.append(row.to_dict())
            if len(picked) >= top_n:
                break
        return pd.DataFrame(picked)

    best = _pick(top=True)
    worst = _pick(top=False)

    def _enrich(seg: pd.DataFrame) -> pd.DataFrame:
        """补期间最高/最低点位与区间最大回撤"""
        rows = []
        for _, r in seg.iterrows():
            w = s.loc[r["起始日"]:r["结束日"]]
            cummax = w.cummax()
            mdd = ((w / cummax) - 1).min() * 100
            rows.append({
                "起始日": r["起始日"].date(), "结束日": r["结束日"].date(),
                "区间涨幅%": round(r["区间涨幅%"], 1),
                "期间最高": round(w.max(), 1), "期间最低": round(w.min(), 1),
                "区间最大回撤%": round(mdd, 1),
            })
        return pd.DataFrame(rows)

    return _enrich(best), _enrich(worst)


def main():
    parser = argparse.ArgumentParser(description="极端行情区段统计")
    parser.add_argument("--index", default="sh000001",
                        choices=["sh000001", "sh000300", "sz399001"])
    parser.add_argument("--window", type=int, default=30, help="连续交易日数")
    parser.add_argument("--top", type=int, default=3, help="最好/最差各取几段")
    parser.add_argument("--start", type=int, default=2021, help="起始年份")
    parser.add_argument("--chart", default="sh000300",
                        choices=["none", "sh000001", "sh000300", "sz399001"],
                        help="画哪个指数的K线（none=不画，默认沪深300）")
    args = parser.parse_args()

    name = INDEXES[args.index]
    cache = IndexData().cache
    s = cache[cache["代码"] == args.index].sort_values("日期")
    s = s[s["日期"] >= pd.Timestamp(year=args.start, month=1, day=1)]
    close = s.set_index("日期")["收盘"].astype(float)

    best, worst = find_extreme_windows(close, args.window, args.top)

    print(f"\n===== {name} 连续{args.window}交易日 极端区段（2010 ~ 至今，段间不重叠）=====\n")

    print("【最好 3 段】")
    print(best.to_string(index=False))
    print("\n【最差 3 段】")
    print(worst.to_string(index=False))

    # 输出
    out_csv = os.path.join(PROJECT_DIR, "output", "best_worst_windows.csv")
    pd.concat([best.assign(类型="最好"), worst.assign(类型="最差")]).to_csv(
        out_csv, index=False, encoding="utf-8")
    print(f"\n✓ CSV: {out_csv}")

    out_html = os.path.join(PROJECT_DIR, "output", "best_worst_windows.html")
    _write_html(name, args.window, best, worst, out_html)
    print(f"✓ HTML: {out_html}")

    if args.chart != "none":
        out_chart = os.path.join(PROJECT_DIR, "output", "best_worst_charts.html")
        _render_charts(args.chart, args.window, best, worst, out_chart)
        if os.path.exists(out_chart):
            print(f"✓ 图表: {out_chart}")
        else:
            print(f"✗ 图表未生成（原因见上方），表格页已正常输出")


def _render_charts(chart_code, window, best, worst, out_path):
    """
    每个区段一张 K 线图：段外±window 个交易日做背景，区间 markArea 高亮，MA20 叠加。
    6 张图合成一个自包含 HTML。
    """
    try:
        from pyecharts import options as opts
        from pyecharts.charts import Kline, Line, Page
        from pyecharts.commons.utils import JsCode
    except ImportError as e:
        print(f"✗ 图表跳过（pyecharts 导入失败: {e}）")
        print(f"  当前解释器: {sys.executable}")
        print("  请确认用 /usr/local/bin/python 运行，或 pip install -r demos/requirements.txt")
        return

    name = INDEXES[chart_code]
    cache = IndexData().cache
    df = cache[cache["代码"] == chart_code].sort_values("日期").set_index("日期")
    dates_all = df.index

    def _seg_chart(seg, color, tag):
        t0, t1 = pd.Timestamp(seg["起始日"]), pd.Timestamp(seg["结束日"])
        pos0 = dates_all.searchsorted(t0)
        pos1 = dates_all.searchsorted(t1, side="right")
        ctx = df.iloc[max(0, pos0 - window): min(len(dates_all), pos1 + window)]

        dates = ctx.index.strftime("%Y-%m-%d").tolist()
        # ECharts 蜡烛顺序: [open, close, low, high]
        ohlc = [[o, c, l, h] for o, h, l, c in
                ctx[["开盘", "最高", "最低", "收盘"]].values.tolist()]
        ma20 = ctx["收盘"].rolling(20).mean().round(1).tolist()

        # 日涨跌幅%（首日无前收，记 null），注入 JS 供 label/tooltip 使用
        chg = (ctx["收盘"].pct_change(fill_method=None) * 100).round(2).tolist()
        chg_js = "[" + ",".join(
            "null" if (v is None or pd.isna(v)) else f"{v:.2f}" for v in chg) + "]"

        label_fmt = JsCode(
            "function(param){var a=" + chg_js + ";var v=a[param.dataIndex];"
            "if(v===null||v===undefined)return '';return (v>0?'+':'')+v.toFixed(2)+'%';}")
        label_color = JsCode(
            "function(param){var a=" + chg_js + ";var v=a[param.dataIndex];"
            "if(v===null||v===undefined)return '#999';"
            "return v>0?'#ef232a':(v<0?'#14b143':'#999');}")
        tip_fmt = JsCode(
            "function(params){var p=params[0];var d=p.data;"
            # K线 data 可能是 [开,收,低,高] 或 [序号,开,收,低,高]，按长度自适应
            "var off=(d.length>4)?1:0;"
            "var o=d[off],c=d[off+1],l=d[off+2],h=d[off+3];"
            "var a=" + chg_js + ";var v=a[p.dataIndex];"
            "var s=p.name+'<br/>开盘: '+o+'<br/>收盘: '+c+'<br/>最低: '+l+'<br/>最高: '+h+'<br/>';"
            "if(v===null||v===undefined){s+='涨跌幅: —';}"
            "else{var col=v>0?'#ef232a':(v<0?'#14b143':'#999');"
            "s+='涨跌幅: <b style=\"color:'+col+'\">'+(v>0?'+':'')+v.toFixed(2)+'%</b>';}"
            "return s;}")

        title = f"{tag} {seg['起始日']} → {seg['结束日']}（{seg['区间涨幅%']:+.1f}%）"
        kline = (
            Kline(init_opts=opts.InitOpts(width="1100px", height="400px"))
            .add_xaxis(dates)
            .add_yaxis(name, ohlc, itemstyle_opts=opts.ItemStyleOpts(
                color="#ef232a", color0="#14b143"))  # A股红涨绿跌
            .set_global_opts(
                title_opts=opts.TitleOpts(title=title,
                                          title_textstyle_opts=opts.TextStyleOpts(font_size=14)),
                tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross",
                                              formatter=tip_fmt),
                datazoom_opts=[opts.DataZoomOpts(range_start=20, range_end=100)],
            )
            .set_series_opts(
                label_opts=opts.LabelOpts(
                    is_show=True, position="top", font_size=9,
                    formatter=label_fmt, color=label_color),
                markarea_opts=opts.MarkAreaOpts(data=[opts.MarkAreaItem(
                    name=title, x=(t0.strftime("%Y-%m-%d"), t1.strftime("%Y-%m-%d")),
                    itemstyle_opts=opts.ItemStyleOpts(color=color, opacity=0.18))])
            )
        )
        ma_line = (
            Line()
            .add_xaxis(dates)
            .add_yaxis("MA20", ma20, is_symbol_show=False,
                       linestyle_opts=opts.LineStyleOpts(width=1, color="#f59e0b"))
        )
        kline.overlap(ma_line)
        return kline

    page = Page(layout=Page.SimplePageLayout, page_title="极端行情K线图")
    for i, (_, seg) in enumerate(best.iterrows(), 1):
        page.add(_seg_chart(seg, "rgba(52,168,83,1)", f"✅ 最好{i}"))
    for i, (_, seg) in enumerate(worst.iterrows(), 1):
        page.add(_seg_chart(seg, "rgba(234,67,53,1)", f"❌ 最差{i}"))
    page.render(out_path)
    _localize_echarts(out_path)


def _localize_echarts(path):
    """
    pyecharts 默认引用 CDN（assets.pyecharts.org，局域网打不开）→
    替换为本地 output/vendor/echarts6.min.js，加载失败再回 CDN。
    """
    with open(path, encoding="utf-8") as f:
        html = f.read()
    html = html.replace(
        '<script type="text/javascript" src="https://assets.pyecharts.org/assets/v6/echarts.min.js"></script>',
        '<script src="vendor/echarts6.min.js" onerror="this.onerror=null;'
        "var s=document.createElement('script');"
        "s.src='https://cdn.jsdelivr.net/npm/echarts@6/dist/echarts.min.js';"
        'document.head.appendChild(s)"></script>',
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _write_html(name, window, best, worst, out_path):
    def _table(df):
        return df.to_html(index=False, classes="tbl", border=0)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{name} 极端行情区段</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f0f2f5;color:#333;padding:16px}}
h2{{font-size:18px}} h3{{font-size:14px;color:#555;margin:18px 0 6px}}
.tbl{{border-collapse:collapse;background:#fff;width:100%;font-size:12px;margin-bottom:8px}}
.tbl th,.tbl td{{border:1px solid #e0e0e0;padding:5px 8px;text-align:center}}
.tbl th{{background:#1a1a2e;color:#fff}}
.green th{{background:#1a6e34}} .red th{{background:#8b1a1a}}
</style></head><body>
<h2>{name} · 连续{window}交易日极端区段（2010 ~ 至今）</h2>
<h3>✅ 最好 {len(best)} 段</h3>{_table(best)}
<h3>❌ 最差 {len(worst)} 段</h3>{_table(worst)}
<div style="color:#999;font-size:11px;margin-top:12px">
脚本: scripts/research/best_worst_windows.py · 段间不重叠（贪心剔除重叠窗口）· 数据: 本地指数缓存</div>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
