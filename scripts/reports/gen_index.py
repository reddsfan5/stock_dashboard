#!/usr/bin/env python3
"""生成 output/index.html 导航页 — 按策略层次分组展示"""

import os
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
INDEX_FILE = os.path.join(OUTPUT_DIR, "index.html")
MINUTE_VIEW_URL = "http://127.0.0.1:8765/minute_view.html"
GRID_SIMULATOR_URL = "http://127.0.0.1:8765/grid_simulator.html"
SERVICE_URLS = {
    "minute_view.html": MINUTE_VIEW_URL,
    "grid_simulator.html": GRID_SIMULATOR_URL,
}

# 层次分组：(组标题, [(文件名, 标题, 描述), ...])
GROUPS = [
    ("📊 选股与回测（系统工具）", [
        ("market_overview.html", "整体行情统计", "每日资金量·大盘走势·板块强度轮动（申万1/2级）"),
        ("market_heatmap.html", "板块轮动热力图", "申万1/2级 × 20~120日窗口，大图完整显示板块名"),
        ("weekday_stats.html", "星期效应统计", "2010至今逐年×星期几涨跌概率——验证黑色星期四：周四唯一低于50%"),
        ("minute_view.html", "分时行情查询", "按名称/代码和交易日查分钟缓存，悬停显示价格、涨跌、OHLC、量额"),
        ("grid_simulator.html", "T+0网格动态回放", "指定标的和参数，逐分钟播放网格线、买卖点、现金、持仓与净值"),

        ("best_worst_windows.html", "极端行情区段", "连续30交易日最好/最差各3段（段间不重叠）——全集中在2014-2016"),
        ("best_worst_charts.html", "极端行情K线图", "沪深300 · 6段极端区间的日K线（区间高亮+MA20+缩放）"),

        ("dashboard.html",     "选股仪表盘", "7大技术形态并行扫描全市场，含K线弹窗、行业分类、键盘切换"),
        ("stats_report.html",  "回测统计报告", "12种策略全市场历史回测，成功率热力图+策略对比散点图"),
        ("backtest_break_resume.html", "连续性中断恢复回测", "连续K日接续→中断→次日恢复概率（对照任意中断基线，随K单调上升）"),
        ("quant_report.html",  "绩效分析报告", "quantstats机构级指标：Sharpe/回撤/月度热力图（策略8权益CSV）"),
    ]),
    ("🔬 单标的调试（交易流验证）", [
        ("overlap_debug.html", "Overlap逐笔调试", "终端逐条打印每次信号重叠区详情，HTML含收益分布图"),
        ("overlap_sim.html",   "单股票Overlap模拟交易", "逐笔模拟：重叠→低买→次日挂单卖出。含完整K线核对"),
    ]),
    ("💰 全市场资金模拟", [
        ("portfolio_sim.html", "Overlap组合模拟（原始版）", "连续3-10天重叠>3%→随机+低价填满→T+1挂单"),
        ("dip_buy_sim.html",   "回撤买入模拟（限价版）", "15日最大跌幅算限价→跌到位成交→层层过滤→上一日最低价+m%止盈"),
        ("upward_gap_sim.html","持续推高买入模拟", "连续推高信号→收阴2-5%+连续≤8天→半仓分散买入"),
    ]),

    # ---- 策略研究：按逻辑链条分组 ----
    ("📉 短线T+1 — 赌次日涨跌（全部亏损）", [
        ("strategy_01.html",   "策略1：超跌反弹博次日", "连跌3天→抄底→+1%止盈/-2%止损。2864笔亏93%"),
        ("strategy_02.html",   "策略2：缩量横盘等突破", "横盘缩量→放量突破→次日追。793笔亏38%"),
    ]),
    ("📉 中线持仓 — 均线趋势类（全部亏损）", [
        ("strategy_03.html",   "策略3：均线多头顺势追", "MA5>MA10>MA20+量增→买→+8%止盈/-4%止损。398笔亏18%"),
        ("strategy_11.html",   "策略11：5/20均线金叉", "MA5上穿MA20→买。115笔亏15%——经典入门，滞后性致命"),
        ("strategy_10.html",   "策略10：MACD金叉+放量", "DIF上穿DEA+量增→买。110笔亏86%——滞后再滞后"),
    ]),
    ("📉 中线持仓 — 超卖均值回归（控制变量实验）", [
        ("strategy_04.html",   "策略4：跌到支撑等反弹 ← 基准", "距20日最低<3%+收阳→买→+5%止盈/-3%止损。305笔亏7%"),
        ("strategy_05.html",   "　└ 策略5：加5天持仓限制", "同策略4信号+持仓≤5天强平。502笔亏11%——证明限时=被动止损"),
    ]),
    ("📉 中线持仓 — 止损实验（核心发现）", [
        ("strategy_06.html",   "策略6：ETF动量+止损 ← 有止损", "周频调仓5只ETF+5%止盈/-3%止损。332笔亏17%"),
        ("strategy_08.html",   "　└ 策略8：ETF动量不止损 ✅", "完全相同的选股逻辑，去掉止盈止损后。100笔赚60%！"),
    ]),
    ("📉 月频轮动 — 同花顺经典战术（全部亏损）", [
        ("strategy_12.html",   "策略12：放量突破前高", "突破20日高点+量>1.5倍均量→买。103笔亏10%"),
        ("strategy_07.html",   "策略7：四因子等权打分", "振幅+动量+量+强势度→月频10只。167笔亏22%"),
    ]),
    ("✅ 盈利策略（实盘可用）", [
        ("strategy_08.html",   "策略8：ETF动量轮动 · 稳健推荐", "30天×5只纯动量排名。+60%年化13%回撤-12%——月频操作省心"),
        ("strategy_09.html",   "策略9：缩量回调洗盘 · 高胜率", "放量涨+缩量跌=洗盘。20天×5只ETF。+71%胜率70%回撤-39%"),
        ("etf_momentum.html",  "ETF动量轮动（正式策略脚本）", "策略8的完整版本，含HTML报告和K线弹窗"),
    ]),
]


