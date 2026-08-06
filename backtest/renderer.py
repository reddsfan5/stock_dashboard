"""
报告渲染器 — 读模板 + 填数据 → HTML 字符串

职责单一：把结构化数据变成 HTML，不关心数据怎么来的。
"""

import json
import os
from collections import defaultdict
from string import Template
from typing import List

import pandas as pd

from backtest.sim_types import Trade, EquityPoint

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _load_template(name: str) -> str:
    path = os.path.join(TEMPLATE_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _board_label(board: str) -> str:
    return {"main": "沪深主板", "all": "全A股", "gem": "创业板", "star": "科创板"}.get(board, board)


def _build_code_list(trades: List[Trade]) -> list:
    """构建与表格顺序一致的代码列表（按月降序，月内按交易顺序）"""
    months = defaultdict(list)
    for t in trades:
        months[t.buy_date[:7]].append(t)
    result = []
    for m in sorted(months.keys(), reverse=True):
        for t in months[m]:
            key = f"{t.code}|{t.buy_date}"
            if key not in result:
                result.append(key)
    return result


def _build_monthly_html(trades: List[Trade]) -> str:
    """构建按月份分组的交易明细 HTML"""
    months = defaultdict(list)
    for t in trades:
        months[t.buy_date[:7]].append(t)

    parts = []
    for mi, m in enumerate(sorted(months.keys(), reverse=True)):
        mt = months[m]
        m_wins = sum(1 for t in mt if t.is_win)
        m_pnl = sum(t.pnl for t in mt)

        rows = ""
        for t in mt:
            status = "✅止盈" if t.filled else "❌尾盘"
            win_cls = "win" if t.is_win else "loss"
            ret_cls = "positive" if t.return_pct > 0 else "negative"
            pnl_cls = "positive" if t.pnl > 0 else "negative"
            rows += (
                f'<tr class="{win_cls}">'
                f'<td class="code-clickable" onclick="showKline(\'{t.code}|{t.buy_date}\')">{t.code}</td>'
                f'<td>{t.name}</td>'
                f'<td>{t.buy_date[5:]}</td><td>{t.sell_date[5:]}</td>'
                f'<td class="num">{t.streak_days}天</td>'
                f'<td class="num">{t.lots}手</td>'
                f'<td class="num">@{t.buy_price:.2f}</td>'
                f'<td>{status}</td>'
                f'<td class="num">@{t.sell_price:.2f}</td>'
                f'<td class="num {ret_cls}">{t.return_pct:+.2f}%</td>'
                f'<td class="num {pnl_cls}">¥{t.pnl:+,.0f}</td></tr>'
            )

        collapsed = "collapsed" if mi > 0 else ""
        parts.append(f"""
<div class="month-group {collapsed}">
  <div class="month-hd" onclick="toggleMonth(this)">
    <span class="month-label">{m}</span>
    <span class="month-stats">{len(mt)}笔 | 胜率{m_wins/len(mt)*100:.0f}% | ¥{m_pnl:+,.0f}</span>
    <span class="month-toggle">▼</span>
  </div>
  <div class="month-body"><table>
    <thead><tr><th>代码</th><th>名称</th><th>买日</th><th>卖日</th><th>连续</th><th>手数</th><th>买价</th><th>结果</th><th>卖价</th><th>收益</th><th>盈亏</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </div>
</div>""")

    return "\n".join(parts)


def render_dip_buy_report(
    *,
    capital: float,
    target_pct: float,
    overlap_pct: float,
    commission_rate: float,
    stamp_tax: float,
    start_date: str,
    lookback: int,
    board: str,
    max_gain: float,
    limit_down: float,
    max_range_20d: float,
    trades: List[Trade],
    equity_curve: List[EquityPoint],
    final_equity: float,
    kline_map: dict,
) -> str:
    """渲染回撤买入模拟报告 HTML"""
    tpl = _load_template("dip_buy_report.html")

    total = len(trades)
    wins = sum(1 for t in trades if t.is_win)
    total_pnl = sum(t.pnl for t in trades)
    hit = sum(1 for t in trades if t.filled)

    eq_dates = [e.date for e in equity_curve]
    eq_values = [round(e.equity, 0) for e in equity_curve]
    daily_pos = [e.positions for e in equity_curve]

    pnl_class = "green" if total_pnl > 0 else "red"
    ret_class = "green" if final_equity >= capital else "red"
    board_label = _board_label(board)

    # 使用 Python string.Template 做简单替换
    # 注意：模板里的 $ 需要转义为 $$，但我们直接用 str.replace 更简单
    subs = {
        "$now": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "$board_label": board_label,
        "$board_note": "仅主板" if board == "main" else "全市场",
        "$lookback": str(lookback),
        "$target_pct": str(target_pct),
        "$commission_bp": str(int(commission_rate * 10000)),
        "$stamp_tax_bp": str(int(stamp_tax * 10000)),
        "$overlap_pct": str(overlap_pct),
        "$max_gain": str(max_gain),
        "$max_range_20d": str(max_range_20d),
        "$capital_fmt": f"{capital:,.0f}",
        "$start_date": start_date,
        "$total_trades": str(total),
        "$win_rate": f"{wins/total*100:.1f}" if total > 0 else "0.0",
        "$hit_rate": f"{hit/total*100:.1f}" if total > 0 else "0.0",
        "$pnl_class": pnl_class,
        "$total_pnl_fmt": f"{total_pnl:+,.0f}",
        "$ret_class": ret_class,
        "$final_equity_fmt": f"{final_equity:,.0f}",
        "$total_return": f"{(final_equity-capital)/max(capital,1)*100:+.1f}",
        "$eq_dates": json.dumps(eq_dates, ensure_ascii=False),
        "$eq_values": json.dumps(eq_values),
        "$daily_pos": json.dumps(daily_pos),
        "$code_list_json": json.dumps(_build_code_list(trades), ensure_ascii=False),
        "$kline_json": json.dumps(kline_map or {}, ensure_ascii=False),
        "$monthly_html": _build_monthly_html(trades),
    }

    html = tpl
    for k, v in subs.items():
        html = html.replace(k, v)

    # 自动刷新导航页
    try:
        from scripts.gen_index import generate
        generate()
    except Exception:
        pass

    return html
