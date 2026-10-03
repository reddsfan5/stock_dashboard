#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阅读 / 心法板块：按 config/reading.yaml 渲染书库首页与每篇文章页。

    python -m scripts.reports.gen_reading

输出（不入库，随时可再生）：
- output/reading.html                 书库首页（按分类分区）
- output/reading/<id>.html            文章页（目录、阅读进度、提示框、术语气泡、图片放大）
- output/reading/<id>/images/...      文章图片（从 content/reading/<id>/ 复制）

标注语法见 scripts/reports/reading_markdown.py 与 docs/34-阅读板块.md。
"""

from __future__ import annotations

import json
import math
import re
import shutil
import sys
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.reports.reading_markdown import MarkdownRenderer, RenderResult, strip_inline  # noqa: E402

CONFIG_PATH = ROOT / "config" / "reading.yaml"
GLOSSARY_PATH = ROOT / "config" / "reading_glossary.yaml"
OUTPUT_DIR = ROOT / "output"
CHARS_PER_MINUTE = 400  # 中文长文阅读速度的保守估计


class ReadingConfigError(ValueError):
    pass


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


# --------------------------------------------------------------- config
def load_config(path: Path = CONFIG_PATH, root: Path = ROOT) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    categories = data.get("categories") or []
    if not categories:
        raise ReadingConfigError("reading.yaml 至少需要一个分类")
    cat_ids = [str(c.get("id", "")).strip() for c in categories]
    if "" in cat_ids or len(set(cat_ids)) != len(cat_ids):
        raise ReadingConfigError("分类 id 不能为空且不能重复")
    series_list = []
    series_ids = set()
    for raw in data.get("series") or []:
        ser = dict(raw)
        sid = str(ser.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid) or sid in series_ids:
            raise ReadingConfigError(f"系列 id 无效或重复：{sid!r}")
        if ser.get("category") not in cat_ids:
            raise ReadingConfigError(f"系列 {sid} 的分类 {ser.get('category')!r} 不在 categories 里")
        if not ser.get("name"):
            raise ReadingConfigError(f"系列 {sid} 缺少 name")
        ser["id"] = sid
        series_ids.add(sid)
        series_list.append(ser)
    series_by_id = {s_["id"]: s_ for s_ in series_list}
    articles = []
    seen = set()
    for raw in data.get("articles") or []:
        art = dict(raw)
        aid = str(art.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", aid):
            raise ReadingConfigError(f"文章 id 只能用小写字母、数字和连字符：{aid!r}")
        if aid in seen:
            raise ReadingConfigError(f"文章 id 重复：{aid}")
        seen.add(aid)
        if not art.get("title"):
            raise ReadingConfigError(f"文章 {aid} 缺少 title")
        if art.get("series"):
            ser = series_by_id.get(str(art["series"]))
            if not ser:
                raise ReadingConfigError(f"文章 {aid} 的系列 {art['series']!r} 不在 series 里")
            art.setdefault("category", ser["category"])
            if art["category"] != ser["category"]:
                raise ReadingConfigError(f"文章 {aid} 的分类与所属系列 {ser['id']} 不一致")
        if art.get("category") not in cat_ids:
            raise ReadingConfigError(f"文章 {aid} 的分类 {art.get('category')!r} 不在 categories 里")
        source = art.get("source") or f"content/reading/{aid}/article.md"
        src_path = (Path(root) / source).resolve()
        if not src_path.is_file():
            raise ReadingConfigError(f"文章 {aid} 的源文件不存在：{source}")
        art["id"] = aid
        art["source_path"] = src_path
        art["order"] = int(art.get("order", 999))
        art["tags"] = [str(t) for t in (art.get("tags") or [])]
        art["glossary_auto"] = bool(art.get("glossary_auto", False))
        art["added"] = str(art.get("added", "") or "")
        articles.append(art)
    articles.sort(key=lambda a: (cat_ids.index(a["category"]), a["order"], a["id"]))
    for ser in series_list:
        ser["articles"] = [a for a in articles if a.get("series") == ser["id"]]
    data["categories"] = categories
    data["articles"] = articles
    data["series"] = [s_ for s_ in series_list if s_["articles"]]
    data.setdefault("title", "阅读 · 心法")
    data.setdefault("subtitle", "")
    data.setdefault("output_page", "reading.html")
    data.setdefault("article_dir", "reading")
    data["callouts"] = data.get("callouts") or {}
    data["emoji_map"] = data.get("emoji_map") or {}
    return data


def load_glossary(path: Path = GLOSSARY_PATH) -> Dict[str, dict]:
    if not Path(path).is_file():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    terms = {}
    for key, entry in (data.get("terms") or {}).items():
        if isinstance(entry, str):
            entry = {"def": entry}
        terms[str(key)] = {"def": str((entry or {}).get("def", "")), "aliases": [str(a) for a in (entry or {}).get("aliases", []) or []]}
    return terms


# -------------------------------------------------------------- render
def build_toc(headings) -> List[dict]:
    """h2 为章节，h3 挂在最近的 h2 下；文章标题（h1）不进目录。"""
    toc: List[dict] = []
    for h in headings:
        if h.level == 2:
            toc.append({"id": h.id, "text": h.text, "children": []})
        elif h.level == 3:
            if not toc:
                toc.append({"id": h.id, "text": h.text, "children": []})
            else:
                toc[-1]["children"].append({"id": h.id, "text": h.text})
    return toc


def make_image_resolver(article: dict, article_dir: str, copies: List[Tuple[Path, str]]):
    base = article["source_path"].parent

    def resolve(src: str) -> Optional[str]:
        if re.match(r"^(https?:)?//", src) or src.startswith(("/", "data:")):
            return src
        target = (base / src).resolve()
        if not target.is_file() or base.resolve() not in target.parents:
            return None
        rel = target.relative_to(base.resolve()).as_posix()
        copies.append((target, rel))
        return f"/{article_dir}/{article['id']}/{rel}"

    return resolve


def render_article(article: dict, config: dict, glossary: Dict[str, dict]):
    text = article["source_path"].read_text(encoding="utf-8")
    copies: List[Tuple[Path, str]] = []
    renderer = MarkdownRenderer(
        callouts=config["callouts"],
        emoji_map=config["emoji_map"],
        glossary=glossary,
        glossary_auto=article["glossary_auto"],
        image_resolver=make_image_resolver(article, config["article_dir"], copies),
    )
    result = renderer.render(text)
    body = result.html
    # 正文第一个 h1 由页面头部展示，避免重复
    body = re.sub(r"^\s*<h1 id=\"[^\"]*\">.*?</h1>\s*", "", body, count=1, flags=re.S)
    plain = re.sub(r"<[^>]+>", "", body)
    chars = len(re.sub(r"\s", "", plain))
    meta = {
        "toc": build_toc(result.headings),
        "chapters": sum(1 for h in result.headings if h.level == 2),
        "chars": chars,
        "minutes": max(1, math.ceil(chars / CHARS_PER_MINUTE)),
        "copies": copies,
        "result": result,
    }
    return body, meta


# ------------------------------------------------------------ templates
HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{title}</title>
<link rel="icon" href="/assets/favicon.svg">
<link rel="stylesheet" href="/assets/app.css">
<link id="workbench-css" rel="stylesheet" href="/assets/workbench.css">
<link rel="stylesheet" href="/assets/reading.css">
<script src="/assets/app-shell.js" defer></script>
<script id="workbench-js" src="/assets/workbench.js" defer></script>
<script src="/assets/reading.js" defer></script>
</head>
"""


