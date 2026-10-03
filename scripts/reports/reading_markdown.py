# -*- coding: utf-8 -*-
"""阅读板块用的小型 Markdown 渲染器（零依赖，可复用）。

覆盖长文常用语法：标题（GitHub 风格锚点）、段落、粗体/斜体/行内代码、链接、图片、
有序/无序/任务列表（可嵌套）、引用（可嵌套）、GFM 表格、分隔线、代码块
（```mermaid 交给前端渲染）、原样透传的 <details>/<summary>。

阅读板块扩展：
- ``::: <type> 可选标题`` … ``:::``   提示框（类型由配置 callouts 定义，可嵌套）
- ``==重点句==``                       荧光笔高亮
- ``[[术语]]`` / ``[[显示文字|术语]]``   术语释义气泡（释义来自术语表）
- 以 💡 / ⚠️ 开头的段落或引用自动套用对应提示框（emoji_map）
- 术语表自动标注：每个术语在全文首次出现处加释义（glossary_auto）；
  提示框类型配置 ``auto_terms: false`` 时，框内不自动标注（如要点卡、阅读说明）

内容来自本仓库、由用户维护，属可信输入；正文文本仍会做 HTML 转义，仅白名单块级标签透传。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

RAW_HTML_TAGS = ("details", "summary")
FENCE_RE = re.compile(r"^(\s*)(```+|~~~+)\s*([\w+-]*)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
CALLOUT_OPEN_RE = re.compile(r"^\s*:::\s*([\w-]+)\s*(.*?)\s*$")
CALLOUT_CLOSE_RE = re.compile(r"^\s*:::\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
IMAGE_ONLY_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)$")
RAW_LINE_RE = re.compile(r"^\s*</?(%s)\b[^>]*>\s*$" % "|".join(RAW_HTML_TAGS), re.I)
RAW_INLINE_SUMMARY_RE = re.compile(r"^\s*<summary>(.*)</summary>\s*$", re.I)


@dataclass
class Heading:
    level: int
    text: str
    id: str


@dataclass
class RenderResult:
    html: str
    headings: List[Heading]
    images: List[str]            # 解析后的图片 URL
    missing_images: List[str]    # 源文件里找不到的图片引用
    terms_used: List[str]
    has_mermaid: bool
    warnings: List[str] = field(default_factory=list)


def slugify(text: str) -> str:
    """GitHub 风格锚点：小写、去掉标点（保留中文/字母/数字/连字符/空格），空格转连字符。"""
    text = strip_inline(text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def strip_inline(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__|==|`)", "", text)
    text = re.sub(r"(?<!\*)\*(?!\*)", "", text)
    return text


class MarkdownRenderer:
    """把一篇 Markdown 渲染为 HTML 片段，同时收集标题、图片、术语等元信息。"""

    def __init__(
        self,
        callouts: Optional[Dict[str, dict]] = None,
        emoji_map: Optional[Dict[str, str]] = None,
        glossary: Optional[Dict[str, dict]] = None,
        glossary_auto: bool = False,
        image_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.callouts = callouts or {}
        self.emoji_map = emoji_map or {}
        self.glossary = glossary or {}
        self.glossary_auto = glossary_auto
        self.image_resolver = image_resolver
        # 别名 → 术语主键；按长度降序匹配，避免"龙头股"被"龙头"截断
        self._alias: Dict[str, str] = {}
        for key, entry in self.glossary.items():
            self._alias[key] = key
            for alias in (entry or {}).get("aliases", []) or []:
                self._alias[str(alias)] = key
        self._alias_order = sorted(self._alias, key=len, reverse=True)
        self._auto_re = (
            re.compile("|".join(re.escape(a) for a in self._alias_order)) if self._alias_order else None
        )

    # ------------------------------------------------------------------ API
    def render(self, text: str) -> RenderResult:
        self.headings: List[Heading] = []
        self._slugs: Dict[str, int] = {}
        self.images: List[str] = []
        self.missing: List[str] = []
        self.terms_used: List[str] = []
        self._terms_seen: set = set()
        self.has_mermaid = False
        self.warnings: List[str] = []
        self._no_emoji = 0
        self._no_auto = 0
        lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
        body = self._blocks(lines)
        return RenderResult(body, self.headings, self.images, self.missing, self.terms_used, self.has_mermaid, self.warnings)

    # --------------------------------------------------------------- blocks
    def _blocks(self, lines: List[str]) -> str:
        out: List[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            m = FENCE_RE.match(line)
            if m:
                fence, lang = m.group(2), m.group(3).lower()
                j = i + 1
                buf = []
                while j < n and not lines[j].strip().startswith(fence[:3]):
                    buf.append(lines[j])
                    j += 1
                out.append(self._code(lang, "\n".join(buf)))
                i = j + 1
                continue
            m = CALLOUT_OPEN_RE.match(line)
            if m:
                depth, j, buf = 1, i + 1, []
                while j < n:
                    if CALLOUT_OPEN_RE.match(lines[j]):
                        depth += 1
                    elif CALLOUT_CLOSE_RE.match(lines[j]):
                        depth -= 1
                        if depth == 0:
                            break
                    buf.append(lines[j])
                    j += 1
                if depth:
                    self.warnings.append(f"第 {i + 1} 行的 ::: {m.group(1)} 没有闭合")
                kind = m.group(1).lower()
                quiet = (self.callouts.get(kind) or {}).get("auto_terms") is False
                self._no_auto += 1 if quiet else 0
                try:
                    inner = self._blocks(buf)
                finally:
                    self._no_auto -= 1 if quiet else 0
                out.append(self._callout(kind, m.group(2), inner))
                i = j + 1
                continue
            m = HEADING_RE.match(line)
            if m:
                out.append(self._heading(len(m.group(1)), m.group(2)))
                i += 1
                continue
            if HR_RE.match(line):
                out.append("<hr>")
                i += 1
                continue
            if RAW_LINE_RE.match(line) or RAW_INLINE_SUMMARY_RE.match(line):
                sm = RAW_INLINE_SUMMARY_RE.match(line)
                out.append(f"<summary>{self._inline(sm.group(1))}</summary>" if sm else line.strip())
                i += 1
                continue
            if line.lstrip().startswith("|") and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
                j = i + 2
                rows = []
                while j < n and lines[j].lstrip().startswith("|"):
                    rows.append(lines[j])
                    j += 1
                out.append(self._table(line, lines[i + 1], rows))
                i = j
                continue
            if line.lstrip().startswith(">"):
                j, buf = i, []
                while j < n and lines[j].lstrip().startswith(">"):
                    buf.append(re.sub(r"^\s*> ?", "", lines[j]))
                    j += 1
                out.append(self._blockquote(buf))
                i = j
                continue
            if LIST_RE.match(line):
                j = self._list_end(lines, i)
                out.append(self._list(lines[i:j]))
                i = j
                continue
            # 段落：遇到空行或其他块起始即结束
            j, buf = i, []
            while j < n and lines[j].strip() and not self._starts_block(lines, j):
                buf.append(lines[j].strip())
                j += 1
            if not buf:  # 防御：未识别的块起始当作段落
                buf, j = [line.strip()], i + 1
            out.append(self._paragraph(buf, lines, j))
            if getattr(self, "_consumed_caption", False):
                self._consumed_caption = False
                j = self._skip_caption(lines, j)
            i = j
        return "\n".join(x for x in out if x)

    def _starts_block(self, lines: List[str], j: int) -> bool:
        line = lines[j]
        s = line.lstrip()
        if FENCE_RE.match(line) or HEADING_RE.match(line) or CALLOUT_OPEN_RE.match(line) or RAW_LINE_RE.match(line) or RAW_INLINE_SUMMARY_RE.match(line):
            return True
        if s.startswith(">") or LIST_RE.match(line) or HR_RE.match(line):
            return True
        if s.startswith("|") and j + 1 < len(lines) and TABLE_SEP_RE.match(lines[j + 1]):
            return True
        return False

    def _list_end(self, lines: List[str], i: int) -> int:
        base = len(LIST_RE.match(lines[i]).group(1))
        j = i + 1
        n = len(lines)
        while j < n:
            line = lines[j]
            if not line.strip():
                # 空行后仍是同级/更深列表项或缩进续行才继续
                k = j + 1
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and (LIST_RE.match(lines[k]) and len(LIST_RE.match(lines[k]).group(1)) >= base
                              or len(lines[k]) - len(lines[k].lstrip()) > base + 1):
                    j = k
                    continue
                return j
            m = LIST_RE.match(line)
            if m and len(m.group(1)) < base:
                return j
            if not m and len(line) - len(line.lstrip()) <= base and self._starts_block(lines, j):
                return j
            j += 1
        return j

    # ------------------------------------------------------------- emitters
    def _heading(self, level: int, raw: str) -> str:
        plain = strip_inline(raw).strip()
        slug = slugify(raw) or f"section-{len(self.headings) + 1}"
        if slug in self._slugs:
            self._slugs[slug] += 1
            slug = f"{slug}-{self._slugs[slug]}"
        else:
            self._slugs[slug] = 0
        self.headings.append(Heading(level, plain, slug))
        anchor = f'<a class="rd-anchor" href="#{slug}" aria-label="本节链接">#</a>' if level in (2, 3) else ""
        return f'<h{level} id="{slug}">{self._inline(raw, glossary=False)}{anchor}</h{level}>'

    def _code(self, lang: str, code: str) -> str:
        if lang == "mermaid":
            self.has_mermaid = True
            return (
                '<figure class="rd-diagram"><div class="rd-diagram-scroll">'
                f'<pre class="mermaid">{html.escape(code)}</pre></div>'
                '<button type="button" class="rd-diagram-zoom" aria-label="放大图示">⤢ 放大</button></figure>'
            )
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return f'<pre class="rd-code"><code{cls}>{html.escape(code)}</code></pre>'

    def _callout(self, kind: str, title: str, inner: str) -> str:
        spec = self.callouts.get(kind) or self.callouts.get("note") or {}
        if kind not in self.callouts:
            self.warnings.append(f"未知提示框类型 ::: {kind}，按 note 渲染")
        tone = spec.get("tone", kind)
        label = title.strip() or spec.get("label", "")
        icon = spec.get("icon", "")
        head = (
            f'<div class="rd-callout-title"><span class="rd-callout-icon" aria-hidden="true">{html.escape(icon)}</span>'
            f'<span>{self._inline(label, glossary=False)}</span></div>' if (label or icon) else ""
        )
        return f'<aside class="rd-callout rd-callout--{html.escape(tone)}" data-kind="{html.escape(kind)}">{head}<div class="rd-callout-body">{inner}</div></aside>'

    def _emoji_kind(self, text: str) -> Optional[str]:
        if getattr(self, "_no_emoji", 0):
            return None
        s = text.lstrip()
        for emoji, kind in self.emoji_map.items():
            if s.startswith(emoji):
                return kind
        return None

    def _blockquote(self, buf: List[str]) -> str:
        first = next((x for x in buf if x.strip()), "")
        kind = self._emoji_kind(first)
        if kind:
            self._no_emoji += 1
            try:
                inner = self._blocks(buf)
            finally:
                self._no_emoji -= 1
            return self._auto_callout(kind, inner)
        inner = self._blocks(buf)
        return f'<blockquote class="rd-quote">{inner}</blockquote>'

    def _auto_callout(self, kind: str, inner: str) -> str:
        spec = self.callouts.get(kind, {})
        tone = spec.get("tone", kind)
        return f'<aside class="rd-callout rd-callout--{html.escape(tone)} rd-callout--auto" data-kind="{html.escape(kind)}"><div class="rd-callout-body">{inner}</div></aside>'

    def _paragraph(self, buf: List[str], lines: List[str], j: int) -> str:
        if len(buf) == 1:
            m = IMAGE_ONLY_RE.match(buf[0])
            if m:
                caption = self._peek_caption(lines, j)
                if caption is not None:
                    self._consumed_caption = True
                return self._figure(m.group(1), m.group(2), m.group(3), caption)
        body = "<br>".join(self._inline(x) for x in buf)
        kind = self._emoji_kind(buf[0])
        if kind:
            return self._auto_callout(kind, f"<p>{body}</p>")
        return f"<p>{body}</p>"

    def _peek_caption(self, lines: List[str], j: int) -> Optional[str]:
        k = j
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k < len(lines):
            s = lines[k].strip()
            m = re.match(r"^\*([^*].*[^*])\*$", s)
            if m and (k + 1 >= len(lines) or not lines[k + 1].strip()):
                return m.group(1)
        return None

    def _skip_caption(self, lines: List[str], j: int) -> int:
        while j < len(lines) and not lines[j].strip():
            j += 1
        return j + 1

    def _resolve_image(self, src: str) -> str:
        if self.image_resolver:
            url = self.image_resolver(src)
            if url is None:
                self.missing.append(src)
                return src
            src = url
        self.images.append(src)
        return src

    def _figure(self, alt: str, src: str, title: Optional[str], caption: Optional[str]) -> str:
        url = self._resolve_image(src)
        e = lambda v: html.escape(v, quote=True)
        cap = caption if caption is not None else (title or "")
        figcap = f"<figcaption>{self._inline(cap, glossary=False)}</figcaption>" if cap else ""
        return (
            f'<figure class="rd-figure"><button type="button" class="rd-zoom" data-image="{e(url)}" data-caption="{e(strip_inline(cap or alt))}" '
            f'aria-label="放大图片：{e(alt)}"><img src="{e(url)}" alt="{e(alt)}" loading="lazy" decoding="async"></button>{figcap}</figure>'
        )

    def _table(self, header: str, sep: str, rows: List[str]) -> str:
        aligns = []
        for cell in self._cells(sep):
            c = cell.strip()
            aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "")
        def tr(cells, tag):
            out = []
            for k, cell in enumerate(cells):
                style = f' style="text-align:{aligns[k]}"' if k < len(aligns) and aligns[k] else ""
                out.append(f"<{tag}{style}>{self._inline(cell.strip())}</{tag}>")
            return "<tr>" + "".join(out) + "</tr>"
        head_cells = self._cells(header)
        width = len(head_cells)
        body = []
        for row in rows:
            cells = self._cells(row)
            cells = (cells + [""] * width)[:width]
            body.append(tr(cells, "td"))
        return (
            '<div class="rd-table-wrap" tabindex="0"><table class="rd-table">'
            f"<thead>{tr(head_cells, 'th')}</thead><tbody>{''.join(body)}</tbody></table></div>"
        )

    @staticmethod
    def _cells(row: str) -> List[str]:
        s = row.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|") and not s.endswith("\\|"):
            s = s[:-1]
        return [c.replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]

    def _list(self, lines: List[str]) -> str:
        first = LIST_RE.match(lines[0])
        base = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        items: List[List[str]] = []
        for line in lines:
            m = LIST_RE.match(line)
            if m and len(m.group(1)) == base:
                items.append([m.group(3)])
            elif items:
                items[-1].append(line[base + 2:] if line.startswith(" " * (base + 2)) else line.strip())
        lis = []
        task_list = False
        for item in items:
            head, rest = item[0], item[1:]
            check = re.match(r"^\[([ xX])\]\s+(.*)$", head)
            cls = ""
            if check:
                task_list = True
                checked = " checked" if check.group(1).lower() == "x" else ""
                head_html = f'<label class="rd-task"><input type="checkbox"{checked}><span>{self._inline(check.group(2))}</span></label>'
                cls = ' class="rd-task-item"'
            else:
                head_html = self._inline(head)
            sub = self._blocks(rest) if any(x.strip() for x in rest) else ""
            lis.append(f"<li{cls}>{head_html}{sub}</li>")
        tag = "ol" if ordered else "ul"
        start = ""
        if ordered:
            num = int(re.match(r"\d+", first.group(2)).group(0))
            if num != 1:
                start = f' start="{num}"'
        klass = ' class="rd-tasks"' if task_list else ""
        return f"<{tag}{start}{klass}>{''.join(lis)}</{tag}>"

    # --------------------------------------------------------------- inline
    def _inline(self, text: str, glossary: bool = True) -> str:
        slots: List[str] = []

        def keep(fragment: str) -> str:
            slots.append(fragment)
            return f"\x00{len(slots) - 1}\x00"

        text = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
        text = re.sub(
            r"!\[([^\]]*)\]\(([^)\s]+)\)",
            lambda m: keep(
                f'<img class="rd-inline-img" src="{html.escape(self._resolve_image(m.group(2)), quote=True)}" alt="{html.escape(m.group(1), quote=True)}" loading="lazy">'
            ),
            text,
        )
        text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", lambda m: keep(self._term(m.group(1), m.group(2))), text)
        text = re.sub(r"\[\[([^\]]+)\]\]", lambda m: keep(self._term(m.group(1), m.group(1))), text)

        def link(m):
            label, href = m.group(1), m.group(2)
            ext = href.startswith(("http://", "https://"))
            attrs = ' target="_blank" rel="noopener"' if ext else ""
            return keep(f'<a href="{html.escape(href, quote=True)}"{attrs}>{self._inline(label, glossary=False)}</a>')

        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
        text = html.escape(text, quote=False)
        text = re.sub(r"==(?=\S)(.+?)(?<=\S)==", r'<mark class="rd-mark">\1</mark>', text)
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<![\*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\*\w])", r"<em>\1</em>", text)
        if glossary and self.glossary_auto and self._auto_re is not None and not getattr(self, "_no_auto", 0):
            text = self._auto_terms(text)
        text = re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)
        return text

    def _term(self, label: str, key: str) -> str:
        key = self._alias.get(key.strip(), key.strip())
        if key not in self.glossary:
            self.warnings.append(f"术语表里没有 [[{key}]]")
            return html.escape(label)
        self._terms_seen.add(key)
        if key not in self.terms_used:
            self.terms_used.append(key)
        return f'<dfn class="rd-term" tabindex="0" role="button" data-term="{html.escape(key, quote=True)}">{html.escape(label)}</dfn>'

    def _auto_terms(self, text: str) -> str:
        """仅替换纯文本片段（跳过标签与占位符），每个术语全文只标第一次。"""
        parts = re.split(r"(<[^>]+>|\x00\d+\x00)", text)
        for idx, part in enumerate(parts):
            if not part or part.startswith("<") or part.startswith("\x00"):
                continue

            def sub(m):
                key = self._alias[m.group(0)]
                if key in self._terms_seen:
                    return m.group(0)
                return self._term(m.group(0), key)

            parts[idx] = self._auto_re.sub(sub, part)
        return "".join(parts)


def render_markdown(text: str, **kwargs) -> RenderResult:
    return MarkdownRenderer(**kwargs).render(text)
