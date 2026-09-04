"""选股→交易研究报告卡（中文标签 HTML + JSON 结构）。"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping


def _num(value, digits=2, sign=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if value != value:  # NaN
        return "—"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def _pct(value, digits=2, sign=False):
    text = _num(value, digits, sign)
    return text if text == "—" else text + "%"


def _tone(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    if value != value:
        return ""
    return "up" if value > 0 else "down" if value < 0 else ""


def report_card_to_jsonable(result: Mapping[str, Any]) -> dict:
    """去掉 DataFrame，保留可序列化报告卡。"""
    card = dict(result.get("report_card") or {})
    config = dict(result.get("config") or {})
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": config,
        "diagnostics": result.get("diagnostics"),
        "report_card": card,
        "baselines": result.get("baselines"),
        "equity_curve": result.get("equity_curve"),
    }


def build_screen_to_trade_report(result: Mapping[str, Any]) -> str:
    card = result.get("report_card") or {}
    diagnostics = result.get("diagnostics") or {}
    config = result.get("config") or {}
    event = card.get("event_stats") or {}
    port = card.get("portfolio_metrics") or {}
    baselines = card.get("baselines") or []
    slices = card.get("slices") or {}
    failures = card.get("failure_examples") or []
    title = card.get("module_title") or config.get("module") or "选股"

    def rows_baseline():
        html = ""
        for row in baselines:
            if not row.get("available"):
                html += (
                    f"<tr><td>{escape(str(row.get('baseline')))}</td>"
                    f"<td colspan='3' class='muted'>"
                    f"{escape(str(row.get('reason') or '不可用'))}</td></tr>"
                )
                continue
            html += (
                f"<tr><td>{escape(str(row.get('baseline')))}</td>"
                f"<td class='{_tone(row.get('baseline_avg_pct'))}'>"
                f"{_pct(row.get('baseline_avg_pct'), sign=True)}</td>"
                f"<td class='{_tone(row.get('strategy_avg_pct'))}'>"
                f"{_pct(row.get('strategy_avg_pct'), sign=True)}</td>"
                f"<td class='{_tone(row.get('excess_pct'))}'>"
                f"{_pct(row.get('excess_pct'), sign=True)}</td></tr>"
            )
        return html

    def rows_year():
        html = ""
        for row in slices.get("by_year") or []:
            html += (
                f"<tr><td>{escape(str(row.get('slice')))}</td>"
                f"<td>{int(row.get('trades') or 0):,}</td>"
                f"<td class='{_tone(row.get('avg_net_return_pct'))}'>"
                f"{_pct(row.get('avg_net_return_pct'), sign=True)}</td>"
                f"<td>{_pct(row.get('win_rate_pct'))}</td></tr>"
            )
        return html or "<tr><td colspan='4' class='muted'>无年度样本</td></tr>"

    def rows_regime():
        html = ""
        for row in slices.get("by_regime") or []:
            html += (
                f"<tr><td>{escape(str(row.get('slice')))}</td>"
                f"<td>{int(row.get('trades') or 0):,}</td>"
                f"<td class='{_tone(row.get('avg_net_return_pct'))}'>"
                f"{_pct(row.get('avg_net_return_pct'), sign=True)}</td>"
                f"<td>{_pct(row.get('win_rate_pct'))}</td></tr>"
            )
        return html or (
            "<tr><td colspan='4' class='muted'>无牛熊切片（需指数均线）</td></tr>"
        )

    def rows_fail():
        html = ""
        for row in failures:
            html += (
                f"<tr><td>{escape(str(row.get('代码')))}</td>"
                f"<td>{escape(str(row.get('信号日')))}</td>"
                f"<td>{escape(str(row.get('入场日') or '—'))}</td>"
                f"<td>{escape(str(row.get('退出日') or '—'))}</td>"
                f"<td class='down'>{_pct(row.get('净收益%'), sign=True)}</td></tr>"
            )
        return html or "<tr><td colspan='5' class='muted'>无失败样本</td></tr>"

    weak = slices.get("weak_slices") or []
    if weak:
        items = "".join(
            f"<li><b>{escape(str(w.get('slice')))}</b> "
            f"({escape(str(w.get('kind')))}) "
            f"均收益 {_pct(w.get('avg_net_return_pct'), sign=True)} / "
            f"{int(w.get('trades') or 0)} 笔</li>"
            for w in weak
        )
        weak_html = f"<div class='warn'><b>偏弱切片</b><ul>{items}</ul></div>"
    else:
        weak_html = "<div class='ok'>未发现达到阈值的偏弱切片。</div>"

    split = card.get("split")
    if split:
        split_html = (
            f"<p>样本内 {escape(str(split.get('train_start')))} ~ "
            f"{escape(str(split.get('train_end')))} "
            f"（{split.get('train_days')} 个信号日）；"
            f"隔离 {split.get('embargo_days')} 日；"
            f"样本外 {escape(str(split.get('validation_start')))} ~ "
            f"{escape(str(split.get('validation_end')))} "
            f"（{split.get('validation_days')} 个信号日）</p>"
        )
    elif card.get("split_note"):
        split_html = f"<p class='muted'>{escape(str(card.get('split_note')))}</p>"
    else:
        split_html = ""

    is_stats = card.get("in_sample") or {}
    oos_stats = card.get("out_of_sample") or {}
    hold = int(card.get("hold_days") or config.get("hold_days") or 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{escape(str(title))} · 选股→交易报告卡</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
margin:24px;color:#1f2937;background:#f8fafc}}
h1{{margin:0 0 8px}} h2{{margin-top:28px;border-bottom:1px solid #e5e7eb;
padding-bottom:6px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
padding:16px 18px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border-bottom:1px solid #f1f5f9;padding:8px 10px;text-align:left}}
th{{background:#f8fafc;font-weight:600}}
.up{{color:#059669}} .down{{color:#dc2626}} .muted{{color:#64748b}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
gap:12px}}
.metric{{background:#f8fafc;border-radius:10px;padding:12px}}
.metric b{{display:block;font-size:20px;margin-top:4px}}
.warn{{background:#fff7ed;border:1px solid #fdba74;border-radius:10px;padding:12px}}
.ok{{background:#ecfdf5;border:1px solid #6ee7b7;border-radius:10px;padding:12px}}
small{{color:#64748b}}
</style>
</head>
<body>
<h1>{escape(str(title))} · 选股→交易报告卡</h1>
<p class="muted">信号日形态确认 → 次日入场 → 持有 {hold} 日；
费用/滑点走统一执行模型。生成时间 {escape(now)}</p>

<div class="card grid">
  <div class="metric">成交笔数<b>{int(event.get('trades') or 0):,}</b>
  <small>信号日 {int(event.get('signal_dates') or 0):,} · 受阻 {int(event.get('blocked') or 0):,}</small></div>
  <div class="metric">平均净收益
  <b class="{_tone(event.get('avg_net_return_pct'))}">{_pct(event.get('avg_net_return_pct'), sign=True)}</b></div>
  <div class="metric">胜率<b>{_pct(event.get('win_rate_pct'))}</b></div>
  <div class="metric">最大回撤<b class="down">{_pct(port.get('max_drawdown_pct'))}</b></div>
  <div class="metric">换手（累计）<b>{_num(card.get('turnover'), 2)}</b>
  <small>成交额/平均权益</small></div>
  <div class="metric">组合总收益
  <b class="{_tone(port.get('total_return_pct'))}">{_pct(port.get('total_return_pct'), sign=True)}</b></div>
</div>

<h2>样本内 / 样本外</h2>
<div class="card">
{split_html}
<table>
<tr><th>区间</th><th>笔数</th><th>均净收益</th><th>胜率</th></tr>
<tr><td>样本内</td><td>{int(is_stats.get('trades') or 0):,}</td>
<td class="{_tone(is_stats.get('avg_net_return_pct'))}">{_pct(is_stats.get('avg_net_return_pct'), sign=True)}</td>
<td>{_pct(is_stats.get('win_rate_pct'))}</td></tr>
<tr><td>样本外</td><td>{int(oos_stats.get('trades') or 0):,}</td>
<td class="{_tone(oos_stats.get('avg_net_return_pct'))}">{_pct(oos_stats.get('avg_net_return_pct'), sign=True)}</td>
<td>{_pct(oos_stats.get('win_rate_pct'))}</td></tr>
</table>
</div>

<h2>相对基线</h2>
<div class="card">
<table>
<tr><th>基线</th><th>基线均收益</th><th>策略均收益</th><th>超额</th></tr>
{rows_baseline()}
</table>
</div>

<h2>衰减 / 制度切片</h2>
<div class="card">{weak_html}</div>
<div class="card">
<h3>按年</h3>
<table><tr><th>年份</th><th>笔数</th><th>均净收益</th><th>胜率</th></tr>
{rows_year()}</table>
<h3>牛熊代理（指数 MA20 vs MA60）</h3>
<table><tr><th>制度</th><th>笔数</th><th>均净收益</th><th>胜率</th></tr>
{rows_regime()}</table>
</div>

<h2>失败样本（最差净收益）</h2>
<div class="card">
<table>
<tr><th>代码</th><th>信号日</th><th>入场日</th><th>退出日</th><th>净收益</th></tr>
{rows_fail()}
</table>
</div>

<h2>诊断</h2>
<div class="card muted">
面板行 {int(diagnostics.get('panel_rows') or 0):,} ·
研究期 {int(diagnostics.get('study_rows') or 0):,} ·
信号 {int(diagnostics.get('signal_rows') or 0):,} ·
成交尝试 {int(diagnostics.get('trade_rows') or 0):,} ·
成交成功 {int(diagnostics.get('filled_trades') or 0):,}
</div>
</body></html>
"""


def write_report_files(result: Mapping[str, Any], html_path: str, json_path: str) -> None:
    html = build_screen_to_trade_report(result)
    payload = report_card_to_jsonable(result)
    Path(html_path).parent.mkdir(parents=True, exist_ok=True)
    Path(html_path).write_text(html, encoding="utf-8")
    Path(json_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
