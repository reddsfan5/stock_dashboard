"""相对低位缓涨 3/4/5 日买入策略 HTML 报告。"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from typing import Mapping

import numpy as np
import pandas as pd


def _num(value, digits=2, sign=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(value):
        return "—"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def _pct(value, digits=2, sign=False):
    value = _num(value, digits, sign)
    return value if value == "—" else value + "%"


def _tone(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    return "up" if value > 0 else "down" if value < 0 else ""


def _status(value):
    css = {
        "支持可操作": "good", "部分支持": "partial", "不支持": "bad",
        "证据不足": "unclear", "样本不足": "unclear",
    }.get(value, "unclear")
    return f'<span class="status {css}">{escape(str(value))}</span>'


def build_slow_rise_report(result: Mapping[str, object]) -> str:
    config = result["config"]
    diagnostics = result["diagnostics"]
    summary = list(result["summary"])
    yearly = pd.DataFrame(result["yearly"])
    trades = result["trades"]
    sensitivity = list(result.get("sensitivity", []))
    unfiltered = list(result.get("unfiltered", []))
    low_filter_enabled = config.get("max_relative_position_pct") is not None
    strategy_label = "低位缓涨" if low_filter_enabled else "缓涨"
    low_position_text = (
        f"不超过 {config['max_relative_position_pct']:g}%"
        if low_filter_enabled else "不限"
    )
    research_question = (
        f"当前价先处于近 {config['relative_low_window']} 日相对低位，缓涨观察 3～5 日"
        if low_filter_enabled else "缓涨观察 3～5 日"
    )
    baseline_scope = "相同低位与流动性门槛" if low_filter_enabled else "相同流动性门槛"
    best = max(summary, key=lambda row: row["avg_net_return_pct"])
    all_supported = [row for row in summary if row["status"] in {"支持可操作", "部分支持"}]
    headline = (
        f"N={best['n_days']} 表现最好，但仍未证明{strategy_label}后追入可操作"
        if not all_supported else
        f"N={best['n_days']} 在固定口径下表现出可操作优势"
    )

    overview = ""
    panels = ""
    tabs = ""
    for index, row in enumerate(summary):
        n_days = int(row["n_days"])
        overview += f"""
        <tr><td><b>N={n_days}</b><small>观察{n_days}日</small></td>
          <td>{int(row['trades']):,}<small>{int(row['signal_dates']):,}个信号日</small></td>
          <td class="{_tone(row['avg_net_return_pct'])}">{_pct(row['avg_net_return_pct'], sign=True)}</td>
          <td>[{_pct(row['net_ci_low_pct'], sign=True)}, {_pct(row['net_ci_high_pct'], sign=True)}]</td>
          <td>{_pct(row['win_rate_pct'])}</td><td>{_pct(row['target_exit_rate_pct'])}</td>
          <td>{_pct(row['baseline_target_rate_pct'])}</td>
          <td class="{_tone(row['target_rate_lift_pp'])}">{_num(row['target_rate_lift_pp'], sign=True)}pp</td>
          <td class="{_tone(row['avg_excess_pct'])}">{_pct(row['avg_excess_pct'], sign=True)}</td>
          <td>{_num(row['profit_factor'])}</td><td>{_status(row['status'])}</td></tr>"""
        tabs += (
            f'<button class="tab {"active" if index == 0 else ""}" '
            f'onclick="showPanel(this,\'n{n_days}\')">观察 {n_days} 日</button>'
        )

        selected = trades[trades["观察天数N"] == n_days].copy()
        selected = selected.sort_values("信号日", ascending=False).head(30)
        trade_rows = ""
        for trade in selected.to_dict("records"):
            trade_rows += f"""
            <tr><td>{escape(str(trade['代码']))}</td><td>{_date(trade['信号日'])}</td>
              <td>{_date(trade['买入日'])}</td><td>{_num(trade['买入成交价'], 3)}</td>
              <td>{_pct(trade['区间相对位置%'])}</td><td>{_date(trade['卖出日期'])}</td><td>{_num(trade['卖出成交价'], 3)}</td>
              <td>{escape(str(trade['退出原因']))}</td><td>{int(trade['持有天数'])}</td>
              <td class="{_tone(trade['策略净收益%'])}">{_pct(trade['策略净收益%'], sign=True)}</td>
              <td class="{_tone(trade['超额收益%'])}">{_pct(trade['超额收益%'], sign=True)}</td>
              <td>{_pct(trade['最大浮亏%'], sign=True)}</td></tr>"""
        panels += f"""
        <section class="panel {'active' if index == 0 else ''}" id="panel-n{n_days}">
          <div class="panel-head"><div><span>观察参数</span><h2>连续 {n_days} 日缓涨</h2></div>{_status(row['status'])}</div>
          <div class="metrics">
            {_metric('平均净收益', _pct(row['avg_net_return_pct'], sign=True), _tone(row['avg_net_return_pct']))}
            {_metric('中位净收益', _pct(row['median_net_return_pct'], sign=True), _tone(row['median_net_return_pct']))}
            {_metric('止盈率', _pct(row['target_exit_rate_pct']), '')}
            {_metric('平均持有', _num(row['avg_holding_days']) + '日', '')}
            {_metric('平均最大浮亏', _pct(row['avg_max_adverse_pct'], sign=True), 'down')}
            {_metric('正收益年份', _pct(row['positive_year_rate_pct']), '')}
          </div>
          <div class="explain"><b>相对同日市场：</b>策略平均超额 {_pct(row['avg_excess_pct'], sign=True)}，
          95%区间 [{_pct(row['excess_ci_low_pct'], sign=True)}, {_pct(row['excess_ci_high_pct'], sign=True)}]；
          止盈率比同日流动性股票基线 {_num(row['target_rate_lift_pp'], sign=True)} 个百分点。
          <b>止盈规则影响：</b>相对一直持有到第5日，平均改变 {_pct(row['take_profit_effect_pct'], sign=True)}。</div>
          <details><summary>查看最近 30 笔交易</summary><div class="table-wrap"><table><thead><tr>
            <th>代码</th><th>信号日</th><th>买入日</th><th>买入价</th><th>区间位置</th><th>卖出日</th><th>卖出价</th><th>原因</th><th>持有</th><th>净收益</th><th>超额</th><th>最大浮亏</th>
          </tr></thead><tbody>{trade_rows}</tbody></table></div></details>
        </section>"""

    labels = [f"N={int(row['n_days'])}" for row in summary]
    chart_data = {
        "labels": labels,
        "net": [round(row["avg_net_return_pct"], 4) for row in summary],
        "fixed": [round(row["avg_fixed5_return_pct"], 4) for row in summary],
        "baseline": [round(row["avg_baseline_return_pct"], 4) for row in summary],
        "target": [round(row["target_exit_rate_pct"], 4) for row in summary],
        "baselineTarget": [round(row["baseline_target_rate_pct"], 4) for row in summary],
    }
    years = sorted(int(year) for year in yearly["年份"].dropna().unique()) if len(yearly) else []
    annual_sets = []
    for row in summary:
        n_days = int(row["n_days"])
        lookup = yearly[yearly["n_days"] == n_days].set_index("年份")["平均净收益"].to_dict()
        annual_sets.append({
            "label": f"N={n_days}",
            "data": [round(float(lookup.get(year, np.nan)), 4) if year in lookup else None for year in years],
        })

    execution = config["execution"]
    filter_compare_html = ""
    if low_filter_enabled and unfiltered:
        before = {int(row["n_days"]): row for row in unfiltered}
        compare_rows = ""
        for row in summary:
            n_days = int(row["n_days"])
            original = before[n_days]
            compare_rows += (
                f"<tr><td><b>N={n_days}</b></td><td>{int(original['trades']):,}</td>"
                f"<td>{int(row['trades']):,}</td>"
                f"<td>{_pct(original['avg_net_return_pct'], sign=True)}</td>"
                f"<td class='{_tone(row['avg_net_return_pct'])}'>{_pct(row['avg_net_return_pct'], sign=True)}</td>"
                f"<td class='{_tone(row['avg_net_return_pct']-original['avg_net_return_pct'])}'>{_pct(row['avg_net_return_pct']-original['avg_net_return_pct'], sign=True)}</td>"
                f"<td>{_pct(original['target_exit_rate_pct'])}</td><td>{_pct(row['target_exit_rate_pct'])}</td>"
                f"<td class='{_tone(row['target_exit_rate_pct']-original['target_exit_rate_pct'])}'>{_num(row['target_exit_rate_pct']-original['target_exit_rate_pct'], sign=True)}pp</td></tr>"
            )
        filter_compare_html = f"""
        <section class="card"><h2>30日相对低位过滤有没有改善？</h2>
        <p class="sub">“低位”定义为当前收盘价位于近 {config['relative_low_window']} 日最高与最低价格区间的下方 {config['max_relative_position_pct']:g}%；下表只改变这一项，其余信号、成交和费用完全一致。</p>
        <div class="table-wrap"><table><thead><tr><th>参数</th><th>过滤前交易</th><th>过滤后交易</th><th>过滤前净收益</th><th>过滤后净收益</th><th>收益改善</th><th>过滤前止盈</th><th>过滤后止盈</th><th>止盈改善</th></tr></thead><tbody>{compare_rows}</tbody></table></div></section>"""
    sensitivity_html = ""
    if sensitivity:
        strict_rows = "".join(
            f"<tr><td><b>N={int(row['n_days'])}</b></td><td>{int(row['trades']):,}</td>"
            f"<td class='{_tone(row['avg_net_return_pct'])}'>{_pct(row['avg_net_return_pct'], sign=True)}</td>"
            f"<td>{_pct(row['win_rate_pct'])}</td><td>{_pct(row['target_exit_rate_pct'])}</td>"
            f"<td>{_pct(row['baseline_target_rate_pct'])}</td>"
            f"<td class='{_tone(row['avg_excess_pct'])}'>{_pct(row['avg_excess_pct'], sign=True)}</td>"
            f"<td>{_status(row['status'])}</td></tr>"
            for row in sensitivity
        )
        sensitivity_html = f"""
        <section class="card"><h2>严格敏感性复核：观察期每天都小涨</h2>
        <p class="sub">保留30日低位过滤，将“允许 -1% 慢跌、上涨日占六成”收紧为每天涨幅都在 0%～1.5%，其余买卖规则完全不变。若口诀可靠，严格定义不应把结论推翻。</p>
        <div class="table-wrap"><table><thead><tr><th>参数</th><th>交易</th><th>平均净收益</th><th>胜率</th><th>止盈率</th><th>基线止盈</th><th>平均超额</th><th>结论</th></tr></thead><tbody>{strict_rows}</tbody></table></div></section>"""
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"><title>低位缓涨后5日止盈策略回测</title>
<script src="vendor/chart.umd.min.js"></script>
<style>
:root{{--bg:#f3f5f8;--paper:#fff;--ink:#182033;--muted:#6e788a;--line:#e2e7ef;--blue:#2563eb;--red:#d13d4a;--green:#12835c;--amber:#aa6808}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:14px}}
.hero{{background:#14223a;color:#fff;padding:30px 32px}}.hero>div{{max-width:1480px;margin:auto}}.hero span{{color:#9eb0ca;font-size:11px;letter-spacing:.1em}}.hero h1{{font-size:28px;margin:8px 0}}.hero p{{color:#c2cde0;line-height:1.7;max-width:960px;margin:0}}
.wrap{{max-width:1480px;margin:auto;padding:20px 24px 45px}}.card,.panel{{background:#fff;border:1px solid var(--line);border-radius:13px;padding:18px;margin-bottom:18px}}h2{{margin:0 0 6px;font-size:19px}}.sub{{margin:0 0 15px;color:var(--muted);line-height:1.65}}
.conclusion{{display:grid;grid-template-columns:2fr repeat(3,1fr);gap:10px;margin-bottom:18px}}.conclusion>div{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px}}.conclusion .lead{{border-left:4px solid var(--blue)}}.conclusion span,.metric span{{display:block;color:var(--muted);font-size:10px;margin-bottom:5px}}.conclusion b{{font-size:19px}}.conclusion .lead b{{font-size:17px;line-height:1.5}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:9px}}table{{width:100%;border-collapse:collapse;min-width:1000px}}th,td{{font-size:12px;padding:9px 8px;border-bottom:1px solid #edf0f4;text-align:right;white-space:nowrap}}th{{color:#59657a;background:#f7f9fc}}th:first-child,td:first-child{{text-align:left}}td small{{display:block;color:var(--muted);font-size:9px;margin-top:3px}}
.up{{color:var(--red);font-weight:700}}.down{{color:var(--green);font-weight:700}}.status{{display:inline-flex;padding:4px 9px;border-radius:999px;font-size:11px;font-weight:700}}.good{{background:#dff5ea;color:#08734d}}.partial{{background:#fff0d9;color:#976000}}.bad{{background:#ffe4e5;color:#aa2d38}}.unclear{{background:#e9edf3;color:#5d687a}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.chart{{height:330px;border:1px solid var(--line);border-radius:10px;padding:13px;position:relative}}.chart.wide{{grid-column:1/-1}}.chart canvas{{max-height:295px}}
.method{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.method div{{background:#f6f8fb;border-left:3px solid #c7d4e8;padding:11px;line-height:1.55}}.method b{{display:block;margin-bottom:3px}}
.tabs{{display:flex;gap:6px;overflow:auto;position:sticky;top:0;z-index:10;background:rgba(243,245,248,.95);padding:10px 0;backdrop-filter:blur(8px)}}.tab{{border:1px solid var(--line);background:#fff;color:#566176;border-radius:999px;padding:8px 14px;cursor:pointer;white-space:nowrap}}.tab.active{{background:var(--blue);color:#fff;border-color:var(--blue)}}.panel{{display:none}}.panel.active{{display:block}}.panel-head{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:12px}}.panel-head span{{color:var(--muted);font-size:10px}}.panel-head h2{{font-size:22px;margin-top:4px}}.metrics{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:14px 0}}.metric{{background:#f6f8fb;padding:11px;border-radius:8px}}.metric b{{font-size:17px}}.explain{{background:#eef5ff;color:#42536f;line-height:1.7;padding:11px 13px;border-radius:8px}}details{{margin-top:14px}}summary{{cursor:pointer;color:var(--blue);font-weight:650;margin-bottom:10px}}.caveat{{color:var(--muted);font-size:12px;line-height:1.7}}.footer{{text-align:center;color:var(--muted);font-size:11px;padding:20px}}
@media(max-width:900px){{.conclusion{{grid-template-columns:1fr 1fr}}.charts{{grid-template-columns:1fr}}.chart.wide{{grid-column:auto}}.method{{grid-template-columns:1fr 1fr}}.metrics{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:560px){{.wrap{{padding:12px 9px 35px}}.hero{{padding:24px 17px}}.conclusion,.method{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr 1fr}}}}
</style></head><body>
<header class="hero"><div><span>EXECUTABLE EVENT BACKTEST · T+1</span><h1>{escape(headline)}</h1><p>验证“{strategy_label}后会有大涨”能否转成可执行交易：{research_question}，信号次日开盘买入，严格 T+1 后触及 +{config['target_pct']:g}% 止盈，否则第 {config['hold_days']} 个持有日尾盘退出。所有结果已扣除佣金、印花税和双边滑点。</p></div></header>
<main class="wrap">
  <section class="conclusion"><div class="lead"><span>当前结论</span><b>{escape(headline)}</b></div>
    <div><span>最好参数</span><b>N={int(best['n_days'])}</b></div>
    <div><span>最好平均净收益</span><b class="{_tone(best['avg_net_return_pct'])}">{_pct(best['avg_net_return_pct'], sign=True)}</b></div>
    <div><span>最好止盈率</span><b>{_pct(max(row['target_exit_rate_pct'] for row in summary))}</b></div></section>
  <section class="card"><h2>N=3/4/5 核心结果</h2><p class="sub">止盈率基线采用相同信号日、{baseline_scope}的全部主板股票，避免把同期大盘行情误认成形态优势。</p><div class="table-wrap"><table><thead><tr><th>参数</th><th>交易/信号日</th><th>平均净收益</th><th>净收益95%区间</th><th>胜率</th><th>止盈率</th><th>基线止盈率</th><th>止盈提升</th><th>平均超额</th><th>盈亏比</th><th>结论</th></tr></thead><tbody>{overview}</tbody></table></div></section>
  {filter_compare_html}
  {sensitivity_html}
  <section class="card"><h2>收益、止盈和年度稳定性</h2><div class="charts"><div class="chart"><canvas id="returnChart"></canvas></div><div class="chart"><canvas id="targetChart"></canvas></div><div class="chart wide"><canvas id="annualChart"></canvas></div></div></section>
  <section class="card"><h2>信号和执行定义</h2><div class="method">
    <div><b>缓涨信号</b>最近 N 日每天涨跌在 {config['daily_min_pct']:g}%～+{config['daily_max_pct']:g}%，上涨天数至少 {config['min_positive_ratio']*100:g}%，累计涨幅 {config['cumulative_min_pct']:g}%～{config['cumulative_max_pct']:g}%。</div>
    <div><b>相对低位</b>当前收盘在近 {config['relative_low_window']} 日最高—最低区间中的位置{low_position_text}。启用时，基线也使用相同低位条件。</div>
    <div><b>买入</b>信号在收盘确认，下一交易日开盘买入；一字涨停近似视为无法成交。</div>
    <div><b>卖出</b>买入当天不可卖；从第2日起先看开盘跳空，再看日内最高价是否触及止盈，否则第5日收盘卖出。</div>
    <div><b>成本</b>佣金{execution['commission_rate']*10000:g}bp/边、卖出税{execution['stamp_tax_rate']*10000:g}bp、滑点{execution['slippage_bps']:g}bp/边。</div>
  </div></section>
  <div class="tabs">{tabs}</div>{panels}
  <section class="card"><h2>结论边界</h2><p class="caveat">本报告是独立交易事件回测，不是有限资金组合净值；同一股票持仓期间不重复开仓，但不同股票可同时触发。日K只能确认某日最高价触及止盈，无法还原盘中路径和排队先后。股票池缺少完整历史退市与历史ST名称，存在幸存者偏差。“近30日低位”只是价格区间位置，不代表估值低或长期底部。若固定阈值结果没有超过同日低位股票基线，则不能据此推断“有人操纵”或未来必然大涨。</p></section>
</main><footer class="footer">数据 {diagnostics['start_date']} 至 {diagnostics['end_date']} · {int(diagnostics['stock_codes']):,}只主板股票 · 生成于 {generated}</footer>
<script>
function showPanel(button,id){{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));button.classList.add('active');document.getElementById('panel-'+id).classList.add('active')}}
const C={json.dumps(chart_data, ensure_ascii=False)};const Y={json.dumps(years)};const A={json.dumps(annual_sets, ensure_ascii=False)};
new Chart(document.getElementById('returnChart'),{{type:'bar',data:{{labels:C.labels,datasets:[{{label:'策略净收益%',data:C.net,backgroundColor:'#2563eb'}},{{label:'固定持有5日%',data:C.fixed,backgroundColor:'#7c8ba5'}},{{label:'同日基线%',data:C.baseline,backgroundColor:'#c7cfdb'}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{title:{{display:true,text:'每笔平均收益（已扣成本）'}}}},scales:{{y:{{title:{{display:true,text:'收益率%'}}}}}}}}}});
new Chart(document.getElementById('targetChart'),{{type:'bar',data:{{labels:C.labels,datasets:[{{label:'缓涨信号止盈率%',data:C.target,backgroundColor:'#d13d4a'}},{{label:'同日基线止盈率%',data:C.baselineTarget,backgroundColor:'#c7cfdb'}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{title:{{display:true,text:'+5%止盈命中率'}}}},scales:{{y:{{beginAtZero:true,title:{{display:true,text:'止盈率%'}}}}}}}}}});
const colors=['#2563eb','#d13d4a','#12835c'];new Chart(document.getElementById('annualChart'),{{type:'line',data:{{labels:Y,datasets:A.map((d,i)=>({{...d,borderColor:colors[i],backgroundColor:colors[i],tension:.2}}))}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{title:{{display:true,text:'各年份每笔平均净收益'}}}},scales:{{y:{{title:{{display:true,text:'平均净收益%'}}}}}}}}}});
</script></body></html>"""


def _metric(label, value, css):
    return f'<div class="metric"><span>{escape(label)}</span><b class="{css}">{escape(value)}</b></div>'


def _date(value):
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return "—"
