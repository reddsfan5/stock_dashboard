#!/usr/bin/env python3
"""
星期效应统计 — 检验"黑色星期四"

统计 2010 年至今每个交易日涨跌，按 年份 × 星期几 分组：
上涨概率 / 下跌概率 / 平均涨跌幅 / 样本数。

用法
----
$ python -m scripts.research.weekday_stats                        # 上证指数（默认）
$ python -m scripts.research.weekday_stats --index sh000300       # 沪深300
$ python -m scripts.research.weekday_stats --index sz399001       # 深证成指
$ python -m scripts.research.weekday_stats --start 2015           # 指定起始年

输出
----
output/weekday_stats.csv    — 逐年 × 星期几 明细
output/weekday_stats.html   — 手机友好表格页（自包含）
"""

import argparse
import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from data.index import IndexData, INDEXES

WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五"]


def load_returns(code: str, start_year: int) -> pd.DataFrame:
    """指数缓存 → 日涨跌幅(%) + 年份 + 星期几 的 DataFrame"""
    cache = IndexData().cache
    df = cache[cache["代码"] == code].sort_values("日期").copy()
    df = df[df["日期"] >= pd.Timestamp(year=start_year, month=1, day=1)]
    df["涨跌幅%"] = df["收盘"].pct_change(fill_method=None) * 100
    df["年份"] = df["日期"].dt.year
    df["星期"] = df["日期"].dt.weekday + 1  # 1=周一 ... 5=周五
    return df.dropna(subset=["涨跌幅%"])


def compute(df: pd.DataFrame) -> "pd.DataFrame":
    """按 年份×星期 分组 → 上涨概率% / 平均涨跌幅% / 样本数"""
    rows = []
    for (year, wd), g in df.groupby(["年份", "星期"]):
        rows.append({
            "年份": year, "星期": wd,
            "样本数": len(g),
            "上涨概率%": round((g["涨跌幅%"] > 0).mean() * 100, 1),
            "下跌概率%": round((g["涨跌幅%"] < 0).mean() * 100, 1),
            "平均涨跌幅%": round(g["涨跌幅%"].mean(), 2),
        })
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="星期效应统计（黑色星期四检验）")
    parser.add_argument("--index", default="sh000001",
                        choices=["sh000001", "sh000300", "sz399001"],
                        help="指数代码（默认 sh000001 上证指数）")
    parser.add_argument("--start", type=int, default=2010, help="起始年份")
    args = parser.parse_args()

    name = INDEXES[args.index]
    df = load_returns(args.index, args.start)
    stats = compute(df)
    print(f"\n===== {name} 星期效应统计（{df['日期'].min().date()} ~ {df['日期'].max().date()}）=====\n")

    # ---- 逐年表：行=年份，列=星期几 上涨概率 ----
    pivot_up = stats.pivot(index="年份", columns="星期", values="上涨概率%").reindex(
        columns=range(1, 6))
    pivot_up.columns = WEEKDAYS_CN
    pivot_up["平均涨跌幅%"] = (df.groupby("年份")["涨跌幅%"].mean() * 1).round(2)
    print("逐年上涨概率（%）: ")
    print(pivot_up.to_string(float_format=lambda v: f"{v:>5.1f}"))

    # ---- 全期汇总：检验黑色星期四 ----
    print("\n全期汇总（2010 至今）: ")
    total = []
    for wd in range(1, 6):
        g = df[df["星期"] == wd]
        total.append({
            "星期": WEEKDAYS_CN[wd - 1],
            "样本数": len(g),
            "上涨概率%": round((g["涨跌幅%"] > 0).mean() * 100, 1),
            "下跌概率%": round((g["涨跌幅%"] < 0).mean() * 100, 1),
            "平均涨跌幅%": round(g["涨跌幅%"].mean(), 2),
        })
    total_df = pd.DataFrame(total)
    print(total_df.to_string(index=False))

    worst = total_df.loc[total_df["平均涨跌幅%"].idxmin()]
    best = total_df.loc[total_df["平均涨跌幅%"].idxmax()]
    print(f"\n结论: 平均涨跌幅最差的是【{worst['星期']}】{worst['平均涨跌幅%']}%（上涨概率 {worst['上涨概率%']}%）")
    print(f"      平均涨跌幅最好的是【{best['星期']}】{best['平均涨跌幅%']}%（上涨概率 {best['上涨概率%']}%）")

    # ---- 输出 CSV / HTML ----
    out_csv = os.path.join(PROJECT_DIR, "output", "weekday_stats.csv")
    stats.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"\n✓ CSV: {out_csv}")

    out_html = os.path.join(PROJECT_DIR, "output", "weekday_stats.html")
    _write_html(name, stats, total_df, pivot_up, out_html)
    print(f"✓ HTML: {out_html}")


def _write_html(name, stats, total_df, pivot_up, out_path):
    """手机友好自包含表格页"""
    def _table(df, caption):
        return f"<h3>{caption}</h3>" + df.to_html(
            index=False, classes="tbl", border=0,
            float_format=lambda v: f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}")

    pivot_html = pivot_up.reset_index().rename(columns={"index": "年份"}).to_html(
        index=False, classes="tbl", border=0, float_format=lambda v: f"{v:.1f}")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{name} 星期效应统计</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f0f2f5;color:#333;padding:16px}}
h2{{font-size:18px}} h3{{font-size:14px;color:#555;margin:20px 0 6px}}
.tbl{{border-collapse:collapse;background:#fff;width:100%;font-size:12px;margin-bottom:8px}}
.tbl th,.tbl td{{border:1px solid #e0e0e0;padding:5px 8px;text-align:center}}
.tbl th{{background:#1a1a2e;color:#fff;font-weight:600}}
.tbl tr:nth-child(even){{background:#fafafa}}
</style></head><body>
<h2>{name} 星期效应统计（2010 ~ 至今）</h2>
{_table(total_df, "全期汇总（上涨/下跌概率、平均涨跌幅）")}
{'<h3>逐年上涨概率（%）</h3>' + pivot_html}
<div style="color:#999;font-size:11px;margin-top:12px">
脚本: scripts/research/weekday_stats.py · 数据: 本地指数缓存（akshare 腾讯源）</div>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
