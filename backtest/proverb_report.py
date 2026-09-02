"""七条市场口诀事件回测的自包含 HTML 报告。"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Dict, List, Mapping

import numpy as np


STATUS_CLASS = {
    "支持": "supported",
    "部分支持": "partial",
    "不支持": "rejected",
    "证据不足": "unclear",
    "样本不足": "unclear",
}


def _number(value, digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number:+.{digits}f}" if sign else f"{number:.{digits}f}"


def _pct(value, digits: int = 2, sign: bool = False) -> str:
    text = _number(value, digits=digits, sign=sign)
    return text if text == "—" else f"{text}%"


def _status_badge(status: str) -> str:
    css = STATUS_CLASS.get(status, "unclear")
    return f'<span class="status {css}">{escape(status)}</span>'


def build_proverb_report(result: Mapping[str, object]) -> str:
    specs: List[Dict[str, object]] = list(result["specs"])
    rows: List[Dict[str, object]] = list(result["summary"])
    diagnostics: Mapping[str, object] = result["diagnostics"]
    config: Mapping[str, object] = result["config"]
    recent_events: Mapping[str, List[Dict[str, object]]] = result["recent_events"]
    by_id = {spec["id"]: spec for spec in specs}
    primary = {row["id"]: row for row in rows if row["primary"]}

    counts = {name: 0 for name in ("支持", "部分支持", "不支持", "证据不足", "样本不足")}
    for row in primary.values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    overview_rows = ""
    for spec in specs:
        row = primary[spec["id"]]
        overview_rows += f"""
        <tr>
          <td><b>{escape(spec['id'].upper())}</b><span class="rule-name">{escape(spec['title'])}</span></td>
          <td>{int(row['horizon'])}日</td>
          <td>{int(row['samples']):,}<small>{int(row['signal_dates']):,}个信号日</small></td>
          <td class="metric {_tone(row['avg_return_pct'])}">{_pct(row['avg_return_pct'], sign=True)}</td>
          <td class="metric {_tone(row['avg_excess_pct'])}">{_pct(row['avg_excess_pct'], sign=True)}</td>
          <td>[{_pct(row['excess_ci_low_pct'], sign=True)}, {_pct(row['excess_ci_high_pct'], sign=True)}]</td>
          <td>{_pct(row['target_hit_rate_pct'])}<small>基线 {_pct(row['baseline_hit_rate_pct'])}</small></td>
          <td class="metric {_tone(row['target_lift_pct_point'])}">{_number(row['target_lift_pct_point'], sign=True)} 个百分点</td>
          <td>{_status_badge(row['status'])}</td>
        </tr>"""

    tabs = "".join(
        f'<button class="tab {"active" if i == 0 else ""}" '
        f'onclick="switchRule(this,\'{spec["id"]}\')">{spec["id"].upper()} · {escape(spec["title"])}</button>'
        for i, spec in enumerate(specs)
    )

    panels = ""
    for index, spec in enumerate(specs):
        rule_rows = [row for row in rows if row["id"] == spec["id"]]
        metric_rows = ""
        for row in rule_rows:
            metric_rows += f"""
            <tr class="{'primary-row' if row['primary'] else ''}">
              <td>{int(row['horizon'])}日{' <span class="primary-tag">主观察</span>' if row['primary'] else ''}</td>
              <td>{int(row['samples']):,}</td>
              <td>{_pct(row['avg_return_pct'], sign=True)}</td>
              <td>{_pct(row['median_return_pct'], sign=True)}</td>
              <td>{_pct(row['win_rate_pct'])}</td>
              <td>{_pct(row['avg_benchmark_pct'], sign=True)}</td>
              <td>{_pct(row['avg_excess_pct'], sign=True)}</td>
              <td>[{_pct(row['excess_ci_low_pct'], sign=True)}, {_pct(row['excess_ci_high_pct'], sign=True)}]</td>
              <td>{_pct(row['target_hit_rate_pct'])}</td>
              <td>{_pct(row['baseline_hit_rate_pct'])}</td>
              <td>{_number(row['target_lift_pct_point'], sign=True)}pp</td>
              <td>{_status_badge(row['status'])}</td>
            </tr>"""

        event_rows = ""
        for event in recent_events.get(spec["id"], [])[:20]:
            event_rows += f"""
            <tr>
              <td>{escape(str(event.get('代码', '')))}</td>
              <td>{escape(str(event.get('信号日', '')))}</td>
              <td>{escape(str(event.get('入场日', '')))}</td>
              <td>{escape(str(event.get('退出日', '')))}</td>
              <td>{_pct(event.get('信号日个股涨跌%'), sign=True)}</td>
              <td>{_pct(event.get('信号日大盘涨跌%'), sign=True)}</td>
              <td class="{_tone(event.get('未来收益%'))}">{_pct(event.get('未来收益%'), sign=True)}</td>
              <td>{_pct(event.get('基准收益%'), sign=True)}</td>
              <td class="{_tone(event.get('超额收益%'))}">{_pct(event.get('超额收益%'), sign=True)}</td>
            </tr>"""
        if not event_rows:
            event_rows = '<tr><td colspan="9" class="empty">当前口径没有完整未来样本</td></tr>'

        main = primary[spec["id"]]
        panels += f"""
        <section class="panel {'active' if index == 0 else ''}" id="panel-{spec['id']}">
          <div class="rule-head">
            <div><span class="eyebrow">{escape(spec['id'].upper())} · 第 {index + 1} 个回测项</span>
              <h2>{escape(spec['title'])}</h2></div>
            {_status_badge(main['status'])}
          </div>
          <div class="definition-grid">
            <div><span>信号定义</span><b>{escape(spec['formula'])}</b></div>
            <div><span>待验证结果</span><b>{escape(spec['expected'])}（{escape(spec['target_label'])}）</b></div>
            <div><span>主观察窗口</span><b>下一交易日开盘 → 第 {int(spec['primary_horizon'])} 日收盘</b></div>
            <div><span>对照方式</span><b>{escape(_baseline_label(spec['baseline_key']))}</b></div>
          </div>
          <p class="interpretation"><b>怎么判：</b>“支持”要求未来绝对收益方向正确、相对指数超额收益的按信号日聚类 95% 区间方向正确，且目标命中率高于同条件基线；只满足部分条件时标为“部分支持”。</p>
          <div class="table-wrap"><table class="detail-table"><thead><tr>
            <th>观察期</th><th>样本</th><th>平均收益</th><th>中位收益</th><th>上涨率</th>
            <th>基准收益</th><th>超额收益</th><th>超额95%区间</th><th>目标命中</th><th>基线命中</th><th>提升</th><th>结论</th>
          </tr></thead><tbody>{metric_rows}</tbody></table></div>
          <details><summary>查看最近 20 个可核对事件</summary>
            <div class="table-wrap"><table><thead><tr><th>代码</th><th>信号日</th><th>入场日</th><th>退出日</th><th>个股当日</th><th>大盘当日</th><th>未来收益</th><th>基准</th><th>超额</th></tr></thead><tbody>{event_rows}</tbody></table></div>
          </details>
        </section>"""

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>市场口诀回测（前七条）</title>
<style>
:root{{--navy:#14213d;--blue:#2563eb;--green:#12845b;--red:#c23b3b;--amber:#b56b09;--ink:#172033;--muted:#6d788c;--line:#e3e8f0;--paper:#fff;--bg:#f3f5f8}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:14px}}
.hero{{background:linear-gradient(135deg,#101a30,#244269);color:white;padding:30px 34px}}.hero-inner{{max-width:1500px;margin:auto}}.hero h1{{font-size:28px;margin:7px 0}}.hero p{{color:#c5d0e2;max-width:950px;line-height:1.7;margin:0}}.eyebrow{{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#7e8ba1;font-weight:700}}.hero .eyebrow{{color:#9db6db}}
.summary{{max-width:1500px;margin:-16px auto 18px;padding:0 24px;display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.summary-card{{background:white;border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:0 5px 18px rgba(15,28,50,.06)}}.summary-card span{{display:block;color:var(--muted);font-size:11px}}.summary-card b{{font-size:23px;display:block;margin-top:4px}}
.content{{max-width:1500px;margin:auto;padding:0 24px 40px}}.card{{background:white;border:1px solid var(--line);border-radius:13px;padding:18px;margin-bottom:18px}}.card h2{{font-size:18px;margin:0 0 5px}}.note{{color:var(--muted);line-height:1.65;margin:4px 0 14px}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}}table{{width:100%;border-collapse:collapse;min-width:940px;background:white}}th,td{{padding:10px 9px;border-bottom:1px solid #edf0f4;text-align:right;white-space:nowrap;font-size:12px}}th{{background:#f7f9fc;color:#566176;font-weight:700;position:sticky;top:0}}th:first-child,td:first-child{{text-align:left}}td small,.rule-name{{display:block;color:var(--muted);font-size:10px;margin-top:3px}}.metric{{font-weight:750}}.positive{{color:var(--red)}}.negative{{color:var(--green)}}
.status{{display:inline-flex;padding:4px 9px;border-radius:999px;font-size:11px;font-weight:750;white-space:nowrap}}.supported{{background:#def5ea;color:#08754d}}.partial{{background:#fff0d8;color:#9a5b00}}.rejected{{background:#ffe3e3;color:#a72f2f}}.unclear{{background:#e9edf3;color:#5f6b7e}}
.tabs{{position:sticky;top:0;z-index:10;display:flex;overflow:auto;gap:6px;background:rgba(243,245,248,.95);backdrop-filter:blur(10px);padding:10px 0}}.tab{{border:1px solid var(--line);background:white;color:#526079;border-radius:999px;padding:8px 12px;white-space:nowrap;cursor:pointer;font-size:12px}}.tab.active{{background:var(--blue);border-color:var(--blue);color:white}}
.panel{{display:none;background:white;border:1px solid var(--line);border-radius:13px;padding:20px}}.panel.active{{display:block}}.rule-head{{display:flex;align-items:center;justify-content:space-between;gap:15px;border-bottom:1px solid var(--line);padding-bottom:14px}}.rule-head h2{{font-size:23px;margin:5px 0 0}}
.definition-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}.definition-grid div{{background:#f6f8fb;border-radius:9px;padding:12px}}.definition-grid span{{display:block;color:var(--muted);font-size:10px;margin-bottom:5px}}.definition-grid b{{font-size:13px;line-height:1.55}}.interpretation{{background:#eef5ff;color:#42536f;padding:11px 13px;border-radius:8px;line-height:1.65}}.primary-row{{background:#f4f8ff}}.primary-tag{{color:var(--blue);font-size:9px;background:#dfeaff;border-radius:4px;padding:2px 4px}}details{{margin-top:14px}}summary{{cursor:pointer;color:var(--blue);font-weight:650;margin-bottom:10px}}.empty{{text-align:center!important;color:var(--muted)}}
.method-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.method-grid div{{border-left:3px solid #c9d5e8;background:#f7f9fc;padding:10px 12px;line-height:1.6}}.method-grid b{{display:block}}.footer{{color:var(--muted);font-size:11px;text-align:center;padding:25px}}
@media(max-width:900px){{.summary{{grid-template-columns:repeat(2,1fr)}}.definition-grid,.method-grid{{grid-template-columns:1fr 1fr}}.content{{padding:0 10px 30px}}.hero{{padding:24px 18px}}}}
@media(max-width:560px){{.definition-grid,.method-grid{{grid-template-columns:1fr}}.summary{{padding:0 10px}}.summary-card b{{font-size:19px}}}}
</style></head><body>
<header class="hero"><div class="hero-inner"><span class="eyebrow">EVENT STUDY · 7 ITEMS</span><h1>市场“21个口诀”回测 · 前七条</h1><p>把口语经验转换成固定公式，用下一交易日开盘后的 1/3/5 日表现验证。结论是统计关联，不是因果关系或交易建议；阈值变化、交易费用、涨跌停排队和股票池幸存者偏差都可能改变结果。</p></div></header>
<div class="summary">
  <div class="summary-card"><span>研究区间</span><b>{diagnostics['start_date']}<small> 至 {diagnostics['end_date']}</small></b></div>
  <div class="summary-card"><span>股票池</span><b>{int(diagnostics['stock_codes']):,}<small>只沪深主板</small></b></div>
  <div class="summary-card"><span>支持</span><b>{counts['支持']}<small> / 7 条</small></b></div>
  <div class="summary-card"><span>部分支持</span><b>{counts['部分支持']}<small> / 7 条</small></b></div>
  <div class="summary-card"><span>不支持 / 其余</span><b>{counts['不支持']}<small> / {counts['证据不足'] + counts['样本不足']}</small></b></div>
</div>
<main class="content">
  <section class="card"><h2>主观察窗口结论</h2><p class="note">“目标提升”是该口诀命中率减去同市场条件、同流动性股票池的基线命中率。95% 区间按信号日聚类，避免同一天大量股票同时触发造成虚假精度。</p>
    <div class="table-wrap"><table><thead><tr><th>回测项</th><th>窗口</th><th>样本</th><th>未来收益</th><th>超额收益</th><th>超额95%区间</th><th>目标命中</th><th>目标提升</th><th>结论</th></tr></thead><tbody>{overview_rows}</tbody></table></div>
  </section>
  <section class="card"><h2>统一研究口径</h2><div class="method-grid">
    <div><b>无前视成交</b>信号在 t 日收盘确认，t+1 日开盘进入，第 h 个交易日收盘观察。</div>
    <div><b>指数对照</b>沪市股票对上证指数，深市股票对深证成指，计算同区间超额收益。</div>
    <div><b>可交易性过滤</b>信号日 20 日平均成交额至少 ¥{float(config['min_avg_amount']) / 1e8:.2f} 亿。</div>
    <div><b>事件去重</b>同一只股票的条件连续成立时只记录首次，避免把一段行情重复计样本。</div>
    <div><b>基线口径</b>大盘涨跌类使用相同的大盘状态作基线，其余使用全体流动性合格股票日。</div>
    <div><b>数据质量</b>行情缺失率 {_pct(float(diagnostics['key_price_missing_rate']) * 100, 4)}，指数匹配率 {_pct(float(diagnostics['market_match_rate']) * 100, 2)}。</div>
  </div></section>
  <div class="tabs">{tabs}</div>{panels}
</main><footer class="footer">生成于 {generated} · 此报告验证固定口径下的历史关联，不保证未来有效。</footer>
<script>function switchRule(button,id){{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));button.classList.add('active');document.getElementById('panel-'+id).classList.add('active');}}</script>
</body></html>"""


def _tone(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(number) or number == 0:
        return ""
    return "positive" if number > 0 else "negative"


def _baseline_label(key: str) -> str:
    return {
        "all": "全部流动性合格股票日",
        "market_down": "同样处于大盘下跌≥1%的股票日",
        "market_up": "同样处于大盘上涨≥1%的股票日",
    }.get(key, key)
