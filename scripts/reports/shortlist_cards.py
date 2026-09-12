"""每日短名单理由卡：把选股命中、决策快照、板块强度、观察池拼成可读卡片。

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
        # 纯下跌命中默认不进短名单（仍可在全量里看到）；评分给极低
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
        risk.append("主命中为连续下跌，默认不作多头短名单主逻辑")
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


def render_shortlist_html(payload: dict) -> str:
    cards = payload.get("cards") or []
    market_date = payload.get("market_date") or "—"
    generated_at = payload.get("generated_at") or "—"
    card_html = []
    for card in cards:
        metrics = card.get("metrics") or {}
        def fmt(key, suffix=""):
            v = metrics.get(key)
            if v is None or v == "":
                return "—"
            try:
                return f"{float(v):.2f}{suffix}"
            except (TypeError, ValueError):
                return str(v)

        why = "".join(f"<li>{_esc(x)}</li>" for x in card.get("why") or [])
        risks = "".join(f"<li>{_esc(x)}</li>" for x in card.get("risks") or [])
        tabs = "、".join(_esc(x) for x in card.get("tab_labels") or [])
        links = card.get("links") or {}
        watch = card.get("watchlist")
        watch_badge = (
            f"<span class='badge'>观察池·{_esc(watch.get('status_label') or watch.get('status'))}</span>"
            if watch else ""
        )
        sector_bit = _esc(card.get("sector") or "—")
        if card.get("sector_change_pct") is not None:
            sector_bit += f" ({card['sector_change_pct']:+.2f}%)"
        card_html.append(f"""
<article class="card">
  <header>
    <div class="title"><span class="rank">#{card.get('rank')}</span>
      <a href="{_esc(links.get('symbol', '#'))}">{_esc(card.get('code'))}</a>
      <b>{_esc(card.get('name') or '')}</b>
      {watch_badge}
    </div>
    <div class="meta">评分 {card.get('score')} · 板块 {sector_bit} · 命中 {tabs or '—'}</div>
  </header>
  <div class="metrics">
    <div><span>量比20</span><b>{fmt('成交量比20')}</b></div>
    <div><span>额比20</span><b>{fmt('成交额比20')}</b></div>
    <div><span>相对强弱</span><b>{fmt('市场相对强弱20%','%')}</b></div>
    <div><span>动量20</span><b>{fmt('20日动量%','%')}</b></div>
    <div><span>ATR14</span><b>{fmt('ATR14%','%')}</b></div>
    <div><span>距高点</span><b>{fmt('距60日高点%','%')}</b></div>
  </div>
  <div class="cols">
    <section><h3>为何入选</h3><ul>{why}</ul></section>
    <section><h3>主要风险</h3><ul>{risks}</ul></section>
  </div>
  <footer class="links">
    <a href="{_esc(links.get('symbol','#'))}">标的上下文</a>
    <a href="{_esc(links.get('minute','#'))}">分时</a>
    <a href="{_esc(links.get('trainer','#'))}">训练</a>
    <a href="{_esc(links.get('journal','#'))}">日记</a>
    <a href="{_esc(links.get('dashboard','#'))}">选股页</a>
  </footer>
</article>""")

    body_cards = "\n".join(card_html) or "<p class='app-empty'>暂无建设性命中可入短名单。请先更新选股仪表盘。</p>"
    notes = "".join(f"<li>{_esc(n)}</li>" for n in payload.get("notes") or [])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日短名单理由卡</title>
<link rel="stylesheet" href="/assets/app.css">
<link rel="stylesheet" href="/assets/workbench.css">
<script defer src="/assets/app-shell.js"></script>
<style>
main.wrap{{max-width:920px;margin:0 auto;padding:16px 14px 40px}}
.hero{{margin-bottom:14px}}
.hero h1{{font-size:1.25rem;margin:0 0 6px}}
.hero p{{margin:0;color:var(--app-muted,#667085);font-size:.9rem}}
.card{{background:var(--app-card,#fff);border:1px solid var(--app-border,#e5e8ef);border-radius:12px;padding:12px 14px;margin:0 0 12px}}
.card .title{{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}}
.card .rank{{color:var(--app-muted,#667085);font-variant-numeric:tabular-nums}}
.card .meta{{margin-top:4px;font-size:.82rem;color:var(--app-muted,#667085)}}
.badge{{font-size:.72rem;background:#eef4ff;color:#2952cc;padding:2px 6px;border-radius:999px}}
.metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:10px 0;font-variant-numeric:tabular-nums}}
.metrics span{{display:block;font-size:.72rem;color:var(--app-muted,#667085)}}
.metrics b{{font-size:.95rem}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.cols h3{{margin:0 0 4px;font-size:.85rem}}
.cols ul{{margin:0;padding-left:1.1rem;font-size:.86rem;line-height:1.45}}
.links{{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;font-size:.85rem}}
.notes{{font-size:.82rem;color:var(--app-muted,#667085)}}
@media(max-width:700px){{
  .metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .cols{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<main class="wrap">
  <div class="hero">
    <h1>每日短名单理由卡</h1>
    <p>指标日 { _esc(str(market_date)) } · 生成 { _esc(str(generated_at)) } · 共 {len(cards)} 只
       （候选 {payload.get('candidate_count', 0)}）</p>
    <p style="margin-top:6px"><a href="/daily_ops.html">← 每日操盘</a> · <a href="/dashboard.html">选股仪表盘</a></p>
  </div>
  {body_cards}
  <ul class="notes">{notes}</ul>
</main>
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
