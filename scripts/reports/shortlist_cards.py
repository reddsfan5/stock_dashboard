"""每日精选：把选股命中、决策快照、板块强度、观察池拼成可读卡片。

数据优先读 output/dashboard.html 内嵌的 KLINE_META / TAB_CODES（与选股页同源），
不重新跑全市场选股。板块强度读 cache/sector_strength_snapshot.json，缺失时可刷新。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")

TAB_LABELS = {
    "continuity": "K线连续性",
    "hammer": "金针探底",
    "long_shadow": "长下影",
    "sideways": "横盘震荡",
    "trend-up": "连续上涨",
    "trend-down": "连续下跌",
    "upward_gap": "向上跳空",
}

# 偏「可观察/可做多思路」的命中权重；下跌趋势默认降权（仍可出现在风险说明里）
TAB_WEIGHT = {
    "hammer": 3.0,
    "continuity": 2.4,
    "sideways": 2.2,
    "upward_gap": 2.0,
    "trend-up": 2.0,
    "long_shadow": 1.6,
    "trend-down": 0.4,
}

CONSTRUCTIVE_TABS = {
    "hammer", "continuity", "sideways", "upward_gap", "trend-up", "long_shadow"
}


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _extract_js_object_after(html: str, marker: str) -> str:
    """按括号深度截取 marker 后第一个 JSON/JS 对象字面量。"""
    start = html.find(marker)
    if start < 0:
        raise ValueError(f"dashboard.html 缺少 {marker.strip('=')}，请先运行 python -m scripts.screen")
    i = html.find("{", start)
    if i < 0:
        raise ValueError(f"无法解析 {marker}")
    depth = 0
    in_str = False
    escape = False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[i : j + 1]
    raise ValueError(f"未闭合对象: {marker}")


def extract_dashboard_payload(html: str) -> Tuple[Dict[str, dict], Dict[str, List[str]]]:
    """从选股仪表盘 HTML 抽出 KLINE_META 与 TAB_CODES。"""
    meta = json.loads(_extract_js_object_after(html, "var KLINE_META="))
    tabs = json.loads(_extract_js_object_after(html, "var TAB_CODES="))
    if not isinstance(meta, dict) or not isinstance(tabs, dict):
        raise ValueError("仪表盘嵌入数据格式异常")
    return meta, {str(k): list(v or []) for k, v in tabs.items()}


def load_dashboard(path: Path) -> Tuple[Dict[str, dict], Dict[str, List[str]]]:
    if not path.exists():
        raise FileNotFoundError(f"找不到选股页: {path}")
    return extract_dashboard_payload(path.read_text(encoding="utf-8"))


def invert_tab_hits(tab_codes: Dict[str, List[str]]) -> Dict[str, List[str]]:
    hits: Dict[str, List[str]] = defaultdict(list)
    for tab, codes in tab_codes.items():
        for code in codes:
            if code and tab not in hits[code]:
                hits[code].append(tab)
    return dict(hits)


def sector_rank_maps(sector_payload: Optional[dict]) -> Tuple[Dict[str, float], Dict[str, int], set]:
    """返回 板块→涨跌幅、板块→名次(1起)、偏弱板块集合。"""
    strength: Dict[str, float] = {}
    ranks: Dict[str, int] = {}
    weak: set = set()
    if not sector_payload:
        return strength, ranks, weak
    sectors = list(sector_payload.get("sectors") or [])
    for i, row in enumerate(sectors):
        name = row.get("sector") or row.get("name")
        if not name:
            continue
        chg = _num(row.get("avg_change_pct", row.get("change_pct")))
        if chg is not None:
            strength[str(name)] = chg
        ranks[str(name)] = i + 1
    for row in sector_payload.get("weak_sectors") or []:
        name = row.get("sector") or row.get("name")
        if name:
            weak.add(str(name))
    # 若无 weak 列表，把涨跌幅后 30% 当偏弱提示
    if not weak and strength:
        ordered = sorted(strength.items(), key=lambda kv: kv[1])
        cut = max(1, len(ordered) // 3)
        weak = {name for name, _ in ordered[:cut]}
    return strength, ranks, weak


def score_candidate(
    *,
    tabs: List[str],
    metrics: dict,
    sector: str,
    sector_chg: Optional[float],
    sector_rank: Optional[int],
    in_watchlist: bool,
) -> float:
    score = 0.0
    constructive = [t for t in tabs if t in CONSTRUCTIVE_TABS]
    if not constructive and "trend-down" in tabs:
        # 纯下跌命中默认不进每日精选（仍可在全量里看到）；评分给极低
        score -= 5.0
    for t in tabs:
        score += TAB_WEIGHT.get(t, 1.0)
    # 多策略共振加分
    if len(constructive) >= 2:
        score += 1.5 * (len(constructive) - 1)

    vol = _num(metrics.get("成交量比20"))
    amt = _num(metrics.get("成交额比20"))
    rs = _num(metrics.get("市场相对强弱20%"))
    mom = _num(metrics.get("20日动量%"))
    atr = _num(metrics.get("ATR14%"))
    dd = _num(metrics.get("距60日高点%"))
    turn = _num(metrics.get("换手率%"))

    if vol is not None:
        score += max(-1.0, min(2.0, (vol - 1.0)))
    if amt is not None:
        score += max(-1.0, min(2.0, (amt - 1.0) * 0.8))
    if rs is not None:
        score += max(-1.5, min(2.5, rs / 5.0))
    if mom is not None and mom > 0:
        score += min(1.0, mom / 10.0)
    if atr is not None and atr > 8:
        score -= min(1.5, (atr - 8) / 8.0)
    if dd is not None and dd < -25:
        score -= 0.6
    if turn is not None and turn < 0.3:
        score -= 0.8  # 过冷流动性

    if sector_chg is not None:
        score += max(-1.2, min(1.5, sector_chg / 3.0))
    if sector_rank is not None and sector_rank <= 5:
        score += 0.8
    if in_watchlist:
        score += 0.7
    if sector == "未分类":
        score -= 0.3
    return round(score, 4)


def build_why_risk(
    *,
    tabs: List[str],
    metrics: dict,
    sector: str,
    sector_chg: Optional[float],
    sector_rank: Optional[int],
    weak_sector: bool,
    watch: Optional[dict],
) -> Tuple[List[str], List[str]]:
    why: List[str] = []
    risk: List[str] = []
    labels = [TAB_LABELS.get(t, t) for t in tabs if t in CONSTRUCTIVE_TABS or t == "trend-down"]
    constructive = [TAB_LABELS.get(t, t) for t in tabs if t in CONSTRUCTIVE_TABS]
    if constructive:
        why.append("命中：" + "、".join(constructive))
    elif "trend-down" in tabs:
        why.append("命中连续下跌（偏风险观察，非做多默认）")

    vol = _num(metrics.get("成交量比20"))
    amt = _num(metrics.get("成交额比20"))
    rs = _num(metrics.get("市场相对强弱20%"))
    mom = _num(metrics.get("20日动量%"))
    atr = _num(metrics.get("ATR14%"))
    dd = _num(metrics.get("距60日高点%"))
    pe = _num(metrics.get("动态PE"))
    mcap = _num(metrics.get("流通市值(亿)"))
    turn = _num(metrics.get("换手率%"))

    if vol is not None and vol >= 1.3:
        why.append(f"成交量比20为 {vol:.2f}，量能相对放大")
    if amt is not None and amt >= 1.3:
        why.append(f"成交额比20为 {amt:.2f}")
    if rs is not None and rs >= 2:
        why.append(f"20日相对市场偏强 {rs:+.2f}%")
    if mom is not None and mom >= 3:
        why.append(f"20日动量 {mom:+.2f}%")
    if sector and sector != "未分类":
        if sector_chg is not None and sector_rank is not None and sector_rank <= 8 and sector_chg >= 0:
            why.append(f"所属「{sector}」当日板块强度靠前（约第{sector_rank}，{sector_chg:+.2f}%）")
        elif sector:
            why.append(f"所属板块「{sector}」")
    if watch:
        label = watch.get("status_label") or watch.get("status") or "观察中"
        why.append(f"已在观察池（{label}）")
        thesis = (watch.get("thesis") or "").strip()
        if thesis:
            why.append("观察池论点：" + thesis[:80])

    if not why:
        why.append("多维字段有限，仅因选股命中进入候选池")

    if "trend-down" in tabs and constructive:
        risk.append("同时命中下跌趋势，注意形态与趋势冲突")
    elif "trend-down" in tabs:
        risk.append("主命中为连续下跌，默认不作多头精选主逻辑")
    if atr is not None and atr >= 7:
        risk.append(f"ATR14% 约 {atr:.1f}，波动偏大")
    if dd is not None and dd <= -20:
        risk.append(f"距60日高点 {dd:.1f}%，仍处较深回撤")
    if vol is not None and vol < 0.7:
        risk.append(f"成交量比20仅 {vol:.2f}，量能偏弱")
    if turn is not None and turn < 0.5:
        risk.append(f"换手率 {turn:.2f}%，流动性一般")
    if pe is not None and pe > 80:
        risk.append(f"动态PE {pe:.1f}，估值偏贵需自行核对")
    if pe is None and mcap is None:
        risk.append("估值/市值快照缺失（常见于ETF或未覆盖标的）")
    if weak_sector and sector and sector != "未分类":
        risk.append(f"板块「{sector}」当日偏弱或排名靠后")
    if sector_chg is not None and sector_chg <= -1.5:
        risk.append(f"板块均涨跌 {sector_chg:+.2f}%，行业拖累风险")
    if not risk:
        risk.append("暂无明显规则化红旗；仍需结合分时与资讯人工确认")
    return why, risk


def diversify_pick(ranked: List[dict], *, limit: int = 15, max_per_sector: int = 3) -> List[dict]:
    """按分数依次选取，同一板块不超过 max_per_sector；宁缺毋滥，不回填超限。"""
    picked: List[dict] = []
    per_sector: Dict[str, int] = defaultdict(int)
    for card in ranked:
        sector = card.get("sector") or "未分类"
        if per_sector[sector] >= max_per_sector:
            continue
        picked.append(card)
        per_sector[sector] += 1
        if len(picked) >= limit:
            break
    return picked


def assemble_shortlist(
    *,
    kline_meta: Dict[str, dict],
    tab_codes: Dict[str, List[str]],
    sector_payload: Optional[dict] = None,
    watch_by_code: Optional[Dict[str, dict]] = None,
    limit: int = 15,
    max_per_sector: int = 3,
    include_down_only: bool = False,
) -> dict:
    """纯函数组装：便于单测。"""
    hits = invert_tab_hits(tab_codes)
    strength, ranks, weak = sector_rank_maps(sector_payload)
    watch_by_code = watch_by_code or {}

    market_date = None
    for meta in kline_meta.values():
        m = (meta or {}).get("metrics") or {}
        if m.get("指标日期"):
            market_date = str(m["指标日期"])[:10]
            break

    candidates: List[dict] = []
    for code, tabs in hits.items():
        meta = kline_meta.get(code) or {}
        metrics = dict(meta.get("metrics") or {})
        name = meta.get("name") or ""
        sector = meta.get("sector") or "未分类"
        constructive = [t for t in tabs if t in CONSTRUCTIVE_TABS]
        if not constructive and not include_down_only:
            continue
        sector_chg = strength.get(sector)
        sector_rank = ranks.get(sector)
        watch = watch_by_code.get(code)
        score = score_candidate(
            tabs=tabs,
            metrics=metrics,
            sector=sector,
            sector_chg=sector_chg,
            sector_rank=sector_rank,
            in_watchlist=bool(watch),
        )
        why, risk = build_why_risk(
            tabs=tabs,
            metrics=metrics,
            sector=sector,
            sector_chg=sector_chg,
            sector_rank=sector_rank,
            weak_sector=sector in weak,
            watch=watch,
        )
        candidates.append({
            "code": code,
            "name": name,
            "sector": sector,
            "tabs": tabs,
            "tab_labels": [TAB_LABELS.get(t, t) for t in tabs],
            "score": score,
            "metrics": {
                "指标日期": metrics.get("指标日期"),
                "成交量比20": metrics.get("成交量比20"),
                "成交额比20": metrics.get("成交额比20"),
                "换手率%": metrics.get("换手率%"),
                "20日动量%": metrics.get("20日动量%"),
                "市场相对强弱20%": metrics.get("市场相对强弱20%"),
                "ATR14%": metrics.get("ATR14%"),
                "距60日高点%": metrics.get("距60日高点%"),
                "动态PE": metrics.get("动态PE"),
                "流通市值(亿)": metrics.get("流通市值(亿)"),
            },
            "sector_change_pct": sector_chg,
            "sector_rank": sector_rank,
            "watchlist": {
                "status": watch.get("status"),
                "status_label": watch.get("status_label"),
                "thesis": watch.get("thesis"),
            } if watch else None,
            "why": why,
            "risks": risk,
            "links": {
                "symbol": f"/symbol.html?code={code}",
                "journal": f"/stock_journal.html?code={code}",
                "trainer": f"/trading_trainer.html?code={code}",
                "minute": f"/minute_view.html?code={code}",
                "dashboard": f"/dashboard.html#code={code}",
                "corr_cloud": f"/sector_corr_cloud.html?stock={code}&from=shortlist",
            },
        })

    ranked = sorted(candidates, key=lambda c: c["score"], reverse=True)
    picked = diversify_pick(ranked, limit=limit, max_per_sector=max_per_sector)
    for i, card in enumerate(picked, 1):
        card["rank"] = i

    return {
        "generated_at": datetime.now(tz=SH_TZ).isoformat(timespec="seconds"),
        "market_date": market_date,
        "limit": limit,
        "candidate_count": len(candidates),
        "cards": picked,
        "notes": [
            "本页为多维事实拼装的决策辅助，不是买卖建议。",
            "命中来自最近一次选股仪表盘；指标口径与仪表盘决策快照一致。",
            "板块强度来自 sector_strength_snapshot；缺失时相关句会省略或降级。",
        ],
    }


def render_shortlist_html(
    payload: dict,
    *,
    history_dates: Optional[List[dict]] = None,
) -> str:
    cards = payload.get("cards") or []
    market_date = payload.get("market_date") or "—"
    generated_at = payload.get("generated_at") or "—"
    generated_label = str(generated_at).replace("T", " ")[:19]
    candidate_count = int(payload.get("candidate_count") or 0)
    selection_rate = (len(cards) / candidate_count * 100) if candidate_count else 0.0
    top_score = _num(cards[0].get("score")) if cards else None
    export_rows = []
    history_dates = list(history_dates or [])
    history_date_values = [
        str(item.get("market_date") or "")
        for item in history_dates
        if item.get("market_date")
    ]
    selected_date = str(market_date)
    if selected_date not in history_date_values and selected_date != "—":
        history_dates.insert(0, {
            "market_date": selected_date,
            "selected_count": len(cards),
            "candidate_count": candidate_count,
        })
        history_date_values.insert(0, selected_date)
    latest_date = history_date_values[0] if history_date_values else selected_date
    selected_index = history_date_values.index(selected_date) if selected_date in history_date_values else 0
    newer_date = history_date_values[selected_index - 1] if selected_index > 0 else None
    older_date = (
        history_date_values[selected_index + 1]
        if selected_index + 1 < len(history_date_values)
        else None
    )
    is_history = bool(latest_date and selected_date != latest_date)

    history_options = "".join(
        (
            f"<option value='{_esc(item.get('market_date'))}'"
            f"{' selected' if str(item.get('market_date')) == selected_date else ''}>"
            f"{_esc(item.get('market_date'))} · {int(item.get('selected_count') or 0)} 只"
            "</option>"
        )
        for item in history_dates
        if item.get("market_date")
    )

    def date_link(value: Optional[str], label: str, relation: str) -> str:
        if not value:
            return f"<span class='history-nav-btn is-disabled' aria-disabled='true'>{label}</span>"
        return (
            f"<a class='history-nav-btn' rel='{relation}' "
            f"href='/shortlist.html?date={_esc(value)}'>{label}</a>"
        )

    history_control = ""
    if history_options:
        history_control = f"""
    <form class="history-toolbar" action="/shortlist.html" method="get">
      <div class="history-label">
        <span>历史候选</span>
        <strong>{'正在查看历史快照' if is_history else '当前为最新快照'}</strong>
      </div>
      <div class="history-controls">
        {date_link(older_date, '← 前一交易日', 'prev')}
        <label class="history-select-wrap">
          <span class="sr-only">选择候选日期</span>
          <select id="historyDateSelect" name="date" aria-label="选择历史候选日期">{history_options}</select>
        </label>
        <button class="history-submit" type="submit">查看</button>
        {date_link(newer_date, '后一交易日 →', 'next')}
        {f'<a class="history-latest" href="/shortlist.html">回到最新</a>' if is_history else ''}
      </div>
    </form>"""
    card_html = []
    for card in cards:
        metrics = card.get("metrics") or {}

        def fmt(key, suffix="", *, signed=False):
            v = metrics.get(key)
            if v is None or v == "":
                return "—"
            try:
                number = float(v)
                prefix = "+" if signed and number > 0 else ""
                return f"{prefix}{number:.2f}{suffix}"
            except (TypeError, ValueError):
                return str(v)

        def metric_tone(key: str) -> str:
            value = _num(metrics.get(key))
            if key not in {"市场相对强弱20%", "20日动量%"} or value is None or value == 0:
                return ""
            return " is-up" if value > 0 else " is-down"

        why = "".join(f"<li>{_esc(x)}</li>" for x in card.get("why") or [])
        risks = "".join(f"<li>{_esc(x)}</li>" for x in card.get("risks") or [])
        tab_chips = "".join(
            f"<span class='signal-chip'>{_esc(x)}</span>" for x in card.get("tab_labels") or []
        ) or "<span class='signal-chip is-muted'>暂无策略标签</span>"
        links = card.get("links") or {}
        watch = card.get("watchlist")
        watch_badge = (
            f"<span class='watch-badge'>观察池 · {_esc(watch.get('status_label') or watch.get('status'))}</span>"
            if watch else ""
        )
        sector_change = _num(card.get("sector_change_pct"))
        sector_tone = ""
        if sector_change is not None:
            sector_tone = " is-up" if sector_change > 0 else " is-down" if sector_change < 0 else ""
        sector_bit = _esc(card.get("sector") or "—")
        if card.get("sector_change_pct") is not None:
            sector_bit += f" <span class='sector-change{sector_tone}'>{card['sector_change_pct']:+.2f}%</span>"
        rank = int(card.get("rank") or 0)
        code = str(card.get("code") or "")
        plain_code = code[2:] if len(code) == 8 and code[:2].lower() in {"sh", "sz", "bj"} else code
        export_rows.append(f"{plain_code} {card.get('name') or ''}".strip())
        score = _num(card.get("score"))
        score_label = f"{score:.2f}" if score is not None else "—"
        card_classes = ["candidate-card"]
        if rank == 1:
            card_classes.append("is-featured")
        if 1 <= rank <= 3:
            card_classes.append("is-podium")
        metric_rows = [
            ("成交量比20", "量比 20", fmt("成交量比20"), ""),
            ("成交额比20", "额比 20", fmt("成交额比20"), ""),
            ("市场相对强弱20%", "相对强弱", fmt("市场相对强弱20%", "%", signed=True), metric_tone("市场相对强弱20%")),
            ("20日动量%", "20 日动量", fmt("20日动量%", "%", signed=True), metric_tone("20日动量%")),
            ("ATR14%", "ATR 14", fmt("ATR14%", "%"), ""),
            ("距60日高点%", "距 60 日高点", fmt("距60日高点%", "%", signed=True), ""),
        ]
        metric_html = "".join(
            f"<div class='metric-item'><span>{_esc(label)}</span><strong class='metric-value{tone}'>{_esc(value)}</strong></div>"
            for _, label, value, tone in metric_rows
        )
        card_html.append(f"""