def _layout_style(config: dict) -> str:
    """reading.yaml 的 layout.measure_chars / wide_chars → 正文栏宽 CSS 变量（每行汉字数）。"""
    layout = config.get("layout") or {}
    parts = []
    for key, var in (("measure_chars", "--rd-measure-ch"), ("wide_chars", "--rd-wide-ch")):
        if key in layout:
            value = int(layout[key])
            if not 30 <= value <= 100:
                raise ReadingConfigError(f"layout.{key} 应在 30–100 之间：{value}")
            parts.append(f"{var}:{value}")
    return f' style="{";".join(parts)}"' if parts else ""


def _legend_html(config: dict) -> str:
    rows = []
    for kind, spec in config["callouts"].items():
        rows.append(
            f'<li><span class="rd-legend-chip rd-callout--{e(spec.get("tone", kind))}">{e(spec.get("icon", ""))} {e(spec.get("label", kind))}</span></li>'
        )
    return (
        '<details class="rd-info"><summary aria-label="标注说明">ⓘ 标注说明</summary><div class="rd-info-panel">'
        "<p><strong>提示框</strong>：不同颜色标出不同用途。</p>"
        f'<ul class="rd-legend">{"".join(rows)}</ul>'
        '<p><mark class="rd-mark">荧光笔</mark>：最值得记住的原话或结论。</p>'
        '<p><dfn class="rd-term rd-term--demo">虚线词</dfn>：术语，桌面悬停、手机点按看释义。</p>'
        "<p>衬线体灰底引用是原文；解读与释义为整理者补充，不代表原作者原话。</p>"
        "<p>阅读进度和清单勾选只保存在本机浏览器。</p></div></details>"
    )


