"""无大模型市场简报保底生成器。

从本地资讯库（事件聚合）与市场情境拼装 morning / close_style JSON，
可直接交给 MarketBriefRepository.publish。不发起外网请求、不调用大模型。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from data.market_briefs import MarketBriefRepository
from data.market_context import build_market_context
from data.market_news import MarketNewsRepository

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

VERIFY_RANK = {
    "verified": 5,
    "official": 5,
    "corroborated": 4,
    "cross_reported": 3,
    "reported": 2,
    "single_source": 2,
    "research": 1,
    "unverified": 0,
}

STYLE_HINTS = (
    ("科技", ("芯片", "半导体", "人工智能", "算力", "机器人", "软件", "电子", "通信", "AI")),
    ("消费", ("消费", "白酒", "零售", "旅游", "酒店", "食品")),
    ("金融", ("银行", "券商", "保险", "证券", "金融")),
    ("周期", ("有色", "煤炭", "石油", "钢铁", "化工", "航运")),
    ("新能源", ("新能源", "光伏", "锂电", "储能", "风电", "汽车")),
    ("军工", ("军工", "航空", "航天", "船舶")),
    ("地产", ("地产", "房地产", "物业")),
)

# 入选简报前的敏感话题过滤（标题/摘要命中任一即跳过）。
# 只服务本地页面内容克制，不碰登录墙、不调用大模型；词表可按需增补。
SENSITIVE_KEYWORDS = (
    # 政治与意识形态
    "政变", "颠覆", "颜色革命", "港独", "台独", "藏独", "疆独",
    "法轮功", "六四", "天安门", "反共", "反党",
    # 暴力恐怖与恶性治安（避免简报渲染血腥社会案）
    "恐怖袭击", "自杀式袭击", "爆炸案", "枪击案", "灭门", "碎尸",
    "强奸", "性侵", "虐童",
    # 极端人身伤亡表述（纯事故行情相关如飞机失事仍可能出现，见白名单思路：宁缺勿滥）
    "万人死亡", "大屠杀", "种族清洗",
    # 明显不符合市场简报的色情/赌博诱导
    "色情", "黄赌毒", "裸聊", "赌场开户",
)

# 行情常见词不应误伤：军工/国防、制裁（贸易制裁常出现）、加息等不在黑名单。


def _today() -> str:
    return datetime.now(SHANGHAI_TZ).date().isoformat()


def _as_of_clock(kind: str) -> str:
    return "08:00" if kind == "morning" else "19:15"


def _score_event(event: dict) -> float:
    importance = float(event.get("importance") or 0)
    verify = VERIFY_RANK.get(str(event.get("verification_status") or ""), 1)
    sources = float(event.get("source_count") or len(event.get("platforms") or []) or 1)
    tags = event.get("tags") or []
    tag_bonus = 0.3 * min(len(tags), 5)
    return importance * 1.5 + verify * 2.0 + sources * 1.2 + tag_bonus


def _sectors_from_event(event: dict) -> list[str]:
    tags = [str(t).strip() for t in (event.get("tags") or []) if str(t).strip()]
    text = f"{event.get('title') or ''} {event.get('digest') or ''}"
    found: list[str] = []
    for label, keys in STYLE_HINTS:
        if any(k in text or k in "".join(tags) for k in keys):
            if label not in found:
                found.append(label)
    for tag in tags:
        if tag not in found and len(found) < 4:
            found.append(tag)
    return found[:4] or ["市场整体"]


def _event_to_news_item(event: dict) -> dict:
    platforms = event.get("platforms") or []
    sources = []
    for p in platforms[:4]:
        if isinstance(p, dict):
            sources.append({
                "publisher": p.get("name") or p.get("key") or "公开来源",
                "url": p.get("url") or "",
                "published_at": p.get("published_at") or event.get("first_published_at") or "",
            })
        else:
            sources.append({"publisher": str(p), "url": "", "published_at": event.get("first_published_at") or ""})
    if not sources:
        sources = [{
            "publisher": event.get("original_source") or "公开资讯",
            "url": "",
            "published_at": event.get("first_published_at") or "",
        }]
    title = str(event.get("title") or "").strip() or "未命名事件"
    digest = str(event.get("digest") or "").strip() or title
    sectors = _sectors_from_event(event)
    verify = event.get("verification_label") or event.get("verification_status") or "单源报道"
    impact = (
        f"事件可信度：{verify}；来源数 {event.get('source_count') or len(platforms) or 1}。"
        f"可能相关方向：{'、'.join(sectors)}。"
    )
    return {
        "event_id": event.get("event_id") or hashlib.sha1(title.encode()).hexdigest()[:16],
        "title": title[:200],
        "digest": digest[:800],
        "publisher": sources[0]["publisher"],
        "published_at": event.get("first_published_at") or event.get("time") or "",
        "sectors": sectors,
        "impact": impact[:400],
        "sources": sources,
        "verification_status": event.get("verification_status") or "reported",
    }


def _event_text(event: dict) -> str:
    tags = " ".join(str(t) for t in (event.get("tags") or []))
    return f"{event.get('title') or ''} {event.get('digest') or ''} {tags}"


def is_sensitive_event(event: dict) -> bool:
    """标题/摘要/标签是否命中敏感词表。"""
    text = _event_text(event)
    return any(k in text for k in SENSITIVE_KEYWORDS)


def _pick_events(events: list[dict], limit: int = 8) -> list[dict]:
    ranked = sorted(events, key=_score_event, reverse=True)
    picked: list[dict] = []
    seen = set()
    for event in ranked:
        if is_sensitive_event(event):
            continue
        title = re.sub(r"\s+", "", str(event.get("title") or ""))[:40]
        if not title or title in seen:
            continue
        seen.add(title)
        picked.append(event)
        if len(picked) >= limit:
            break
    return picked


def _dominant_style(news_items: list[dict]) -> str:
    counts: dict[str, int] = {}
    for item in news_items:
        for sector in item.get("sectors") or []:
            counts[sector] = counts.get(sector, 0) + 1
    if not counts:
        return "风格未明"
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    return "、".join(name for name, _ in top)


def _index_metrics(context: dict) -> list[dict]:
    metrics = []
    for card in context.get("a_share") or []:
        name = card.get("name") or card.get("code")
        change = card.get("change_pct")
        price = card.get("price")
        if change is None and price is None:
            continue
        entry: dict[str, Any] = {"label": name}
        if change is not None:
            entry["change_pct"] = round(float(change), 2)
            entry["value"] = f"{float(change):+.2f}%"
        elif price is not None:
            entry["value"] = str(price)
        metrics.append(entry)
        if len(metrics) >= 5:
            break
    for card in (context.get("overseas") or [])[:3]:
        name = card.get("name") or card.get("code")
        change = card.get("change_pct")
        if change is None:
            continue
        metrics.append({
            "label": name,
            "change_pct": round(float(change), 2),
            "value": f"{float(change):+.2f}%",
        })
    return metrics


def _risk_level(news_items: list[dict], context: dict) -> str:
    text = " ".join(
        f"{i.get('title')} {i.get('digest')}" for i in news_items
    )
    hot = ("风险", "制裁", "战争", "暴跌", "违约", "爆仓", "监管处罚", "崩盘")
    soft = ("降准", "降息", "刺激", "回暖", "创新高")
    if any(k in text for k in hot):
        return "中高"
    changes = [
        abs(float(c.get("change_pct")))
        for c in (context.get("a_share") or [])
        if c.get("change_pct") is not None
    ]
    if changes and max(changes) >= 2.0:
        return "中高"
    if any(k in text for k in soft):
        return "中低"
    return "中等"


def build_brief(
    *,
    kind: str,
    brief_date: Optional[str] = None,
    news_repo: Optional[MarketNewsRepository] = None,
    refresh_news: bool = False,
) -> dict:
    """拼装一份可发布的简报 payload（不写库）。"""
    if kind not in {"morning", "close_style"}:
        raise ValueError("kind 只允许 morning 或 close_style")
    brief_date = brief_date or _today()
    clock = _as_of_clock(kind)
    news_repo = news_repo or MarketNewsRepository()
    if refresh_news and not getattr(news_repo, "read_only", False):
        # 仅刷新同花顺缓存；东财/见闻由外部 fetch CLI 负责，避免隐式打站
        try:
            news_repo.day(brief_date, refresh=True)
        except Exception:
            pass
    payload_news = news_repo.day(brief_date, as_of=clock)
    events = list(payload_news.get("events") or [])
    skipped_sensitive = sum(1 for e in events if is_sensitive_event(e))
    picked = _pick_events(events, limit=8)
    news_items = [_event_to_news_item(e) for e in picked]

    warnings: list[dict] = []
    status = "complete"
    if skipped_sensitive:
        warnings.append({
            "title": "已跳过敏感话题",
            "detail": f"规则过滤掉 {skipped_sensitive} 条命中敏感词的事件，不进入简报正文。",
        })
    if len(news_items) < 8:
        status = "partial"
        warnings.append({
            "title": "重要资讯不足 8 条",
            "detail": f"截至 {brief_date} {clock} 仅聚合到 {len(news_items)} 条可用事件，已按 partial 发布，不强行凑数。",
        })
    if not events:
        warnings.append({
            "title": "资讯库为空",
            "detail": "本地资讯库该日无事件；请先运行 fetch_market_news 或刷新同花顺。",
        })

    try:
        context = build_market_context(brief_date, as_of=f"{clock}:00")
    except Exception as exc:  # noqa: BLE001
        context = {"a_share": [], "overseas": [], "notes": [str(exc)]}
        warnings.append({
            "title": "市场情境不完整",
            "detail": f"读取市场情境失败：{exc}",
        })
        status = "partial"

    metrics = _index_metrics(context)
    style = _dominant_style(news_items)
    risk = _risk_level(news_items, context)
    titles = [i["title"] for i in news_items[:3]]
    confirmed = "；".join(titles) if titles else "当日公开资讯仍稀缺"
    if kind == "morning":
        headline = f"{brief_date} 晨间简报：关注{style}" if news_items else f"{brief_date} 晨间简报：资讯待补"
        narrative = (
            f"截至北京时间 {clock}，资讯中心聚合到 {len(events)} 个事件，本报选取 {len(news_items)} 条。"
            f"主线偏向「{style}」。已确认事实侧重：{confirmed}。"
            f"以下判断来自规则模板，不是大模型推理。"
        )
        judgment = (
            f"开盘后优先验证「{style}」相关方向的量价承接；"
            f"若指数高开低走且宽度快速收缩，则降低追涨强度。"
        )
        pending = "观察开盘30分钟涨跌家数、主线成交占比、以及隔夜外盘映射是否被A股定价。"
        stance = "结构性观察，仓位克制"
        sections = {
            "news": news_items,
            "core_conflicts": [
                {
                    "title": "事件催化 vs 开盘定价",
                    "detail": "盘前叙事是否被开盘量价确认，是当日首要矛盾。",
                }
            ],
            "sector_impacts": [
                {"title": style, "detail": f"由入选资讯标签与标题关键词归并得到：{style}。"}
            ],
            "watch_variables": [
                {"title": "开盘宽度", "detail": "上涨家数与涨停数量是否同步改善。"},
                {"title": "主线成交", "detail": f"「{style}」相关板块成交占比是否提升。"},
            ],
            "risk_conditions": [
                {"title": "高开低走", "detail": "若高开后半小时内转跌且成交萎缩，降级乐观假设。"}
            ],
            "transmission_chains": [
                {
                    "title": "资讯 → 风险偏好 → 风格",
                    "detail": "高可信度政策/产业事件抬升风险偏好时，优先映射到资讯中反复出现的风格标签。",
                }
            ],
        }
    else:
        headline = f"{brief_date} 收盘风格简报：{style}" if news_items else f"{brief_date} 收盘风格简报：数据待补"
        narrative = (
            f"截至北京时间 {clock}，当日聚合事件 {len(events)} 个，本报选取 {len(news_items)} 条做收盘归因。"
            f"规则模板认为主导风格接近「{style}」。指数与外盘摘要见 metrics。"
        )
        judgment = (
            f"若「{style}」全日维持量价优势，则倾向事件驱动成立；"
            f"若仅脉冲冲高回落，则归因为情绪交易，不宜外推。"
        )
        pending = "次日关注主线是否缩量、高低切换是否出现、以及隔夜外盘能否继续提供映射。"
        stance = "以证据强度决定是否延续当日风格"
        morning = MarketBriefRepository().latest(kind="morning") if False else None
        # 读取同日晨报做复盘（若有）
        morning_review = []
        try:
            day_pack = MarketBriefRepository().day(brief_date)
            for item in day_pack.get("items") or []:
                if item.get("kind") == "morning":
                    mh = (item.get("summary") or {}).get("headline") or ""
                    ms = (item.get("summary") or {}).get("stance") or ""
                    morning_review.append({
                        "bias": "部分印证" if news_items else "样本不足",
                        "detail": f"晨报标题「{mh}」；立场「{ms}」。收盘用规则对照资讯主线「{style}」。",
                    })
                    break
        except Exception:
            morning_review = []
        if not morning_review:
            morning_review = [{
                "bias": "无晨报可对账",
                "detail": "同日尚无 morning 简报；收盘归因仅基于当日资讯与行情摘要。",
            }]
        sections = {
            "news": news_items,
            "news_attribution": [
                {
                    "title": item["title"][:80],
                    "detail": item.get("impact") or item.get("digest") or "",
                    "verdict": "部分解释",
                }
                for item in news_items[:5]
            ],
            "morning_review": morning_review,
            "evidence": [
                {
                    "title": "资讯主线",
                    "detail": f"入选事件标签归并为：{style}。",
                },
                {
                    "title": "指数摘要",
                    "detail": "见 metrics；缺失项已在 warnings 说明。" if metrics else "指数摘要缺失。",
                },
            ],
            "confirmations": [
                {
                    "title": "主线回撤后仍有承接",
                    "detail": "次日若缩量跌破当日关键位，则降低延续评级。",
                }
            ],
            "invalidations": [
                {
                    "title": "主线高开低走且宽度坍塌",
                    "detail": "视为情绪交易结束，不宜把当日涨幅外推。",
                }
            ],
        }

    summary = {
        "headline": headline,
        "market_state": f"规则保底 · 资讯事件 {len(events)} · 入选 {len(news_items)}",
        "dominant_style": style,
        "stance": stance,
        "risk_level": risk,
        "event_count": len(news_items),
        "confirmed_facts": confirmed[:500],
        "analysis_judgment": judgment,
        "pending_validation": pending,
        "narrative": narrative,
        "source_coverage": (
            f"本地资讯库 as_of={brief_date} {clock}；"
            f"来源目录覆盖见资讯中心；本报生成器=rule_fallback。"
        ),
        "status_note": (
            "完整规则保底稿。"
            if status == "complete"
            else "数据不足，已标记 partial，不把缺失项伪装成完整。"
        ),
        "revision_note": "由 brief_builder 规则引擎自动生成，可被后续大模型润色版覆盖为新 revision。",
    }

    now = datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
    return {
        "schema_version": "1.0",
        "kind": kind,
        "brief_date": brief_date,
        "market_date": brief_date,
        "status": status,
        "data_as_of": f"{brief_date}T{clock}:00+08:00",
        "generated_at": now,
        "summary": summary,
        "metrics": metrics,
        "sections": sections,
        "charts": {},
        "sources": [
            {
                "publisher": s.get("publisher") or "",
                "url": s.get("url") or "",
                "published_at": s.get("published_at") or "",
            }
            for item in news_items
            for s in (item.get("sources") or [])[:1]
        ][:12],
        "warnings": warnings,
        "generator": "rule_fallback",
    }


def generate_and_publish(
    *,
    kind: str,
    brief_date: Optional[str] = None,
    refresh_news: bool = False,
    refresh_html: bool = True,
) -> dict:
    payload = build_brief(kind=kind, brief_date=brief_date, refresh_news=refresh_news)
    result = MarketBriefRepository().publish(payload, kind=kind)
    html_path = None
    if refresh_html:
        from scripts.services.market_brief import write_app
        html_path = str(write_app())
    return {
        "ok": True,
        "kind": result.get("kind"),
        "brief_date": result.get("brief_date"),
        "revision": result.get("revision"),
        "status": payload.get("status"),
        "idempotent": result.get("idempotent"),
        "headline": (payload.get("summary") or {}).get("headline"),
        "news_count": len((payload.get("sections") or {}).get("news") or []),
        "html": html_path,
        "warnings": payload.get("warnings") or [],
    }