<article class="{' '.join(card_classes)}" data-shortlist-code="{_esc(code)}">
  <header class="candidate-head">
    <div class="candidate-identity">
      <span class="rank-badge" aria-label="排名第 {rank} 名">TOP {rank:02d}</span>
      <div>
        <h2><a href="{_esc(links.get('symbol', '#'))}">{_esc(card.get('name') or code)}</a></h2>
        <div class="candidate-code">{_esc(code)}</div>
      </div>
    </div>
    <div class="score-block"><span>综合评分</span><strong>{score_label}</strong></div>
  </header>
  <div class="signal-strip">
    <span class="sector-chip">{sector_bit}</span>
    {tab_chips}
    {watch_badge}
  </div>
  <div class="metric-grid">{metric_html}</div>
  <div class="analysis-grid">
    <section class="reason-panel"><h3><span aria-hidden="true">✓</span> 入选依据</h3><ul>{why}</ul></section>
    <section class="risk-panel"><h3><span aria-hidden="true">!</span> 风险核对</h3><ul>{risks}</ul></section>
  </div>
  <div class="candidate-outcome" data-outcome-code="{_esc(code)}">
    <span class="outcome-label">收益监控</span><span class="outcome-placeholder">正在读取 5 日窗口…</span>
    <a href="/shortlist_monitor.html?from={_esc(selected_date)}&amp;to={_esc(selected_date)}&amp;horizon=5" class="outcome-link">查看当日</a>
  </div>
  <footer class="candidate-actions">
    <a class="card-primary-action" href="{_esc(links.get('symbol','#'))}">打开标的研究 <span aria-hidden="true">→</span></a>
    <nav aria-label="{_esc(card.get('name') or code)}快捷入口">
      <a class="candidate-action-context" href="{_esc(links.get('corr_cloud','#'))}" title="查看当前申万二级板块的同步相关与领先传导">板块联动</a>
      <a href="{_esc(links.get('minute','#'))}">分时</a>
      <a href="{_esc(links.get('trainer','#'))}">训练</a>
      <a href="{_esc(links.get('journal','#'))}">日记</a>
      <a href="{_esc(links.get('dashboard','#'))}">选股页</a>
    </nav>
  </footer>
