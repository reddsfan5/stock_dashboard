"""研究结论摘要（digest）：回测脚本产出统一结构的“总体总结”，同时落成 Markdown 文档和 JSON，
导航页 / 研究页用同一份 JSON 渲染摘要卡片。

新增一个回测的摘要卡：在该回测脚本里拼一个 digest dict 并调用 ``save(digest)`` 即可，
导航页（scripts/reports/gen_index.py）会自动收录 ``output/research/digests/*.json``。

digest 结构（除 id / title / page 外均可选）::

    {
      "id": "holiday_effect", "order": 50,
      "title": "节假日效应 · 总体总结", "page": "/holiday_effect.html", "icon": "🏮",
      "generated": "2026-09-26 08:00", "sample": "2015-01-01 ~ 2026-09-25",
      "headline": "一句话结论",
      "kpis":     [{"label": "完整事件", "value": "53", "sub": "…", "tone": "up|down|neutral"}],
      "sections": [{"title": "收益窗口", "items": [{"text": "…", "tone": "…", "tag": "…"}]}],
      "tables":   [{"title": "各节日对比", "columns": [...], "rows": [[...], ...]}],
      "caveats":  ["历史统计关联，不构成投资建议。"],
      "md_path":  "output/research/holiday_effect/holiday_summary.md"   # 相对项目根
    }

所有数字必须来自回测计算结果，这里只负责排版。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DIGEST_DIR = PROJECT_DIR / "output" / "research" / "digests"
TONES = ("up", "down", "neutral")


def validate(d: dict) -> dict:
    for key in ("id", "title", "page"):
        if not d.get(key):
            raise ValueError(f"digest 缺少字段 {key}")
    for k in d.get("kpis") or []:
        if k.get("tone", "neutral") not in TONES:
            raise ValueError(f"未知 tone: {k.get('tone')}")
    return d


def to_markdown(d: dict) -> str:
    L = [f"# {d['title']}", ""]
    meta = [x for x in (f"生成：{d['generated']}" if d.get("generated") else "",
                        f"样本：{d['sample']}" if d.get("sample") else "",
                        f"页面：{d['page']}") if x]
    L += ["> " + " · ".join(meta), ""]
    if d.get("headline"):
        L += [f"**{d['headline']}**", ""]
    if d.get("kpis"):
        L += ["## 关键数字", "", "| 指标 | 数值 | 说明 |", "|---|---|---|"]
        L += [f"| {k['label']} | {k['value']} | {k.get('sub', '')} |" for k in d["kpis"]]
        L.append("")
    for s in d.get("sections") or []:
        L += [f"## {s['title']}", ""]
        for it in s.get("items") or []:
            tag = f"【{it['tag']}】" if it.get("tag") else ""
            L.append(f"- {tag}{it['text']}")
        L.append("")
    for t in d.get("tables") or []:
        L += [f"## {t['title']}", "", "| " + " | ".join(t["columns"]) + " |",
              "|" + "|".join("---" for _ in t["columns"]) + "|"]
        L += ["| " + " | ".join(str(c) for c in r) + " |" for r in t["rows"]]
        L.append("")
    for c in d.get("caveats") or []:
        L.append(f"> {c}")
    return "\n".join(L).rstrip() + "\n"


def save(d: dict, digest_dir: Path | None = None) -> Path:
    """写 JSON（供导航页 / 页面卡片）与 Markdown 文档（md_path），返回 JSON 路径。"""
    validate(d)
    digest_dir = Path(digest_dir or DIGEST_DIR)
    digest_dir.mkdir(parents=True, exist_ok=True)
    if d.get("md_path"):
        md = Path(d["md_path"])
        md = md if md.is_absolute() else PROJECT_DIR / md
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(to_markdown(d), encoding="utf-8")
    out = digest_dir / f"{d['id']}.json"
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def prune(prefix: str, keep_ids, digest_dir: Path | None = None) -> list[str]:
    """删除 id == prefix 或以 “prefix-” 开头、但不在 keep_ids 里的摘要（多标的回测去掉已下线的标的）。"""
    digest_dir = Path(digest_dir or DIGEST_DIR)
    keep, removed = set(keep_ids), []
    for p in sorted(digest_dir.glob("*.json")):
        if (p.stem == prefix or p.stem.startswith(prefix + "-")) and p.stem not in keep:
            p.unlink()
            removed.append(p.stem)
    return removed


def load_all(digest_dir: Path | None = None) -> list[dict]:
    digest_dir = Path(digest_dir or DIGEST_DIR)
    out = []
    for p in sorted(digest_dir.glob("*.json")):
        try:
            out.append(validate(json.loads(p.read_text(encoding="utf-8"))))
        except (ValueError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda d: (d.get("order", 100), d.get("id")))


INDEX_CSS = """
.rd-wrap{max-width:1300px;margin:0 auto;padding:18px 32px 0}
.rd-label{font-size:12px;font-weight:700;letter-spacing:.06em;color:#8a94a6;margin-bottom:8px}
.rd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:11px}
.rd-card{display:block;text-decoration:none;color:var(--text);background:var(--card);border:1px solid var(--border);border-left:3px solid var(--research);border-radius:11px;padding:14px 16px;transition:box-shadow .15s,transform .15s}
.rd-card:hover{box-shadow:0 7px 20px rgba(18,27,49,.09);transform:translateY(-2px);text-decoration:none}
.rd-top{display:flex;align-items:center;gap:8px;justify-content:space-between}
.rd-title{font-size:14px;font-weight:700}.rd-date{font-size:10.5px;color:var(--muted);white-space:nowrap}
.rd-head{font-size:12.5px;line-height:1.6;margin:6px 0 10px;color:var(--text)}
.rd-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.rd-kpi{background:color-mix(in srgb,var(--research) 7%,var(--card));border-radius:7px;padding:6px 8px;min-width:0}
.rd-kpi span{display:block;font-size:10.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rd-kpi b{display:block;font-size:16px;font-variant-numeric:tabular-nums;margin-top:1px}
.rd-kpi b.up{color:var(--app-up,#e5484d)}.rd-kpi b.down{color:var(--app-down,#159568)}
.rd-more{margin-top:9px;font-size:11.5px;color:var(--blue)}
@media (max-width:700px){.rd-wrap{padding:14px 14px 0}.rd-grid{grid-template-columns:1fr}}
"""


def index_section_html(digests: list[dict] | None = None) -> str:
    """导航页顶部的“研究结论速览”区块（无 digest 时返回空串）。"""
    digests = load_all() if digests is None else digests
    if not digests:
        return ""
    e = html.escape
    cards = []
    for d in digests:
        kpis = "".join(
            f'<div class="rd-kpi"><span>{e(k["label"])}</span><b class="{e(k.get("tone", "neutral"))}">{e(str(k["value"]))}</b></div>'
            for k in (d.get("kpis") or [])[:3])
        cards.append(
            f'<a class="rd-card" href="{e(d["page"])}#summary">'
            f'<div class="rd-top"><span class="rd-title">{e(d.get("icon", "📌"))} {e(d["title"])}</span>'
            f'<span class="rd-date">{e(d.get("generated", ""))}</span></div>'
            f'<p class="rd-head">{e(d.get("headline", ""))}</p>'
            f'<div class="rd-kpis">{kpis}</div><div class="rd-more">查看总结与图表 ›</div></a>')
    return (f'<style>{INDEX_CSS}</style><section class="rd-wrap" id="research-digests" aria-label="研究结论速览">'
            f'<div class="rd-label">研究结论速览</div><div class="rd-grid">{"".join(cards)}</div></section>')
