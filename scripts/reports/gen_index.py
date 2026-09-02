#!/usr/bin/env python3
"""生成 output/index.html 导航页，按使用场景和访问频率组织入口。"""

import os
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
INDEX_FILE = os.path.join(OUTPUT_DIR, "index.html")
MINUTE_VIEW_URL = "http://127.0.0.1:8765/minute_view.html"
GRID_SIMULATOR_URL = "http://127.0.0.1:8765/grid_simulator.html"
TRADING_TRAINER_URL = "http://127.0.0.1:8765/trading_trainer.html"
STOCK_JOURNAL_URL = "http://127.0.0.1:8765/stock_journal.html"
SERVICE_URLS = {
    "minute_view.html": MINUTE_VIEW_URL,
    "grid_simulator.html": GRID_SIMULATOR_URL,
    "trading_trainer.html": TRADING_TRAINER_URL,
    "stock_journal.html": STOCK_JOURNAL_URL,
}

# 用户视角的导航结构：高频入口在前，低频研究与历史实验在后。
# items: (文件名, 图标, 标题, 描述, 用途标签)
GROUPS = [
    {
        "id": "daily",
        "nav": "日常看盘",
        "kicker": "高频入口 · 每日 / 盘中",
        "title": "日常工作台",
        "description": "每天更新数据后优先使用：先看全局，再看板块、个股与分时细节。",
        "tone": "daily",
        "items": [
            ("dashboard.html", "🎛️", "选股仪表盘", "技术形态扫描叠加量比、换手、动量、风险和估值指标，可筛选排序并查看K线", "每日选股"),
            ("stock_journal.html", "📓", "选股日记工作台", "日K叠加决策记录，内嵌分时可从开盘动态回放，按当时可见行情记录心理与交易逻辑", "决策记录"),
            ("market_overview.html", "🌐", "整体行情统计", "每日资金量、大盘走势与申万1/2级板块强度轮动", "市场全景"),
            ("market_heatmap.html", "🧭", "板块轮动热力图", "申万1/2级 × 20～120日窗口，观察板块强弱和轮动方向", "板块跟踪"),
            ("minute_view.html", "⏱️", "分时行情查询", "按名称、代码和交易日查询分钟缓存，可悬停查价或从开盘动态回放", "交互查询"),
        ],
    },
    {
        "id": "training",
        "nav": "交易训练",
        "kicker": "交互工具 · 按需启动",
        "title": "交易训练与动态回放",
        "description": "用于练习主观进出场和理解自动交易过程；两个入口共用本地交互服务。",
        "tone": "training",
        "items": [
            ("trading_trainer.html", "🎯", "T+1无剧透交易训练", "日K与分时逐步揭示，显示当时可见的盘中量比、VWAP和风险指标，并严格执行T+1", "T+1训练"),
            ("grid_simulator.html", "🕸️", "T+0网格动态回放", "逐分钟播放到价触发型与成交驱动型网格的委托、成交、持仓和净值", "T+0回放"),
        ],
    },
    {
        "id": "results",
        "nav": "策略成果",
        "kicker": "阶段复盘 · 周期查看",
        "title": "策略结果与绩效分析",
        "description": "集中查看已验证策略、全市场回测结果和组合绩效，不与探索性实验混放。",
        "tone": "results",
        "items": [
            ("stats_report.html", "📊", "回测统计报告", "多种策略的全市场历史回测、成功率热力图与策略对比", "横向对比"),
            ("quant_report.html", "📈", "绩效分析报告", "Sharpe、回撤、月度热力图等机构级绩效指标", "绩效归因"),
            ("strategy_08.html", "✅", "策略8：ETF动量轮动", "30天动量排名，不设机械止盈止损的稳健轮动样本", "盈利样本"),
            ("strategy_09.html", "✅", "策略9：缩量回调洗盘", "放量上涨后缩量回调，验证强势趋势中的洗盘机会", "盈利样本"),
            ("etf_momentum.html", "🥇", "ETF动量轮动（正式版）", "策略8的完整策略报告，含结果明细和K线交互核对", "正式策略"),
        ],
    },
    {
        "id": "simulation",
        "nav": "资金模拟",
        "kicker": "组合验证 · 按策略迭代",
        "title": "全市场资金模拟",
        "description": "验证信号进入真实资金约束后，仓位、成交顺序与T+1规则对结果的影响。",
        "tone": "simulation",
        "items": [
            ("portfolio_sim.html", "💼", "Overlap组合模拟（原始版）", "连续重叠信号后随机与低价填仓，次日挂单退出", "组合基线"),
            ("dip_buy_sim.html", "📥", "回撤买入模拟（限价版）", "按近期最大跌幅计算限价，跌到位成交并分层过滤", "回撤买入"),
            ("upward_gap_sim.html", "📤", "持续推高买入模拟", "连续推高信号结合收阴回撤，采用半仓分散买入", "趋势跟随"),
        ],
    },
    {
        "id": "research",
        "nav": "专题研究",
        "kicker": "低频入口 · 偶尔复查",
        "title": "长期统计与专题验证",
        "description": "回答市场规律和方法论问题，适合样本扩展或逻辑调整后重新运行。",
        "tone": "research",
        "items": [
            ("market_proverbs.html", "🧠", "市场口诀回测（前7条）", "把小涨、大涨、横盘和急涨慢跌量化为七项事件研究，对比后续收益、指数超额和条件基线", "经验检验"),
            ("slow_rise_backtest.html", "🌱", "低位缓涨3～5日回测", "近30日相对低位后缓涨N日，次日开盘买入，严格T+1，触及5%止盈，否则第5日退出", "可执行验证"),
            ("weekday_stats.html", "🗓️", "星期效应统计", "2010年至今逐年统计各星期的涨跌概率，检验日历效应", "长期样本"),
            ("backtest_break_resume.html", "🔁", "连续性中断恢复回测", "连续K日接续、中断和次日恢复概率及其对照基线", "序列规律"),
            ("best_worst_windows.html", "🔭", "极端行情区段", "识别连续30个交易日最好与最差的非重叠区段", "极端样本"),
            ("best_worst_charts.html", "🗺️", "极端行情K线图", "沪深300六段极端区间的K线、MA20与区间高亮", "图形核对"),
            ("overlap_debug.html", "🔬", "Overlap逐笔调试", "逐条检查信号重叠区和收益分布，定位交易流问题", "逻辑调试"),
            ("overlap_sim.html", "🧾", "单股票Overlap模拟交易", "重叠、低买、次日挂单卖出的逐笔交易与K线核对", "单标的验证"),
        ],
    },
    {
        "id": "archive",
        "nav": "实验档案",
        "kicker": "研究档案 · 长期保留",
        "title": "历史策略与控制变量实验",
        "description": "保留失败路径、参数变体和待验证因子，主要用于回顾设计思路，避免重复踩坑。",
        "tone": "archive",
        "items": [
            ("strategy_01.html", "🧪", "策略1：超跌反弹博次日", "连跌后抄底，使用固定止盈止损验证短线反弹", "短线T+1"),
            ("strategy_02.html", "🧪", "策略2：缩量横盘等突破", "横盘缩量后放量突破，验证次日追涨的有效性", "短线T+1"),
            ("strategy_03.html", "🧪", "策略3：均线多头顺势追", "均线多头与量能扩张组合的趋势跟随实验", "均线趋势"),
            ("strategy_04.html", "🔬", "策略4：跌到支撑等反弹", "接近20日低点并收阳的均值回归基准实验", "回归基准"),
            ("strategy_05.html", "🔬", "策略5：加入5天持仓限制", "在策略4基础上增加最长持仓，用于控制变量比较", "参数变体"),
            ("strategy_06.html", "🔬", "策略6：ETF动量加止损", "与策略8对照，检验机械止盈止损对动量策略的影响", "止损实验"),
            ("strategy_07.html", "🧪", "策略7：四因子等权打分", "振幅、动量、量能和强势度的月频多因子实验", "多因子"),
            ("strategy_10.html", "🧪", "策略10：MACD金叉加放量", "检验双重滞后信号在中线持仓中的表现", "技术指标"),
            ("strategy_11.html", "🧪", "策略11：5/20均线金叉", "经典均线金叉的基础有效性实验", "均线趋势"),
            ("strategy_12.html", "🧪", "策略12：放量突破前高", "突破20日高点并放量后的月频持仓实验", "突破策略"),
            ("strategy_13.html", "🧪", "策略13：强趋势缩量回调", "长期均线多头且接近年度高点时等待缩量回踩", "待验证因子"),
            ("strategy_14.html", "🧪", "策略14：低波动强趋势", "在长期上升趋势中选择低振幅、强动量标的", "待验证因子"),
        ],
    },
]