</article>""")

    body_cards = "\n".join(card_html) or "<p class='app-empty shortlist-empty'>暂无建设性命中可入每日精选。请先更新选股仪表盘。</p>"
    notes = "".join(f"<li>{_esc(n)}</li>" for n in payload.get("notes") or [])
    export_json = json.dumps("\n".join(export_rows), ensure_ascii=False).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日精选</title>
<link rel="stylesheet" href="/assets/app.css">
<link rel="stylesheet" href="/assets/workbench.css">
<link rel="stylesheet" href="/assets/shortlist.css?v=3">
<script defer src="/assets/app-shell.js"></script>
<script defer src="/assets/shortlist.js?v=2"></script>
</head>
<body>
<main class="wrap">
  <section class="shortlist-hero" aria-labelledby="pageTitle">
    <div class="hero-topline">
      <div class="hero-copy">
        <div class="eyebrow">DAILY RESEARCH SHORTLIST</div>
        <h1 id="pageTitle">每日精选</h1>
        <p>把形态命中、量价表现、板块强度与风险提示压缩成一页，先看优先级，再进入标的研究。</p>
      </div>
      <div class="hero-actions">
        <button class="btn btn-primary" id="copyShortlist" type="button">复制全部标的</button>
        <a class="btn" href="/shortlist_monitor.html?from={_esc(selected_date)}&amp;to={_esc(selected_date)}">查看当日收益监控</a>
        <a class="btn" href="/daily_ops.html">每日操盘</a>
        <a class="btn" href="/dashboard.html">选股仪表盘</a>
      </div>
    </div>
    {history_control}
    <div class="summary-grid" aria-label="每日精选摘要">
      <div class="summary-item summary-date"><span>指标交易日</span><strong>{_esc(str(market_date))}</strong><small>生成于 {_esc(generated_label)}</small></div>
      <div class="summary-item"><span>当日入选</span><strong>{len(cards)}<small> 只</small></strong><small>按综合评分排序</small></div>
      <div class="summary-item"><span>全量候选</span><strong>{candidate_count}<small> 只</small></strong><small>进入多维拼装池</small></div>
      <div class="summary-item"><span>入选比例</span><strong>{selection_rate:.2f}<small>%</small></strong><small>宁缺毋滥，板块分散</small></div>
      <div class="summary-item"><span>最高评分</span><strong>{f'{top_score:.2f}' if top_score is not None else '—'}</strong><small>仅用于候选排序</small></div>
    </div>
    <div class="copy-status" id="copyStatus" role="status" aria-live="polite"></div>
  </section>
  <section class="section-heading" aria-labelledby="candidateHeading">
    <div><span class="section-kicker">按优先级排列</span><h2 id="candidateHeading">{_esc(selected_date)} 候选</h2></div>
    <p>红绿只表达行情方向；蓝色表示研究优先级，不代表买入建议。</p>
  </section>
  <div class="candidate-grid">{body_cards}</div>
  <details class="method-notes">
    <summary>口径与使用提示</summary>
    <ul>{notes}</ul>
  </details>
</main>
<script id="shortlistExportData" type="application/json">{export_json}</script>
<script>window.SHORTLIST_DATE={json.dumps(selected_date, ensure_ascii=False)};</script>
</body>
</html>
"""