def _toc_html(toc: List[dict]) -> str:
    items = []
    for ch in toc:
        sub = "".join(f'<li><a href="#{e(c["id"])}" data-toc="{e(c["id"])}">{e(c["text"])}</a></li>' for c in ch["children"])
        sub_html = f"<ol>{sub}</ol>" if sub else ""
        items.append(f'<li data-chapter="{e(ch["id"])}"><a href="#{e(ch["id"])}" data-toc="{e(ch["id"])}">{e(ch["text"])}</a>{sub_html}</li>')
    return f'<ol class="rd-toc-list">{"".join(items)}</ol>'


def build_article_html(article: dict, body: str, meta: dict, config: dict, glossary: Dict[str, dict], neighbours) -> str:
    cat = next(c for c in config["categories"] if c["id"] == article["category"])
    used = {k: glossary[k]["def"] for k in meta["result"].terms_used if k in glossary}
    gloss_json = json.dumps(used, ensure_ascii=False).replace("</", "<\\/")
    page_json = json.dumps({"id": article["id"], "mermaid": meta["result"].has_mermaid}, ensure_ascii=False)
    tags = "".join(f'<span class="rd-tag">{e(t)}</span>' for t in article["tags"])
    prev_a, next_a = neighbours
    lib = "/" + config["output_page"]
    nav_links = ""
    if prev_a:
        nav_links += f'<a class="rd-pager-prev" href="/{config["article_dir"]}/{e(prev_a["id"])}.html"><small>上一篇</small>{e(prev_a["title"])}</a>'
    if next_a:
        nav_links += f'<a class="rd-pager-next" href="/{config["article_dir"]}/{e(next_a["id"])}.html"><small>下一篇</small>{e(next_a["title"])}</a>'
    meta_bits = [article.get("author", ""), f"约 {meta['minutes']} 分钟", f"{meta['chapters']} 章", f"{meta['chars'] // 1000}k 字"]
    if article["added"]:
        meta_bits.append(f"收录 {article['added']}")
    meta_line = " · ".join(e(x) for x in meta_bits if x)
    return HEAD.format(title=e(article["title"]) + " · 阅读") + f"""<body class="app-workbench rd-page" data-page="reading"{_layout_style(config)}>
<script>try{{if(localStorage.getItem('rdToc')==='collapsed')document.body.classList.add('rd-toc-collapsed');if(localStorage.getItem('rdWidth')==='wide')document.body.classList.add('rd-wide')}}catch(e){{}}</script>
<div id="app-shell" data-active="reading"></div>
<div class="rd-progress" aria-hidden="true"><span id="rdProgress"></span></div>
<main class="rd-main">
<nav class="rd-crumbs" aria-label="位置"><a href="{lib}">{e(config['title'])}</a><span>/</span><a href="{lib}#{e(cat['id'])}">{e(cat['name'])}</a></nav>
<header class="rd-hero">
  <p class="rd-kicker">{e(cat.get('icon', ''))} {e(cat['name'])}</p>
  <h1>{e(article['title'])}</h1>
  <p class="rd-meta">{meta_line}</p>
  <div class="rd-hero-row"><div class="rd-tags">{tags}</div>
  <div class="rd-tools">{_legend_html(config)}<button type="button" class="rd-toggle rd-toggle--desktop" id="rdTocToggle" aria-controls="rdToc" aria-pressed="true" title="显示/隐藏目录（快捷键 T）">☰ 目录</button><button type="button" class="rd-toggle rd-toggle--desktop" id="rdWidthToggle" aria-pressed="false" title="正文栏宽：标准 / 宽（快捷键 W）">↔ 宽栏</button><div class="rd-font" role="group" aria-label="字号"><button type="button" data-font="-1" aria-label="减小字号">A−</button><button type="button" data-font="1" aria-label="增大字号">A+</button></div></div></div>
</header>
{_series_nav_html(config, article)}
<div class="rd-layout">
  <aside class="rd-toc" id="rdToc" aria-label="文章目录">
    <div class="rd-toc-head"><span>目录</span><button type="button" class="rd-toc-collapse" id="rdTocCollapse" aria-controls="rdToc" title="收起目录（快捷键 T）">« 收起</button><button type="button" class="rd-toc-close" aria-label="关闭目录">✕</button></div>
    <nav>{_toc_html(meta['toc'])}</nav>
  </aside>
  <article class="rd-article" id="rdArticle" data-article="{e(article['id'])}">
{body}
  </article>
</div>
<footer class="rd-footer"><div class="rd-pager">{nav_links}</div><a class="rd-back" href="{lib}">← 返回书库</a></footer>
</main>
<button type="button" class="rd-toc-fab" id="rdTocFab" aria-controls="rdToc" aria-expanded="false">☰ 目录</button>
<button type="button" class="rd-resume" id="rdResume" hidden></button>
<div class="rd-scrim" id="rdScrim" hidden></div>
<div class="rd-term-pop" id="rdTermPop" role="tooltip" hidden></div>
<dialog class="rd-lightbox" id="rdLightbox" aria-label="图片详情"><header><span id="rdLightboxCaption"></span><button type="button" id="rdLightboxZoom" aria-pressed="false">原始尺寸</button><button type="button" id="rdLightboxClose">关闭</button></header><div class="rd-lightbox-body" id="rdLightboxBody" tabindex="0"></div></dialog>
<script type="application/json" id="rdGlossary">{gloss_json}</script>
<script type="application/json" id="rdPage">{page_json}</script>
</body>
</html>
"""


