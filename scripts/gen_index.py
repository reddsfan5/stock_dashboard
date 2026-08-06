#!/usr/bin/env python3
"""生成 output/index.html 导航页，汇总所有 HTML 报告"""

import os, time, json
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
INDEX_FILE = os.path.join(OUTPUT_DIR, "index.html")

REPORT_META = {
    "dashboard.html":        ("📊", "选股仪表盘", "多维度选股结果 + K线弹窗"),
    "stats_report.html":     ("📈", "回测统计报告", "12个策略历史回测统计"),
    "portfolio_sim.html":    ("💰", "全市场 Overlap 组合模拟", "5万起，全市场主板，重叠策略"),
    "dip_buy_sim.html":      ("🎯", "回撤买入模拟", "基于历史最大跌幅的限价买入"),
    "upward_gap_sim.html":   ("🚀", "持续推高买入模拟", "连续N天推高 + 收阴买入"),
    "overlap_sim.html":      ("🔬", "单股票 Overlap 模拟", "单标的逐笔交易模拟"),
    "overlap_debug.html":    ("🔍", "单股票 Overlap 调试", "逐笔信号详细分析"),
}


def generate():
    files = sorted(
        [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".html") and f != "index.html"],
        key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)), reverse=True,
    )

    rows = ""
    for f in files:
        path = os.path.join(OUTPUT_DIR, f)
        size_kb = os.path.getsize(path) / 1024
        mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        icon, title, desc = REPORT_META.get(f, ("📄", f.replace(".html", "").replace("_", " "), ""))
        rows += f"""
        <a href="{f}" class="card">
          <div class="icon">{icon}</div>
          <div class="info">
            <div class="title">{title}</div>
            <div class="desc">{desc}</div>
            <div class="meta">{mtime} · {size_kb:.0f}KB</div>
          </div>
        </a>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A股量化系统 — 报告导航</title>
<style>
:root{{--bg:#f0f2f5;--card:#fff;--blue:#1a73e8;--text:#333;--muted:#999}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);font-size:14px;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:24px 32px}}
.header h1{{font-size:22px;font-weight:600}}
.header .sub{{color:#8892b0;font-size:12px;margin-top:4px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;padding:24px 32px;max-width:1200px}}
.card{{background:var(--card);border-radius:10px;padding:20px;text-decoration:none;color:var(--text);display:flex;align-items:center;gap:16px;box-shadow:0 1px 3px rgba(0,0,0,.06);transition:all .15s}}
.card:hover{{box-shadow:0 4px 12px rgba(0,0,0,.1);transform:translateY(-1px)}}
.icon{{font-size:32px;width:48px;text-align:center;flex-shrink:0}}
.info{{flex:1;min-width:0}}
.title{{font-size:15px;font-weight:600;color:var(--blue);margin-bottom:2px}}
.desc{{font-size:12px;color:var(--muted);margin-bottom:4px}}
.meta{{font-size:11px;color:#bbb}}
.footer{{text-align:center;color:var(--muted);font-size:11px;padding:20px}}
</style>
</head>
<body>
<div class="header">
  <h1>📋 A股量化系统 — 报告导航</h1>
  <div class="sub">{datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(files)} 份报告</div>
</div>
<div class="grid">{rows}
</div>
<div class="footer">每次运行选股/回测/模拟后自动更新 · 打开对应报告即可查看</div>
</body>
</html>"""

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ {INDEX_FILE} ({len(files)} 份报告)")


if __name__ == "__main__":
    generate()