def generate():
    existing = {
        filename
        for filename in os.listdir(OUTPUT_DIR)
        if filename.endswith(".html") and filename != "index.html"
    }

    visible_groups = []
    for group in GROUPS:
        visible = [item for item in group["items"] if item[0] in existing]
        if visible:
            visible_groups.append((group, visible))

    nav_links = "".join(
        f'<a href="#{group["id"]}" class="nav-link nav-{group["tone"]}">'
        f'{group["nav"]}<span>{len(visible)}</span></a>'
        for group, visible in visible_groups
    )

    sections = ""
    for group, visible in visible_groups:
        cards = ""
        for filename, icon, title, desc, badge in visible:
            path = os.path.join(OUTPUT_DIR, filename)
            href = SERVICE_URLS.get(filename, filename)
            size_kb = os.path.getsize(path) / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%m-%d %H:%M")
            service_hint = " · 本地服务" if filename in SERVICE_URLS else ""
            cards += f"""
            <a href="{href}" class="card">
              <div class="icon" aria-hidden="true">{icon}</div>
              <div class="info">
                <div class="card-heading">
                  <div class="title">{title}</div>
                  <span class="badge">{badge}</span>
                </div>
                <div class="desc">{desc}</div>
                <div class="meta">更新 {mtime} · {size_kb:.0f}KB{service_hint}</div>
              </div>
              <span class="arrow" aria-hidden="true">›</span>
            </a>"""

        sections += f"""
        <section class="section section-{group['tone']}" id="{group['id']}">
          <div class="section-heading">
            <div>
              <div class="kicker">{group['kicker']}</div>
              <h2>{group['title']} <span class="count">{len(visible)}</span></h2>
              <p>{group['description']}</p>
            </div>
            <a class="back-top" href="#top">返回顶部 ↑</a>
          </div>
          <div class="grid">{cards}</div>
        </section>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A股量化系统 — 工作台导航</title>
<style>
:root{{--bg:#f3f5f8;--card:#fff;--blue:#2563eb;--text:#182033;--muted:#687386;--border:#e4e8ef;--daily:#2563eb;--training:#7c3aed;--results:#0f9f6e;--simulation:#d97706;--research:#536277;--archive:#7b8494}}
*{{margin:0;padding:0;box-sizing:border-box}}
html{{scroll-behavior:smooth;scroll-padding-top:76px}}
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);font-size:14px;min-height:100vh}}
.header{{background:linear-gradient(135deg,#121b31,#1d3158);color:#fff;padding:30px 32px 28px}}
.header-inner{{max-width:1300px;margin:0 auto}}
.header h1{{font-size:26px;font-weight:700;letter-spacing:.01em}}
.header .sub{{color:#b5c1d9;font-size:13px;line-height:1.7;margin-top:7px}}
.quick-nav-wrap{{position:sticky;top:0;z-index:10;background:rgba(243,245,248,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}}
.quick-nav{{max-width:1300px;margin:0 auto;padding:10px 32px;display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}}
.quick-nav::-webkit-scrollbar{{display:none}}
.nav-link{{display:inline-flex;align-items:center;gap:7px;white-space:nowrap;text-decoration:none;color:#475267;background:#fff;border:1px solid var(--border);border-radius:999px;padding:8px 12px;font-size:12px;font-weight:600}}
.nav-link span{{display:grid;place-items:center;min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:#eef1f6;color:#707a8b;font-size:10px}}
.nav-link:hover{{color:var(--blue);border-color:#b7caf5}}
.nav-daily{{color:var(--daily);border-color:#cbdafb}}
.nav-training{{color:var(--training);border-color:#ddd0fb}}
main{{max-width:1300px;margin:0 auto;padding:8px 32px 20px}}
.section{{--accent:var(--archive);padding:26px 0 8px;border-top:1px solid var(--border)}}
.section:first-child{{border-top:0}}
.section-daily{{--accent:var(--daily)}}
.section-training{{--accent:var(--training)}}
.section-results{{--accent:var(--results)}}
.section-simulation{{--accent:var(--simulation)}}
.section-research{{--accent:var(--research)}}
.section-archive{{--accent:var(--archive)}}
.section-heading{{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:14px}}
.kicker{{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--accent);margin-bottom:5px}}
.section h2{{font-size:20px;line-height:1.3}}
.section-heading p{{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.6}}
.count{{display:inline-grid;place-items:center;min-width:22px;height:22px;padding:0 6px;border-radius:11px;background:color-mix(in srgb,var(--accent) 12%,white);color:var(--accent);font-size:11px;vertical-align:2px}}
.back-top{{font-size:11px;color:#9aa2af;text-decoration:none;white-space:nowrap}}
.back-top:hover{{color:var(--accent)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:11px}}
.card{{position:relative;background:var(--card);border:1px solid var(--border);border-radius:11px;padding:16px;text-decoration:none;color:var(--text);display:flex;align-items:flex-start;gap:12px;box-shadow:0 1px 2px rgba(18,27,49,.025);transition:transform .15s,box-shadow .15s,border-color .15s}}
.card:hover{{box-shadow:0 7px 20px rgba(18,27,49,.09);transform:translateY(-2px);border-color:color-mix(in srgb,var(--accent) 42%,white)}}
.icon{{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:color-mix(in srgb,var(--accent) 9%,white);font-size:20px;flex-shrink:0}}
.info{{flex:1;min-width:0}}
.card-heading{{display:flex;align-items:flex-start;gap:8px;justify-content:space-between}}
.title{{font-size:14px;font-weight:700;color:#27324a;line-height:1.45}}
.badge{{flex-shrink:0;color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,white);border:1px solid color-mix(in srgb,var(--accent) 16%,white);font-size:10px;font-weight:600;padding:2px 6px;border-radius:5px;line-height:1.5}}
.desc{{font-size:12px;color:var(--muted);line-height:1.55;margin:5px 20px 7px 0}}
.meta{{font-size:10px;color:#a0a8b5}}
.arrow{{position:absolute;right:13px;top:50%;transform:translateY(-50%);font-size:21px;color:#c3c9d2}}
.section-daily .card,.section-training .card{{border-top:3px solid color-mix(in srgb,var(--accent) 72%,white)}}
.section-research,.section-archive{{margin-top:18px}}
.section-archive .card{{background:#fafbfc;box-shadow:none}}
.footer{{text-align:center;color:#929baa;font-size:11px;padding:18px 24px 30px}}
@media (max-width:700px){{
  html{{scroll-padding-top:62px}}
  .header{{padding:24px 18px 21px}}
  .header h1{{font-size:21px}}
  .header .sub{{font-size:12px}}
  .quick-nav{{padding:8px 14px}}
  main{{padding:4px 14px 16px}}
  .section{{padding-top:22px}}
  .section-heading{{align-items:flex-start}}
  .section h2{{font-size:18px}}
  .back-top{{display:none}}
  .grid{{grid-template-columns:1fr}}
  .card{{padding:14px 13px}}
  .card-heading{{display:block}}
  .badge{{display:inline-block;margin-top:4px}}
  .desc{{margin-right:14px}}
}}
@media (prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.card{{transition:none}}}}
</style>
</head>
<body id="top">
<header class="header">
  <div class="header-inner">
    <h1>A股量化系统 · 工作台</h1>
    <div class="sub">{datetime.now().strftime('%Y-%m-%d %H:%M')} 更新 · 日常看盘与训练优先，长期研究和历史实验归档在后</div>
  </div>
</header>
<div class="quick-nav-wrap" aria-label="页面分类导航">
  <nav class="quick-nav">{nav_links}</nav>
</div>
<main>{sections}</main>
<footer class="footer">运行选股、回测或模拟后会自动刷新对应报告 · 交互工具需启动本地服务</footer>
</body>
</html>"""

    with open(INDEX_FILE, "w", encoding="utf-8") as file:
        file.write(html)
    print(f"✓ {INDEX_FILE}")


if __name__ == "__main__":
    generate()