def _series_card_html(config: dict, ser: dict, metas: Dict[str, dict]) -> str:
    members = ser["articles"]
    minutes = sum(metas[a["id"]]["minutes"] for a in members)
    rows = []
    for i, a in enumerate(members, 1):
        m = metas[a["id"]]
        rows.append(
            f'<li><a href="/{config["article_dir"]}/{e(a["id"])}.html" data-article="{e(a["id"])}"><span class="rd-series-no">{i}</span>'
            f'<span class="rd-series-title"><strong>{e(a.get("short_title") or a["title"])}</strong><small>{e(a.get("summary", ""))}</small></span>'
            f'<span class="rd-series-meta">{m["minutes"]} 分钟<em data-progress-for="{e(a["id"])}"></em></span></a></li>'
        )
    tags = "".join(f'<span class="rd-tag">{e(t)}</span>' for t in ser.get("tags", []) or [])
    ids = ",".join(a["id"] for a in members)
    return f"""<section class="rd-card rd-series-card" data-series="{e(ser['id'])}" data-series-articles="{e(ids)}">
  <div class="rd-card-top"><span class="rd-card-icon" aria-hidden="true">{e(ser.get('icon', '📚'))}</span><span class="rd-card-meta">{len(members)} 篇 · 共约 {minutes} 分钟 · <span data-series-progress>未开始</span></span></div>
  <h3>{e(ser['name'])} <small>（{len(members)} 篇）</small></h3>
  <p class="rd-card-author">{e(ser.get('author', ''))}</p>
  <p class="rd-card-summary">{e(ser.get('description', ''))}</p>
  <ol class="rd-series-list">{''.join(rows)}</ol>
  <div class="rd-card-foot"><div class="rd-tags">{tags}</div></div>
</section>"""


def build_index_html(config: dict, metas: Dict[str, dict]) -> str:
    sections = []
    nav = []
    for cat in config["categories"]:
        arts = [a for a in config["articles"] if a["category"] == cat["id"]]
        nav.append(f'<a href="#{e(cat["id"])}">{e(cat.get("icon", ""))} {e(cat["name"])}<span>{len(arts)}</span></a>')
        cards = []
        done_series = set()
        for a in arts:
            ser = series_of(config, a)
            if ser:
                if ser["id"] in done_series:
                    continue
                done_series.add(ser["id"])
                cards.append(_series_card_html(config, ser, metas))
                continue
            m = metas[a["id"]]
            tags = "".join(f'<span class="rd-tag">{e(t)}</span>' for t in a["tags"])
            cards.append(f"""<a class="rd-card" href="/{config['article_dir']}/{e(a['id'])}.html" data-article="{e(a['id'])}">
  <div class="rd-card-top"><span class="rd-card-icon" aria-hidden="true">{e(cat.get('icon', ''))}</span><span class="rd-card-meta">约 {m['minutes']} 分钟 · {m['chapters']} 章</span></div>
  <h3>{e(a['title'])}</h3>
  <p class="rd-card-author">{e(a.get('author', ''))}</p>
  <p class="rd-card-summary">{e(a.get('summary', ''))}</p>
  <div class="rd-card-foot"><div class="rd-tags">{tags}</div><span class="rd-card-progress" data-progress-for="{e(a['id'])}">开始阅读 →</span></div>
</a>""")
        if not cards:
            cards.append('<div class="rd-card rd-card--empty"><span>待收录</span><p>暂无文章，收录后会自动出现在这里。</p></div>')
        sections.append(f"""<section class="rd-shelf" id="{e(cat['id'])}">
  <div class="rd-shelf-head"><h2><span aria-hidden="true">{e(cat.get('icon', ''))}</span> {e(cat['name'])} <small>{len(arts)}</small></h2><p>{e(cat.get('description', ''))}</p></div>
  <div class="rd-shelf-grid">{''.join(cards)}</div>
</section>""")
    total = len(config["articles"])
    return HEAD.format(title=e(config["title"])) + f"""<body class="app-workbench rd-page rd-library" data-page="reading">
<div id="app-shell" data-active="reading"></div>
<main class="rd-main">
<header class="rd-hero rd-hero--library">
  <p class="rd-kicker">READING · 交易心法书库</p>
  <div class="rd-hero-row"><h1>{e(config['title'])}</h1>
  <details class="rd-info"><summary aria-label="关于本板块">ⓘ</summary><div class="rd-info-panel"><p>{e(config.get('subtitle', ''))}。</p><p>文章按分类陈列；每篇都有自动目录、阅读进度、重点提示框和术语释义。</p><p>阅读进度只保存在本机浏览器，换设备不会同步。</p></div></details></div>
  <p class="rd-meta">已收录 {total} 篇 · {len(config['categories'])} 个分类</p>
  <nav class="rd-shelf-nav" aria-label="分类">{''.join(nav)}</nav>
</header>
{''.join(sections)}
</main>
</body>
</html>
"""