def generate():
    existing = set(f for f in os.listdir(OUTPUT_DIR)
                   if f.endswith(".html") and f != "index.html")

    sections = ""
    for group_title, items in GROUPS:
        visible = [(f, t, d) for f, t, d in items if f in existing]
        if not visible:
            continue

        cards = ""
        for filename, title, desc in visible:
            path = os.path.join(OUTPUT_DIR, filename)
            href = SERVICE_URLS.get(filename, filename)
            size_kb = os.path.getsize(path) / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%m-%d %H:%M")
            # 根据标题决定图标
            if "✅" in title:
                icon = "✅"
            elif "　└" in title:
                icon = "└▶"
            elif "←" in title:
                icon = "🔬"
            else:
                icon = "📉"
            cards += f"""
            <a href="{href}" class="card">
              <div class="icon">{icon}</div>
              <div class="info">
                <div class="title">{title}</div>
                <div class="desc">{desc}</div>
                <div class="meta">{mtime} · {size_kb:.0f}KB</div>
              </div>
            </a>"""

        sections += f"""
        <div class="section">
          <h2 class="section-title">{group_title} <span class="count">{len(visible)}</span></h2>
          <div class="grid">{cards}</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A股量化系统 — 报告导航</title>
<style>
:root{{--bg:#f0f2f5;--card:#fff;--blue:#1a73e8;--red:#ea4335;--green:#34a853;--text:#333;--muted:#999;--border:#e0e0e0}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);font-size:14px;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:24px 32px}}
.header h1{{font-size:22px;font-weight:600}}
.header .sub{{color:#8892b0;font-size:12px;margin-top:4px}}
.section{{padding:0 32px;max-width:1300px;margin-bottom:4px}}
.section-title{{font-size:14px;font-weight:600;color:#555;padding:14px 0 8px;border-bottom:2px solid var(--border);margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.section-title .count{{background:var(--blue);color:#fff;font-size:10px;padding:2px 8px;border-radius:10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:10px;margin-bottom:16px}}
.card{{background:var(--card);border-radius:8px;padding:16px;text-decoration:none;color:var(--text);display:flex;align-items:flex-start;gap:12px;box-shadow:0 1px 3px rgba(0,0,0,.05);transition:all .15s;border-left:3px solid transparent}}
.card:hover{{box-shadow:0 4px 12px rgba(0,0,0,.1);transform:translateY(-1px);border-left-color:var(--blue)}}
.icon{{font-size:22px;width:32px;text-align:center;flex-shrink:0;line-height:1.2}}
.info{{flex:1;min-width:0}}
.title{{font-size:14px;font-weight:600;color:var(--blue);margin-bottom:3px}}
.desc{{font-size:12px;color:var(--muted);line-height:1.5;margin-bottom:4px}}
.meta{{font-size:11px;color:#bbb}}
.footer{{text-align:center;color:var(--muted);font-size:11px;padding:24px}}
</style>
</head>
<body>
<div class="header">
  <h1>📋 A股量化系统 — 策略研究导航</h1>
  <div class="sub">{datetime.now().strftime('%Y-%m-%d %H:%M')} · 按逻辑关系分组：└ 表示参数变体，✅ 表示盈利策略</div>
</div>
{sections}
<div class="footer">每次运行选股/回测/模拟后自动刷新 · 箭头/缩进表示策略演变关系</div>
</body>
</html>"""

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ {INDEX_FILE}")


if __name__ == "__main__":
    generate()
