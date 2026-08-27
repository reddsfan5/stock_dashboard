#!/usr/bin/env python3
"""生成手机版导航页 output/mobile.html"""

import os, subprocess, json
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")


def get_mtime(fname):
    path = os.path.join(OUTPUT_DIR, fname)
    return datetime.fromtimestamp(os.path.getmtime(path)) if os.path.exists(path) else None


def card(icon, title, desc, href, mtime=None):
    t = mtime.strftime("%m/%d %H:%M") if mtime else ""
    return f"""<a href="{href}" class="card">
  <span class="icon">{icon}</span>
  <div class="info"><span class="title">{title}</span>
  <span class="desc">{desc}</span></div>
  <span class="arrow">›</span></a>"""


def generate():
    now = datetime.now().strftime("%m/%d %H:%M")

    # 选股结果
    dash = get_mtime("dashboard.html")
    # 策略结果
    s8 = get_mtime("etf_momentum.html")  # 策略8 输出文件即 etf_momentum.html
    s9 = get_mtime("strategy_09.html")
    s13 = get_mtime("strategy_13.html")
    s14 = get_mtime("strategy_14.html")

    sections = [
        ("📈 整体行情", [
            card("📊", "整体行情统计", "资金量·指数·板块轮动", "market_mobile.html", get_mtime("market_mobile.html")),
            card("🗺️", "板块轮动热力图", "申万1/2级·大图完整板块名", "market_heatmap.html", get_mtime("market_heatmap.html")),
            card("📅", "星期效应统计", "黑色星期四验证·逐年涨跌概率", "weekday_stats.html", get_mtime("weekday_stats.html")),
            card("🌊", "极端行情区段", "30交易日最好/最差各3段", "best_worst_windows.html", get_mtime("best_worst_windows.html")),
            card("🕯️", "极端行情K线图", "沪深300·6段区间高亮K线", "best_worst_charts.html", get_mtime("best_worst_charts.html")),
        ]),
        ("📊 今日选股", [
            card("🔍", "选股仪表盘", "7模块全市场扫描", "dashboard_mobile.html", dash),
            card("🕐", "分时图查看器", "分时+均价VWAP·近两个月缓存", "minute_view.html", get_mtime("minute_view.html")),
        ]),
        ("✅ 盈利策略", [
            card("🥇", "策略8: ETF动量轮动", "+60% 年化13% 回撤-12%", "etf_momentum.html", s8),
            card("🥈", "策略9: 缩量回调洗盘", "+71% 胜率70% 43笔", "strategy_09.html", s9),
            card("🥉", "策略13: 强趋势回调", "MA150>MA200强势股回踩", "strategy_13.html", s13),
            card("📈", "策略14: 低波强势", "低波动+强趋势慢牛", "strategy_14.html", s14),
            card("📄", "ETF动量轮动·正式脚本", "策略8完整版+CSV导出", "etf_momentum.html", get_mtime("etf_momentum.html")),
        ]),
        ("💰 全市场模拟", [
            card("🎯", "回撤买入模拟", "限价单+层层过滤", "dip_buy_sim.html", get_mtime("dip_buy_sim.html")),
            card("🚀", "持续推高模拟", "推高信号+收阴买入", "upward_gap_sim.html", get_mtime("upward_gap_sim.html")),
            card("💵", "Overlap组合模拟", "全市场重叠策略", "portfolio_sim.html", get_mtime("portfolio_sim.html")),
        ]),
        ("🔬 单票分析", [
            card("📋", "Overlap调试", "逐笔信号详情", "overlap_debug.html", get_mtime("overlap_debug.html")),
            card("📉", "Overlap模拟交易", "单票资金模拟", "overlap_sim.html", get_mtime("overlap_sim.html")),
        ]),
        ("📈 统计回测", [
            card("📊", "回测统计报告", "12策略全市场统计", "stats_report.html", get_mtime("stats_report.html")),
            card("🔗", "连续性中断恢复回测", "连续K日→中断→次日恢复率", "backtest_break_resume.html", get_mtime("backtest_break_resume.html")),
            card("📋", "绩效分析报告", "quantstats: Sharpe/回撤/热力图", "quant_report.html", get_mtime("quant_report.html")),
        ]),
        ("📉 短线T+1 — 赌次日涨跌（全亏）", [
            card("📉", "策略1: 超跌反弹博次日", "2864笔亏93%", "strategy_01.html", get_mtime("strategy_01.html")),
            card("📉", "策略2: 缩量横盘等突破", "793笔亏38%", "strategy_02.html", get_mtime("strategy_02.html")),
        ]),
        ("📉 中线持仓 — 均线趋势类（全亏）", [
            card("📉", "策略3: 均线多头顺势追", "398笔亏18%", "strategy_03.html", get_mtime("strategy_03.html")),
            card("📉", "策略10: MACD金叉+放量", "110笔亏86%——滞后再滞后", "strategy_10.html", get_mtime("strategy_10.html")),
            card("📉", "策略11: 5/20均线金叉", "115笔亏15%——经典入门", "strategy_11.html", get_mtime("strategy_11.html")),
        ]),
        ("📉 中线持仓 — 超卖均值回归（全亏）", [
            card("📉", "策略4: 跌到支撑等反弹", "305笔亏7%——基准", "strategy_04.html", get_mtime("strategy_04.html")),
            card("📉", "策略5: 加5天持仓限制", "502笔亏11%——证明限时=被动止损", "strategy_05.html", get_mtime("strategy_05.html")),
        ]),
        ("📉 止损实验 / 月频轮动（全亏）", [
            card("📉", "策略6: ETF动量+止损", "332笔亏17%——对照策略8", "strategy_06.html", get_mtime("strategy_06.html")),
            card("📉", "策略7: 月度多因子", "167笔亏22%", "strategy_07.html", get_mtime("strategy_07.html")),
            card("📉", "策略12: 放量突破前高", "103笔亏10%", "strategy_12.html", get_mtime("strategy_12.html")),
        ]),
    ]

    body = ""
    for title, cards_html in sections:
        body += f'<div class="section"><h2>{title}</h2>{"".join(cards_html)}</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>📋 A股量化</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f0f2f5;color:#333;font-size:15px;-webkit-tap-highlight-color:transparent}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:18px 16px;position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px}}.header .time{{font-size:11px;color:#8892b0}}
.section{{padding:12px 12px 0}}
.section h2{{font-size:13px;color:#999;padding:4px 4px 8px;font-weight:500}}
.card{{display:flex;align-items:center;gap:12px;background:#fff;padding:14px 12px;margin-bottom:6px;border-radius:10px;text-decoration:none;color:#333;box-shadow:0 1px 2px rgba(0,0,0,.04);touch-action:manipulation}}
.card:active{{background:#e8f0fe}}
.icon{{font-size:24px;width:32px;text-align:center}}
.info{{flex:1;display:flex;flex-direction:column;gap:2px;min-width:0}}
.title{{font-size:15px;font-weight:600;color:#1a73e8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.desc{{font-size:12px;color:#999;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.arrow{{font-size:20px;color:#ccc}}
.footer{{text-align:center;padding:20px;font-size:11px;color:#bbb}}
</style>
</head>
<body>
<div class="header"><h1>📋 A股量化系统</h1><span class="time">{now}</span></div>
{body}
<div class="footer">在电脑上打开 <b>output/index.html</b> 查看完整层次导航</div>
</body>
</html>"""

    path = os.path.join(OUTPUT_DIR, "mobile.html")
    with open(path, "w") as f:
        f.write(html)
    print(f"✓ {path}")


if __name__ == "__main__":
    generate()