def _esc(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def load_watch_map(user_id: int = 1) -> Dict[str, dict]:
    try:
        from scripts.services.watchlist import WatchlistService
        payload = WatchlistService().list_items(user_id=user_id)
    except Exception:
        return {}
    out = {}
    for item in payload.get("items") or []:
        code = item.get("code")
        if code:
            out[str(code)] = item
    return out


def load_or_refresh_sector(*, refresh: bool, top_n: int = 15) -> Optional[dict]:
    from scripts.reports.gen_daily_ops import SECTOR_SNAPSHOT, refresh_sector_snapshot
    if refresh or not SECTOR_SNAPSHOT.exists():
        try:
            return refresh_sector_snapshot(top_n=top_n)
        except Exception:
            if SECTOR_SNAPSHOT.exists():
                return json.loads(SECTOR_SNAPSHOT.read_text(encoding="utf-8"))
            return None
    try:
        return json.loads(SECTOR_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_from_paths(
    *,
    dashboard_path: Path,
    limit: int = 15,
    refresh_sector: bool = True,
    user_id: int = 1,
) -> dict:
    meta, tabs = load_dashboard(dashboard_path)
    sector = load_or_refresh_sector(refresh=refresh_sector)
    watch = load_watch_map(user_id=user_id)
    return assemble_shortlist(
        kline_meta=meta,
        tab_codes=tabs,
        sector_payload=sector,
        watch_by_code=watch,
        limit=limit,
    )