def series_of(config: dict, article: dict) -> Optional[dict]:
    sid = article.get("series")
    return next((s_ for s_ in config.get("series", []) if s_["id"] == sid), None) if sid else None


def series_members(config: dict, article: dict) -> List[dict]:
    ser = series_of(config, article)
    return list(ser["articles"]) if ser else []


def _series_nav_html(config: dict, article: dict) -> str:
    ser = series_of(config, article)
    if not ser:
        return ""
    members = ser["articles"]
    pos = members.index(article) + 1
    items = []
    for i, a in enumerate(members, 1):
        label = e(a.get("short_title") or a["title"])
        if a is article:
            items.append(f'<li class="is-current" aria-current="page"><span class="rd-series-no">{i}</span><span>{label}</span></li>')
        else:
            items.append(f'<li><a href="/{config["article_dir"]}/{e(a["id"])}.html"><span class="rd-series-no">{i}</span><span>{label}</span></a></li>')
    return (
        f'<nav class="rd-series" aria-label="{e(ser["name"])}"><div class="rd-series-head"><span class="rd-series-name">{e(ser.get("icon", "📚"))} {e(ser["name"])}</span>'
        f'<span class="rd-series-pos">第 {pos} / {len(members)} 篇</span></div><ol>{"".join(items)}</ol></nav>'
    )


# ------------------------------------------------------------- generate
def generate(config_path: Path = CONFIG_PATH, glossary_path: Path = GLOSSARY_PATH, out_dir: Path = OUTPUT_DIR, root: Path = ROOT) -> List[Path]:
    config = load_config(config_path, root)
    glossary = load_glossary(glossary_path)
    out_dir = Path(out_dir)
    art_root = out_dir / config["article_dir"]
    art_root.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    metas: Dict[str, dict] = {}
    rendered = {}
    for article in config["articles"]:
        body, meta = render_article(article, config, glossary)
        metas[article["id"]] = meta
        rendered[article["id"]] = body
        res: RenderResult = meta["result"]
        for msg in res.warnings:
            print(f"! {article['id']}: {msg}")
        for src in res.missing_images:
            print(f"! {article['id']}: 找不到图片 {src}")
        img_dir = art_root / article["id"]
        if img_dir.exists():
            shutil.rmtree(img_dir)
        for src, rel in meta["copies"]:
            dest = img_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    for idx, article in enumerate(config["articles"]):
        same = series_members(config, article) or [
            a for a in config["articles"] if a["category"] == article["category"] and not a.get("series")
        ]
        pos = same.index(article)
        neighbours = (same[pos - 1] if pos > 0 else None, same[pos + 1] if pos + 1 < len(same) else None)
        html = build_article_html(article, rendered[article["id"]], metas[article["id"]], config, glossary, neighbours)
        path = art_root / f"{article['id']}.html"
        path.write_text(html, encoding="utf-8")
        written.append(path)
    index = out_dir / config["output_page"]
    index.write_text(build_index_html(config, metas), encoding="utf-8")
    written.insert(0, index)
    print(f"✓ 阅读板块：{len(config['articles'])} 篇 → {index}")
    return written


if __name__ == "__main__":
    generate()
